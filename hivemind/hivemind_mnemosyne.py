#!/usr/bin/env python3
"""
HiveMind Mnemosyne Adapter
===========================

Pont entre Mnemosyne et le HiveMind Event Log.

ARCHITECTURE :
  ┌──────────────┐     write      ┌─────────────────┐
  │  Mnemosyne   │ ─────────────► │  Event Log       │
  │  (Hermes)    │                │  events/*.jsonl  │
  └──────┬───────┘                └────────┬─────────┘
         │                                │
         │ read                           │ Syncthing
         ▼                                ▼
  ┌──────────────┐               ┌─────────────────┐
  │ consolidated │◄─── merge ────│  Merge Engine    │
  │    .db       │               └─────────────────┘
  └──────────────┘

ÉCRITURE : remember/update/forget → JSONL event + native Mnemosyne
LECTURE  : recall → consolidated.db (avec fallback native Mnemosyne)
MERGE    : Reconstruit consolidated.db depuis tous les event logs

Usage (standalone):
    from hivemind.hivemind_mnemosyne import HiveMindMemory
    hm = HiveMindMemory(events_dir="./memory/events", agent="alice")
    hm.remember("Client Omega : vérifier cash-flow", importance=0.9)
    hm.merge()
    results = hm.recall("cash-flow")

Usage (CLI):
    python3 hivemind_mnemosyne.py remember "Contenu..." --agent alice
    python3 hivemind_mnemosyne.py merge
    python3 hivemind_mnemosyne.py recall "requête"
    python3 hivemind_mnemosyne.py bootstrap  # Export Mnemosyne → Event Log
"""

import json
import os
import sys
import uuid
import sqlite3
import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ── Configuration ──────────────────────────────────────────────────

DEFAULT_MNEMOSYNE_DB = os.path.expanduser("~/.hermes/mnemosyne/data/mnemosyne.db")
DEFAULT_EVENTS_DIR = "./memory/events"
DEFAULT_CONSOLIDATED_DB = "./memory/consolidated.db"

# DRY : import shared helpers (aliased for backward compatibility)
from hivemind.hivemind_common import now_iso as _now_iso, event_id as _event_id


# ── Core Class ─────────────────────────────────────────────────────

