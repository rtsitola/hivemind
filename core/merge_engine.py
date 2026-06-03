#!/usr/bin/env python3
"""
HiveMind Merge Engine — Phase 1 Prototype
==========================================

Lit tous les journaux d'événements JSONL, rejoue en ordre chronologique,
produit une base SQLite consolidée identique sur toutes les machines.

Usage:
    python3 merge_engine.py [--events-dir EVENTS_DIR] [--db DB_PATH]

Format des événements:
    {"op":"remember","id":"evt-001","agent":"alice","ts":"2026-05-26T12:00:00Z",
     "payload":{"content":"...","importance":0.8,"source":"...","scope":"shared"}}

    {"op":"update","id":"evt-002","agent":"bob","ts":"...",
     "payload":{"memory_id":"mem-abc","content":"...","importance":0.9}}

    {"op":"forget","id":"evt-003","agent":"alice","ts":"...",
     "payload":{"memory_id":"mem-abc"}}
"""

import json
import sqlite3
import os
import sys
import glob
import argparse
import fcntl
from datetime import datetime, timezone
from typing import Optional

# Shared helpers
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hivemind_common import now_iso, generate_memory_id


# ── Database schema ────────────────────────────────────────────────

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    importance REAL DEFAULT 0.5,
    source TEXT DEFAULT 'unknown',
    scope TEXT DEFAULT 'shared',
    agent TEXT,
    created_at TEXT,
    updated_at TEXT,
    event_id TEXT,           -- dernier event_id qui a touché cette mémoire
    event_ts TEXT             -- timestamp du dernier événement
);

CREATE TABLE IF NOT EXISTS processed_events (
    event_id TEXT PRIMARY KEY,
    processed_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope);
CREATE INDEX IF NOT EXISTS idx_memories_updated ON memories(updated_at);
CREATE INDEX IF NOT EXISTS idx_memories_agent ON memories(agent);

-- FTS5 pour recherche full-text
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    source,
    scope,
    agent,
    content='memories',
    content_rowid='rowid'
);

-- Triggers pour maintenir l'index FTS5 à jour
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, source, scope, agent)
    VALUES (new.rowid, new.content, new.source, new.scope, new.agent);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, source, scope, agent)
    VALUES ('delete', old.rowid, old.content, old.source, old.scope, old.agent);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, source, scope, agent)
    VALUES ('delete', old.rowid, old.content, old.source, old.scope, old.agent);
    INSERT INTO memories_fts(rowid, content, source, scope, agent)
    VALUES (new.rowid, new.content, new.source, new.scope, new.agent);
END;
"""


# ── Event parsing ──────────────────────────────────────────────────

def _dedup_conflict_files(files: list[str]) -> list[str]:
    """If both `agent.jsonl` and `agent.sync-conflict-xxx.jsonl` exist,
    keep only the conflict file (newer data)."""
    base_map = {}
    for f in files:
        basename = os.path.basename(f)
        base = basename.replace(".sync-conflict-", "\x00").split("\x00")[0]
        base = os.path.splitext(base)[0]
        base_map[base] = f  # last wins (sorted = conflict files come after)
    return sorted(base_map.values())

def parse_events(events_dir: str) -> list[dict]:
    """
    Parse tous les fichiers *.jsonl du dossier events/.
    Retourne une liste d'événements triés chronologiquement.
    Ignore les lignes vides et les lignes mal formées.
    """
    # Match *.jsonl AND Syncthing conflict files (*.sync-conflict-*.jsonl)
    pattern = os.path.join(events_dir, "*.jsonl*")
    files = sorted(glob.glob(pattern))
    # Deduplicate: if conflict file exists, prefer it over original (more recent data)
    files = _dedup_conflict_files(files)

    if not files:
        print(f"[WARN] Aucun fichier .jsonl trouvé dans {events_dir}")
        return []

    events = []
    errors = 0

    for filepath in files:
        # Extract agent name, stripping .sync-conflict-* suffix
        basename = os.path.basename(filepath)
        agent_raw = basename.replace(".sync-conflict-", "\x00").split("\x00")[0]
        agent = os.path.splitext(agent_raw)[0]
        with open(filepath, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    # Injecter l'agent depuis le nom du fichier si absent
                    if "agent" not in event:
                        event["agent"] = agent
                    events.append(event)
                except json.JSONDecodeError as e:
                    errors += 1
                    print(f"[WARN] {filepath}:{line_no} — JSON invalide: {e}", file=sys.stderr)

    if errors:
        print(f"[WARN] {errors} ligne(s) ignorée(s)")

    # Tri chronologique (timestamp) puis par event_id pour la stabilité
    events.sort(key=lambda e: (e.get("ts", ""), e.get("id", "")))
    return events


# ── Merge logic ─────────────────────────────────────────────────────

def merge(
    events_dir: str,
    db_path: str = "consolidated.db",
    dry_run: bool = False,
) -> dict:
    """Merge depuis un dossier d'événements."""
    events = parse_events(events_dir)
    return merge_events(events, db_path, dry_run)


