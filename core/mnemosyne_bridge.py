#!/usr/bin/env python3
"""
mnemosyne_bridge.py — Mnemosyne ↔ HiveMind Event Log Bridge
=============================================================

Pont temps réel entre Mnemosyne native et le HiveMind Event Log.

MODE DUAL (temps réel, idéal):
  Chaque appel remember/forget/update écrit dans les DEUX :
    → master.db (Mnemosyne native, comportement normal)
    → events/<agent>.jsonl (Event Log, sync via Syncthing vers Global)

MODE SYNC (rattrapage, cron):
  Lit master.db, trouve les nouvelles mémoires depuis la dernière sync,
  les ajoute dans l'Event Log. À lancer toutes les 2 minutes.

MODE WATCH (démon léger):
  Surveille master.db pour changements, sync automatique.

USAGE:
  # Mode temps réel — à utiliser DANS Hermes (remplace mnemosyne_remember)
  python3 mnemosyne_bridge.py remember "Contenu..." --importance 0.9

  # Mode cron — toutes les 2 minutes
  python3 mnemosyne_bridge.py sync

  # Mode démon
  python3 mnemosyne_bridge.py watch

ARCHITECTURE:
  ┌──────────────┐     dual write    ┌─────────────────┐
  │  Mnemosyne   │ ─────────────────►│  Event Log       │
  │  (Hermes)    │    + native       │  events/*.jsonl  │
  └──────────────┘                   └────────┬────────┘
                                              │ Syncthing
                                              ▼
                                     ┌─────────────────┐
                                     │  Global merge    │
                                     │  engine          │
                                     └─────────────────┘
"""

import json
import os
import sys
import uuid
import sqlite3
import socket
import time
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ── Paths ─────────────────────────────────────────────────────────────

HERMES_HOME = Path(os.path.expanduser("~/.hermes"))
MNEMOSYNE_DB = HERMES_HOME / "mnemosyne" / "data" / "mnemosyne.db"
SHARED_DIR = HERMES_HOME / "shared"
EVENTS_DIR = SHARED_DIR / "hivemind-cabinet" / "events"
STATE_FILE = SHARED_DIR / ".mnemosyne_bridge_state.json"
HOSTNAME = socket.gethostname().split(".")[0].lower().replace("-", "")
AGENT = f"{HOSTNAME}-hermes"


# ── Helpers ──────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _event_id() -> str:
    return f"evt-{uuid.uuid4().hex[:8]}"


# ── State ─────────────────────────────────────────────────────────────

class BridgeState:
    """Suit la dernière synchronisation pour le mode sync."""

    def __init__(self):
        self.data = self._load()

    def _load(self) -> dict:
        if STATE_FILE.exists():
            try:
                return json.loads(STATE_FILE.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {"last_sync_ts": None, "synced_count": 0, "synced_ids": []}

    def save(self):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(self.data, indent=2))

    @property
    def last_sync(self) -> Optional[str]:
        return self.data.get("last_sync_ts")

    def mark_synced(self, mem_ids: list[str]):
        self.data["last_sync_ts"] = _now_iso()
        self.data["synced_count"] += len(mem_ids)
        # Garde les 500 derniers IDs pour éviter de grossir
        self.data["synced_ids"] = (self.data.get("synced_ids", []) + mem_ids)[-500:]


# ── Core ──────────────────────────────────────────────────────────────

