"""
HiveMind Core — Phase 1
========================

Modules:
    merge_engine        — Merge JSONL events → consolidated.db (FTS5)
    hivemind_mnemosyne  — Pont Mnemosyne ↔ Event Log
    hivemind_cli        — CLI onboarding (init, join, status, serve)
    watcher             — Poll + debounce + merge auto
    event_writer        — Écriture manuelle d'événements
    hivemind_common     — Helpers partagés
"""

from .hivemind_common import now_iso, event_id, memory_hash, generate_memory_id
from .merge_engine import merge, merge_events, parse_events
from .hivemind_mnemosyne import HiveMindMemory

__version__ = "1.0.0"
__all__ = [
    "now_iso", "event_id", "memory_hash", "generate_memory_id",
    "merge", "merge_events", "parse_events",
    "HiveMindMemory",
]
