#!/usr/bin/env python3
"""
Test de l'Export Engine + Global Merge
=======================================

Scénario complet Phase 2 :
  1. Créer 2 clusters simulés (audit, fiscal)
  2. Chaque cluster a ses propres mémoires (consolidated.db)
  3. Export engine : cluster → export/<cluster>.jsonl
  4. Global merge : lit les 2 exports → consolidated.db global
  5. Vérifier que le global voit tout
  6. Incrémental : nouvelle mémoire → export → global mis à jour
"""

import os
import sys
import json
import glob
import sqlite3
import subprocess

PROTOTYPE_DIR = os.path.dirname(os.path.abspath(__file__))
# Import from hivemind-core (Phase 1)
_core_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hivemind-core")
if os.path.exists(_core_dir):
    sys.path.insert(0, _core_dir)
sys.path.insert(0, PROTOTYPE_DIR)

from merge_engine import merge as merge_engine_fn
from hivemind_mnemosyne import HiveMindMemory

# Chemins
TEST_DIR = os.path.join(PROTOTYPE_DIR, "test_clusters")
AUDIT_EVENTS = os.path.join(TEST_DIR, "audit", "memory", "events")
AUDIT_DB = os.path.join(TEST_DIR, "audit", "memory", "consolidated.db")
FISCAL_EVENTS = os.path.join(TEST_DIR, "fiscal", "memory", "events")
FISCAL_DB = os.path.join(TEST_DIR, "fiscal", "memory", "consolidated.db")
EXPORT_DIR = os.path.join(TEST_DIR, "exports")
GLOBAL_EVENTS = os.path.join(TEST_DIR, "global", "memory", "events")
GLOBAL_DB = os.path.join(TEST_DIR, "global", "memory", "consolidated.db")


def cleanup():
    for d in [TEST_DIR]:
        if os.path.exists(d):
            for root, dirs, files in os.walk(d, topdown=False):
                for f in files:
                    os.remove(os.path.join(root, f))
                for d2 in dirs:
                    os.rmdir(os.path.join(root, d2))
            os.rmdir(d)
    print("🧹 Nettoyage\n")


