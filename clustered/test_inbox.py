#!/usr/bin/env python3
"""
Test Inbox Inter-Cluster
=========================

Scénario :
  1. Créer 2 clusters simulés (audit, fiscal) avec leurs events/
  2. Audit envoie un message à Fiscal via inbox_writer
  3. Le message apparaît dans events/ de Fiscal (simulé Syncthing)
  4. Watcher + merge de Fiscal traite le message
  5. Vérifier que Fiscal voit le message avec scope cross-cluster
  6. Fiscal répond → Audit reçoit
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

from inbox_writer import send_message, read_inbox, read_inbox_summary
from merge_engine import merge as merge_engine_fn, parse_events
from hivemind_mnemosyne import HiveMindMemory

TEST_DIR = None


def setup():
    """Crée un environnement de test simulé pour 2 clusters."""
    global TEST_DIR
    TEST_DIR = tempfile.mkdtemp(prefix="hivemind_inbox_test_")

    # Dossier cross-cluster partagé (simule Syncthing)
    cross_dir = os.path.join(TEST_DIR, "cross-cluster")

    # Cluster Audit
    audit_events = os.path.join(TEST_DIR, "audit", "memory", "events")
    audit_db = os.path.join(TEST_DIR, "audit", "memory", "consolidated.db")
    os.makedirs(audit_events)

    # Cluster Fiscal
    fiscal_events = os.path.join(TEST_DIR, "fiscal", "memory", "events")
    fiscal_db = os.path.join(TEST_DIR, "fiscal", "memory", "consolidated.db")
    os.makedirs(fiscal_events)

    # Cluster Juridique
    juridique_events = os.path.join(TEST_DIR, "juridique", "memory", "events")
    juridique_db = os.path.join(TEST_DIR, "juridique", "memory", "consolidated.db")
    os.makedirs(juridique_events)

    return {
        "cross_dir": cross_dir,
        "audit": {"events": audit_events, "db": audit_db},
        "fiscal": {"events": fiscal_events, "db": fiscal_db},
        "juridique": {"events": juridique_events, "db": juridique_db},
    }


def cleanup():
    if TEST_DIR and os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR)


def test_inbox():
    print("=" * 60)
    print("  TEST INBOX INTER-CLUSTER")
    print("=" * 60)

    env = setup()
    cross_dir = env["cross_dir"]

    # ── Phase 1 : Audit envoie un message à Fiscal ───────────────

    print("\n─── Phase 1 : Audit → Fiscal ───\n")

    msg1_id = send_message(
        cross_dir=cross_dir,
        from_cluster="audit",
        from_agent="alice",
        to_cluster="fiscal",
        content="⚠️ Client Omega : montage de prix de transfert douteux. Pouvez-vous confirmer la conformité fiscale ?",
        priority="high",
    )
    print(f"   Message envoyé : {msg1_id}")
    print(f"   De: cluster:audit (alice)")
    print(f"   À:  cluster:fiscal")

    # Vérifier le fichier créé
    target_file = os.path.join(cross_dir, "fiscal", "from-audit.jsonl")
    assert os.path.exists(target_file), "Fichier inbox non créé"
    with open(target_file) as f:
        lines = f.readlines()
    assert len(lines) == 1, f"Attendu 1 message, {len(lines)} lignes"
    msg = json.loads(lines[0])
    assert msg["op"] == "message"
    assert msg["payload"]["scope"] == "cross-cluster"
    assert msg["payload"]["priority"] == "high"
    print(f"   ✅ Fichier créé : fiscal/from-audit.jsonl (1 message, priority=high)")

    # ── Phase 2 : Fiscal lit son inbox ───────────────────────────

    print("\n─── Phase 2 : Fiscal lit son inbox ───\n")

    messages = read_inbox(cross_dir, "fiscal")
    print(f"   Messages reçus : {len(messages)}")
    assert len(messages) == 1
    m = messages[0]
    print(f"   De    : {m['payload']['from_cluster']}/{m['payload']['from_agent']}")
    print(f"   Contenu : {m['payload']['content'][:80]}...")

    summary = read_inbox_summary(cross_dir, "fiscal")
    print(f"   Résumé : {json.dumps(summary, indent=2)}")
    assert summary["total"] == 1
    assert summary["by_from"]["audit"] == 1
    assert summary["by_priority"]["high"] == 1
    assert summary["unread"] == 1
    print(f"   ✅ Résumé OK (1 message, high priority, unread)")

    # ── Phase 3 : Merge de Fiscal — traite le message ────────────

    print("\n─── Phase 3 : Merge Fiscal → message dans consolidated.db ───\n")

    # Simuler Syncthing : copier le message dans events/ de Fiscal
    shutil.copy(target_file, os.path.join(env["fiscal"]["events"], "from-audit.jsonl"))

    # Merge local de Fiscal
    result = merge_engine_fn(
        events_dir=env["fiscal"]["events"],
        db_path=env["fiscal"]["db"],
    )
    print(f"   Merge Fiscal : {result['merged']} nouveau, {result['skipped']} ignoré")
    assert result["merged"] >= 1, f"Attendu ≥1 nouveau message, obtenu {result}"

    # Vérifier dans consolidated.db
    conn = sqlite3.connect(env["fiscal"]["db"])
    rows = conn.execute(
        "SELECT id, content, scope, source, agent FROM memories WHERE scope = 'cross-cluster'"
    ).fetchall()
    print(f"   Mémoires cross-cluster : {len(rows)}")
    for r in rows:
        print(f"     [{r[2]}] {r[4]} → {r[1][:80]}...")
    assert len(rows) == 1, f"Attendu 1 mémoire cross-cluster, obtenu {len(rows)}"
    assert "cross-cluster" in rows[0][2]
    assert "cluster:audit" in rows[0][4]
    print(f"   ✅ Message visible dans consolidated.db de Fiscal")
    conn.close()

    # ── Phase 4 : Fiscal répond à Audit ──────────────────────────

    print("\n─── Phase 4 : Fiscal → Audit (réponse) ───\n")

    msg2_id = send_message(
        cross_dir=cross_dir,
        from_cluster="fiscal",
        from_agent="david",
        to_cluster="audit",
        content="✅ Confirmé : le montage d'Omega est non conforme. Prix de transfert sous-évalué de ~40%. Je prépare une note détaillée. Priorité : bloquer la clôture.",
        priority="urgent",
        reply_to=msg1_id,
    )
    print(f"   Réponse envoyée : {msg2_id}")
    print(f"   En réponse à     : {msg1_id}")

    # Simuler Syncthing : copier dans events/ de Audit
    reply_file = os.path.join(cross_dir, "audit", "from-fiscal.jsonl")
    assert os.path.exists(reply_file)
    shutil.copy(reply_file, os.path.join(env["audit"]["events"], "from-fiscal.jsonl"))

    # Merge local de Audit
    result = merge_engine_fn(
        events_dir=env["audit"]["events"],
        db_path=env["audit"]["db"],
    )
    print(f"   Merge Audit : {result['merged']} nouveau")

    conn = sqlite3.connect(env["audit"]["db"])
    rows = conn.execute(
        "SELECT content, source, agent FROM memories WHERE scope = 'cross-cluster'"
    ).fetchall()
    assert len(rows) == 1, f"Audit: attendu 1 cross-cluster, obtenu {len(rows)}"
    print(f"   Audit voit : {rows[0][0][:80]}...")
    print(f"   ✅ Audit a reçu la réponse de Fiscal")
    conn.close()

    # ── Phase 5 : Triple cluster ─────────────────────────────────

    print("\n─── Phase 5 : Audit → Juridique (CC Fiscal) ───\n")

    # Audit envoie à Juridique, avec Fiscal en copie
    send_message(
        cross_dir=cross_dir,
        from_cluster="audit",
        from_agent="alice",
        to_cluster="juridique",
        content="📋 Dossier Omega : implications juridiques de la non-conformité fiscale. Fiscal confirme sous-évaluation prix de transfert. Quelles conséquences contractuelles ?",
        priority="high",
    )

    # Copier dans les events des deux destinataires
    juridique_inbox = os.path.join(cross_dir, "juridique", "from-audit.jsonl")
    shutil.copy(juridique_inbox, os.path.join(env["juridique"]["events"], "from-audit.jsonl"))

    # Merge Juridique
    result = merge_engine_fn(
        events_dir=env["juridique"]["events"],
        db_path=env["juridique"]["db"],
    )
    conn = sqlite3.connect(env["juridique"]["db"])
    rows = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE scope = 'cross-cluster'"
    ).fetchone()[0]
    print(f"   Juridique : {rows} message(s) cross-cluster reçus")
    assert rows == 1
    conn.close()

    # ── Phase 6 : Récapitulatif cross-cluster ────────────────────

    print("\n─── Phase 6 : Récapitulatif ───\n")

    for cluster in ["audit", "fiscal", "juridique"]:
        summary = read_inbox_summary(cross_dir, cluster)
        print(f"   {cluster:<12} inbox: {summary['total']} msg, "
              f"de: {list(summary['by_from'].keys())}, "
              f"priorités: {list(summary['by_priority'].keys())}")

    cleanup()
    print("\n" + "=" * 60)
    print("  ✅ INBOX INTER-CLUSTER FONCTIONNEL")
    print("=" * 60)


if __name__ == "__main__":
    try:
        test_inbox()
    finally:
        cleanup()
