#!/usr/bin/env python3
"""
HiveMind Export Engine — Phase 2
=================================

Exporte les mémoires d'un cluster vers un fichier JSONL
consommable par le HiveMind global.

ARCHITECTURE :
  ┌─────────────────┐
  │  CLUSTER        │
  │  consolidated.db│──► export_engine.py ──► export/<cluster>.jsonl
  └─────────────────┘                              │
                                                   │ Syncthing
                                                   ▼
                                          ┌─────────────────┐
                                          │  GLOBAL          │
                                          │  events/         │
                                          │   <cluster>.jsonl│
                                          └─────────────────┘

MODES :
  full        → Ré-exporte TOUT (bootstrap ou reset)
  incremental → Exporte seulement les mémoires nouvelles/modifiées
  dry-run     → Affiche ce qui serait exporté sans écrire

FILTRES :
  --scope shared,global    → Quels scopes exporter (défaut: shared)
  --min-importance 0.3     → Seuil minimum
  --cluster audit           → Nom du cluster (tag dans agent: "cluster:audit")

IDEMPOTENCE :
  L'export engine maintient un fichier .export_state.json
  qui suit l'état de chaque mémoire exportée.
  Re-exécuter ne duplique pas les événements.
  Seules les mémoires nouvelles ou modifiées génèrent un événement.

USAGE :
  python3 export_engine.py \
    --db ../cabinet-audit/memory/consolidated.db \
    --export-dir ./export \
    --cluster audit \
    --mode incremental
"""

import json
import os
import sys
import sqlite3
import argparse
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ── Helpers ────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _content_hash(content: str) -> str:
    """Hash du contenu pour détecter les changements."""
    return hashlib.sha256(content.encode()).hexdigest()[:16]


# ── Export state ────────────────────────────────────────────────────

class ExportState:
    """
    Suit l'état d'exportation des mémoires.
    Stocké dans un fichier JSON à côté de l'export.
    """

    def __init__(self, state_path: str):
        self.state_path = Path(state_path)
        self.data: dict = self._load()

    def _load(self) -> dict:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {"memories": {}, "last_export": None, "total_exported": 0}

    def save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.data, indent=2))

    def is_exported(self, memory_id: str, content: str) -> bool:
        """
        Vérifie si une mémoire a déjà été exportée avec le même contenu.
        Returns True si déjà exportée et inchangée.
        """
        entry = self.data["memories"].get(memory_id)
        if not entry:
            return False
        return entry.get("hash") == _content_hash(content)

    def mark_exported(self, memory_id: str, content: str):
        """Marque une mémoire comme exportée."""
        self.data["memories"][memory_id] = {
            "hash": _content_hash(content),
            "exported_at": _now_iso(),
        }
        self.data["total_exported"] = len(self.data["memories"])

    def reset(self):
        """Réinitialise l'état (pour un full export)."""
        self.data = {"memories": {}, "last_export": None, "total_exported": 0}


# ── Export logic ────────────────────────────────────────────────────

