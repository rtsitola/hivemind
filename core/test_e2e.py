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

PROTOTYPE_DIR = os.path.dirname(os.path.abspath(__file__))
EVENTS_DIR = os.path.join(PROTOTYPE_DIR, "memory", "events")
DB_PATH = os.path.join(PROTOTYPE_DIR, "memory", "consolidated.db")


def run(*args, cwd=None):
    """Lance une commande avec des arguments safe (pas de shell)."""
    cmd = [str(a) for a in args]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd or PROTOTYPE_DIR)
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

    WRITER = os.path.join(PROTOTYPE_DIR, "event_writer.py")
    MERGER = os.path.join(PROTOTYPE_DIR, "merge_engine.py")

    # ── Scénario ──────────────────────────────────────────────────

    print("\n─── Phase 1 : Alice et Bob écrivent des souvenirs ───\n")

    run("python3", WRITER, "--agent", "alice", "remember",
        "Client Omega : toujours demander le cash-flow statement avant l'audit",
        "--importance", "0.9", "--source", "correction", "--scope", "audit")

    run("python3", WRITER, "--agent", "alice", "remember",
        "Seuil matérialité IFRS : 5% du résultat net, pas du CA pour ce cabinet",
        "--importance", "0.95", "--source", "decision-comite", "--scope", "shared")

    run("python3", WRITER, "--agent", "bob", "remember",
        "Client Gamma : structure de prix de transfert complexe, nécessite fiscaliste dédié",
        "--importance", "0.85", "--source", "discovery", "--scope", "fiscal")

    run("python3", WRITER, "--agent", "bob", "remember",
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

    run("python3", WRITER, "--agent", "alice", "update",
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

    print("\n─── Phase 6 : Bob supprime une mémoire obsolète ───\n")

    mem_gamma = conn.execute(
        "SELECT id FROM memories WHERE content LIKE '%Gamma%'"
    ).fetchone()
    gamma_id = mem_gamma[0]

    run("python3", WRITER, "--agent", "bob", "forget", "--memory-id", gamma_id)
    run("python3", MERGER, "--events-dir", EVENTS_DIR, "--db", DB_PATH)

    final_count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    print(f"\n   Mémoires après forget : {final_count}")
    assert final_count == 3, f"Après forget: attendu 3, obtenu {final_count}"

    print("\n─── Phase 7 : Idempotence finale ───\n")
    run("python3", MERGER, "--events-dir", EVENTS_DIR, "--db", DB_PATH)
    run("python3", MERGER, "--stats", "--db", DB_PATH)

    conn.close()
    print("\n" + "=" * 60)
    print("  ✅ TOUS LES TESTS PASSENT")
    print("=" * 60)


if __name__ == "__main__":
    cleanup()
    test_scenario()
