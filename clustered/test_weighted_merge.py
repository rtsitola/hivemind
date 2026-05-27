#!/usr/bin/env python3
"""
Test du Merge Engine Pondéré
==============================

Scénario :
  1. Créer 3 clusters avec exports simulant leurs mémoires shared
  2. Appliquer le merge pondéré avec cluster_weights.json
  3. Vérifier que les poids sont correctement appliqués
  4. Vérifier le bonus expertise et le bonus monopole
"""

import os
import sys
import json
import sqlite3
import shutil
import tempfile
from pathlib import Path

PROTOTYPE_DIR = os.path.dirname(os.path.abspath(__file__))
# Import from hivemind-core (Phase 1)
_core_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hivemind-core")
if os.path.exists(_core_dir):
    sys.path.insert(0, _core_dir)
sys.path.insert(0, PROTOTYPE_DIR)

from merge_engine_weighted import weighted_merge, apply_weights, load_config, compute_cluster_weight
from merge_engine import parse_events

CONFIG_PATH = os.path.join(PROTOTYPE_DIR, "cluster_weights.json")
TEST_DIR = None


def setup():
    """Crée un environnement de test temporaire avec 3 exports de clusters."""
    global TEST_DIR
    TEST_DIR = tempfile.mkdtemp(prefix="hivemind_weighted_test_")
    events_dir = os.path.join(TEST_DIR, "events")
    os.makedirs(events_dir)

    # ── Export cluster AUDIT ──────────────────────────────────────
    audit_events = [
        {
            "op": "remember",
            "id": "export-audit-mem-001",
            "agent": "cluster:audit",
            "ts": "2026-05-26T10:00:00Z",
            "payload": {
                "content": "Client Omega : circularisation obligatoire — risque fraude élevé",
                "importance": 0.9,
                "source": "audit/fieldwork",
                "scope": "shared",
                "original_agent": "alice",
            },
        },
        {
            "op": "remember",
            "id": "export-audit-mem-002",
            "agent": "cluster:audit",
            "ts": "2026-05-26T10:01:00Z",
            "payload": {
                "content": "Seuil de matérialité : 5% du résultat net selon normes IFRS",
                "importance": 0.85,
                "source": "audit/methodology",
                "scope": "shared",
                "original_agent": "bob",
            },
        },
        {
            "op": "remember",
            "id": "export-audit-mem-003",
            "agent": "cluster:audit",
            "ts": "2026-05-26T10:02:00Z",
            "payload": {
                "content": "Vérification conformité : checklist mise à jour pour 2026",
                "importance": 0.7,
                "source": "audit/compliance",
                "scope": "shared",
                "original_agent": "alice",
            },
        },
    ]
    with open(os.path.join(events_dir, "audit.jsonl"), "w") as f:
        for e in audit_events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    # ── Export cluster FISCAL ─────────────────────────────────────
    fiscal_events = [
        {
            "op": "remember",
            "id": "export-fiscal-mem-001",
            "agent": "cluster:fiscal",
            "ts": "2026-05-26T10:00:00Z",
            "payload": {
                "content": "Client Gamma : structure de prix de transfert complexe à documenter",
                "importance": 0.9,
                "source": "fiscal/analysis",
                "scope": "shared",
                "original_agent": "david",
            },
        },
        {
            "op": "remember",
            "id": "export-fiscal-mem-002",
            "agent": "cluster:fiscal",
            "ts": "2026-05-26T10:01:00Z",
            "payload": {
                "content": "TVA intracommunautaire : nouveau seuil à 10 000€",
                "importance": 0.85,
                "source": "fiscal/regulation",
                "scope": "shared",
                "original_agent": "eve",
            },
        },
        {
            "op": "remember",
            "id": "export-fiscal-mem-003",
            "agent": "cluster:fiscal",
            "ts": "2026-05-26T10:02:00Z",
            "payload": {
                "content": "Note générale sur le crédit d'impôt recherche",
                "importance": 0.6,
                "source": "fiscal/general",
                "scope": "shared",
                "original_agent": "david",
            },
        },
    ]
    with open(os.path.join(events_dir, "fiscal.jsonl"), "w") as f:
        for e in fiscal_events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    # ── Export cluster JURIDIQUE ──────────────────────────────────
    juridique_events = [
        {
            "op": "remember",
            "id": "export-juridique-mem-001",
            "agent": "cluster:juridique",
            "ts": "2026-05-26T10:00:00Z",
            "payload": {
                "content": "Avis droit des sociétés : fusion absorption et conséquences fiscales",
                "importance": 0.9,
                "source": "juridique/consultation",
                "scope": "shared",
                "original_agent": "marie",
            },
        },
        {
            "op": "remember",
            "id": "export-juridique-mem-002",
            "agent": "cluster:juridique",
            "ts": "2026-05-26T10:01:00Z",
            "payload": {
                "content": "Vérification conformité RGPD : nouveau formulaire de consentement",
                "importance": 0.8,
                "source": "juridique/compliance",
                "scope": "shared",
                "original_agent": "paul",
            },
        },
        {
            "op": "remember",
            "id": "export-juridique-mem-003",
            "agent": "cluster:juridique",
            "ts": "2026-05-26T10:02:00Z",
            "payload": {
                "content": "Recours contentieux : stratégie pour le dossier Epsilon",
                "importance": 0.95,
                "source": "juridique/litigation",
                "scope": "shared",
                "original_agent": "marie",
            },
        },
    ]
    with open(os.path.join(events_dir, "juridique.jsonl"), "w") as f:
        for e in juridique_events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    return events_dir


