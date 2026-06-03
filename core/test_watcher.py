#!/usr/bin/env python3
"""
Test du Watcher
================

Scénario :
  1. Nettoyer l'environnement de test
  2. Démarrer le watcher en arrière-plan
  3. Alice écrit une mémoire → le watcher détecte → merge auto
  4. Bob écrit une mémoire → re-détection → re-merge
  5. Vérifier que le merge a bien eu lieu
  6. Tuer le watcher
"""

import os
import sys
import time
import glob
import json
import signal
import sqlite3
import subprocess

PROTOTYPE_DIR = os.path.dirname(os.path.abspath(__file__))
EVENTS_DIR = os.path.join(PROTOTYPE_DIR, "memory", "events")
DB_PATH = os.path.join(PROTOTYPE_DIR, "memory", "consolidated.db")

sys.path.insert(0, PROTOTYPE_DIR)
from hivemind_mnemosyne import HiveMindMemory


def cleanup():
    for f in glob.glob(os.path.join(EVENTS_DIR, "*.jsonl*")):
        os.remove(f)
    for suffix in ["", "-wal", "-shm", ".lock", ".watcher.lock"]:
        lock_path = DB_PATH + suffix
        if os.path.exists(lock_path):
            os.remove(lock_path)
    print("🧹 Nettoyage\n")


def test_watcher():
    cleanup()

    print("=" * 60)
    print("  TEST WATCHER — merge automatique")
    print("=" * 60)

    # ── Phase 1 : Démarrer le watcher en arrière-plan ────────────

    print("\n─── Démarrage du watcher en arrière-plan ───\n")

    watcher = subprocess.Popen(
        [sys.executable, "watcher.py",
         "--events-dir", EVENTS_DIR,
         "--db", DB_PATH,
         "--interval", "0.5",    # poll rapide pour le test
         "--debounce", "1.0"],   # debounce court
        cwd=PROTOTYPE_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    time.sleep(1.5)  # Laisser le watcher démarrer
    print(f"   Watcher PID: {watcher.pid}")

    # ── Phase 2 : Alice écrit 2 mémoires ─────────────────────────

    print("\n─── Alice écrit 2 mémoires ───\n")

    alice = HiveMindMemory(
        events_dir=EVENTS_DIR, consolidated_db=DB_PATH,
        agent="alice",
    )

    alice.remember("Test watcher 1 : mémoire d'Alice", importance=0.9)
    alice.remember("Test watcher 2 : autre mémoire", importance=0.7)

    # ── Phase 3 : Attendre que le watcher détecte et merge ───────

    print("\n─── Attente du merge automatique... ───\n")
    time.sleep(3.0)  # intervalle + debounce

    # Vérifier le résultat
    if os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        conn.close()
        print(f"   Mémoires dans consolidated.db : {count}")
        assert count >= 2, f"Attendu ≥ 2, obtenu {count}"
        print(f"   ✅ Merge automatique détecté (2 mémoires)")
    else:
        print("   ❌ consolidated.db non créé")
        assert False, "Watcher n'a pas mergé"

    # ── Phase 4 : Bob écrit une mémoire ──────────────────────────

    print("\n─── Bob écrit une mémoire ───\n")

    bob = HiveMindMemory(
        events_dir=EVENTS_DIR, consolidated_db=DB_PATH,
        agent="bob",
    )
    bob.remember("Test watcher 3 : mémoire de Bob", importance=0.8)

    time.sleep(2.5)

    conn = sqlite3.connect(DB_PATH)
    count2 = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    conn.close()
    print(f"   Mémoires après ajout Bob : {count2}")
    assert count2 >= 3, f"Attendu ≥ 3, obtenu {count2}"
    print(f"   ✅ Second merge automatique détecté")

    # ── Phase 5 : Arrêter le watcher ─────────────────────────────

    print("\n─── Arrêt du watcher ───\n")

    watcher.send_signal(signal.SIGINT)
    try:
        stdout, _ = watcher.communicate(timeout=3)
        print(stdout[-500:] if len(stdout) > 500 else stdout)
    except subprocess.TimeoutExpired:
        watcher.kill()
        print("   (killed)")

    # ── Vérification finale ──────────────────────────────────────

    conn = sqlite3.connect(DB_PATH)
    final = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    by_agent = conn.execute(
        "SELECT agent, COUNT(*) FROM memories GROUP BY agent"
    ).fetchall()
    conn.close()

    print(f"\n   Mémoires finales : {final}")
    for agent, n in by_agent:
        print(f"     {agent}: {n}")

    assert final == 3, f"Attendu 3, obtenu {final}"

    print("\n" + "=" * 60)
    print("  ✅ WATCHER FONCTIONNEL")
    print("=" * 60)


if __name__ == "__main__":
    test_watcher()