def export_cluster(
    db_path: str,
    export_dir: str,
    cluster_name: str,
    mode: str = "incremental",
    scopes: list[str] = None,
    min_importance: float = 0.0,
    dry_run: bool = False,
) -> dict:
    """
    Exporte les mémoires du cluster vers un fichier JSONL.

    Args:
        db_path: Chemin de consolidated.db du cluster
        export_dir: Dossier de sortie (contiendra <cluster>.jsonl)
        cluster_name: Nom du cluster (tag agent = "cluster:<name>")
        mode: "full" ou "incremental"
        scopes: Liste des scopes à exporter (défaut: ["shared"])
        min_importance: Importance minimum
        dry_run: Si True, n'écrit rien

    Returns:
        dict avec stats
    """
    if scopes is None:
        scopes = ["shared"]

    db_path = Path(db_path)
    export_dir = Path(export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)

    if not db_path.exists():
        print(f"[ERROR] DB introuvable: {db_path}", file=sys.stderr)
        return {"error": "db_not_found", "exported": 0, "skipped": 0}

    # État d'export
    state_path = export_dir / f".{cluster_name}_export_state.json"
    state = ExportState(str(state_path))

    if mode == "full":
        state.reset()
        print(f"[EXPORT] Mode FULL — réinitialisation de l'état")

    # Lire consolidated.db
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    placeholders = ",".join("?" * len(scopes))
    rows = conn.execute(
        f"SELECT id, content, importance, source, scope, agent, created_at, updated_at "
        f"FROM memories "
        f"WHERE scope IN ({placeholders}) AND importance >= ? "
        f"ORDER BY updated_at",
        scopes + [min_importance],
    ).fetchall()

    conn.close()

    # Déterminer quoi exporter
    to_export = []
    skipped = 0

    for row in rows:
        mem_id = row["id"]
        content = row["content"]

        if mode == "incremental" and state.is_exported(mem_id, content):
            skipped += 1
            continue

        event = {
            "op": "remember",
            "id": f"export-{cluster_name}-{mem_id}",
            "agent": f"cluster:{cluster_name}",
            "ts": row["updated_at"] or row["created_at"] or _now_iso(),
            "payload": {
                "content": content,
                "importance": row["importance"] or 0.5,
                "source": f"{cluster_name}/{row['source'] or 'unknown'}",
                "scope": row["scope"] or "shared",
                "original_agent": row["agent"] or "unknown",
            },
        }
        to_export.append(event)

    # Écrire
    if not dry_run and to_export:
        export_file = export_dir / f"{cluster_name}.jsonl"

        if mode == "full":
            # Overwrite
            with open(export_file, "w", encoding="utf-8") as f:
                for event in to_export:
                    f.write(json.dumps(event, ensure_ascii=False) + "\n")
        else:
            # Append
            with open(export_file, "a", encoding="utf-8") as f:
                for event in to_export:
                    f.write(json.dumps(event, ensure_ascii=False) + "\n")

        # Mettre à jour l'état
        for event in to_export:
            mem_id = event["id"].replace(f"export-{cluster_name}-", "")
            state.mark_exported(mem_id, event["payload"]["content"])

        state.data["last_export"] = _now_iso()
        state.save()

    stats = {
        "mode": mode,
        "cluster": cluster_name,
        "total_in_db": len(rows),
        "exported": len(to_export),
        "skipped": skipped,
        "export_file": str(export_dir / f"{cluster_name}.jsonl") if not dry_run else "(dry-run)",
    }

    if dry_run:
        print(f"\n[DRY RUN] {stats['exported']} mémoires à exporter, {stats['skipped']} ignorées")
        for e in to_export[:5]:
            print(f"  [{e['payload']['importance']}] {e['payload']['content'][:80]}...")
        if len(to_export) > 5:
            print(f"  ... et {len(to_export) - 5} autres")

    return stats


# ── CLI ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="HiveMind Export Engine")
    parser.add_argument(
        "--db", required=True,
        help="Chemin de consolidated.db du cluster",
    )
    parser.add_argument(
        "--export-dir", default="./export",
        help="Dossier de sortie (défaut: ./export)",
    )
    parser.add_argument(
        "--cluster", required=True,
        help="Nom du cluster (ex: audit, fiscal, juridique)",
    )
    parser.add_argument(
        "--mode", choices=["full", "incremental"], default="incremental",
        help="Mode d'export (défaut: incremental)",
    )
    parser.add_argument(
        "--scope", nargs="+", default=["shared"],
        help="Scopes à exporter (défaut: shared)",
    )
    parser.add_argument(
        "--min-importance", type=float, default=0.0,
        help="Importance minimum (défaut: 0.0)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Affiche sans écrire",
    )

    args = parser.parse_args()

    stats = export_cluster(
        db_path=args.db,
        export_dir=args.export_dir,
        cluster_name=args.cluster,
        mode=args.mode,
        scopes=args.scope,
        min_importance=args.min_importance,
        dry_run=args.dry_run,
    )

    if "error" in stats:
        sys.exit(1)

    print(f"\n✅ Export terminé")
    print(f"   Mode       : {stats['mode']}")
    print(f"   Cluster    : {stats['cluster']}")
    print(f"   Total DB   : {stats['total_in_db']}")
    print(f"   Exportés   : {stats['exported']}")
    print(f"   Ignorés    : {stats['skipped']}")
    print(f"   Fichier    : {stats['export_file']}")


if __name__ == "__main__":
    main()
