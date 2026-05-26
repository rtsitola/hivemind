# HiveMind — Prototype Merge Engine + Watcher + Intégration Mnemosyne

## Résultat des tests

```
✅ merge_engine  — Parse JSONL → SQLite, idempotent, 7 phases OK
✅ mnemosyne     — Bootstrap 45 mémoires, write/update/forget/recall OK
✅ watcher       — Détection auto + merge, 2 merges en 5 secondes
```

## Structure

```
prototype/
├── merge_engine.py          ← Parse JSONL → SQLite consolidé
├── event_writer.py          ← Simule un agent qui écrit des événements
├── hivemind_mnemosyne.py    ← Adaptateur Mnemosyne ↔ Event Log
├── watcher.py               ← Détection auto + merge ★ NOUVEAU
├── test_e2e.py              ← Test merge engine (7 phases)
├── test_integration.py      ← Test intégration Mnemosyne (7 phases)
├── test_watcher.py          ← Test watcher (merge auto) ★ NOUVEAU
└── memory/
    ├── events/              ← Journaux partagés via Syncthing
    └── consolidated.db      ← Vue locale (générée, jamais sync)
```

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                                                                 │
│  MACHINE A                          MACHINE B                  │
│                                                                 │
│  Hermes                             Hermes                     │
│    │                                  │                         │
│    ▼                                  ▼                         │
│  hivemind_mnemosyne.py              hivemind_mnemosyne.py      │
│    │                                  │                         │
│    ├─ write ──► alice.jsonl          ├─ write ──► bob.jsonl   │
│    │              │                  │              │           │
│    │         ┌────┴────┐             │         ┌────┴────┐     │
│    │         │Syncthing│◄────────────┼────────►│Syncthing│     │
│    │         └────┬────┘             │         └────┬────┘     │
│    │              │                  │              │           │
│    ▼              ▼                  ▼              ▼           │
│  watcher.py   events/*.jsonl     watcher.py   events/*.jsonl   │
│    │                                │                           │
│    ├─ détecte changement            ├─ détecte changement       │
│    │                                │                           │
│    ▼                                ▼                           │
│  merge_engine.py                merge_engine.py               │
│    │                                │                           │
│    ▼                                ▼                           │
│  consolidated.db                consolidated.db               │
│    │                                │                           │
│    ▼                                ▼                           │
│  recall() → identique            recall() → identique          │
│                                                                 │
│  OFFLINE-FIRST : machine éteinte = zéro impact.                │
│  Syncthing rattrape au démarrage, watcher merge.               │
│                                                                 │
└────────────────────────────────────────────────────────────────┘
```

## Quick start

```bash
# 1. Démarrer le watcher (arrière-plan)
python3 watcher.py --events-dir ./memory/events --db ./memory/consolidated.db &

# 2. Écrire via l'adaptateur Mnemosyne
python3 hivemind_mnemosyne.py --agent alice remember \
  "Client Omega : vérifier cash-flow" --importance 0.9

# → Le watcher détecte et merge automatiquement

# 3. Rechercher
python3 hivemind_mnemosyne.py --agent alice recall "cash-flow"

# 4. Merge oneshot (sans watcher)
python3 watcher.py --oneshot
```

## Modes du watcher

| Mode | Commande | Usage |
|---|---|---|
| **Daemon** | `python3 watcher.py &` | Fond, merge auto continu |
| **Oneshot** | `python3 watcher.py --oneshot` | Merge une fois, pour cron |
| **Custom** | `--interval 2 --debounce 5` | Poll toutes les 2s, merge après 5s calme |

## Ce qui reste à faire

- [ ] CLI onboarding — `hermes hivemind init <org>`
- [ ] Test réel — 2 machines physiques + Syncthing
- [ ] Skill Hermes — wrapper les opérations en execute_code
