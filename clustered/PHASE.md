# HiveMind — Phase 2 : Clustered HiveMind

> **Statut** : Spécification conceptuelle  
> **Date** : 2026-05-26  
> **Dépendance** : Phase 1 (HiveMind) fonctionnelle

---

## 1. Principe

Un cluster = un HiveMind (Phase 1).  
Le global = un HiveMind qui agrège les événements des clusters.

Pas de nouvelle brique technique. Juste de la **composition**.

```
┌──────────────────────────────────────────────────────────────────┐
│                                                                   │
│  CLUSTER "AUDIT"              CLUSTER "FISCAL"                   │
│  ┌───────────────────┐        ┌───────────────────┐              │
│  │ HiveMind Phase 1  │        │ HiveMind Phase 1  │              │
│  │                    │        │                    │              │
│  │ events/            │        │ events/            │              │
│  │  alice.jsonl       │        │  david.jsonl       │              │
│  │  bob.jsonl         │        │  eve.jsonl         │              │
│  │  charles.jsonl     │        │                    │              │
│  │                    │        │                    │              │
│  │ consolidated.db    │        │ consolidated.db    │              │
│  │  (vue locale)      │        │  (vue locale)      │              │
│  └────────┬───────────┘        └────────┬───────────┘              │
│           │                             │                          │
│           │    Chaque cluster exporte    │                          │
│           │    SES événements vers       │                          │
│           │    le global                 │                          │
│           │                             │                          │
│           ▼                             ▼                          │
│  ┌─────────────────────────────────────────────────┐               │
│  │              HIVEMIND GLOBAL                     │               │
│  │                                                  │               │
│  │  events/                                         │               │
│  │   cluster-audit.jsonl    ← export du cluster     │               │
│  │   cluster-fiscal.jsonl   ← export du cluster     │               │
│  │   cluster-juridique.jsonl                        │               │
│  │                                                  │               │
│  │  merge_engine.py → consolide AVEC pondération   │               │
│  │                                                  │               │
│  │  consolidated.db  ← "voici comment le cabinet    │               │
│  │                       pense, toutes équipes      │               │
│  │                       confondues"                │               │
│  └──────────────────────────────────────────────────┘              │
│                                                                   │
└──────────────────────────────────────────────────────────────────┘
```

---

## 2. Architecture technique

```
~/.hermes/profiles/
│
├── cabinet/                          ← Profil global (lit tous les clusters)
│   ├── skills/          ← Git       ← Hérité de tous les clusters
│   ├── config.yaml      ← Git
│   ├── USER.md          ← Git
│   ├── .env             ← LOCAL
│   └── memory/
│       ├── events/
│       │   ├── audit.jsonl        ← Export du cluster audit
│       │   ├── fiscal.jsonl       ← Export du cluster fiscal
│       │   └── juridique.jsonl    ← Export du cluster juridique
│       └── consolidated.db        ← Vue globale pondérée
│
├── cabinet-audit/                   ← Cluster : une Phase 1 standard
│   ├── skills/
│   ├── memory/
│   │   ├── events/
│   │   │   ├── alice.jsonl
│   │   │   ├── bob.jsonl
│   │   │   └── charles.jsonl
│   │   ├── consolidated.db         ← Vue locale du cluster
│   │   └── export/                  ★ NOUVEAU
│   │       └── audit.jsonl         ← Export filtré pour le global
│   └── ...
│
├── cabinet-fiscal/                  ← Cluster : une Phase 1 standard
│   ├── skills/
│   ├── memory/
│   │   ├── events/
│   │   │   ├── david.jsonl
│   │   │   └── eve.jsonl
│   │   ├── consolidated.db
│   │   └── export/
│   │       └── fiscal.jsonl
│   └── ...
│
└── cabinet-juridique/
    └── ...
```

---

## 3. Ce qui est nouveau par rapport à la Phase 1

| Élément | Phase 1 | Phase 2 |
|---|---|---|
| Nombre de profils | 1 | N clusters + 1 global |
| Events | 1 fichier par agent | 1 fichier par cluster (agrégé) |
| Merge | Local uniquement | Local + global (avec pondération) |
| Export | — | Cluster → Global (JSONL filtré) |
| Sync entre niveaux | — | Syncthing (cluster → global) |
| Pondération | — | Chaque cluster a un poids |

---

## 4. Flux de données

