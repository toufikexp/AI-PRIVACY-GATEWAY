from __future__ import annotations

import pytest
from src.detectors.base import Detection
from src.merge import MergeConfig, MergeEngine
from src.merge.engine import ExceptionEntry


@pytest.fixture
def merger() -> MergeEngine:
    return MergeEngine(
        config=MergeConfig(tier1_threshold=0.80, tier2_threshold=0.70, tier3_threshold=0.60)
    )


def _det(
    *,
    start: int,
    end: int,
    text: str,
    conf: float,
    tier: int,
    det: str = "structural",
    rid: str | None = None,
) -> Detection:
    return Detection(
        entity_type="person",
        start=start,
        end=end,
        text=text,
        confidence=conf,
        tier=tier,
        detector=det,
        rule_id=rid,
    )


def test_span_validation_drops_hallucinated(merger: MergeEngine) -> None:
    text = "Hello Alice"
    fake = _det(start=6, end=11, text="Bobbb", conf=0.9, tier=2, det="contextual")
    real = _det(start=6, end=11, text="Alice", conf=0.85, tier=2, det="ner")
    result = merger.merge(original_text=text, detections=[fake, real])
    assert len(result.span_invalid) == 1
    assert result.span_invalid[0] is fake
    assert len(result.accepted) == 1
    assert result.accepted[0].text == "Alice"


def test_below_threshold_filtered(merger: MergeEngine) -> None:
    text = "Hello Alice"
    weak = _det(start=6, end=11, text="Alice", conf=0.50, tier=2, det="contextual")
    result = merger.merge(original_text=text, detections=[weak])
    assert result.accepted == []
    assert len(result.below_threshold) == 1


def test_overlap_longer_wins(merger: MergeEngine) -> None:
    text = "Hello Alice Cooper today"
    short = _det(start=6, end=11, text="Alice", conf=0.85, tier=2, det="ner")
    longer = _det(start=6, end=18, text="Alice Cooper", conf=0.85, tier=2, det="ner")
    result = merger.merge(original_text=text, detections=[short, longer])
    assert len(result.accepted) == 1
    assert result.accepted[0].text == "Alice Cooper"


def test_overlap_detector_precedence_on_tie(merger: MergeEngine) -> None:
    text = "Hello Alice"
    a_struct = _det(start=6, end=11, text="Alice", conf=0.85, tier=2, det="structural")
    a_ner = _det(start=6, end=11, text="Alice", conf=0.85, tier=2, det="ner")
    a_ctx = _det(start=6, end=11, text="Alice", conf=0.85, tier=2, det="contextual")
    result = merger.merge(original_text=text, detections=[a_ctx, a_ner, a_struct])
    assert len(result.accepted) == 1
    assert result.accepted[0].detector == "structural"


def test_confidence_aggregated_when_corroborated(merger: MergeEngine) -> None:
    text = "Hello Alice"
    a_ner = _det(start=6, end=11, text="Alice", conf=0.85, tier=2, det="ner")
    a_ctx = _det(start=6, end=11, text="Alice", conf=0.85, tier=2, det="contextual")
    result = merger.merge(original_text=text, detections=[a_ner, a_ctx])
    assert len(result.accepted) == 1
    assert result.accepted[0].confidence == pytest.approx(0.90, abs=1e-6)


def test_exception_suppresses_match(merger: MergeEngine) -> None:
    text = "Call Alice"
    d = _det(start=5, end=10, text="Alice", conf=0.95, tier=1, det="ner", rid="ner.person")
    exc = [ExceptionEntry(rule_id="ner.person", entity_type=None, text_match="Alice")]
    result = merger.merge(original_text=text, detections=[d], exceptions=exc)
    assert result.accepted == []
    assert len(result.exception_suppressed) == 1


def test_higher_tier_wins_on_equal_span(merger: MergeEngine) -> None:
    text = "Hello Alice"
    t2 = _det(start=6, end=11, text="Alice", conf=0.85, tier=2, det="ner")
    t1 = _det(start=6, end=11, text="Alice", conf=0.85, tier=1, det="structural")
    result = merger.merge(original_text=text, detections=[t2, t1])
    assert len(result.accepted) == 1
    assert result.accepted[0].tier == 1
