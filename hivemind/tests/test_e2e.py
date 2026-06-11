#!/usr/bin/env python3
"""
Test end-to-end du Merge Engine
================================

Simule 3 agents (alice, bob, charles) qui écrivent des événements,
puis merge et vérifie que la DB consolidée est correcte.
"""

import subprocess
import sys
import os
import json
import sqlite3

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVENTS_DIR = os.path.join(REPO_ROOT, "memory", "events")
DB_PATH = os.path.join(REPO_ROOT, "memory", "consolidated.db")


def run(*args, cwd=None):
    """Lance une commande avec des arguments safe (pas de shell)."""
    cmd = [str(a) for a in args]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd or PACKAGE_DIR)
    print(result.stdout.rstrip())
    if result.returncode != 0:
        print(f"[ERREUR] {result.stderr.rstrip()}", file=sys.stderr)
    return result


def cleanup():
    """Supprime les données de test précédentes."""
    for f in os.listdir(EVENTS_DIR):
        os.remove(os.path.join(EVENTS_DIR, f))
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    print("🧹 Nettoyage terminé\n")


def test_scenario():
    cleanup()  # Nettoie les données de test précédentes avant chaque run
    print("=" * 60)
    print("  TEST HIVEMIND MERGE ENGINE")
    print("=" * 60)

    WRITER = os.path.join(PACKAGE_DIR, "event_writer.py")  # package-relative
    MERGER = os.path.join(PACKAGE_DIR, "merge_engine.py")

    # ── Scénario ──────────────────────────────────────────────────

    print("\n─── Phase 1 : Alice et Bob écrivent des souvenirs ───\n")

    run("python3", WRITER, "--events-dir", EVENTS_DIR, "--agent", "alice", "remember",
        "Client Omega : toujours demander le cash-flow statement avant l'audit",
        "--importance", "0.9", "--source", "correction", "--scope", "audit")

    run("python3", WRITER, "--events-dir", EVENTS_DIR, "--agent", "alice", "remember",
        "Seuil matérialité IFRS : 5% du résultat net, pas du CA pour ce cabinet",
        "--importance", "0.95", "--source", "decision-comite", "--scope", "shared")

    run("python3", WRITER, "--events-dir", EVENTS_DIR, "--agent", "bob", "remember",
        "Client Gamma : structure de prix de transfert complexe, nécessite fiscaliste dédié",
        "--importance", "0.85", "--source", "discovery", "--scope", "fiscal")

    run("python3", WRITER, "--events-dir", EVENTS_DIR, "--agent", "bob", "remember",
        "Toujours vérifier les parties liées avant la circularisation",
        "--importance", "0.7", "--source", "best-practice", "--scope", "shared")

    print("\n─── Phase 2 : Premier merge ───\n")

    run("python3", MERGER, "--events-dir", EVENTS_DIR, "--db", DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    print(f"\n   Mémoires dans consolidated.db : {count}")
    assert count == 4, f"Attendu 4, obtenu {count}"

    print("\n─── Phase 3 : Charles arrive (idempotence) ───\n")

    run("python3", MERGER, "--events-dir", EVENTS_DIR, "--db", DB_PATH)
    conn2 = sqlite3.connect(DB_PATH)
    count2 = conn2.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    assert count2 == 4, f"Idempotence: attendu 4, obtenu {count2}"

    print("\n─── Phase 4 : Alice corrige la mémoire de Bob ───\n")

    mem = conn.execute(
        "SELECT id FROM memories WHERE content LIKE '%parties liées%'"
    ).fetchone()
    memory_id = mem[0]
    print(f"   memory_id cible : {memory_id}")

    run("python3", WRITER, "--events-dir", EVENTS_DIR, "--agent", "alice", "update",
        "--memory-id", memory_id,
        "--content", "Toujours vérifier les parties liées et les covenants bancaires avant la circularisation",
        "--importance", "0.85")

    print("\n─── Phase 5 : Merge après correction ───\n")

    run("python3", MERGER, "--events-dir", EVENTS_DIR, "--db", DB_PATH)

    updated = conn.execute(
        "SELECT content, importance FROM memories WHERE id = ?", (memory_id,)
    ).fetchone()
    print(f"   Contenu  : \"{updated[0]}\"")
    print(f"   Importance : {updated[1]}")
    assert "covenants bancaires" in updated[0], "Mise à jour non appliquée"
    assert updated[1] == 0.85, f"Importance: attendu 0.85, obtenu {updated[1]}"

    print("\n─── Phase 6 : Bob supprime une mémoire obsolète (tombstone) ───\n")

    mem_gamma = conn.execute(
        "SELECT id FROM memories WHERE content LIKE '%Gamma%'"
    ).fetchone()
    gamma_id = mem_gamma[0]

    run("python3", WRITER, "--events-dir", EVENTS_DIR, "--agent", "bob", "forget", "--memory-id", gamma_id,
        "--reason", "Client archivé")
    run("python3", MERGER, "--events-dir", EVENTS_DIR, "--db", DB_PATH)

    # Vérifier tombstone : mémoire toujours en base mais is_deleted=1
    total_after_forget = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    active_after_forget = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE is_deleted = 0"
    ).fetchone()[0]
    tombstoned = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE is_deleted = 1"
    ).fetchone()[0]
    print(f"\n   Total mémoires : {total_after_forget} (actives: {active_after_forget}, tombstone: {tombstoned})")
    assert total_after_forget == 4, f"Tombstone: attendu 4 total, obtenu {total_after_forget}"
    assert active_after_forget == 3, f"Tombstone: attendu 3 actives, obtenu {active_after_forget}"
    assert tombstoned == 1, f"Tombstone: attendu 1 tombstone, obtenu {tombstoned}"

    # Vérifier les métadonnées du tombstone
    tombstone_row = conn.execute(
        "SELECT deleted_by, tombstone_reason FROM memories WHERE id = ?", (gamma_id,)
    ).fetchone()
    assert tombstone_row[0] == "bob", f"deleted_by: attendu bob, obtenu {tombstone_row[0]}"
    assert tombstone_row[1] == "Client archivé", f"reason: attendu 'Client archivé', obtenu {tombstone_row[1]}"
    print(f"   deleted_by={tombstone_row[0]}, reason={tombstone_row[1]} ✅")

    print("\n─── Phase 7 : Alice restaure la mémoire (revert) ───\n")

    run("python3", WRITER, "--events-dir", EVENTS_DIR, "--agent", "alice", "revert", "--memory-id", gamma_id)
    run("python3", MERGER, "--events-dir", EVENTS_DIR, "--db", DB_PATH)

    # Vérifier restauration
    active_after_revert = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE is_deleted = 0"
    ).fetchone()[0]
    tombstoned_after_revert = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE is_deleted = 1"
    ).fetchone()[0]
    reverted_row = conn.execute(
        "SELECT is_deleted, reverted_by, deleted_by FROM memories WHERE id = ?", (gamma_id,)
    ).fetchone()
    print(f"\n   Actives: {active_after_revert}, Tombstones: {tombstoned_after_revert}")
    assert active_after_revert == 4, f"Revert: attendu 4 actives, obtenu {active_after_revert}"
    assert tombstoned_after_revert == 0, f"Revert: attendu 0 tombstone, obtenu {tombstoned_after_revert}"
    assert reverted_row[0] == 0, f"is_deleted doit être 0"
    assert reverted_row[1] == "alice", f"reverted_by: attendu alice, obtenu {reverted_row[1]}"
    assert reverted_row[2] is None, f"deleted_by doit être NULL après revert"
    print(f"   reverted_by={reverted_row[1]}, is_deleted={reverted_row[0]}, deleted_by=NULL ✅")

    print("\n─── Phase 8 : Idempotence finale ───\n")
    run("python3", MERGER, "--events-dir", EVENTS_DIR, "--db", DB_PATH)
    run("python3", MERGER, "--stats", "--db", DB_PATH)

    conn.close()
    print("\n" + "=" * 60)
    print("  ✅ TOUS LES TESTS PASSENT")
    print("=" * 60)


if __name__ == "__main__":
    cleanup()
    test_scenario()