def cleanup():
    if TEST_DIR and os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR)


def test_weighted_merge():
    print("=" * 60)
    print("  TEST MERGE ENGINE PONDÉRÉ")
    print("=" * 60)

    events_dir = setup()
    db_path = os.path.join(TEST_DIR, "consolidated.db")

    print(f"\n   Events dir : {events_dir}")
    print(f"   Config     : {CONFIG_PATH}")

    # ── Phase 1 : Vérifier la pondération unitaire ───────────────

    print("\n─── Phase 1 : Calcul des poids unitaires ───\n")

    config = load_config(CONFIG_PATH)

    test_cases = [
        # (cluster, content, expected_weight_explanation)
        ("audit", "circularisation obligatoire — risque fraude élevé",
         "audit×1.0 + expertise(fraude,circularisation) ×2 + monopole ×3 = ×6.0"),
        ("audit", "vérification conformité checklist 2026",
         "audit×1.0 + pas d'expertise → ×1.0"),
        ("fiscal", "TVA intracommunautaire nouveau seuil",
         "fiscal×1.5 + expertise(TVA) ×2 + monopole ×3 = ×9.0"),
        ("juridique", "recours contentieux stratégie dossier",
         "juridique×2.0 + expertise(contentieux,recours) ×2 + monopole ×3 = ×12.0"),
        ("juridique", "vérification conformité RGPD formulaire",
         "juridique×2.0 + pas d'expertise match → ×2.0 (conformité n'est PAS dans l'expertise juridique)"),
        ("fiscal", "note générale crédit impôt recherche",
         "fiscal×1.5 + pas d'expertise → ×1.5"),
    ]

    for cluster, content, expected in test_cases:
        weight = compute_cluster_weight(cluster, content, config)
        print(f"   {cluster:<12} weight={weight:.1f}  ← {expected}")

    # Vérifications
    assert compute_cluster_weight("audit", "risque fraude élevé", config) == 6.0
    assert compute_cluster_weight("fiscal", "TVA intracommunautaire", config) == 9.0
    assert compute_cluster_weight("juridique", "recours contentieux", config) == 12.0
    # Audit "conformité" — pas d'expertise match → weight 1.0
    assert compute_cluster_weight("audit", "vérification conformité", config) == 1.0
    print(f"\n   ✅ Tous les poids unitaires sont corrects")

    # ── Phase 2 : Appliquer les poids aux événements ─────────────

    print("\n─── Phase 2 : Application des poids aux événements ───\n")

    events = parse_events(events_dir)
    print(f"   Événements chargés : {len(events)}")

    apply_weights(events, config)

    # Vérifier les importances modifiées
    for e in events:
        p = e.get("payload", {})
        orig = p.get("_original_importance", p.get("importance"))
        new_imp = p.get("importance")
        weight = p.get("_weight", 1.0)
        agent = e.get("agent", "")
        content = p.get("content", "")[:60]

        if weight != 1.0:
            print(f"   {agent:<18} {orig} → {new_imp} (×{weight:.1f}) | {content}...")

    # Vérifications spécifiques
    # Mémoire "fraude" de l'audit : importance 0.9 × 6.0 = 5.4 → capped at 1.0
    fraude_event = [e for e in events if "fraude" in e["payload"]["content"].lower()][0]
    assert fraude_event["payload"]["importance"] == 1.0, \
        f"Fraude capped: attendu 1.0, obtenu {fraude_event['payload']['importance']}"
    print(f"\n   ✅ Importance capped à 1.0 (fraude: 0.9×6.0=5.4→1.0)")

    # Mémoire "recours contentieux" du juridique : 0.95 × 12 = 11.4 → 1.0
    recours_event = [e for e in events if "contentieux" in e["payload"]["content"].lower()][0]
    assert recours_event["payload"]["importance"] == 1.0, \
        f"Recours capped: attendu 1.0, obtenu {recours_event['payload']['importance']}"
    print(f"   ✅ Importance capped à 1.0 (recours: 0.95×12=11.4→1.0)")

    # Mémoire "conformité" audit : pas d'expertise → inchangé
    conf_event = [e for e in events
                  if "vérification conformité" in e["payload"]["content"].lower()
                  and e["agent"] == "cluster:audit"][0]
    assert conf_event["payload"]["importance"] == 0.7, \
        f"Conformité audit: attendu 0.7, obtenu {conf_event['payload']['importance']}"
    print(f"   ✅ Mémoire sans expertise conservée (conformité audit: 0.7)")

    # Mémoire "note générale" fiscal : pas d'expertise → 0.6 × 1.5 = 0.9
    note_event = [e for e in events if "crédit d'impôt" in e["payload"]["content"].lower()][0]
    assert note_event["payload"]["importance"] == 0.9, \
        f"Note fiscale: attendu 0.9, obtenu {note_event['payload']['importance']}"
    print(f"   ✅ Poids de base appliqué sans expertise (note fiscale: 0.6×1.5=0.9)")

    # ── Phase 3 : Merge pondéré complet ──────────────────────────

    print("\n─── Phase 3 : Merge pondéré → consolidated.db ───\n")

    result = weighted_merge(
        events_dir=events_dir,
        db_path=db_path,
        config_path=CONFIG_PATH,
    )

    conn = sqlite3.connect(db_path)
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    print(f"   Total mémoires consolidées : {total} (attendu 9)")
    assert total == 9, f"Attendu 9, obtenu {total}"

    # Vérifier que les importances pondérées sont dans la DB
    rows = conn.execute(
        "SELECT agent, content, importance FROM memories ORDER BY importance DESC"
    ).fetchall()
    print(f"\n   Top mémoires par importance :")
    for agent, content, imp in rows:
        print(f"     [{imp:.4f}] {agent:<18} {content[:60]}...")

    # La plus haute importance doit être 1.0 (capped)
    top = conn.execute(
        "SELECT MAX(importance) FROM memories"
    ).fetchone()[0]
    assert top == 1.0, f"Top importance: attendu 1.0, obtenu {top}"
    print(f"\n   ✅ Importance max = 1.0 (cap fonctionnel)")

    conn.close()

    # ── Phase 4 : Dry-run ────────────────────────────────────────

    print("\n─── Phase 4 : Dry-run (pas d'écriture) ───\n")

    result = weighted_merge(
        events_dir=events_dir,
        db_path=db_path,
        config_path=CONFIG_PATH,
        dry_run=True,
    )
    assert result.get("dry_run") is True
    print(f"   ✅ Dry-run OK")

    cleanup()
    print("\n" + "=" * 60)
    print("  ✅ MERGE ENGINE PONDÉRÉ FONCTIONNEL")
    print("=" * 60)


if __name__ == "__main__":
    try:
        test_weighted_merge()
    finally:
        cleanup()