def merge_events(
    events: list[dict],
    db_path: str = "consolidated.db",
    dry_run: bool = False,
) -> dict:
    """
    Lit tous les événements, rejoue dans l'ordre, écrit la DB consolidée.

    Règles :
      - remember → INSERT (si pas déjà vu via processed_events)
      - update   → UPDATE si la mémoire existe, sinon crée
      - forget   → DELETE
      - Même event_id = idempotent (via processed_events)
      - Last-write-wins pour les conflits (timestamp le plus récent)

    Returns:
        dict avec les stats
    """

    if dry_run:
        print(f"[DRY RUN] {len(events)} événements chargés, pas d'écriture")
        return {"events_loaded": len(events), "merged": 0, "updated": 0,
                "deleted": 0, "skipped": 0, "errors": 0}

    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    # Rebuild FTS index pour données existantes (idempotent)
    try:
        conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
    except sqlite3.OperationalError:
        pass  # FTS table pas encore créée (premier run)

    # Verrouiller pour éviter merge concurrent sur la même DB
    lock_path = db_path + ".lock"
    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("[WARN] Un autre merge est déjà en cours sur cette DB, abandon.")
        lock_fd.close()
        conn.close()
        return {"events_loaded": len(events), "merged": 0, "updated": 0,
                "deleted": 0, "skipped": 0, "errors": 0, "locked": True}

    stats = {"events_loaded": len(events), "merged": 0, "updated": 0,
             "deleted": 0, "skipped": 0, "errors": 0}

    processed_ids = set()
    to_process = []

    for event in events:
        eid = event.get("id")
        if not eid:
            stats["errors"] += 1
            continue

        # Idempotence : ne pas rejouer un événement déjà traité
        already_processed = conn.execute(
            "SELECT 1 FROM processed_events WHERE event_id = ?", (eid,)
        ).fetchone()

        if already_processed:
            stats["skipped"] += 1
            continue

        op = event.get("op")
        ts = event.get("ts", now_iso())

        if op == "remember":
            _handle_remember(conn, event, ts)
            stats["merged"] += 1

        elif op == "update":
            _handle_update(conn, event, ts)
            stats["updated"] += 1

        elif op == "forget":
            _handle_forget(conn, event, ts)
            stats["deleted"] += 1

        elif op == "message":
            event.setdefault("payload", {})
            event["payload"]["scope"] = "cross-cluster"
            event["payload"]["source"] = f"cross-cluster/{event.get('agent', 'unknown')}"
            _handle_remember(conn, event, ts)
            stats["merged"] += 1

        else:
            print(f"[WARN] Opération inconnue '{op}' pour event {eid}", file=sys.stderr)
            stats["errors"] += 1
            continue

        # Marquer l'événement comme traité (batché à la fin)
        to_process.append(eid)

    # Batch insert des processed_events
    if to_process:
        conn.executemany(
            "INSERT OR IGNORE INTO processed_events (event_id) VALUES (?)",
            [(eid,) for eid in to_process],
        )

    conn.commit()
    conn.close()
    fcntl.flock(lock_fd, fcntl.LOCK_UN)
    lock_fd.close()
    return stats


