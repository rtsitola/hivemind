# HiveMind — Phase 1 : Intelligence Collective

> Un Hermes partagé par un groupe. Pas un assistant individuel — une intelligence collective.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│   ~/.hermes/profiles/<org>/                                  │
│   ├── skills/          ← Git (push/pull)                    │
│   ├── config.yaml      ← Git                                │
│   ├── USER.md          ← Git                                │
│   ├── memory/                                               │
│   │   ├── events/       ← Syncthing (JSONL append-only)     │
│   │   └── consolidated.db ← Local (reconstruit par merge)   │
│   ├── .env             ← JAMAIS sync                        │
│   └── .gitignore                                             │
└─────────────────────────────────────────────────────────────┘
```

**Transport :**
- Git → skills, config, USER.md (asynchrone, versionné)
- Syncthing → memory/events/ (P2P, temps quasi-réel)
- Local → consolidated.db (reconstruit, jamais sync)

## Composants

| Module | Rôle |
|---|---|
| `merge_engine.py` | Lit tous les JSONL, rejoue chronologiquement, produit consolidated.db |
| `hivemind_mnemosyne.py` | Pont Mnemosyne ↔ Event Log (remember, recall, bootstrap) |
| `hivemind_cli.py` | CLI d'onboarding (init, join, status, serve) |
| `watcher.py` | Surveille events/, déclenche merge automatique |
| `event_writer.py` | Écriture manuelle d'événements |
| `hivemind_common.py` | Helpers partagés |

## Installation

```bash
# Depuis le repo
cd hivemind/core

# Créer un HiveMind
python3 hivemind_cli.py init mon-cabinet

# Configurer Syncthing (voir instructions affichées)
# Éditer ~/.hermes/profiles/mon-cabinet/.env

# Démarrer le watcher
python3 hivemind_cli.py serve mon-cabinet
```

> 📖 **Nouveau membre ?** Lis [ONBOARDING.md](../ONBOARDING.md) — guide complet étape par étape.

## Usage

```bash
# Écrire une mémoire
python3 hivemind_mnemosyne.py --agent alice remember "Client Omega : vérifier cash-flow"

# Merger les événements
python3 hivemind_mnemosyne.py merge

# Rechercher (FTS5)
python3 hivemind_mnemosyne.py recall "cash-flow"

# Bootstrap depuis Mnemosyne existante
python3 hivemind_mnemosyne.py --agent alice bootstrap

# Status
python3 hivemind_cli.py status
```

## Format Event Log

```jsonl
{"op":"remember","id":"evt-001","agent":"alice","ts":"2026-05-26T12:00:00Z",
 "payload":{"content":"Client Omega : demander cash-flow avant audit",
            "importance":0.8,"source":"correction","scope":"shared"}}

{"op":"update","id":"evt-002","agent":"bob","ts":"2026-05-26T12:05:00Z",
 "payload":{"memory_id":"mem-abc","content":"Mise à jour...","importance":0.9}}

{"op":"forget","id":"evt-003","agent":"alice","ts":"2026-05-26T12:10:00Z",
 "payload":{"memory_id":"mem-abc"}}
```

## Résolution de conflits

| Conflit | Résolution |
|---|---|
| Même event.id rejoué | Idempotent (processed_events) |
| remember + forget même mémoire | Last-write-wins (ts) |
| Deux update concurrents | Last-write-wins (ts) |
| remember même contenu, sources différentes | Fusion (sources combinées) |

## Tests

```bash
python3 test_e2e.py           # End-to-end complet
python3 test_integration.py   # 1000+ mémoires Mnemosyne
python3 test_watcher.py       # Watcher + merge auto
python3 test_cli.py           # CLI onboarding
```

## FTS5

La recherche full-text utilise SQLite FTS5. L'index est automatiquement maintenu par des triggers. Le merge engine fait un `rebuild` idempotent à chaque cycle pour garantir la cohérence.

```sql
-- Requête FTS5
SELECT m.* FROM memories m
JOIN memories_fts fts ON m.rowid = fts.rowid
WHERE memories_fts MATCH 'fraude OR circularisation'
ORDER BY m.importance DESC;
```

## Étape suivante

Phase 2 : Clustered HiveMind → voir `../clustered/`
