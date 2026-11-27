"""Controlled slot meaning catalog and presets, re-exported from the formal provider."""

from app.domain.knowbots.formal_method import (
    DOMAIN_ID_PATTERN,
    SLOT_MEANING_CATALOG,
    SlotPresetKind,
    build_slot_preset,
    slot_meaning_compatible,
)

__all__ = [
    "DOMAIN_ID_PATTERN",
    "SLOT_MEANING_CATALOG",
    "SlotPresetKind",
    "build_slot_preset",
    "slot_meaning_compatible",
]
