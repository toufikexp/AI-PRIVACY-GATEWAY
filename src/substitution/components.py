"""Component decomposition for reverse substitution.

ARCHITECTURE §4.5.1: when an entity is substituted, the session map records
the full form AND every reasonable component / honorific variation, so
reverse substitution can match a partial response token like "Karim" alone.

Entity type drives the decomposition strategy. Person names get name-part
splits (first / last / honorifics in AR, FR, EN). Other entity types get
just their full form for now.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.detectors.base import EntityType

# Canonical honorific prefixes by language.
_HONORIFICS = {
    "en": ("Mr.", "Mrs.", "Ms.", "Dr."),
    "fr": ("M.", "Mme", "Mlle", "Dr"),
    "ar": ("السيد", "السيدة", "الآنسة", "الدكتور"),
}


@dataclass(frozen=True, slots=True)
class Component:
    """A registered variant of an original ↔ synthetic pair."""

    surface: str  # form that may appear in the LLM response
    original_surface: str  # the corresponding form in the original input
    kind: str  # full | first | last | honorific
    language: str | None  # 'ar' | 'fr' | 'en' | None for non-name types


def decompose_person(original_full: str, synthetic_full: str) -> list[Component]:
    """Decompose a person name pair into matchable components.

    Best-effort: if the name has at least two whitespace-separated parts
    we treat them as first/last. Single-token names produce only the
    full-form component.
    """
    components: list[Component] = [
        Component(
            surface=synthetic_full, original_surface=original_full, kind="full", language=None
        ),
    ]
    o_parts = original_full.split()
    s_parts = synthetic_full.split()
    if len(o_parts) < 2 or len(s_parts) < 2:
        return components
    o_first, o_last = o_parts[0], o_parts[-1]
    s_first, s_last = s_parts[0], s_parts[-1]

    components.append(Component(s_first, o_first, "first", None))
    components.append(Component(s_last, o_last, "last", None))

    for lang, prefixes in _HONORIFICS.items():
        for prefix in prefixes:
            components.append(
                Component(
                    surface=f"{prefix} {s_last}",
                    original_surface=f"{prefix} {o_last}",
                    kind="honorific",
                    language=lang,
                )
            )
    return components


def decompose(
    *,
    entity_type: EntityType,
    original: str,
    synthetic: str,
) -> list[Component]:
    if entity_type == "person":
        return decompose_person(original, synthetic)
    return [Component(synthetic, original, "full", None)]