class HiveMindMemory:
    """
    HiveMind memory adapter — écrit dans l'Event Log, lit depuis consolidated.db.

    Flux :
      remember()  → append dans events/<agent>.jsonl
      recall()    → query consolidated.db
      merge()     → reconstruit consolidated.db depuis tous les events
      bootstrap() → export toute la Mnemosyne locale vers l'Event Log
    """

    def __init__(
        self,
        events_dir: str = DEFAULT_EVENTS_DIR,
        consolidated_db: str = DEFAULT_CONSOLIDATED_DB,
        mnemosyne_db: str = DEFAULT_MNEMOSYNE_DB,
        agent: str = "unknown",
        
    ):
        self.events_dir = Path(events_dir)
        self.consolidated_db = Path(consolidated_db)
        self.mnemosyne_db = Path(mnemosyne_db)
        self.agent = agent
        self.merge_engine = None  # unused — merge() now imports directly

        # Hash chain + signatures
        try:
            from hivemind.hivemind_chain import ChainState
            self.chain_state = ChainState(agent=agent, events_dir=str(events_dir))
        except Exception:
            self.chain_state = None  # cryptography pas installé

        # Créer le dossier events si nécessaire
        self.events_dir.mkdir(parents=True, exist_ok=True)


    @property
    def _event_file(self) -> Path:
        return self.events_dir / f"{self.agent}.jsonl"

    # ── Write operations ───────────────────────────────────────

    def remember(
        self,
        content: str,
        importance: float = 0.5,
        source: str = "hivemind",
        scope: str = "shared",
        veracity: str = "unknown",
    ) -> str:
        """
        Ajoute une mémoire dans l'Event Log.

        Returns: l'event_id
        """
        event = {
            "op": "remember",
            "id": _event_id(),
            "agent": self.agent,
            "ts": _now_iso(),
            "payload": {
                "content": content,
                "importance": importance,
                "source": source,
                "scope": scope,
                "veracity": veracity,
            },
        }
        self._append_event(event)
        return event["id"]

    def update(
        self,
        memory_id: str,
        content: Optional[str] = None,
        importance: Optional[float] = None,
        source: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> str:
        """Modifie une mémoire existante via l'Event Log."""
        payload = {"memory_id": memory_id}
        if content is not None:
            payload["content"] = content
        if importance is not None:
            payload["importance"] = importance
        if source is not None:
            payload["source"] = source
        if scope is not None:
            payload["scope"] = scope

        event = {
            "op": "update",
            "id": _event_id(),
            "agent": self.agent,
            "ts": _now_iso(),
            "payload": payload,
        }
        self._append_event(event)
        return event["id"]

    def forget(self, memory_id: str, reason: Optional[str] = None) -> str:
        """Supprime une mémoire (tombstone — soft delete, réversible)."""
        payload = {"memory_id": memory_id}
        if reason:
            payload["reason"] = reason
        event = {
            "op": "forget",
            "id": _event_id(),
            "agent": self.agent,
            "ts": _now_iso(),
            "payload": payload,
        }
        self._append_event(event)
        return event["id"]

    def revert(self, memory_id: str) -> str:
        """Restaure une mémoire supprimée (annule le tombstone)."""
        event = {
            "op": "revert",
            "id": _event_id(),
            "agent": self.agent,
            "ts": _now_iso(),
            "payload": {"memory_id": memory_id},
        }
        self._append_event(event)
        return event["id"]

    def _append_event(self, event: dict):
        """Ajoute un événement au journal de l'agent (append-only), signé si possible."""
        # Signer avec la hash chain si dispo
        if self.chain_state:
            event = self.chain_state.sign_event(event)

        with open(self._event_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

        # Mettre à jour le prev_hash pour le prochain événement
        if self.chain_state:
            from hivemind.hivemind_chain import _event_hash
            self.chain_state._prev_hash = _event_hash(event)

    # ── Read operations ────────────────────────────────────────

    def recall(
        self,
        query: str,
        limit: int = 5,
        scope: Optional[str] = None,
        agent: Optional[str] = None,
    ) -> list[dict]:
        """
        Recherche dans la base consolidée.
        Utilise FTS5 si dispo, sinon LIKE.

        Returns: liste de mémoires [{id, content, importance, source, scope, agent, ...}]
        """
        if not self.consolidated_db.exists():
            return self._recall_native(query, limit, scope, agent)

        conn = sqlite3.connect(str(self.consolidated_db))
        conn.row_factory = sqlite3.Row

        try:
            # Essayer FTS5 d'abord
            rows = conn.execute(
                "SELECT m.id, m.content, m.importance, m.source, m.scope, m.agent, m.created_at "
                "FROM memories m "
                "JOIN memories_fts fts ON m.rowid = fts.rowid "
                "WHERE memories_fts MATCH ? AND m.is_deleted = 0 "
                "ORDER BY m.importance DESC LIMIT ?",
                (query, limit),
            ).fetchall()

            if not rows:
                # FTS5 n'a rien trouvé → LIKE sur consolidated.db
                rows = self._like_query(conn, query, limit, scope, agent)

            results = [dict(r) for r in rows]
            return results

        except sqlite3.OperationalError:
            # FTS5 pas dispo → LIKE sur consolidated.db
            rows = self._like_query(conn, query, limit, scope, agent)
            results = [dict(r) for r in rows]
            conn.close()
            return results
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _like_query(
        self, conn: sqlite3.Connection, query: str, limit: int,
        scope: Optional[str], agent: Optional[str],
    ) -> list:
        """Requête LIKE sur la DB consolidée."""
        like_q = f"%{query}%"
        where = "WHERE content LIKE ? AND is_deleted = 0"
        params = [like_q]
        if scope:
            where += " AND scope = ?"
            params.append(scope)
        if agent:
            where += " AND agent = ?"
            params.append(agent)

        return conn.execute(
            f"SELECT id, content, importance, source, scope, agent, created_at "
            f"FROM memories {where} ORDER BY importance DESC LIMIT ?",
            params + [limit],
        ).fetchall()

    def _recall_native(
        self, query: str, limit: int, scope: Optional[str], agent: Optional[str]
    ) -> list[dict]:
        """
        Fallback : recherche dans la Mnemosyne native.
        Utile quand consolidated.db n'existe pas encore.
        """
        if not self.mnemosyne_db.exists():
            return []

        conn = sqlite3.connect(str(self.mnemosyne_db))
        conn.row_factory = sqlite3.Row

        like_q = f"%{query}%"
        rows = conn.execute(
            "SELECT id, content, importance, source, "
            "COALESCE(scope, 'global') as scope, "
            "COALESCE(author_id, 'unknown') as agent, created_at "
            "FROM working_memory WHERE content LIKE ? "
            "ORDER BY importance DESC LIMIT ?",
            (like_q, limit),
        ).fetchall()

        conn.close()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        """Statistiques de la base consolidée."""
        if not self.consolidated_db.exists():
            return {"total": 0, "by_agent": {}, "by_scope": {}}

        conn = sqlite3.connect(str(self.consolidated_db))
        total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        active = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE is_deleted = 0"
        ).fetchone()[0]
        tombstoned = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE is_deleted = 1"
        ).fetchone()[0]

        by_agent = {}
        for row in conn.execute(
            "SELECT agent, COUNT(*) as n FROM memories GROUP BY agent"
        ):
            by_agent[row[0]] = row[1]

        by_scope = {}
        for row in conn.execute(
            "SELECT scope, COUNT(*) as n FROM memories GROUP BY scope"
        ):
            by_scope[row[0]] = row[1]

        conn.close()
        return {
            "total": total,
            "active": active,
            "tombstoned": tombstoned,
            "by_agent": by_agent,
            "by_scope": by_scope,
        }

    # ── Merge ──────────────────────────────────────────────────

    def merge(self) -> dict:
        """
        Lance le merge engine pour reconstruire consolidated.db.
        Utilise un import direct (plus de subprocess).
        """
        try:
            from hivemind.merge_engine import merge
            result = merge(
                events_dir=str(self.events_dir),
                db_path=str(self.consolidated_db),
            )
            return {"ok": True, "output": str(result)}
        except Exception as e:
            print(f"[MERGE ERROR] {e}", file=sys.stderr)
            return {"error": str(e)}

    # ── Bootstrap ──────────────────────────────────────────────

    def bootstrap(self, scope: str = "shared", min_importance: float = 0.0) -> int:
        """
        Exporte toutes les mémoires Mnemosyne existantes vers l'Event Log.
        À exécuter UNE FOIS par agent, au moment de rejoindre le HiveMind.

        Returns: nombre de mémoires exportées
        """
        if not self.mnemosyne_db.exists():
            print(f"[BOOTSTRAP] Mnemosyne DB introuvable: {self.mnemosyne_db}")
            return 0

        conn = sqlite3.connect(str(self.mnemosyne_db))
        conn.row_factory = sqlite3.Row

        rows = conn.execute(
            "SELECT id, content, importance, source, veracity, created_at "
            "FROM working_memory WHERE importance >= ? "
            "ORDER BY created_at",
            (min_importance,),
        ).fetchall()

        count = 0
        for row in rows:
            event = {
                "op": "remember",
                "id": f"evt-bootstrap-{uuid.uuid4().hex[:8]}-{row['id'][:12]}",
                "agent": self.agent,
                "ts": (row["created_at"] or _now_iso()),
                "payload": {
                    "content": row["content"],
                    "importance": row["importance"] or 0.5,
                    "source": (row["source"] if row["source"] else "bootstrap"),
                    "scope": scope,
                    "veracity": (row["veracity"] if row["veracity"] else "unknown"),
                },
            }
            self._append_event(event)
            count += 1

        conn.close()
        print(f"[BOOTSTRAP] {count} mémoires exportées de Mnemosyne → Event Log")
        return count

    # ── Dual write (Mnemosyne native + Event Log) ────────────────

    def remember_dual(self, content: str, importance: float = 0.5,
                      source: str = "hivemind", scope: str = "shared") -> str:
        """
        Écrit DANS les deux : Mnemosyne native + Event Log.
        Pour la transition — permet de garder la compatibilité.
        """
        # Écrire dans l'Event Log (HiveMind)
        event_id = self.remember(content, importance, source, scope)

        # Écrire dans Mnemosyne native
        if self.mnemosyne_db.exists():
            conn = sqlite3.connect(str(self.mnemosyne_db))
            mem_id = f"hm-{uuid.uuid4().hex[:12]}"
            conn.execute(
                """INSERT INTO working_memory
                   (id, content, source, timestamp, importance, veracity, scope, author_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (mem_id, content, source, _now_iso(), importance, "unknown", scope, self.agent),
            )
            conn.commit()
            conn.close()

        return event_id


# ── CLI ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="HiveMind Mnemosyne Adapter")
    parser.add_argument("--events-dir", default=DEFAULT_EVENTS_DIR)
    parser.add_argument("--db", default=DEFAULT_CONSOLIDATED_DB)
    parser.add_argument("--mnemosyne-db", default=DEFAULT_MNEMOSYNE_DB)
    parser.add_argument("--agent", default="unknown")

    sub = parser.add_subparsers(dest="command", required=True)

    # remember
    p_rem = sub.add_parser("remember")
    p_rem.add_argument("content")
    p_rem.add_argument("--importance", type=float, default=0.5)
    p_rem.add_argument("--source", default="cli")
    p_rem.add_argument("--scope", default="shared")
    p_rem.add_argument("--dual", action="store_true",
                       help="Écrire aussi dans Mnemosyne native")

    # update
    p_upd = sub.add_parser("update")
    p_upd.add_argument("--memory-id", required=True)
    p_upd.add_argument("--content")
    p_upd.add_argument("--importance", type=float)
    p_upd.add_argument("--source")
    p_upd.add_argument("--scope")

    # forget
    p_fgt = sub.add_parser("forget")
    p_fgt.add_argument("--memory-id", required=True)
    p_fgt.add_argument("--reason", help="Raison de la suppression")

    # revert
    p_rvt = sub.add_parser("revert", help="Restaurer une mémoire supprimée")
    p_rvt.add_argument("--memory-id", required=True)

    # recall
    p_rec = sub.add_parser("recall")
    p_rec.add_argument("query")
    p_rec.add_argument("--limit", type=int, default=5)
    p_rec.add_argument("--scope")
    p_rec.add_argument("--agent")

    # merge
    sub.add_parser("merge")

    # stats
    sub.add_parser("stats")

    # bootstrap
    p_bs = sub.add_parser("bootstrap")
    p_bs.add_argument("--scope", default="shared")
    p_bs.add_argument("--min-importance", type=float, default=0.0)

    args = parser.parse_args()

    hm = HiveMindMemory(
        events_dir=args.events_dir,
        consolidated_db=args.db,
        mnemosyne_db=args.mnemosyne_db,
        agent=args.agent,
    )

    if args.command == "remember":
        if args.dual:
            event_id = hm.remember_dual(args.content, args.importance, args.source, args.scope)
        else:
            event_id = hm.remember(args.content, args.importance, args.source, args.scope)
        print(json.dumps({"event_id": event_id, "agent": args.agent}))

    elif args.command == "update":
        event_id = hm.update(args.memory_id, args.content, args.importance,
                            args.source, args.scope)
        print(json.dumps({"event_id": event_id}))

    elif args.command == "forget":
        event_id = hm.forget(args.memory_id, reason=getattr(args, 'reason', None))
        print(json.dumps({"event_id": event_id}))

    elif args.command == "revert":
        event_id = hm.revert(args.memory_id)
        print(json.dumps({"event_id": event_id}))

    elif args.command == "recall":
        results = hm.recall(args.query, args.limit, args.scope, args.agent)
        print(json.dumps(results, indent=2, ensure_ascii=False))

    elif args.command == "merge":
        result = hm.merge()
        print(json.dumps(result, indent=2))

    elif args.command == "stats":
        stats = hm.stats()
        print(json.dumps(stats, indent=2))

    elif args.command == "bootstrap":
        count = hm.bootstrap(args.scope, args.min_importance)
        print(json.dumps({"exported": count}))


if __name__ == "__main__":
    main()
