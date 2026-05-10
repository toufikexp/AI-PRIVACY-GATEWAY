"""Merge engine — combines outputs of detectors A, B, and C.

ARCHITECTURE §4.3. Resolution order:
  1. Span coverage validation against the original input text.
     Hallucinated spans (Detector C) are dropped — CLAUDE.md hard rule #6.
  2. Customer Tier-1 exception application: detections matching a
     customer-validated false-positive entry are removed but logged.
  3. Confidence threshold filtering by tier.
  4. Overlap resolution: longer span wins; on equal length the detector
     precedence is structural > NER > LLM.
  5. Confidence aggregation: spans seen by >1 detector get max + 0.05
     (capped at 0.99).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.detectors.base import Detection

# Lower number = higher precedence.
DETECTOR_PRECEDENCE: dict[str, int] = {
    "structural": 0,
    "structural.dz": 0,
    "ner": 1,
    "contextual": 2,
}


@dataclass(frozen=True, slots=True)
class MergeConfig:
    tier1_threshold: float
    tier2_threshold: float
    tier3_threshold: float


@dataclass(slots=True)
class MergeResult:
    """Result returned by `MergeEngine.merge`."""

    accepted: list[Detection]
    below_threshold: list[Detection] = field(default_factory=list)
    exception_suppressed: list[Detection] = field(default_factory=list)
    span_invalid: list[Detection] = field(default_factory=list)


class MergeEngine:
    """Combines per-detector outputs into a canonical detection list."""

    def __init__(self, *, config: MergeConfig) -> None:
        self._cfg = config

    def merge(
        self,
        *,
        original_text: str,
        detections: list[Detection],
        exceptions: list[ExceptionEntry] | None = None,
    ) -> MergeResult:
        result = MergeResult(accepted=[])
        validated: list[Detection] = []
        for d in detections:
            if not self._span_matches(original_text, d):
                result.span_invalid.append(d)
                continue
            validated.append(d)

        # Apply customer exceptions (removes matching spans)
        if exceptions:
            kept: list[Detection] = []
            for d in validated:
                if any(_exception_applies(exc, d) for exc in exceptions):
                    result.exception_suppressed.append(d)
                else:
                    kept.append(d)
            validated = kept

        # Threshold filter
        threshold_filtered: list[Detection] = []
        for d in validated:
            if d.confidence >= self._threshold(d.tier):
                threshold_filtered.append(d)
            else:
                result.below_threshold.append(d)

        # Overlap resolution + confidence aggregation
        result.accepted = _resolve_overlaps(threshold_filtered)
        return result

    def _threshold(self, tier: int) -> float:
        return {
            1: self._cfg.tier1_threshold,
            2: self._cfg.tier2_threshold,
            3: self._cfg.tier3_threshold,
        }.get(tier, self._cfg.tier3_threshold)

    @staticmethod
    def _span_matches(original: str, d: Detection) -> bool:
        if d.start < 0 or d.end > len(original) or d.end <= d.start:
            return False
        return original[d.start : d.end] == d.text


@dataclass(frozen=True, slots=True)
class ExceptionEntry:
    """Customer-validated false-positive entry for Tier 1 detections."""

    rule_id: str | None
    entity_type: str | None
    text_match: str | None  # exact text the customer marked as not sensitive

    def matches_anything(self) -> bool:
        return bool(self.rule_id or self.entity_type or self.text_match)


def _exception_applies(exc: ExceptionEntry, d: Detection) -> bool:
    if not exc.matches_anything():
        return False
    if exc.rule_id is not None and exc.rule_id != d.rule_id:
        return False
    if exc.entity_type is not None and exc.entity_type != d.entity_type:
        return False
    return not (exc.text_match is not None and exc.text_match != d.text)


def _resolve_overlaps(detections: list[Detection]) -> list[Detection]:
    if not detections:
        return []

    # Group detections by overlapping span clusters.
    sorted_dets = sorted(detections, key=lambda d: (d.start, -d.end))
    clusters: list[list[Detection]] = []
    current: list[Detection] = [sorted_dets[0]]
    current_end = sorted_dets[0].end
    for d in sorted_dets[1:]:
        if d.start < current_end:
            current.append(d)
            current_end = max(current_end, d.end)
        else:
            clusters.append(current)
            current = [d]
            current_end = d.end
    clusters.append(current)

    out: list[Detection] = []
    for cluster in clusters:
        winner = _pick_cluster_winner(cluster)
        out.append(winner)
    return out


def _pick_cluster_winner(cluster: list[Detection]) -> Detection:
    if len(cluster) == 1:
        return cluster[0]

    # 1) longer span wins; 2) higher tier (lower number) wins; 3) detector
    # precedence (lower number wins). On all-equal, first-detected wins.
    def sort_key(d: Detection) -> tuple[int, int, int]:
        length = -(d.end - d.start)  # longer is better, so negate for asc sort
        tier_key = d.tier  # lower tier number = higher authority (1 > 2 > 3)
        det_key = DETECTOR_PRECEDENCE.get(d.detector, 99)
        return (length, tier_key, det_key)

    ordered = sorted(cluster, key=sort_key)
    winner = ordered[0]

    # Boost confidence if the winner's exact span was independently detected
    # by another detector. ARCHITECTURE §4.3 confidence aggregation.
    independently_corroborated = any(
        other is not winner
        and other.start == winner.start
        and other.end == winner.end
        and other.detector != winner.detector
        for other in cluster
    )
    if independently_corroborated:
        boosted = min(0.99, winner.confidence + 0.05)
        if boosted > winner.confidence:
            from dataclasses import replace as _replace

            return _replace(winner, confidence=boosted)
    return winner