def test_export_engine():
    cleanup()

    print("=" * 60)
    print("  TEST EXPORT ENGINE + GLOBAL MERGE")
    print("=" * 60)

    # ── Phase 1 : Créer les clusters ─────────────────────────────

    print("\n─── Phase 1 : Création des clusters Audit et Fiscal ───\n")

    # Cluster Audit
    audit = HiveMindMemory(events_dir=AUDIT_EVENTS, consolidated_db=AUDIT_DB, agent="alice")
    audit.remember("Client Omega : risque fraude élevé — circularisation obligatoire",
                   importance=0.95, scope="shared", source="audit")
    audit.remember("Seuil matérialité : 5% du résultat net",
                   importance=0.9, scope="shared", source="audit")
    audit.remember("Note interne : Alice suspecte un faux bilan chez Omega",
                   importance=0.8, scope="private", source="audit")  # ← NE DOIT PAS être exporté
    audit.merge()

    # Cluster Fiscal
    fiscal = HiveMindMemory(events_dir=FISCAL_EVENTS, consolidated_db=FISCAL_DB, agent="david")
    fiscal.remember("Client Gamma : prix de transfert à documenter avant le 30/06",
                    importance=0.9, scope="shared", source="fiscal")
    fiscal.remember("TVA intracommunautaire : nouveau seuil à 10 000€ depuis janvier",
                    importance=0.85, scope="shared", source="fiscal")
    fiscal.remember("Note interne : David pense que Gamma minimise ses bénéfices",
                    importance=0.75, scope="private", source="fiscal")  # ← NE DOIT PAS
    fiscal.merge()

    print(f"   Audit  : {audit.stats()['total']} mémoires (dont 1 private)")
    print(f"   Fiscal : {fiscal.stats()['total']} mémoires (dont 1 private)")

    # ── Phase 2 : Export engine ──────────────────────────────────

    print("\n─── Phase 2 : Export des clusters (scope=shared uniquement) ───\n")

    # Export Audit (full)
    result = subprocess.run(
        [sys.executable, "export_engine.py",
         "--db", AUDIT_DB,
         "--export-dir", EXPORT_DIR,
         "--cluster", "audit",
         "--mode", "full",
         "--scope", "shared"],
        cwd=PROTOTYPE_DIR, capture_output=True, text=True,
    )
    print(result.stdout)
    assert result.returncode == 0, f"Export audit failed: {result.stderr}"

    # Export Fiscal (full)
    result = subprocess.run(
        [sys.executable, "export_engine.py",
         "--db", FISCAL_DB,
         "--export-dir", EXPORT_DIR,
         "--cluster", "fiscal",
         "--mode", "full",
         "--scope", "shared"],
        cwd=PROTOTYPE_DIR, capture_output=True, text=True,
    )
    print(result.stdout)
    assert result.returncode == 0, f"Export fiscal failed: {result.stderr}"

    # Vérifier les fichiers exportés
    audit_export = os.path.join(EXPORT_DIR, "audit.jsonl")
    fiscal_export = os.path.join(EXPORT_DIR, "fiscal.jsonl")
    assert os.path.exists(audit_export), "audit.jsonl manquant"
    assert os.path.exists(fiscal_export), "fiscal.jsonl manquant"

    # Vérifier que le scope private n'est PAS exporté
    with open(audit_export) as f:
        audit_lines = f.readlines()
    assert len(audit_lines) == 2, f"Audit: attendu 2 shared, obtenu {len(audit_lines)}"
    for line in audit_lines:
        assert "private" not in json.loads(line)["payload"].get("scope", ""), \
            "Mémoire private exportée !"
    print(f"   ✅ Audit : {len(audit_lines)} mémoires shared exportées, 0 private")

    with open(fiscal_export) as f:
        fiscal_lines = f.readlines()
    assert len(fiscal_lines) == 2, f"Fiscal: attendu 2 shared, obtenu {len(fiscal_lines)}"
    print(f"   ✅ Fiscal : {len(fiscal_lines)} mémoires shared exportées, 0 private")

    # ── Phase 3 : Global merge ───────────────────────────────────

    print("\n─── Phase 3 : Merge global (lit les 2 exports) ───\n")

    os.makedirs(GLOBAL_EVENTS, exist_ok=True)

    # Copier les exports dans le dossier events du global
    import shutil
    shutil.copy(audit_export, os.path.join(GLOBAL_EVENTS, "audit.jsonl"))
    shutil.copy(fiscal_export, os.path.join(GLOBAL_EVENTS, "fiscal.jsonl"))

    # Merge global
    stats = merge_engine_fn(events_dir=GLOBAL_EVENTS, db_path=GLOBAL_DB)
    print(f"   Global : {stats['merged']} mémoires consolidées")

    conn = sqlite3.connect(GLOBAL_DB)
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    print(f"   Total global : {total} (attendu 4)")
    assert total == 4, f"Attendu 4, obtenu {total}"

    # Vérifier que les agents sont taggés cluster:*
    agents = conn.execute(
        "SELECT DISTINCT agent FROM memories"
    ).fetchall()
    agents = [a[0] for a in agents]
    print(f"   Agents : {agents}")
    assert "cluster:audit" in agents, "cluster:audit manquant"
    assert "cluster:fiscal" in agents, "cluster:fiscal manquant"

    # Vérifier que les scopes sont préservés
    scopes = conn.execute(
        "SELECT DISTINCT scope FROM memories"
    ).fetchall()
    print(f"   Scopes : {[s[0] for s in scopes]}")
    conn.close()

    # ── Phase 4 : Incrémental ────────────────────────────────────

    print("\n─── Phase 4 : Incrémental — nouvelle mémoire audit ───\n")

    # Ajouter une nouvelle mémoire dans le cluster audit
    audit.remember("Client Omega : confirmer les provisions pour litiges avant le 15/07",
                   importance=0.88, scope="shared", source="audit")
    audit.merge()

    # Relancer l'export en incremental
    result = subprocess.run(
        [sys.executable, "export_engine.py",
         "--db", AUDIT_DB,
         "--export-dir", EXPORT_DIR,
         "--cluster", "audit",
         "--mode", "incremental",
         "--scope", "shared"],
        cwd=PROTOTYPE_DIR, capture_output=True, text=True,
    )
    print(result.stdout)
    assert "Exportés   : 1" in result.stdout, \
        f"Incremental: attendu 1 exporté, obtenu:\n{result.stdout}"

    # Copier l'export mis à jour vers le global
    shutil.copy(audit_export, os.path.join(GLOBAL_EVENTS, "audit.jsonl"))

    # Re-merge global
    stats = merge_engine_fn(events_dir=GLOBAL_EVENTS, db_path=GLOBAL_DB)
    conn = sqlite3.connect(GLOBAL_DB)
    total2 = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    conn.close()
    print(f"   Global après incrémental : {total2} (attendu 5)")
    assert total2 == 5, f"Attendu 5, obtenu {total2}"
    print(f"   ✅ Incrémental propagé au global")

    # ── Phase 5 : Recherche dans le global ──────────────────────

    print("\n─── Phase 5 : Recall dans le global ───\n")

    global_hm = HiveMindMemory(
        events_dir=GLOBAL_EVENTS, consolidated_db=GLOBAL_DB, agent="global",
    )
    results = global_hm.recall("Client", limit=10)
    print(f"   Recherche 'Client' → {len(results)} résultats :")
    for r in results:
        print(f"     [{r['agent']}] {r['content'][:70]}...")
    assert len(results) >= 2, f"Attendu ≥ 2 résultats pour 'Client'"

    # Les scopes private ne doivent PAS apparaître
    private_results = global_hm.recall("suspecte", limit=5)
    print(f"\n   Recherche 'suspecte' (private) → {len(private_results)} résultats")
    assert len(private_results) == 0, "Une mémoire private a fuité dans le global !"
    print(f"   ✅ Aucune mémoire private dans le global")

    print("\n" + "=" * 60)
    print("  ✅ EXPORT ENGINE + GLOBAL MERGE FONCTIONNELS")
    print("=" * 60)


if __name__ == "__main__":
    test_export_engine()