def _handle_remember(conn: sqlite3.Connection, event: dict, ts: str):
    """Insère une nouvelle mémoire."""
    memory_id = generate_memory_id(event["id"], event.get("agent", "unknown"))
    payload = event.get("payload", {})

    conn.execute("""
        INSERT OR REPLACE INTO memories
            (id, content, importance, source, scope, agent, created_at, updated_at, event_id, event_ts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        memory_id,
        payload.get("content", ""),
        payload.get("importance", 0.5),
        payload.get("source", "unknown"),
        payload.get("scope", "shared"),
        event.get("agent", "unknown"),
        ts,
        ts,
        event["id"],
        ts,
    ))


def _handle_update(conn: sqlite3.Connection, event: dict, ts: str):
    """Met à jour une mémoire existante, ou la crée si absente."""
    payload = event.get("payload", {})
    memory_id = payload.get("memory_id")

    if not memory_id:
        print(f"[WARN] update {event.get('id')} sans memory_id", file=sys.stderr)
        return

    existing = conn.execute(
        "SELECT id FROM memories WHERE id = ?", (memory_id,)
    ).fetchone()

    if existing:
        # Update partiel : seuls les champs fournis sont modifiés
        fields = []
        values = []
        for field in ["content", "importance", "source", "scope"]:
            if field in payload:
                fields.append(f"{field} = ?")
                values.append(payload[field])

        if fields:
            fields.append("updated_at = ?")
            fields.append("event_id = ?")
            fields.append("event_ts = ?")
            values.append(ts)
            values.append(event["id"])
            values.append(ts)
            values.append(memory_id)
            conn.execute(
                f"UPDATE memories SET {', '.join(fields)} WHERE id = ?",
                values,
            )
    else:
        # Traiter comme un remember
        _handle_remember(conn, event, ts)


def _handle_forget(conn: sqlite3.Connection, event: dict, ts: str):
    """Supprime une mémoire."""
    memory_id = event.get("payload", {}).get("memory_id")

    if not memory_id:
        print(f"[WARN] forget {event.get('id')} sans memory_id", file=sys.stderr)
        return

    conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))


# ── CLI ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="HiveMind Merge Engine")
    parser.add_argument(
        "--events-dir",
        default="./memory/events",
        help="Dossier contenant les fichiers .jsonl (défaut: ./memory/events)",
    )
    parser.add_argument(
        "--db",
        default="./memory/consolidated.db",
        help="Chemin de la base SQLite consolidée (défaut: ./memory/consolidated.db)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Charge les événements sans écrire dans la DB",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Affiche les stats de la DB sans merger",
    )
    parser.add_argument(
        "--verify-chains",
        action="store_true",
        help="Vérifie l'intégrité des hash chains avant le merge",
    )
    args = parser.parse_args()

    if args.stats:
        _show_stats(args.db)
        return

    if args.verify_chains:
        try:
            from hivemind_chain import verify_all_chains
            results = verify_all_chains(args.events_dir)
            if not results:
                print("\n✅ Toutes les chaînes sont valides")
            else:
                print(f"\n❌ {sum(len(v) for v in results.values())} erreur(s) détectée(s) :")
                for agent, errs in results.items():
                    for e in errs[:5]:
                        print(f"   • {agent}: {e}")
                print("\n⚠️  Merge annulé — corrigez les chaînes avec --verify-chains")
                return
        except ImportError:
            print("[WARN] cryptography non installé — vérification ignorée")
        except Exception as e:
            print(f"[WARN] Erreur de vérification: {e}")

    stats = merge(
        events_dir=args.events_dir,
        db_path=args.db,
        dry_run=args.dry_run,
    )

    print(f"\n✅ Merge terminé")
    print(f"   Événements chargés : {stats['events_loaded']}")
    print(f"   Nouveaux (remember): {stats['merged']}")
    print(f"   Mis à jour (update) : {stats['updated']}")
    print(f"   Supprimés (forget) : {stats['deleted']}")
    print(f"   Ignorés (déjà vus) : {stats['skipped']}")
    print(f"   Erreurs            : {stats['errors']}")
    print(f"   DB                 : {os.path.abspath(args.db)}")


def _show_stats(db_path: str):
    """Affiche les statistiques de la base consolidée."""
    if not os.path.exists(db_path):
        print(f"Aucune base trouvée à {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    total = conn.execute("SELECT COUNT(*) as n FROM memories").fetchone()["n"]
    per_agent = conn.execute(
        "SELECT agent, COUNT(*) as n FROM memories GROUP BY agent ORDER BY n DESC"
    ).fetchall()
    per_scope = conn.execute(
        "SELECT scope, COUNT(*) as n FROM memories GROUP BY scope ORDER BY n DESC"
    ).fetchall()
    processed = conn.execute("SELECT COUNT(*) as n FROM processed_events").fetchone()["n"]

    print(f"\n📊 Statistiques consolidated.db")
    print(f"   Mémoires totales    : {total}")
    print(f"   Événements traités  : {processed}")
    print(f"\n   Par agent :")
    for row in per_agent:
        print(f"     {row['agent']:<20} {row['n']}")
    print(f"\n   Par scope :")
    for row in per_scope:
        print(f"     {row['scope']:<20} {row['n']}")

    conn.close()


if __name__ == "__main__":
    main()
