"""Reference-corpus eval harness.

Runs Detector A (and B/C if their backends are wired) against a JSONL
reference corpus, computes precision / recall / F1 per entity type and
overall, and prints a markdown summary. Used in CI on PRs that touch
detection (`pytest tests/integration -m eval`).

The reference corpus uses ONLY synthetic data per CLAUDE.md hard rule #7.
A starter corpus generator is `scripts/build_synthetic_corpus.py`; the
output is what this harness consumes.

Corpus schema (one JSON object per line):
    {
      "id": "synth-0001",
      "country": "DZ",
      "text": "Ali Benali, NIN 119504500001234567, ...",
      "spans": [
        {"start": 14, "end": 32, "entity_type": "national_id"},
        ...
      ]
    }
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from src.detectors.structural import StructuralDetector
from src.tenancy import CustomerContext, bind_customer, reset_customer


@dataclass(slots=True)
class _Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0


def _f1(c: _Counts) -> tuple[float, float, float]:
    p = c.tp / (c.tp + c.fp) if (c.tp + c.fp) else 0.0
    r = c.tp / (c.tp + c.fn) if (c.tp + c.fn) else 0.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f


async def _run(corpus_path: Path) -> dict[str, _Counts]:
    detector = StructuralDetector()
    counts: dict[str, _Counts] = defaultdict(_Counts)

    ctx = CustomerContext(customer_id="eval", country_code="DZ", plan="enterprise")
    tok = bind_customer(ctx)
    try:
        with corpus_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                text = rec["text"]
                gold = {(s["start"], s["end"], s["entity_type"]) for s in rec["spans"]}
                pred = {(d.start, d.end, d.entity_type) for d in await detector.detect(text)}
                for span in pred & gold:
                    counts[span[2]].tp += 1
                for span in pred - gold:
                    counts[span[2]].fp += 1
                for span in gold - pred:
                    counts[span[2]].fn += 1
    finally:
        reset_customer(tok)
    return counts


def _print_report(counts: dict[str, _Counts]) -> None:
    sys.stdout.write("# Eval report\n\n")
    sys.stdout.write("| entity_type | TP | FP | FN | precision | recall | F1 |\n")
    sys.stdout.write("|---|---|---|---|---|---|---|\n")
    overall = _Counts()
    for et, c in sorted(counts.items()):
        p, r, f = _f1(c)
        overall.tp += c.tp
        overall.fp += c.fp
        overall.fn += c.fn
        sys.stdout.write(f"| {et} | {c.tp} | {c.fp} | {c.fn} | {p:.3f} | {r:.3f} | {f:.3f} |\n")
    p, r, f = _f1(overall)
    sys.stdout.write(
        f"| **overall** | {overall.tp} | {overall.fp} | {overall.fn} | "
        f"**{p:.3f}** | **{r:.3f}** | **{f:.3f}** |\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("corpus", type=Path)
    parser.add_argument(
        "--min-f1", type=float, default=0.0, help="Exit non-zero if overall F1 < this value."
    )
    args = parser.parse_args()

    counts = asyncio.run(_run(args.corpus))
    _print_report(counts)

    overall = _Counts()
    for c in counts.values():
        overall.tp += c.tp
        overall.fp += c.fp
        overall.fn += c.fn
    _, _, f = _f1(overall)
    if args.min_f1 and f < args.min_f1:
        sys.stderr.write(f"\nFAIL: overall F1 {f:.3f} < min {args.min_f1:.3f}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
