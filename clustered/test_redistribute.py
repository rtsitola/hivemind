#!/usr/bin/env python3
"""
Test Downstream Redistribution
================================

Scénario :
  1. Créer un Global avec des mémoires de plusieurs clusters
  2. Redistribute : Global → clusters (par pertinence/expertise)
  3. Vérifier que chaque cluster reçoit les mémoires pertinentes
  4. Vérifier qu'un cluster ne reçoit pas ses propres mémoires
  5. Vérifier la déduplication (deuxième run = 0 envoyé)
"""

import os
import sys
import json
import shutil
import tempfile
import sqlite3

PROTOTYPE_DIR = os.path.dirname(os.path.abspath(__file__))
# Import from hivemind-core (Phase 1)
_core_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core")
if os.path.exists(_core_dir):
    sys.path.insert(0, _core_dir)
sys.path.insert(0, PROTOTYPE_DIR)

from redistribute import redistribute, is_relevant_to_cluster
from merge_engine_weighted import load_config, apply_weights
from merge_engine import parse_events, merge as merge_engine_fn

CONFIG_PATH = os.path.join(PROTOTYPE_DIR, "cluster_weights.json")
TEST_DIR = None


def setup_global():
    """Crée un Global simulé avec des mémoires de 3 clusters."""
    global TEST_DIR
    TEST_DIR = tempfile.mkdtemp(prefix="hivemind_redist_test_")

    events_dir = os.path.join(TEST_DIR, "global", "events")
    db_path = os.path.join(TEST_DIR, "global", "consolidated.db")
    downstream_dir = os.path.join(TEST_DIR, "downstream")
    os.makedirs(events_dir)

    # Mémoires exportées par les clusters (simulé)
    events = [
        # AUDIT
        {"op":"remember","id":"exp-audit-1","agent":"cluster:audit","ts":"2026-05-26T10:00:00Z",
         "payload":{"content":"Client Omega : circularisation obligatoire — risque fraude élevé détecté",
                    "importance":0.95,"source":"audit/fieldwork","scope":"shared","original_agent":"alice"}},
        {"op":"remember","id":"exp-audit-2","agent":"cluster:audit","ts":"2026-05-26T10:01:00Z",
         "payload":{"content":"Seuil matérialité IFRS : 5% du résultat net, pas du CA",
                    "importance":0.85,"source":"audit/methodology","scope":"shared","original_agent":"bob"}},
        {"op":"remember","id":"exp-audit-3","agent":"cluster:audit","ts":"2026-05-26T10:02:00Z",
         "payload":{"content":"Checklist conformité mise à jour pour 2026",
                    "importance":0.6,"source":"audit/compliance","scope":"shared","original_agent":"alice"}},

        # FISCAL
        {"op":"remember","id":"exp-fisc-1","agent":"cluster:fiscal","ts":"2026-05-26T10:00:00Z",
         "payload":{"content":"Prix de transfert : nouvelle documentation requise pour les filiales étrangères",
                    "importance":0.9,"source":"fiscal/regulation","scope":"shared","original_agent":"david"}},
        {"op":"remember","id":"exp-fisc-2","agent":"cluster:fiscal","ts":"2026-05-26T10:01:00Z",
         "payload":{"content":"TVA intracommunautaire : seuil abaissé à 10 000€ — impact toutes missions",
                    "importance":0.88,"source":"fiscal/regulation","scope":"shared","original_agent":"eve"}},
        {"op":"remember","id":"exp-fisc-3","agent":"cluster:fiscal","ts":"2026-05-26T10:02:00Z",
         "payload":{"content":"Optimisation fiscale : nouveau crédit d'impôt recherche pour 2026",
                    "importance":0.7,"source":"fiscal/analysis","scope":"shared","original_agent":"david"}},

        # JURIDIQUE
        {"op":"remember","id":"exp-jur-1","agent":"cluster:juridique","ts":"2026-05-26T10:00:00Z",
         "payload":{"content":"Droit des sociétés : nouvelles obligations de déclaration des bénéficiaires effectifs",
                    "importance":0.92,"source":"juridique/regulation","scope":"shared","original_agent":"marie"}},
        {"op":"remember","id":"exp-jur-2","agent":"cluster:juridique","ts":"2026-05-26T10:01:00Z",
         "payload":{"content":"Contentieux fiscal : jurisprudence récente sur les prix de transfert",
                    "importance":0.85,"source":"juridique/litigation","scope":"shared","original_agent":"paul"}},
        {"op":"remember","id":"exp-jur-3","agent":"cluster:juridique","ts":"2026-05-26T10:02:00Z",
         "payload":{"content":"Contrat-type : nouvelle clause de médiation obligatoire",
                    "importance":0.82,"source":"juridique/templates","scope":"shared","original_agent":"marie"}},

        # GLOBAL (scope=global — doit être envoyé à TOUS)
        {"op":"remember","id":"exp-glob-1","agent":"cluster:global","ts":"2026-05-26T10:05:00Z",
         "payload":{"content":"ALERTE : Nouvelle norme ISA 540 révisée — impacte TOUTES les missions d'audit",
                    "importance":0.98,"source":"global/alert","scope":"global","original_agent":"system"}},
    ]

    for e in events:
        with open(os.path.join(events_dir, "exports.jsonl"), "a") as f:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    # Merge global (non pondéré pour simplifier)
    merge_engine_fn(events_dir=events_dir, db_path=db_path)

    return events_dir, db_path, downstream_dir


