#!/usr/bin/env python3
"""
Test d'intégration HiveMind + Mnemosyne
========================================

Scénario :
  1. Bootstrap : exporte 5 vraies mémoires Mnemosyne → Event Log
  2. Alice écrit 3 nouvelles mémoires via l'adaptateur
  3. Bob écrit 2 mémoires
  4. Merge
  5. Recall depuis consolidated.db
  6. Alice corrige une mémoire de Bob (update)
  7. Bob supprime une de ses mémoires (forget)
  8. Re-merge + recall
"""

import os
import sys
import glob
import json
import sqlite3

PROTOTYPE_DIR = os.path.dirname(os.path.abspath(__file__))
EVENTS_DIR = os.path.join(PROTOTYPE_DIR, "memory", "events")
CONSOLIDATED_DB = os.path.join(PROTOTYPE_DIR, "memory", "consolidated.db")
MNEMOSYNE_DB = os.path.expanduser("~/.hermes/mnemosyne/data/mnemosyne.db")

sys.path.insert(0, PROTOTYPE_DIR)
from hivemind_mnemosyne import HiveMindMemory


def cleanup():
    """Nettoie les données de test HiveMind (pas Mnemosyne native)."""
    for f in glob.glob(os.path.join(EVENTS_DIR, "*.jsonl")):
        os.remove(f)
    if os.path.exists(CONSOLIDATED_DB):
        os.remove(CONSOLIDATED_DB)
    print("🧹 Nettoyage Event Log + consolidated.db\n")


def sep(title):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


