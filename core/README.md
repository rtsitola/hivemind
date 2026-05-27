# HiveMind Core — Phase 1

Mémoire collective pour un groupe. N personnes, UN Hermes.

## Structure

```
hivemind-core/
├── hivemind_common.py        ← Helpers partagés
├── merge_engine.py           ← JSONL → SQLite consolidé
├── event_writer.py           ← Écriture agent dans le journal
├── hivemind_mnemosyne.py     ← Adaptateur Mnemosyne ↔ Event Log
├── watcher.py                ← Merge automatique sur changement
├── hivemind_cli.py           ← Onboarding (init, join, status, serve)
├── cluster_weights.json      ← Config partagée
├── PHASE-1.md                ← Spécification complète
├── test_e2e.py               ← Test merge engine
├── test_integration.py       ← Test intégration Mnemosyne
├── test_watcher.py           ← Test watcher
└── test_cli.py               ← Test CLI
```

## Quick start

```bash
# Créer un HiveMind
python3 hivemind_cli.py init mon-organisation

# Démarrer le watcher
python3 hivemind_cli.py serve mon-organisation

# Écrire une mémoire
python3 hivemind_mnemosyne.py --agent alice remember "Contenu..." --importance 0.9

# Rechercher
python3 hivemind_mnemosyne.py --agent alice recall "recherche"
```

## Tests

```bash
python3 test_e2e.py && python3 test_integration.py && python3 test_watcher.py && python3 test_cli.py
```

## Dépendance du projet clustered

Le projet `hivemind-clustered/` (Phase 2) importe depuis ce projet.
Les deux doivent être dans le même dossier parent.
