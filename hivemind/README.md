# HiveMind — Phase 1 : Intelligence Collective

<p align="center">
  <img width="800" alt="Athena — Wisdom, Industry, Collective Intelligence" src="../assets/hivemind-athena.jpg" />
</p>

> Un Hermes partagé par un groupe. Pas un assistant individuel — une intelligence collective.

<img width="1168" height="784" alt="HiveMind Architecture" src="../hivemind-architecture.svg" />

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
cd hivemind

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

## Phase 2 : Clustered HiveMind

Les composants Phase 2 sont dans le repo **hivemind-cluster** (https://github.com/rtsitola/hivemind-cluster). Voir `../SPEC-PHASE-2.md` pour l'architecture complète.

### Définition des clusters et membres

**Fichier canonique : `clusters.yaml`** (à la racine du repo ou dans `~/.hermes/profiles/<global>/`)

```yaml
clusters:
  audit:
    profile: cabinet-audit       # Profil Hermes associé
    weight: 1.0                  # Poids de base
    expertise: [audit, IFRS, ...]  # Domaines d'expertise
    members: [alice, bob, charles] # Agents autorisés
```

C'est LA source unique de vérité pour la Phase 2. Le fichier `cluster_weights.json` est généré automatiquement à partir de ce YAML.

**Commandes :**
```bash
hivemind cluster list              # Tous les clusters et membres
hivemind cluster show audit        # Détails d'un cluster
hivemind cluster validate          # Valider la configuration
hivemind status                    # Inclut les infos cluster si clusters.yaml présent
```

**Validation automatique :** le merge engine avertit si un agent inconnu (non listé dans aucun cluster) écrit des événements. L'événement est quand même traité (soft validation).

### Tests Phase 2

```bash
# Voir repo hivemind-cluster pour ces tests :
python3 test_export.py           # Export cluster → global
python3 test_weighted_merge.py   # Merge pondéré
python3 test_inbox.py            # Messages inter-cluster
python3 test_redistribute.py     # Downstream global → clusters
```

### Cluster Weights — Comment configurer les pondérations

La pondération est définie dans `clusters.yaml` du repo hivemind-cluster. Le merge engine du **niveau global** multiplie l'importance de chaque mémoire par un poids qui dépend de son cluster d'origine et de son contenu.

**Formule :**
```
importance_finale = min(importance × weight × bonus, 1.0)

bonus = 1.0
SI un mot-clé d'expertise du cluster est présent dans le contenu → bonus × 2.0
SI ce mot-clé n'est dans AUCUN autre cluster (monopole)              → bonus × 3.0
```

**Exemple concret :**
```json
// Mémoire : "TVA intracommunautaire : nouveau seuil à 10 000€"
// Cluster : fiscal (weight=1.5)
// Match "TVA" → bonus expertise ×2.0
// "TVA" n'est que dans fiscal → monopole ×3.0
// → importance × 1.5 × 2.0 × 3.0 = ×9.0
```

**Guide d'ajout d'un cluster :**
```json
"mon_cluster": {
  "weight": 1.2,                              // Poids de base (>0, défaut 1.0)
  "expertise": ["mot1", "mot2", "mot3"]       // Mots-clés distinctifs
}
```

**⚠️ Pièges du matching :**
- Le matching est une **sous-chaîne simple** (insensible à la casse). `"droit"` matchera `"endroit"`, `"taxe"` matchera `"syntaxe"`.
- Choisir des termes **suffisamment longs et distinctifs** (ex: `"fiscalité"` plutôt que `"fisc"`).
- Si deux clusters partagent le même mot-clé, **aucun** n'aura le bonus monopole pour ce domaine.
- Vérifier les chevauchements : `python3 -m hivemind_cluster.cluster_config --validate`

**Configuration des multiplicateurs :**
```json
"expertise_multiplier": 2.0,    // Bonus quand le contenu matche l'expertise du cluster
"monopoly_multiplier": 3.0      // Bonus quand le cluster est le SEUL à avoir cette expertise
```

Le fichier `cluster_weights.json` contient des clés `_about`, `_matching`, `_formula`, `_validation` et `_how_to_add_cluster` qui documentent le format directement dans le fichier de config. Ces clés (préfixées `_`) sont ignorées par le merge engine.

## Étape suivante

Déploiement réel → voir `../ONBOARDING.md`
