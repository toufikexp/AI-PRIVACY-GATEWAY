"""Detector B — multilingual NER.

Two backends:
  * `stub`  — pure-Python heuristic (capitalised tokens, common honorifics).
              Fast, dependency-free, used in CI and dev. Not production-grade.
  * `onnx`  — mDeBERTa-v3-base ONNX int8 runtime. Loaded at startup; CPU
              inference at 40-80 ms per request. Requires `onnxruntime` and
              `transformers` (declared as optional extras).

Backend choice flows from `GATEWAY_NER_BACKEND` and is wired in `app.py`.
Both backends produce `Detection` objects with detector="ner". The merge
engine downgrades NER spans behind structural ones in overlap clusters.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from src.detectors.base import Detection, EntityType
from src.tenancy import require_customer

DETECTOR_NAME = "ner"

# Honorifics that mark the next capitalised token(s) as a person name.
_HONORIFICS = re.compile(
    r"\b(?:Mr|Mrs|Ms|Dr|M|Mme|Mlle|Sayed|Sayyid|Sheikh|Madame|Monsieur)\.?\s+",
    re.IGNORECASE,
)
# Capitalised token, with at least one letter; permits Latin + Arabic ranges.
_CAP_TOKEN = re.compile(r"\b[A-ZÀ-ÖØ-Ý][A-Za-zÀ-ÖØ-öø-ÿ؀-ۿ'\-]+")
# Two-or-more consecutive capitalised tokens look like a name.
_NAME_RUN = re.compile(
    r"\b[A-ZÀ-ÖØ-Ý][A-Za-zÀ-ÖØ-öø-ÿ'\-]+(?:\s+[A-ZÀ-ÖØ-Ý][A-Za-zÀ-ÖØ-öø-ÿ'\-]+){1,3}"
)


@dataclass(frozen=True, slots=True)
class _Span:
    start: int
    end: int
    text: str
    entity_type: EntityType


class StubNERDetector:
    """Heuristic NER for dev/CI.

    Recognises:
      * person  — token sequence following a known honorific, OR two
                  consecutive capitalised tokens
      * location, organization — not produced by the stub (NER quality
                  here is only a smoke check; rely on Detector C / ONNX)
    """

    name = DETECTOR_NAME

    async def detect(self, text: str) -> list[Detection]:
        require_customer()
        spans = list(self._person_spans(text))
        spans = _merge_overlapping(spans)
        return [
            Detection(
                entity_type=s.entity_type,
                start=s.start,
                end=s.end,
                text=text[s.start : s.end],
                confidence=0.85,
                tier=2,
                detector=DETECTOR_NAME,
                rule_id="ner.stub.person",
            )
            for s in spans
        ]

    def _person_spans(self, text: str) -> Iterable[_Span]:
        # Honorific + capitalised run(s)
        for match in _HONORIFICS.finditer(text):
            tail = text[match.end() :]
            tail_match = re.match(
                r"[A-ZÀ-ÖØ-Ý][A-Za-zÀ-ÖØ-öø-ÿ'\-]+(?:\s+[A-ZÀ-ÖØ-Ý][A-Za-zÀ-ÖØ-öø-ÿ'\-]+)*",
                tail,
            )
            if tail_match:
                start = match.end() + tail_match.start()
                end = match.end() + tail_match.end()
                yield _Span(start, end, text[start:end], "person")
        # Two-or-more capitalised tokens in a row
        for match in _NAME_RUN.finditer(text):
            yield _Span(match.start(), match.end(), match.group(0), "person")


class OnnxNERDetector:
    """Real mDeBERTa NER backend. Lazy imports to keep the stub path
    runnable without onnxruntime/transformers installed.
    """

    name = DETECTOR_NAME

    def __init__(self, *, model_path: str, tokenizer_path: str) -> None:
        # Lazy imports — production deployments install onnxruntime + transformers.
        try:
            import onnxruntime as ort
            from transformers import AutoTokenizer
        except ImportError as exc:  # pragma: no cover - exercised in prod env
            raise RuntimeError(
                "OnnxNERDetector requires `onnxruntime` and `transformers`; "
                "install them with `pip install -e '.[ner]'`"
            ) from exc

        self._session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        self._tokenizer = AutoTokenizer.from_pretrained(tokenizer_path)
        # Label map mirrors the model's training data; remap to our EntityType vocab.
        self._label_map: dict[int, EntityType | None] = {
            0: None,  # O
            1: "person",
            2: "person",
            3: "organization",
            4: "organization",
            5: "location",
            6: "location",
        }

    async def detect(self, text: str) -> list[Detection]:  # pragma: no cover - infra
        require_customer()
        encoding = self._tokenizer(text, return_offsets_mapping=True, return_tensors="np")
        offsets = encoding.pop("offset_mapping")[0]
        outputs = self._session.run(None, dict(encoding))
        logits = outputs[0][0]
        labels = logits.argmax(-1)

        spans: list[_Span] = []
        cur: _Span | None = None
        for idx, label_id in enumerate(labels):
            ent = self._label_map.get(int(label_id))
            start, end = int(offsets[idx][0]), int(offsets[idx][1])
            if ent is None or end == 0:
                if cur is not None:
                    spans.append(cur)
                    cur = None
                continue
            if cur is None or cur.entity_type != ent or start > cur.end + 1:
                if cur is not None:
                    spans.append(cur)
                cur = _Span(start=start, end=end, text=text[start:end], entity_type=ent)
            else:
                cur = _Span(
                    start=cur.start,
                    end=end,
                    text=text[cur.start : end],
                    entity_type=ent,
                )
        if cur is not None:
            spans.append(cur)

        return [
            Detection(
                entity_type=s.entity_type,
                start=s.start,
                end=s.end,
                text=s.text,
                confidence=0.90,
                tier=2,
                detector=DETECTOR_NAME,
                rule_id=f"ner.onnx.{s.entity_type}",
            )
            for s in spans
        ]


def _merge_overlapping(spans: list[_Span]) -> list[_Span]:
    if not spans:
        return []
    spans.sort(key=lambda s: (s.start, -s.end))
    out = [spans[0]]
    for s in spans[1:]:
        last = out[-1]
        if s.start < last.end:
            if s.end > last.end:
                out[-1] = _Span(
                    start=last.start,
                    end=s.end,
                    text=last.text + s.text[last.end - s.start :],
                    entity_type=last.entity_type,
                )
            continue
        out.append(s)
    return out


class TransformersNERDetector:
    """Real HuggingFace `transformers` NER pipeline.

    Loads any multilingual NER model — default is
    `Davlan/distilbert-base-multilingual-cased-ner-hrl` (PER/LOC/ORG, Arabic +
    French + English among many). Supports any token-classification model
    that returns IOB2 / IOB1 tags. Heavy import path; gated behind
    `GATEWAY_NER_BACKEND=transformers`.
    """

    name = DETECTOR_NAME

    def __init__(self, *, hf_model: str, aggregation: str = "simple") -> None:
        try:
            from transformers import (
                AutoModelForTokenClassification,
                AutoTokenizer,
                pipeline,
            )
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "TransformersNERDetector requires `transformers` and `torch`; "
                "install them with `pip install -e '.[ner]'`"
            ) from exc

        tok = AutoTokenizer.from_pretrained(hf_model)
        mdl = AutoModelForTokenClassification.from_pretrained(hf_model)
        self._pipeline = pipeline(
            "token-classification",
            model=mdl,
            tokenizer=tok,
            aggregation_strategy=aggregation,
        )

    async def detect(self, text: str) -> list[Detection]:
        require_customer()
        # Pipeline is sync (PyTorch). Run it in a worker thread so we don't
        # block the event loop.
        import asyncio

        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(None, self._pipeline, text)

        out: list[Detection] = []
        for item in raw:
            label = str(item.get("entity_group") or item.get("entity") or "").upper()
            entity_type = _hf_label_to_entity_type(label)
            if entity_type is None:
                continue
            start = int(item["start"])
            end = int(item["end"])
            score = float(item.get("score", 0.7))
            out.append(
                Detection(
                    entity_type=entity_type,
                    start=start,
                    end=end,
                    text=text[start:end],
                    confidence=min(0.99, score),
                    tier=2,
                    detector=DETECTOR_NAME,
                    rule_id=f"ner.hf.{entity_type}",
                )
            )
        return out


def _hf_label_to_entity_type(label: str) -> EntityType | None:
    # IOB-style tags often look like "B-PER" / "I-LOC". Split on '-'.
    bare = label.split("-")[-1]
    if bare in {"PER", "PERSON"}:
        return "person"
    if bare in {"LOC", "GPE", "LOCATION"}:
        return "location"
    if bare in {"ORG", "ORGANIZATION"}:
        return "organization"
    if bare in {"DATE", "TIME"}:
        return "date"
    if bare in {"MONEY"}:
        return "monetary"
    return None


def make_detector(
    *,
    backend: str,
    model_path: str | None = None,
    tokenizer_path: str | None = None,
    hf_model: str | None = None,
    aggregation: str = "simple",
) -> object:
    """Construct the configured NER detector. Called by app.py at startup."""
    if backend == "onnx":
        if not model_path or not tokenizer_path:
            raise ValueError("ner_backend=onnx requires ner_model_path and ner_tokenizer_path")
        return OnnxNERDetector(model_path=model_path, tokenizer_path=tokenizer_path)
    if backend == "transformers":
        if not hf_model:
            raise ValueError("ner_backend=transformers requires ner_hf_model")
        return TransformersNERDetector(hf_model=hf_model, aggregation=aggregation)
    return StubNERDetector()
