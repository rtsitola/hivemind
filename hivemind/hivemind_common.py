"""
HiveMind — Shared Helpers
==========================

Fonctions utilitaires partagées par tous les modules HiveMind.
Évite la duplication de _now_iso(), _event_id(), etc.
"""

import uuid
from datetime import datetime, timezone


def now_iso() -> str:
    """Timestamp ISO 8601 UTC."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def event_id(prefix: str = "evt") -> str:
    """ID unique pour un événement."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def memory_hash(content: str) -> str:
    """Hash court du contenu (détection changements)."""
    import hashlib
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def generate_memory_id(event_id: str, agent: str) -> str:
    """ID déterministe pour une mémoire basé sur event_id + agent."""
    import hashlib
    raw = f"{event_id}|{agent}"
    return "mem-" + hashlib.sha256(raw.encode()).hexdigest()[:12]
