#!/usr/bin/env python3
"""
HiveMind Redistribute — Phase 2
================================

Redistribution downstream : le Global pousse des informations
vers les clusters concernés.

ARCHITECTURE :
  ┌─────────────────┐
  │  GLOBAL          │
  │  consolidated.db │
  │                  │
  │  redistribute.py │──► downstream/
  └─────────────────┘      ├── to-audit.jsonl
                            ├── to-fiscal.jsonl     ── Syncthing ──►
                            └── to-juridique.jsonl      chaque cluster
                                                        lit son fichier
                                                        → merge dans
                                                        consolidated.db locale

RÈGLES DE REDISTRIBUTION :
  1. Seuil d'importance : mémoires avec imp > threshold → tous les clusters
  2. Pertinence ciblée : si le contenu match l'expertise d'un cluster
     → poussé vers CE cluster spécifique
  3. Scope global : mémoires explicitement marquées scope=global
  4. Déduplication : ne pas renvoyer une mémoire déjà redistribuée

USAGE :
  python3 redistribute.py \
    --db ./global/consolidated.db \
    --downstream-dir ./downstream \
    --config ./cluster_weights.json

NOTE : Un watcher dans chaque cluster doit surveiller son fichier
downstream/to-<cluster>.jsonl pour le merger automatiquement.
"""

import json
import os
import sys
import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from merge_engine_weighted import load_config


# ── Helpers ────────────────────────────────────────────────────────

# Imported from hivemind_common
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # already exists but just in case
from hivemind_common import now_iso, memory_hash

def _now_iso():
    return now_iso()

def _content_hash(content):
    return memory_hash(content)


# ── State ──────────────────────────────────────────────────────────

class RedistributeState:
    """Suit quelles mémoires ont déjà été redistribuées."""

    def __init__(self, state_path: str):
        self.state_path = Path(state_path)
        self.data = self._load()

    def _load(self) -> dict:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {"redistributed": {}, "last_run": None}

    def save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.data, indent=2))

    def was_sent(self, memory_id: str, target_cluster: str) -> bool:
        key = f"{memory_id}→{target_cluster}"
        return key in self.data["redistributed"]

    def mark_sent(self, memory_id: str, target_cluster: str):
        key = f"{memory_id}→{target_cluster}"
        self.data["redistributed"][key] = _now_iso()


# ── Relevance ──────────────────────────────────────────────────────

def _build_expertise_index(config: dict) -> dict:
    """Construit un index inversé: domaine → [clusters qui l'ont]."""
    index = {}
    for cluster_name, cfg in config.get("clusters", {}).items():
        for domain in cfg.get("expertise", []):
            d = domain.lower()
            if d not in index:
                index[d] = []
            index[d].append(cluster_name)
    return index


def is_relevant_to_cluster(
    content: str,
    cluster_name: str,
    config: dict,
    _expertise_index: dict = None,
) -> bool:
    """
    Vérifie si un contenu est pertinent pour un cluster donné,
    basé sur ses domaines d'expertise.

    Optimisation : utilise un index inversé pré-calculé.
    """
    if _expertise_index is None:
        _expertise_index = _build_expertise_index(config)

    content_lower = content.lower()

    for domain, clusters in _expertise_index.items():
        if domain in content_lower and cluster_name in clusters:
            return True

    return False


def find_relevant_clusters(
    content: str,
    config: dict,
    _expertise_index: dict = None,
) -> set:
    """Trouve tous les clusters pertinents pour un contenu."""
    if _expertise_index is None:
        _expertise_index = _build_expertise_index(config)

    content_lower = content.lower()
    relevant = set()

    for domain, clusters in _expertise_index.items():
        if domain in content_lower:
            relevant.update(clusters)

    return relevant


# ── Redistribute ────────────────────────────────────────────────────

