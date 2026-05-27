# HiveMind Clustered — Phase 2

Multi-cluster : N HiveMinds (Phase 1) nourrissent 1 Global.

**Dépendance :** `hivemind-core/` doit être dans le dossier parent.

## Structure

```
hivemind-clustered/
├── export_engine.py           ← Cluster → Global (filtrage scope)
├── merge_engine_weighted.py   ← Merge avec pondération par cluster
├── inbox_writer.py            ← Communication directe inter-cluster
├── redistribute.py            ← Global → Clusters (downstream)
├── cluster_weights.json       ← Config des poids et expertises
├── PHASE-2.md                 ← Spécification complète
├── test_export.py             ← Test export engine
├── test_weighted_merge.py     ← Test merge pondéré
├── test_inbox.py              ← Test inbox inter-cluster
└── test_redistribute.py       ← Test downstream
```

## Quick start

```bash
# Exporter un cluster vers le global
python3 export_engine.py \
  --db ../cabinet-audit/memory/consolidated.db \
  --export-dir ./exports --cluster audit

# Merge global avec pondération
python3 merge_engine_weighted.py \
  --events-dir ./global/events --db ./global/consolidated.db \
  --config cluster_weights.json

# Envoyer un message inter-cluster
python3 inbox_writer.py --cross-dir ./cross-cluster \
  --from audit --from-agent alice --to fiscal \
  "Ce montage est-il conforme ?"

# Redistribuer du global vers les clusters
python3 redistribute.py \
  --db ./global/consolidated.db --downstream-dir ./downstream \
  --config cluster_weights.json
```

## Tests

```bash
python3 test_export.py && python3 test_weighted_merge.py && \
python3 test_inbox.py && python3 test_redistribute.py
```

## Architecture

```
Cluster Audit ──export──┐
Cluster Fiscal ─export──┼──► Global (merge pondéré)
Cluster Juridique ──────┘         │
                                  ▼
                          downstream/ ──► chaque cluster

Inter-cluster : inbox_writer.py → cross-cluster/ → Syncthing → autres clusters
```