def test_integration():
    cleanup()

    print("=" * 60)
    print("  TEST INTÉGRATION HIVEMIND ↔ MNEMOSYNE")
    print("=" * 60)

    # ── Phase 1 : Bootstrap ──────────────────────────────────────

    sep("Phase 1 : Bootstrap — export Mnemosyne native → Event Log")

    alice = HiveMindMemory(
        events_dir=EVENTS_DIR,
        consolidated_db=CONSOLIDATED_DB,
        mnemosyne_db=MNEMOSYNE_DB,
        agent="alice",
    )

    # Exporte toutes les mémoires existantes (importance > 0)
    count = alice.bootstrap(scope="shared", min_importance=0.0)
    print(f"   ✅ {count} mémoires exportées dans alice.jsonl")
    assert count > 0, "Bootstrap a exporté 0 mémoires"

    # ── Phase 2 : Nouvelles écritures ────────────────────────────

    sep("Phase 2 : Alice et Bob écrivent de nouvelles mémoires")

    bob = HiveMindMemory(
        events_dir=EVENTS_DIR,
        consolidated_db=CONSOLIDATED_DB,
        mnemosyne_db=MNEMOSYNE_DB,
        agent="bob",
    )

    # Alice écrit
    e1 = alice.remember(
        "Client Delta : dossier sensible — nécessite signature N+2 pour toute communication externe",
        importance=0.95, source="correction", scope="audit",
    )
    print(f"   Alice: {e1}")

    e2 = alice.remember(
        "Procédure revue qualité : toujours vérifier les pièces justificatives > 10 000€",
        importance=0.8, source="procedure", scope="shared",
    )
    print(f"   Alice: {e2}")

    e3 = alice.remember(
        "Contact PCAOB : utiliser uniquement l'adresse pcaob@cabinet.com pour toute correspondance",
        importance=0.7, source="contact", scope="shared",
    )
    print(f"   Alice: {e3}")

    # Bob écrit
    e4 = bob.remember(
        "Client Epsilon : le DG exige un rapport exécutif en 2 pages max, format visuel",
        importance=0.85, source="client-feedback", scope="audit",
    )
    print(f"   Bob: {e4}")

    e5 = bob.remember(
        "Norme IFRS 16 : toujours vérifier les contrats de location > 12 mois pour les filiales",
        importance=0.9, source="formation", scope="shared",
    )
    print(f"   Bob: {e5}")

    # ── Phase 3 : Merge ──────────────────────────────────────────

    sep("Phase 3 : Merge — reconstruit consolidated.db")

    result = alice.merge()
    print(f"   {result}")

    # Vérifier
    conn = sqlite3.connect(CONSOLIDATED_DB)
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    print(f"   Total mémoires consolidées : {total}")
    conn.close()

    # ── Phase 4 : Recall ─────────────────────────────────────────

    sep("Phase 4 : Recall depuis consolidated.db")

    results = alice.recall("IFRS", limit=5)
    print(f"   Recherche 'IFRS' → {len(results)} résultats :")
    for r in results:
        print(f"     [{r.get('importance', '?')}] {r['content'][:80]}...")

    results = alice.recall("client", limit=10)
    print(f"\n   Recherche 'client' → {len(results)} résultats")
    assert len(results) > 0, "Recall ne retourne rien"

    # Bob voit la même chose
    bob_results = bob.recall("client", limit=10)
    assert len(bob_results) == len(results), \
        f"Bob ({len(bob_results)}) ≠ Alice ({len(results)})"
    print(f"   ✅ Bob voit le même nombre de résultats qu'Alice")

    # ── Phase 5 : Update ─────────────────────────────────────────

    sep("Phase 5 : Alice corrige une mémoire de Bob")

    # Trouver la mémoire sur IFRS 16
    target = alice.recall("IFRS 16", limit=1)
    if target:
        mem_id = target[0]["id"]
        print(f"   Mémoire cible : {mem_id}")
        print(f"   Contenu avant  : {target[0]['content'][:80]}...")

        alice.update(
            memory_id=mem_id,
            content="Norme IFRS 16 : vérifier les contrats de location > 12 mois pour TOUTES les entités, y compris filiales ET coentreprises",
            importance=0.95,
        )
        alice.merge()

        # Vérifier
        updated = alice.recall("coentreprises", limit=1)
        if updated:
            print(f"   Contenu après : {updated[0]['content'][:80]}...")
            assert "coentreprises" in updated[0]["content"], "Update non appliqué"
            print(f"   ✅ Update appliqué et visible")

    # ── Phase 6 : Forget ─────────────────────────────────────────

    sep("Phase 6 : Bob supprime une mémoire")

    # Supprimer la mémoire "PCAOB"
    pcaob = bob.recall("PCAOB", limit=1)
    if pcaob:
        mem_id = pcaob[0]["id"]
        print(f"   Suppression : {mem_id} — {pcaob[0]['content'][:60]}...")
        bob.forget(mem_id)
        bob.merge()

        # Vérifier
        conn = sqlite3.connect(CONSOLIDATED_DB)
        new_total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        conn.close()
        print(f"   Total avant forget : {total}")
        print(f"   Total après forget : {new_total}")
        assert new_total == total - 1, f"Attendu {total-1}, obtenu {new_total}"
        print(f"   ✅ Suppression effective")

    # ── Phase 7 : Idempotence ────────────────────────────────────

    sep("Phase 7 : Idempotence — re-merge ne change rien")

    before = sqlite3.connect(CONSOLIDATED_DB).execute(
        "SELECT COUNT(*) FROM memories"
    ).fetchone()[0]

    alice.merge()

    after = sqlite3.connect(CONSOLIDATED_DB).execute(
        "SELECT COUNT(*) FROM memories"
    ).fetchone()[0]

    assert before == after, f"Idempotence échouée: {before} → {after}"
    print(f"   ✅ {before} mémoires, inchangé après re-merge")

    # ── Stats ────────────────────────────────────────────────────

    sep("Stats finales")
    stats = alice.stats()
    print(f"   Total : {stats['total']}")
    print(f"   Par agent : {json.dumps(stats['by_agent'], indent=2)}")
    print(f"   Par scope : {json.dumps(stats['by_scope'], indent=2)}")

    print("\n" + "=" * 60)
    print("  ✅ INTÉGRATION MNEMOSYNE RÉUSSIE")
    print("=" * 60)


if __name__ == "__main__":
    test_integration()
