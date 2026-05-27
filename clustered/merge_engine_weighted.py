#!/usr/bin/env python3
"""
HiveMind Weighted Merge Engine — Phase 2
=========================================

Merge engine pour le niveau GLOBAL qui applique une pondération
par cluster avant consolidation.

PRINCIPE :
  1. Lit tous les exports de clusters (cluster-*.jsonl)
  2. Applique les poids configurés → modifie l'importance
  3. Délègue au merge_engine.py standard

CONFIG (cluster_weights.json) :
{
  "clusters": {
    "audit": {
      "weight": 1.0,
      "expertise": ["audit", "IFRS", "ISA", "fraude", "circularisation"]
    },
    "fiscal": {
      "weight": 1.5,
      "expertise": ["fiscalité", "TVA", "prix-transfert", "imposition"]
    },
    "juridique": {
      "weight": 2.0,
      "expertise": ["droit", "contrat", "contentieux", "recours"]
    }
  },
  "expertise_multiplier": 2.0,
  "monopoly_multiplier": 3.0
}

ALGORITHME DE PONDÉRATION :
  importance_finale = importance × weight_base × bonus

  bonus = 1.0
  Si contenu match expertise du cluster → bonus × expertise_multiplier
  Si AUCUN autre cluster n'a cette expertise → bonus × monopoly_multiplier

USAGE :
  python3 merge_engine_weighted.py \
    --events-dir ./global/events \
    --db ./global/consolidated.db \
    --config ./cluster_weights.json
"""

import json
import os
import sys
import copy
import argparse
from pathlib import Path
from typing import Optional

# Importer le merge engine standard
# Import from hivemind-core (Phase 1)
_core_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hivemind-core")
if os.path.exists(_core_dir):
    sys.path.insert(0, _core_dir)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from merge_engine import merge_events, parse_events


# ── Config ──────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "clusters": {},
    "expertise_multiplier": 2.0,
    "monopoly_multiplier": 3.0,
}


def load_config(config_path: str) -> dict:
    """Charge la configuration de pondération."""
    path = Path(config_path)
    if not path.exists():
        print(f"[WARN] Config introuvable: {config_path}, utilisation des défauts")
        return DEFAULT_CONFIG

    with open(path, "r", encoding="utf-8") as f:
        config = json.load(f)

    # Fusionner avec les défauts pour les champs manquants
    merged = copy.deepcopy(DEFAULT_CONFIG)
    merged.update(config)
    return merged


# ── Weighting logic ─────────────────────────────────────────────────

def compute_cluster_weight(
    cluster_name: str,
    content: str,
    config: dict,
) -> float:
    """
    Calcule le poids d'un événement en fonction de son cluster d'origine
    et de son contenu.

    Returns: multiplicateur d'importance (≥ 0)
    """
    clusters = config.get("clusters", {})
    cluster_cfg = clusters.get(cluster_name, {})

    if not cluster_cfg:
        return 1.0  # cluster inconnu → neutre

    weight = cluster_cfg.get("weight", 1.0)
    expertise = cluster_cfg.get("expertise", [])
    expertise_mult = config.get("expertise_multiplier", 2.0)
    monopoly_mult = config.get("monopoly_multiplier", 3.0)

    content_lower = content.lower()

    # Vérifier si le contenu matche une expertise de ce cluster
    matched_domains = []
    for domain in expertise:
        # Matcher le domaine ou ses variantes dans le contenu
        domain_lower = domain.lower()
        if domain_lower in content_lower:
            matched_domains.append(domain)

    bonus = 1.0

    if matched_domains:
        bonus *= expertise_mult

        # Vérifier le monopole : est-ce qu'un autre cluster a ce domaine ?
        for domain in matched_domains:
            is_monopoly = True
            for other_name, other_cfg in clusters.items():
                if other_name == cluster_name:
                    continue
                other_expertise = [e.lower() for e in other_cfg.get("expertise", [])]
                if domain.lower() in other_expertise:
                    is_monopoly = False
                    break

            if is_monopoly:
                bonus *= monopoly_mult
                break  # Un seul monopole suffit

    return weight * bonus


