"""Forward and reverse substitution.

Forward substitution:
  Given a list of accepted detections and the original text, produce
  (sanitised_text, session_map). Detections must be non-overlapping
  (the merge engine guarantees this).

Reverse substitution:
  Given an LLM response and a session map, return de-sanitised text.
  Strategy (ARCHITECTURE §4.5):
    1. Direct match: response substring equal to a registered synthetic
       full form → swap to its original.
    2. Component match: response substring equal to a registered
       component (e.g. first name only) → swap if disambiguation says so.
    3. Novel entity (post-NER): not in session map at all → log, keep
       as-is. This is recorded so the dashboard can flag it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.detectors.base import Detection
from src.substitution.components import Component, decompose
from src.substitution.session_map import EncryptedSessionMap, SessionMapEntry
from src.substitution.synthetic import synthetic_for


@dataclass(slots=True)
class ForwardResult:
    sanitised_text: str
    components_by_synthetic: dict[str, list[Component]] = field(default_factory=dict)
    detection_count: int = 0


def apply_substitution(
    *,
    original_text: str,
    detections: list[Detection],
    session_map: EncryptedSessionMap,
    country_code: str,
    salt: str,
) -> ForwardResult:
    """Replace each accepted detection with a synthetic; persist in session map."""
    out_parts: list[str] = []
    cursor = 0
    components_by_synthetic: dict[str, list[Component]] = {}

    for det in sorted(detections, key=lambda d: d.start):
        if det.start < cursor:
            # Overlap — shouldn't happen post-merge, skip defensively.
            continue
        out_parts.append(original_text[cursor : det.start])
        synthetic = synthetic_for(
            entity_type=det.entity_type,
            original=det.text,
            country_code=country_code,
            salt=salt,
        )
        out_parts.append(synthetic)
        session_map.add(
            SessionMapEntry(original=det.text, synthetic=synthetic, entity_type=det.entity_type)
        )
        components_by_synthetic[synthetic] = decompose(
            entity_type=det.entity_type, original=det.text, synthetic=synthetic
        )
        cursor = det.end
    out_parts.append(original_text[cursor:])

    return ForwardResult(
        sanitised_text="".join(out_parts),
        components_by_synthetic=components_by_synthetic,
        detection_count=len(detections),
    )


@dataclass(slots=True)
class ReverseResult:
    text: str
    direct_reversed: int = 0
    component_reversed: int = 0
    novel_entities: list[str] = field(default_factory=list)


def reverse_substitution(
    *,
    response_text: str,
    session_map: EncryptedSessionMap,
    components_by_synthetic: dict[str, list[Component]],
    ner_entities: list[str] | None = None,
) -> ReverseResult:
    """Replace synthetics + registered components in the response.

    Order of replacement: longest surface first to avoid partial overlaps
    (e.g. replace "Mr. Hadji" before plain "Hadji"). Replacement is
    word-boundary insensitive on purpose: the synthetic dictionary is
    chosen to avoid common-word collisions, and Arabic / French boundary
    rules are not uniform.
    """
    rev = ReverseResult(text=response_text)

    # Build flat list of (surface, original, is_full).
    candidates: list[tuple[str, str, bool]] = []
    for synthetic, comps in components_by_synthetic.items():
        for c in comps:
            candidates.append((c.surface, c.original_surface, c.kind == "full"))
        # Defensive: also include the synthetic itself if no components were
        # registered (e.g. non-person entity types).
        if not comps:
            entry = session_map.reverse(synthetic)
            if entry is not None:
                candidates.append((synthetic, entry.original, True))

    candidates.sort(key=lambda t: -len(t[0]))

    text = response_text
    for surface, original, is_full in candidates:
        if surface and surface in text:
            text = text.replace(surface, original)
            if is_full:
                rev.direct_reversed += 1
            else:
                rev.component_reversed += 1

    # Novel entity flagging from post-response NER.
    if ner_entities:
        known_surfaces = {surface for surface, _, _ in candidates}
        for ent in ner_entities:
            if ent and ent not in known_surfaces and ent not in text:
                # Was it in the response originally and got reversed? skip if so.
                continue
            if ent and ent not in known_surfaces and ent in response_text:
                rev.novel_entities.append(ent)
    rev.text = text
    return rev