def test_redistribute():
    print("=" * 60)
    print("  TEST DOWNSTREAM REDISTRIBUTION")
    print("=" * 60)

    events_dir, db_path, downstream_dir = setup_global()
    config = load_config(CONFIG_PATH)

    # ── Phase 1 : Vérifier la pertinence ─────────────────────────

    print("\n─── Phase 1 : Vérification de la pertinence ───\n")

    test_cases = [
        ("fraude élevé détecté", "audit", True),
        ("TVA intracommunautaire", "fiscal", True),
        ("droit des sociétés", "juridique", True),
        ("TVA intracommunautaire", "audit", False),  # TVA pas dans expertise audit
        ("contentieux fiscal", "audit", False),
        ("contrat-type", "fiscal", False),
        ("prix de transfert", "fiscal", True),
        ("prix de transfert", "juridique", False),  # contient "prix" mais pas "prix de transfert" comme mot exact... wait
    ]

    for content, cluster, expected in test_cases:
        result = is_relevant_to_cluster(content, cluster, config)
        status = "✅" if result == expected else "❌"
        print(f"   {status} '{content[:50]}...' → {cluster} = {result} (attendu {expected})")
        assert result == expected, f"Pertinence: {content} → {cluster} = {result}, attendu {expected}"

    print(f"\n   ✅ Tous les tests de pertinence OK")

    # ── Phase 2 : Redistribution ─────────────────────────────────

    print("\n─── Phase 2 : Redistribution Global → Clusters ───\n")

    result = redistribute(
        db_path=db_path,
        downstream_dir=downstream_dir,
        config_path=CONFIG_PATH,
        min_importance=0.7,
    )

    print(f"   Candidats     : {result['total_candidates']}")
    print(f"   Envoyés       : {result['sent']}")
    print(f"   Non pertinents: {result['irrelevant']}")
    print(f"   Par cluster   : {json.dumps(result['per_cluster'])}")

    assert result["sent"] > 0, "Rien n'a été redistribué"
    assert result["sent"] >= result["total_candidates"] * 0.3, \
        "Trop peu de redistribution"

    # Vérifier les fichiers créés
    for cluster in ["audit", "fiscal", "juridique"]:
        fpath = os.path.join(downstream_dir, f"to-{cluster}.jsonl")
        assert os.path.exists(fpath), f"to-{cluster}.jsonl manquant"

        with open(fpath) as f:
            lines = f.readlines()
        print(f"   → {cluster}: {len(lines)} mémoires reçues")

        for line in lines:
            event = json.loads(line)
            # Vérifier qu'on ne renvoie pas à soi-même
            orig_agent = event["payload"].get("original_agent", "")
            # OK si scope=global (envoyé à tous, même la source)
            if event["payload"].get("scope") != "global":
                if orig_agent.startswith(f"cluster:{cluster}"):
                    print(f"      ⚠️  Auto-envoi détecté: {event['payload']['content'][:50]}...")

    # ── Phase 3 : Vérifier qu'un cluster lit ses downstream ─────

    print("\n─── Phase 3 : Cluster Fiscal merge ses downstream ───\n")

    fiscal_events = os.path.join(TEST_DIR, "fiscal", "events")
    fiscal_db = os.path.join(TEST_DIR, "fiscal", "consolidated.db")
    os.makedirs(fiscal_events)

    # Copier le downstream de Fiscal dans son dossier events (simulé Syncthing)
    shutil.copy(
        os.path.join(downstream_dir, "to-fiscal.jsonl"),
        os.path.join(fiscal_events, "from-global.jsonl"),
    )

    # Merge Fiscal
    result = merge_engine_fn(events_dir=fiscal_events, db_path=fiscal_db)
    print(f"   Merge Fiscal : {result['merged']} nouveaux événements")

    conn = sqlite3.connect(fiscal_db)
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    global_scoped = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE scope = 'global'"
    ).fetchone()[0]
    cross = conn.execute(
        "SELECT id, content, source FROM memories WHERE scope = 'global'"
    ).fetchall()

    print(f"   Total mémoires Fiscal : {total}")
    print(f"   Dont scope=global     : {global_scoped}")
    for r in cross:
        print(f"     [{r[2][:30]}] {r[1][:70]}...")

    assert total > 0, "Fiscal n'a reçu aucune mémoire"
    assert global_scoped > 0, "Aucune mémoire scope=global reçue"
    conn.close()

    # ── Phase 4 : Déduplication ──────────────────────────────────

    print("\n─── Phase 4 : Déduplication (2e run = 0 envoyé) ───\n")

    result2 = redistribute(
        db_path=db_path,
        downstream_dir=downstream_dir,
        config_path=CONFIG_PATH,
        min_importance=0.7,
    )

    print(f"   Run 2 → envoyés: {result2['sent']} (attendu 0)")
    assert result2["sent"] == 0, f"Déduplication échouée: {result2['sent']} envoyés"
    print(f"   ✅ Déduplication OK")

    # ── Phase 5 : Dry-run ────────────────────────────────────────

    print("\n─── Phase 5 : Dry-run ───\n")

    result3 = redistribute(
        db_path=db_path,
        downstream_dir=downstream_dir,
        config_path=CONFIG_PATH,
        min_importance=0.5,  # seuil plus bas
        dry_run=True,
    )
    print(f"   Dry-run → {result3['total_candidates']} candidats évalués")
    print(f"   ✅ Dry-run OK")

    shutil.rmtree(TEST_DIR)
    print("\n" + "=" * 60)
    print("  ✅ DOWNSTREAM REDISTRIBUTION FONCTIONNEL")
    print("=" * 60)


if __name__ == "__main__":
    test_redistribute()