def redistribute(
    db_path: str,
    downstream_dir: str,
    config_path: str,
    min_importance: float = 0.7,
    dry_run: bool = False,
) -> dict:
    """
    Lit la DB globale et redistribue vers les clusters.

    Règles :
      - importance > min_importance → envoyé à tous les clusters concernés
      - Pertinence par expertise → envoyé au cluster ciblé
      - Scope=global → envoyé à TOUS

    Args:
        db_path: consolidated.db du Global
        downstream_dir: dossier downstream (contiendra to-*.jsonl)
        config_path: cluster_weights.json (pour les expertises)
        min_importance: seuil minimum
        dry_run: mode simulation

    Returns: stats
    """
    db_path = Path(db_path)
    downstream_dir = Path(downstream_dir)
    downstream_dir.mkdir(parents=True, exist_ok=True)

    if not db_path.exists():
        return {"error": "db_not_found"}

    config = load_config(config_path)
    clusters = config.get("clusters", {})

    if not clusters:
        return {"error": "no_clusters_configured", "sent": 0}

    # Pré-calculer l'index inversé une seule fois
    expertise_index = _build_expertise_index(config)

    state_path = downstream_dir / ".redistribute_state.json"
    state = RedistributeState(str(state_path))

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT id, content, importance, source, scope, agent, created_at, updated_at "
        "FROM memories WHERE importance >= ? ORDER BY importance DESC",
        (min_importance,),
    ).fetchall()

    conn.close()

    stats = {
        "total_candidates": len(rows),
        "sent": 0,
        "skipped": 0,
        "irrelevant": 0,
        "per_cluster": {c: 0 for c in clusters},
    }

    for row in rows:
        mem_id = row["id"]
        content = row["content"]
        importance = row["importance"]
        scope = row["scope"] if row["scope"] else "shared"

        # Déterminer les clusters cibles
        targets = set()

        # Règle 1 : scope=global → TOUS les clusters
        if scope == "global":
            targets.update(clusters.keys())

        # Règle 2 : Pertinence par expertise (index inversé)
        targets.update(find_relevant_clusters(content, config, expertise_index))

        # Si aucun cluster ciblé → on skip
        if not targets:
            stats["irrelevant"] += 1
            continue

        # Écrire l'événement downstream pour chaque cluster cible
        for target in sorted(targets):
            # Éviter les doublons (cluster source = ne pas se renvoyer à soi-même
            # si la mémoire vient déjà de ce cluster)
            source_cluster = row["agent"] if row["agent"] else ""
            if source_cluster.startswith("cluster:") and source_cluster.split(":", 1)[1] == target:
                # C'est sa propre mémoire → skip (déjà dans sa DB)
                if scope != "global":
                    continue

            # Déduplication
            if state.was_sent(mem_id, target):
                stats["skipped"] += 1
                continue

            if not dry_run:
                target_file = downstream_dir / f"to-{target}.jsonl"

                event = {
                    "op": "remember",
                    "id": f"downstream-{mem_id}",
                    "agent": "cluster:global",
                    "ts": _now_iso(),
                    "payload": {
                        "content": content,
                        "importance": importance,
                        "source": f"global-downstream/{row['source'] or 'unknown'}",
                        "scope": "global",
                        "downstream_target": target,
                        "original_agent": row["agent"] or "unknown",
                    },
                }

                with open(target_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(event, ensure_ascii=False) + "\n")

                state.mark_sent(mem_id, target)
                stats["sent"] += 1
                stats["per_cluster"][target] += 1

    if not dry_run and stats["sent"] > 0:
        state.data["last_run"] = _now_iso()
        state.save()

    return stats


# ── CLI ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="HiveMind Redistribute — Global → Clusters"
    )
    parser.add_argument(
        "--db", required=True,
        help="consolidated.db du Global",
    )
    parser.add_argument(
        "--downstream-dir", default="./downstream",
        help="Dossier downstream (défaut: ./downstream)",
    )
    parser.add_argument(
        "--config", required=True,
        help="cluster_weights.json",
    )
    parser.add_argument(
        "--min-importance", type=float, default=0.7,
        help="Seuil minimum d'importance (défaut: 0.7)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Simulation sans écriture",
    )

    args = parser.parse_args()

    result = redistribute(
        db_path=args.db,
        downstream_dir=args.downstream_dir,
        config_path=args.config,
        min_importance=args.min_importance,
        dry_run=args.dry_run,
    )

    if "error" in result:
        print(f"❌ {result['error']}", file=sys.stderr)
        sys.exit(1)

    print(f"\n📤 Redistribution terminée")
    print(f"   Candidats évalués  : {result['total_candidates']}")
    print(f"   Envoyés            : {result['sent']}")
    print(f"   Déjà envoyés       : {result['skipped']}")
    print(f"   Non pertinents     : {result['irrelevant']}")
    print(f"\n   Par cluster :")
    for cluster, count in result.get("per_cluster", {}).items():
        print(f"     → {cluster:<15} {count} mémoires")


if __name__ == "__main__":
    main()