def apply_weights(events: list[dict], config: dict) -> list[dict]:
    """
    Applique la pondération à une liste d'événements.
    Modifie event["payload"]["importance"] en place.

    Returns: la liste modifiée (même référence).
    """
    stats = {"weighted": 0, "unchanged": 0, "per_cluster": {}}

    for event in events:
        agent = event.get("agent", "")
        cluster_name = "unknown"

        # Extraire le nom du cluster depuis agent (format: "cluster:audit")
        if agent.startswith("cluster:"):
            cluster_name = agent.split(":", 1)[1]
        else:
            # Événement sans tag cluster → pas de pondération
            stats["unchanged"] += 1
            continue

        payload = event.get("payload", {})
        content = payload.get("content", "")
        original_importance = payload.get("importance", 0.5)

        weight = compute_cluster_weight(cluster_name, content, config)

        if weight == 1.0:
            stats["unchanged"] += 1
        else:
            new_importance = min(original_importance * weight, 1.0)
            payload["importance"] = round(new_importance, 4)
            event["payload"]["_weight"] = weight  # Trace
            event["payload"]["_original_importance"] = original_importance
            stats["weighted"] += 1

        # Stats par cluster
        if cluster_name not in stats["per_cluster"]:
            stats["per_cluster"][cluster_name] = {"count": 0, "total_weight": 0.0}
        stats["per_cluster"][cluster_name]["count"] += 1
        stats["per_cluster"][cluster_name]["total_weight"] += weight

    return events


# ── Main ─────────────────────────────────────────────────────────────

def weighted_merge(
    events_dir: str,
    db_path: str,
    config_path: str,
    dry_run: bool = False,
) -> dict:
    """
    Merge pondéré complet :
    1. Charge la config
    2. Parse les événements
    3. Applique les poids
    4. Délègue au merge engine standard
    """
    config = load_config(config_path)

    if not config.get("clusters"):
        print("[WARN] Aucun cluster configuré, merge standard sans pondération")

    events = parse_events(events_dir)

    if not events:
        print("[WARN] Aucun événement à merger")
        return {"events_loaded": 0, "weighted": 0, "merged": 0}

    # Appliquer les poids
    weight_stats = {
        "before": len(events),
        "sample": [],
    }
    apply_weights(events, config)

    # Échantillon pour affichage
    for e in events[:5]:
        p = e.get("payload", {})
        if "_weight" in p:
            weight_stats["sample"].append({
                "agent": e.get("agent"),
                "content": p.get("content", "")[:60],
                "importance": p.get("importance"),
                "weight": p.get("_weight"),
                "original": p.get("_original_importance"),
            })

    print(f"\n⚖️  Pondération appliquée :")
    print(f"   Événements traités : {weight_stats['before']}")
    if weight_stats["sample"]:
        print(f"   Échantillon :")
        for s in weight_stats["sample"]:
            print(f"     {s['agent']:<18} imp={s['original']} → {s['importance']} (×{s['weight']:.1f})")
            print(f"       \"{s['content']}...\"")

    if dry_run:
        return {"events_loaded": len(events), "weighted": True, "dry_run": True}

    # Délègue au merge engine standard — directement en mémoire
    result = merge_events(events=events, db_path=db_path)


# ── CLI ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="HiveMind Weighted Merge Engine — merge avec pondération par cluster"
    )
    parser.add_argument(
        "--events-dir", required=True,
        help="Dossier contenant les exports des clusters (.jsonl)",
    )
    parser.add_argument(
        "--db", required=True,
        help="Chemin de la DB consolidée globale",
    )
    parser.add_argument(
        "--config", required=True,
        help="Fichier de configuration des poids (JSON)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Affiche la pondération sans merger",
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="Affiche les stats de la DB sans merger",
    )

    args = parser.parse_args()

    if args.stats:
        from merge_engine import _show_stats
        _show_stats(args.db)
        return

    result = weighted_merge(
        events_dir=args.events_dir,
        db_path=args.db,
        config_path=args.config,
        dry_run=args.dry_run,
    )

    if "error" in result:
        print(f"\n❌ {result['error']}", file=sys.stderr)
        sys.exit(1)

    if not args.dry_run:
        print(f"\n✅ Merge pondéré terminé")
        for k, v in result.items():
            if k not in ("output", "ok"):
                print(f"   {k}: {v}")


if __name__ == "__main__":
    main()
