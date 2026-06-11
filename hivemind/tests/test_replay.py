#!/usr/bin/env python3
"""
Test de reproductibilité multi-machine (replay test)
=====================================================

Vérifie que deux machines recevant les mêmes fichiers .jsonl
via Syncthing produisent des consolidated.db identiques.

Scénario :
  1. Créer un dossier events/ avec 3 agents
  2. Copier events/ → events_peer1/ et events_peer2/
  3. Lancer merge_engine sur chaque peer (DB séparées)
  4. Comparer les deux DB : doivent être identiques bit-à-bit
     pour toutes les données utiles (seul processed_events.processed_at
     peut différer — timestamp du merge, non déterministe)
"""

import subprocess
import sys
import os
import shutil
import sqlite3
import hashlib
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MERGER = os.path.join(PACKAGE_DIR, "merge_engine.py")
WRITER = os.path.join(PACKAGE_DIR, "event_writer.py")  # package-relative


def run(*args, cwd=None):
    """Lance une commande, retourne stdout."""
    cmd = [str(a) for a in args]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd or PACKAGE_DIR)
    if result.returncode != 0:
        print(f"[ERREUR] {' '.join(cmd)}: {result.stderr[:200]}", file=sys.stderr)
    return result.stdout


def db_memories_hash(db_path: str) -> str:
    """
    Calcule un hash SHA-256 du contenu utile de la DB.
    Exclut processed_events.processed_at (timestamp de merge non déterministe).
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Collecter toutes les mémoires triées (ordre déterministe)
    memories = conn.execute(
        "SELECT id, content, importance, source, scope, agent, "
        "created_at, updated_at, event_id, event_ts, "
        "is_deleted, deleted_at, deleted_by, tombstone_reason, "
        "reverted_by, reverted_at "
        "FROM memories ORDER BY id"
    ).fetchall()

    # Collecter les event_id traités (sans processed_at)
    processed = conn.execute(
        "SELECT event_id FROM processed_events ORDER BY event_id"
    ).fetchall()

    conn.close()

    # Construire une représentation canonique
    h = hashlib.sha256()
    for m in memories:
        # Chaque champ, séparé par |
        row_str = "|".join(str(v) if v is not None else "NULL" for v in m)
        h.update(row_str.encode("utf-8"))
        h.update(b"\n")

    h.update(b"---PROCESSED---\n")
    for p in processed:
        h.update(p["event_id"].encode("utf-8"))
        h.update(b"\n")

    return h.hexdigest()


def test_replay():
    print("=" * 60)
    print("  TEST REPLAY — DÉTERMINISME MULTI-MACHINE")
    print("=" * 60)

    # ── Phase 1 : Créer les événements ──────────────────────────

    print("\n─── Phase 1 : Création des événements de test ───\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        events_dir = os.path.join(tmpdir, "events")
        os.makedirs(events_dir)

        # Alice écrit
        run("python3", WRITER, "--events-dir", events_dir, "--agent", "alice",
            "remember", "Client Omega : demander cash-flow et balance âgée",
            "--importance", "0.9", "--source", "correction", "--scope", "shared")
        run("python3", WRITER, "--events-dir", events_dir, "--agent", "alice",
            "remember", "Seuil matérialité groupe : 5% RN",
            "--importance", "0.95", "--scope", "shared")

        # Bob écrit
        run("python3", WRITER, "--events-dir", events_dir, "--agent", "bob",
            "remember", "Client Gamma : toujours vérifier parties liées",
            "--importance", "0.85", "--scope", "audit")
        run("python3", WRITER, "--events-dir", events_dir, "--agent", "bob",
            "remember", "Norme IFRS 16 : contrats location > 12 mois",
            "--importance", "0.8", "--scope", "shared")

        # Charles écrit
        run("python3", WRITER, "--events-dir", events_dir, "--agent", "charles",
            "remember", "Procédure circularisation : envoi recommandé + email",
            "--importance", "0.75", "--scope", "shared")

        # Alice update une mémoire de Bob
        run("python3", WRITER, "--events-dir", events_dir, "--agent", "alice",
            "update", "--memory-id", "mem-todo",  # sera ignoré car id inexistant
            "--content", "should be ignored", "--importance", "0.5", "--scope", "shared")

        # Alice forget (tombstone) sa première mémoire
        run("python3", WRITER, "--events-dir", events_dir, "--agent", "alice",
            "forget", "--memory-id", "mem-todo", "--reason", "test tombstone")

        files = sorted(os.listdir(events_dir))
        print(f"   Événements créés : {len(files)} fichiers")
        for f in files:
            with open(os.path.join(events_dir, f)) as fh:
                lines = sum(1 for _ in fh)
            print(f"     {f}: {lines} événement(s)")

        # ── Phase 2 : Merge sur Peer 1 ─────────────────────────

        print("\n─── Phase 2 : Merge Peer 1 ───\n")

        peer1_events = os.path.join(tmpdir, "peer1", "events")
        peer2_events = os.path.join(tmpdir, "peer2", "events")
        shutil.copytree(events_dir, peer1_events)
        shutil.copytree(events_dir, peer2_events)

        db1 = os.path.join(tmpdir, "peer1", "consolidated.db")
        db2 = os.path.join(tmpdir, "peer2", "consolidated.db")

        out1 = run("python3", MERGER, "--events-dir", peer1_events, "--db", db1)
        print(f"   Peer 1: {out1.strip()}")

        # ── Phase 3 : Merge sur Peer 2 ─────────────────────────

        print("\n─── Phase 3 : Merge Peer 2 ───\n")

        out2 = run("python3", MERGER, "--events-dir", peer2_events, "--db", db2)
        print(f"   Peer 2: {out2.strip()}")

        # ── Phase 4 : Comparer les DB ──────────────────────────

        print("\n─── Phase 4 : Comparaison des DB ───\n")

        hash1 = db_memories_hash(db1)
        hash2 = db_memories_hash(db2)

        print(f"   Peer 1 hash: {hash1}")
        print(f"   Peer 2 hash: {hash2}")

        # Vérifier les comptes
        conn1 = sqlite3.connect(db1)
        conn2 = sqlite3.connect(db2)

        count1 = conn1.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        count2 = conn2.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        tomb1 = conn1.execute("SELECT COUNT(*) FROM memories WHERE is_deleted = 1").fetchone()[0]
        tomb2 = conn2.execute("SELECT COUNT(*) FROM memories WHERE is_deleted = 1").fetchone()[0]
        proc1 = conn1.execute("SELECT COUNT(*) FROM processed_events").fetchone()[0]
        proc2 = conn2.execute("SELECT COUNT(*) FROM processed_events").fetchone()[0]

        print(f"   Mémoires Peer 1: {count1} (tombstones: {tomb1}, processed: {proc1})")
        print(f"   Mémoires Peer 2: {count2} (tombstones: {tomb2}, processed: {proc2})")

        conn1.close()
        conn2.close()

        assert hash1 == hash2, (
            f"REPLAY ÉCHEC: les DB ne sont pas identiques!\n"
            f"  Peer 1: {hash1}\n"
            f"  Peer 2: {hash2}"
        )
        assert count1 == count2, f"Nombre de mémoires différent: {count1} vs {count2}"
        assert tomb1 == tomb2, f"Nombre de tombstones différent: {tomb1} vs {tomb2}"
        assert proc1 == proc2, f"Nombre d'events traités différent: {proc1} vs {proc2}"

        print("\n" + "=" * 60)
        print("  ✅ REPLAY TEST — DB IDENTIQUES SUR LES DEUX PEERS")
        print("=" * 60)


if __name__ == "__main__":
    test_replay()