```
┌──────────────────────────────────────────────────────────────────────┐
│                                                                       │
│  NIVEAU CLUSTER (ex: Audit)                                           │
│  ─────────────────────────                                            │
│                                                                       │
│  1. Alice écrit → alice.jsonl                                        │
│  2. Watcher → merge → consolidated.db (vue locale Audit)             │
│                                                                       │
│  3. Export Engine (nouveau) :                                         │
│     Lit consolidated.db                                              │
│     → Filtre : ne garde que les mémoires scope=shared                │
│     → Anonymise si nécessaire                                        │
│     → Écrit dans export/audit.jsonl                                  │
│                                                                       │
│  4. Syncthing sync export/audit.jsonl                                 │
│     → Dossier events/ du global                                       │
│                                                                       │
├───────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  NIVEAU GLOBAL                                                        │
│  ─────────────                                                        │
│                                                                       │
│  5. Watcher global détecte export/audit.jsonl                        │
│     → Merge Engine (version pondérée) :                               │
│       • Lit tous les cluster-*.jsonl                                  │
│       • Applique les poids configurés                                │
│       • Produit consolidated.db global                               │
│                                                                       │
│  6. Hermes Global peut recall() sur TOUTE la mémoire du cabinet      │
│                                                                       │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 5. L'Export Engine (nouveau composant)

```python
# export_engine.py — exécuté dans chaque cluster

def export_cluster_memories(
    consolidated_db: str,       # DB locale du cluster
    export_path: str,           # export/cluster-name.jsonl
    cluster_name: str,
    scope_filter: list = ["shared"],  # quels scopes exporter
    min_importance: float = 0.0,
):
    """
    Exporte les mémoires du cluster vers un fichier JSONL
    pour consommation par le global.
    
    Chaque mémoire exportée devient un événement remember
    taggé avec le cluster d'origine.
    """
    conn = sqlite3.connect(consolidated_db)
    rows = conn.execute(
        "SELECT * FROM memories "
        "WHERE scope IN ({}) AND importance >= ? "
        "ORDER BY updated_at".format(
            ",".join("?" * len(scope_filter))
        ),
        scope_filter + [min_importance],
    ).fetchall()
    
    for row in rows:
        event = {
            "op": "remember",
            "id": f"export-{cluster_name}-{row['id']}",
            "agent": f"cluster:{cluster_name}",  # tag cluster
            "ts": row["updated_at"],
            "payload": {
                "content": row["content"],
                "importance": row["importance"],
                "source": f"{cluster_name}/{row['source']}",
                "scope": row["scope"],
            },
        }
        # Écrit dans export/cluster-name.jsonl
    
    conn.close()
```

---

## 6. Pondération — merge engine global

```
┌──────────────────────────────────────────────────────────────┐
│  Config de pondération (profiles/cabinet/config.yaml)         │
│                                                               │
│  clusters:                                                    │
│    audit:                                                     │
│      weight: 1.0                                              │
│      expertise: [audit, IFRS, ISA, fraude]                   │
│    fiscal:                                                    │
│      weight: 1.5                                              │
│      expertise: [fiscalité, TVA, prix-transfert]             │
│    juridique:                                                 │
│      weight: 2.0                                              │
│      expertise: [droit-sociétés, contrats, contentieux]      │
│                                                               │
│  Le poids de base est multiplié par le poids d'expertise     │
│  quand la mémoire touche un domaine d'expertise du cluster.  │
└──────────────────────────────────────────────────────────────┘
```

### Algorithme de pondération

```python
def weighted_merge(events, cluster_config):
    """
    Pour chaque événement exporté par un cluster :
    - Poids de base = cluster.weight
    - Si le contenu matche une expertise du cluster → ×2
    - Si le cluster est le SEUL à avoir cette expertise → ×3
    
    L'importance finale de la mémoire = importance × poids.
    """
    for event in events:
        cluster = event["agent"].replace("cluster:", "")
        cfg = cluster_config.get(cluster, {"weight": 1.0})
        
        weight = cfg["weight"]
        content = event["payload"]["content"].lower()
        
        # Bonus expertise
        for domain in cfg.get("expertise", []):
            if domain.lower() in content:
                weight *= 2.0
                break
        
        # Bonus monopole (aucun autre cluster n'a cette expertise)
        if _is_unique_expert(cluster, content, cluster_config):
            weight *= 3.0
        
        event["payload"]["importance"] *= weight
```

---

## 7. Communication inter-cluster (directe)

```
┌──────────┐     ┌──────────┐
│  AUDIT   │────▶│  FISCAL  │  "Ce montage est douteux,
│          │     │          │   vous confirmez ?"
└──────────┘     └──────────┘