class MnemosyneBridge:
    """
    Écriture duale : Mnemosyne native + Event Log.

    Flux :
      remember() → Mnemosyne DB + events/<agent>.jsonl
      forget()   → Event Log uniquement (pas d'accès direct Mnemosyne depuis ici)
      sync()     → Export les nouvelles mémoires Mnemosyne → Event Log
    """

    def __init__(self):
        self.events_dir = EVENTS_DIR
        self.mnemosyne_db = MNEMOSYNE_DB
        self.agent = AGENT
        self.events_dir.mkdir(parents=True, exist_ok=True)

    @property
    def event_file(self) -> Path:
        return self.events_dir / f"{self.agent}.jsonl"

    def _append_event(self, event: dict):
        """Ajoute un événement au journal de l'agent (append-only)."""
        with open(self.event_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    # ── Dual write operations ────────────────────────────────────

    def remember(
        self,
        content: str,
        importance: float = 0.5,
        source: str = "hermes",
        scope: str = "shared",
    ) -> str:
        """
        Écrit dans Mnemosyne native + Event Log.
        Returns: event_id
        """
        event_id = _event_id()
        ts = _now_iso()

        # 1. Écrire dans l'Event Log
        event = {
            "op": "remember",
            "id": event_id,
            "agent": self.agent,
            "ts": ts,
            "payload": {
                "content": content,
                "importance": importance,
                "source": source,
                "scope": scope,
            },
        }
        self._append_event(event)

        # 2. Écrire dans Mnemosyne native
        if self.mnemosyne_db.exists():
            try:
                conn = sqlite3.connect(str(self.mnemosyne_db))
                mem_id = f"hm-{uuid.uuid4().hex[:12]}"
                conn.execute(
                    """INSERT INTO working_memory
                       (id, content, source, timestamp, importance, veracity, scope, author_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (mem_id, content, source, ts, importance, "unknown", scope, self.agent),
                )
                conn.commit()
                conn.close()
            except sqlite3.Error as e:
                print(f"[WARN] Mnemosyne native write failed: {e}", file=sys.stderr)

        return event_id

    def forget(self, memory_id: str) -> str:
        """Supprime via Event Log."""
        event_id = _event_id()
        event = {
            "op": "forget",
            "id": event_id,
            "agent": self.agent,
            "ts": _now_iso(),
            "payload": {"memory_id": memory_id},
        }
        self._append_event(event)
        return event_id

    def update(
        self,
        memory_id: str,
        content: Optional[str] = None,
        importance: Optional[float] = None,
        scope: Optional[str] = None,
    ) -> str:
        """Modifie via Event Log."""
        payload = {"memory_id": memory_id}
        if content is not None:
            payload["content"] = content
        if importance is not None:
            payload["importance"] = importance
        if scope is not None:
            payload["scope"] = scope

        event_id = _event_id()
        event = {
            "op": "update",
            "id": event_id,
            "agent": self.agent,
            "ts": _now_iso(),
            "payload": payload,
        }
        self._append_event(event)
        return event_id

    # ── Sync mode (cron) ─────────────────────────────────────────

    def sync(self) -> dict:
        """
        Mode rattrapage : exporte les nouvelles mémoires Mnemosyne → Event Log.
        À lancer toutes les 2 minutes par cron.
        """
        if not self.mnemosyne_db.exists():
            return {"synced": 0, "error": "mnemosyne_db_not_found"}

        state = BridgeState()
        conn = sqlite3.connect(str(self.mnemosyne_db))
        conn.row_factory = sqlite3.Row

        # Récupère les mémoires créées depuis la dernière sync
        if state.last_sync:
            rows = conn.execute(
                """SELECT id, content, importance, source, scope, veracity, author_id, timestamp
                   FROM working_memory
                   WHERE (timestamp > ? OR (timestamp IS NULL AND created_at > ?))
                   ORDER BY timestamp, created_at""",
                (state.last_sync, state.last_sync),
            ).fetchall()
        else:
            # Première sync : prend tout
            rows = conn.execute(
                """SELECT id, content, importance, source, scope, veracity, author_id, timestamp
                   FROM working_memory ORDER BY timestamp, created_at"""
            ).fetchall()

        conn.close()

        synced = 0
        synced_ids = []

        for row in rows:
            # Skip si déjà synced
            if row["id"] in state.data.get("synced_ids", []):
                continue

            event = {
                "op": "remember",
                "id": f"evt-sync-{_event_id()}",
                "agent": self.agent,
                "ts": (row["timestamp"] or _now_iso()),
                "payload": {
                    "content": row["content"],
                    "importance": row["importance"] or 0.5,
                    "source": f"mnemosyne/{row['source'] or 'unknown'}",
                    "scope": row["scope"] or "shared",
                    "veracity": row["veracity"] or "unknown",
                    "mnemosyne_id": row["id"],
                },
            }
            self._append_event(event)
            synced_ids.append(row["id"])
            synced += 1

        if synced > 0:
            state.mark_synced(synced_ids)
            state.save()
            print(f"[SYNC] {synced} mémoires exportées Mnemosyne → Event Log")
        else:
            print(f"[SYNC] Rien à synchroniser (dernière: {state.last_sync or 'jamais'})")

        return {"synced": synced, "agent": self.agent}

    # ── Watch mode (démon) ───────────────────────────────────────

    def watch(self, interval: int = 30):
        """
        Surveille master.db et sync automatiquement.
        interval: secondes entre chaque check.
        """
        print(f"[WATCH] Démarrage — agent={self.agent}, interval={interval}s")
        print(f"[WATCH] Events → {self.event_file}")

        try:
            while True:
                result = self.sync()
                if result.get("synced", 0) > 0:
                    print(f"[WATCH] {result['synced']} nouvelles mémoires syncées")
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n[WATCH] Arrêt")

    # ── Stats ────────────────────────────────────────────────────

    def stats(self) -> dict:
        """État actuel du bridge."""
        mnemosyne_count = 0
        if self.mnemosyne_db.exists():
            conn = sqlite3.connect(str(self.mnemosyne_db))
            mnemosyne_count = conn.execute("SELECT COUNT(*) FROM working_memory").fetchone()[0]
            conn.close()

        event_count = 0
        if self.event_file.exists():
            with open(self.event_file) as f:
                event_count = sum(1 for l in f if l.strip())

        state = BridgeState()
        return {
            "agent": self.agent,
            "events_file": str(self.event_file),
            "mnemosyne_db": str(self.mnemosyne_db),
            "event_log_entries": event_count,
            "mnemosyne_memories": mnemosyne_count,
            "last_sync": state.last_sync or "jamais",
            "total_synced": state.data.get("synced_count", 0),
        }


# ── CLI ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Mnemosyne ↔ HiveMind Event Log Bridge"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # remember (dual write)
    p_rem = sub.add_parser("remember", help="Dual write: Mnemosyne + Event Log")
    p_rem.add_argument("content", help="Contenu de la mémoire")
    p_rem.add_argument("--importance", type=float, default=0.5)
    p_rem.add_argument("--source", default="hermes")
    p_rem.add_argument("--scope", default="shared")

    # forget
    p_fgt = sub.add_parser("forget", help="Forget via Event Log")
    p_fgt.add_argument("--memory-id", required=True)

    # update
    p_upd = sub.add_parser("update", help="Update via Event Log")
    p_upd.add_argument("--memory-id", required=True)
    p_upd.add_argument("--content")
    p_upd.add_argument("--importance", type=float)
    p_upd.add_argument("--scope")

    # sync (cron)
    sub.add_parser("sync", help="Export new Mnemosyne memories → Event Log")

    # watch (daemon)
    p_watch = sub.add_parser("watch", help="Watch Mnemosyne DB and auto-sync")
    p_watch.add_argument("--interval", type=int, default=30,
                         help="Check interval in seconds (default: 30)")

    # stats
    sub.add_parser("stats", help="Bridge status")

    args = parser.parse_args()
    bridge = MnemosyneBridge()

    if args.command == "remember":
        event_id = bridge.remember(args.content, args.importance, args.source, args.scope)
        print(json.dumps({"event_id": event_id, "agent": bridge.agent}))

    elif args.command == "forget":
        event_id = bridge.forget(args.memory_id)
        print(json.dumps({"event_id": event_id}))

    elif args.command == "update":
        event_id = bridge.update(args.memory_id, args.content, args.importance, args.scope)
        print(json.dumps({"event_id": event_id}))

    elif args.command == "sync":
        result = bridge.sync()
        print(json.dumps(result))

    elif args.command == "watch":
        bridge.watch(args.interval)

    elif args.command == "stats":
        stats = bridge.stats()
        print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