Pas besoin de passer par le global pour tout.

Implémentation :
  Chaque cluster a un dossier inbox/ dans son events/
  
  events/
  ├── alice.jsonl           ← Écritures locales
  ├── bob.jsonl
  ├── inbox/                 ← Messages des autres clusters
  │   ├── from-fiscal.jsonl
  │   └── from-juridique.jsonl
  └── export/
      └── audit.jsonl        ← Pour le global

  → Syncthing sync les inbox/ entre clusters
  → Le merge engine local lit aussi inbox/
  → Les mémoires inbox ont un scope spécial "cross-cluster"
```

---

## 8. Règles de scope

| Scope | Signification | Visible par |
|---|---|---|
| `private` | Interne au cluster | Cluster uniquement |
| `shared` | Pertinent pour tous | Cluster + Global |
| `cross-cluster` | Message d'un autre cluster | Cluster destinataire |
| `global` | Publié par le global | Tous les clusters (downstream) |

---

## 9. Flux downstream (global → clusters)

```
┌──────────────────────────────────────────────────────────┐
│                                                           │
│  Le global ne fait pas qu'agréger.                        │
│  Il REDISTRIBUE.                                          │
│                                                           │
│  Exemple :                                                │
│    Le cluster fiscal découvre un edge case TVA            │
│    → Export vers global                                   │
│    → Global détecte "ceci impacte aussi l'audit"         │
│    → Global publie un événement scope=global              │
│    → Watcher du cluster audit le récupère                │
│    → Merge dans consolidated.db audit                    │
│                                                           │
│  Mécanisme :                                              │
│    Global/events/                                        │
│    ├── cluster-audit.jsonl     ← Upstream                │
│    ├── cluster-fiscal.jsonl                               │
│    └── downstream/              ★ NOUVEAU                 │
│        ├── to-audit.jsonl                                 │
│        ├── to-fiscal.jsonl                                │
│        └── to-juridique.jsonl                             │
│                                                           │
│    Chaque cluster lit son to-<cluster>.jsonl              │
│    via Syncthing                                          │
│                                                           │
└──────────────────────────────────────────────────────────┘
```

---

## 10. Vue d'ensemble

```
                        ┌─────────────────┐
                        │  HIVEMIND       │
                        │  GLOBAL         │
                        │                 │
                        │  merge pondéré  │
                        │  redistribution │
                        └────────┬────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              │                  │                  │
              ▼                  ▼                  ▼
        ┌──────────┐      ┌──────────┐      ┌──────────┐
        │  AUDIT   │◄────►│  FISCAL  │◄────►│JURIDIQUE │
        │          │inbox │          │inbox │          │
        │ 30 users │      │ 8 users  │      │ 5 users  │
        │ w: 1.0   │      │ w: 1.5   │      │ w: 2.0   │
        └────┬─────┘      └────┬─────┘      └────┬─────┘
             │                 │                 │
             │   export/       │   export/       │   export/
             │   audit.jsonl   │   fiscal.jsonl  │   juridique.jsonl
             │                 │                 │
             └─────────┬───────┴────────┬────────┘
                       │                │
                       ▼                ▼
                 ┌──────────┐   ┌──────────────┐
                 │Syncthing │   │  downstream/  │
                 │  mesh    │   │  to-*.jsonl   │
                 └──────────┘   └──────────────┘
```

---

## 11. Nouveaux composants à construire

| Composant | Rôle | Basé sur |
|---|---|---|
| `export_engine.py` | Exporte les mémoires cluster → JSONL pour le global | `merge_engine.py` (lecture) |
| `merge_engine_global.py` | Merge avec pondération | `merge_engine.py` + config poids |
| `redistribute.py` | Global → clusters (downstream) | `export_engine.py` inversé |
| `inbox_watcher.py` | Lit les messages d'autres clusters | `watcher.py` adapté |

---

## 12. Ce qui reste de la Phase 1 (inchangé)

- Event Log JSONL → Syncthing (même mécanisme)
- Merge engine déterministe (même algorithme)
- Watcher par polling (même code)
- Adaptateur Mnemosyne (même API)
- Offline-first, pas de consensus bloquant

---

## 13. Prochaines étapes Phase 2

1. **export_engine.py** — premier proto
2. **merge_engine pondéré** — adapté de merge_engine.py
3. **Test 3 clusters simulés** → 1 global
4. **inbox inter-cluster**
5. **downstream redistribution**
