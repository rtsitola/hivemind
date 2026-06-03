# HiveMind — Phase 1 : Un esprit, un groupe

> **Statut** : Finalisé ✅
> **Date** : 2026-05-26 (finalisé 2026-05-31)
> **Auteur** : Tsitola + Hermes

---

## 1. Vision

Un **Hermes partagé** par un groupe. Pas un assistant individuel — une intelligence collective qui absorbe la manière de penser, les skills, les outils et la mémoire du groupe.

Chaque membre utilise le **même Hermes**. Chaque correction d'un senior, chaque découverte d'un edge case, chaque nouveau skill enrichit l'esprit commun. La personnalité du groupe émerge naturellement.

```
AVANT                                  APRÈS
──────                                ──────

Alice a son Hermes                    Alice, Bob, Charles
Bob a son Hermes                      utilisent LE MÊME Hermes
Charles a son Hermes
                                      ┌─────────────────┐
Chacun lit ses propres skills         │   HIVEMIND       │
Chacun a sa propre mémoire            │                  │
Chacun corrige dans son coin          │ skills/   ← Git  │
                                      │ memory/   ← Sync │
→ 3 Hermes, 0 synergie                │ config/   ← Git  │
                                      └─────────────────┘
                                      ↑       ↑       ↑
                                    Alice    Bob   Charles
```

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                      │
│   ~/.hermes/profiles/<org-name>/                                     │
│                                                                      │
│   ├── skills/                  ← Git (push/pull)                     │
│   │   ├── audit-fraude.md                                            │
│   │   ├── circularisation.md                                         │
│   │   └── ...                                                        │
│   │                                                                  │
│   ├── config.yaml              ← Git                                 │
│   │   (modèle, provider, outils standardisés)                        │
│   │                                                                  │
│   ├── USER.md                  ← Git                                 │
│   │   ("Qui est le cabinet" — personnalité collective)               │
│   │                                                                  │
│   ├── memory/                  ← Syncthing (événements)              │
│   │   ├── events/                                                    │
│   │   │   ├── alice.jsonl                                            │
│   │   │   ├── bob.jsonl                                              │
│   │   │   └── charles.jsonl                                          │
│   │   └── consolidated.db      ← Local (généré par merge engine)     │
│   │                                                                  │
│   ├── .env                     ← JAMAIS sync (clés API locales)      │
│   └── .gitignore                                                     │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘

TRANSPORT :
  Git       → skills, config, USER.md  (asynchrone, versionné)
  Syncthing → memory/events/           (P2P, temps quasi-réel)
  Local     → consolidated.db          (reconstruit, jamais sync)

TOUT est décentralisé. Pas de serveur central.
```

---

## 3. Mécanismes de sync — détail

### 3.1 Skills, Config, USER.md → Git

```
Chaque membre :
  git clone <org-repo> ~/.hermes/profiles/<org>/

Modification d'un skill :
  hermes skill patch audit-fraude → modifie le fichier local
  git add, commit, push → les autres pull

  Fréquence : manuel, ou cron "git pull" toutes les 5 min
  
Résolution de conflit :
  Merge humain. Git standard.
```

### 3.2 Mémoire → Syncthing + Event Log + Merge Engine

```
┌──────────────────────────────────────────────────────┐
│                                                       │
│  PRINCIPE : Ne jamais syncer le SQLite.               │
│  Syncer des journaux d'événements JSONL.              │
│                                                       │
│  ÉCRITURE (quand Alice memorise un fait) :             │
│  ┌─────────────────────────────────────────────────┐ │
│  │ Hermes → Mnemosyne.remember()                   │ │
│  │         → Écrit dans alice.jsonl (append)       │ │
│  │         → Syncthing sync alice.jsonl aux autres │ │
│  └─────────────────────────────────────────────────┘ │
│                                                       │
│  LECTURE (quand Bob cherche un souvenir) :             │
│  ┌─────────────────────────────────────────────────┐ │
│  │ Merge Engine lit tous les *.jsonl               │ │
│  │ → Reconstruit consolidated.db                   │ │
│  │ → Hermes lit consolidated.db                    │ │
│  │ → Recall instantané (<100ms)                     │ │
│  └─────────────────────────────────────────────────┘ │
│                                                       │
│  LATENCE :                                           │
│    Syncthing LAN     : ~2-5 secondes                  │
│    Syncthing relais  : ~10-30 secondes                │
│    Merge Engine      : configurable (5s par défaut)  │
│    → Eventually consistent, pas real-time             │
│                                                       │
└──────────────────────────────────────────────────────┘
```

### 3.3 Format du Event Log

```jsonl
// Ajout d'une mémoire
{"op":"remember","id":"evt-a1b2c3","agent":"alice","ts":"2026-05-26T12:00:00Z",
 "payload":{"content":"Client Omega : demander cash-flow avant audit",
            "importance":0.8,"source":"correction","scope":"shared"}}

// Modification
{"op":"update","id":"evt-d4e5f6","agent":"bob","ts":"2026-05-26T12:05:00Z",
 "payload":{"memory_id":"mem-xyz","content":"Client Omega : demander cash-flow ET balance âgée",
            "importance":0.9}}

// Suppression
{"op":"forget","id":"evt-g7h8i9","agent":"alice","ts":"2026-05-26T12:10:00Z",
 "payload":{"memory_id":"mem-xyz"}}

// Métadonnée (pour la pondération Phase 2)
{"op":"remember","id":"evt-j0k1l2","agent":"charles","ts":"...","cluster":"audit",
 "payload":{...}}
```

### 3.4 Merge Engine — algorithme

```python
def merge(events_dir: str, consolidated_db: str):
    """
    Lit tous les *.jsonl, rejoue les événements dans l'ordre chronologique.
    Résultat : vue consolidée identique sur toutes les machines.
    """
    events = []
    for jsonl_file in glob(f"{events_dir}/*.jsonl"):
        for line in open(jsonl_file):
            events.append(json.loads(line))
    
    events.sort(key=lambda e: e["ts"])
    
    seen = set()
    for event in events:
        if event["id"] in seen:
            continue  # idempotent
        seen.add(event["id"])
        
        if event["op"] == "remember":
            upsert_memory(event["payload"])
        elif event["op"] == "update":
            update_memory(event["payload"]["memory_id"], event["payload"])
        elif event["op"] == "forget":
            delete_memory(event["payload"]["memory_id"])
```

### 3.5 Résolution de conflits

| Conflit | Résolution |
|---|---|
| Même `event.id` rejoué deux fois | Idempotent : ignoré |
| `remember` + `forget` même mémoire | `forget` gagne (ts plus récent si même id, sinon dernier dans l'ordre chrono) |
| Deux `update` concurrents | Last-write-wins (ts le plus récent) |
| `remember` même contenu, sources différentes | Fusion : les deux sources sont conservées en metadata |

---

## 4. Onboarding d'un nouveau membre

```
ÉTAPE 1 : Rejoindre le groupe
  git clone git@github.com:<org>/hivemind-skills.git ~/.hermes/profiles/<org>/
  
ÉTAPE 2 : Rejoindre la mémoire
  Syncthing : accepter le partage "hivemind-<org>-memory"
  → Path : ~/.hermes/profiles/<org>/memory/events/
  
ÉTAPE 3 : Configurer Hermes
  hermes config set profile <org>
  cp .env.example .env  → configurer ses propres clés API
  
ÉTAPE 4 : Premier merge
  python3 ~/.hermes/scripts/hivemind-merge.py
  → Génère consolidated.db avec TOUTE la mémoire existante

ÉTAPE 5 : C'est prêt
  hermes → maintenant il pense comme le groupe
```

---

## 5. Expérience utilisateur

### 5.1 Alice, senior, corrige un skill après une erreur

```
Alice : "Hermes, le seuil de matérialité c'est 5% du résultat net,
        pas du CA. Patch le skill audit-seuils."

Hermes patch audit-seuils → met à jour skills/audit-seuils.md
Alice : git commit -m "fix: seuil matérialité = 5% RN"
        git push

Bob (5 min plus tard, après git pull) :
Bob : "Hermes, quel est le seuil de matérialité ?"
Hermes → "5% du résultat net"  ✅ corrigé pour tout le monde
```

### 5.2 Bob, junior, découvre un edge case

```
Bob : "Hermes, le client Gamma refuse la circularisation.
      Note-le."

Hermes → mnemosyne_remember("Client Gamma : refuse circularisation")
       → écrit dans bob.jsonl
       → Syncthing sync

Alice (30 secondes plus tard, merge engine relancé) :
Alice : "Hermes, des alertes sur le client Gamma ?"
Hermes → "⚠️ Client Gamma refuse circularisation. Risque de fraude."
       → Alice n'a rien eu à faire, la mémoire du groupe a fonctionné
```

### 5.3 Le raisonnement émerge par accumulation

```
SEMAINE 1 :
  Skill audit-tresorerie = "Vérifier le cash-flow"
  
SEMAINE 4 (après 3 corrections) :
  Skill audit-tresorerie = "Vérifier cash-flow + balance âgée +
    circularisation banques + cutoff tests"
  
  Hermes ne propose PLUS juste le cash-flow.
  Il propose la checklist complète, enrichie par le groupe.
  
  La personnalité "ici on est exhaustif sur la trésorerie" a émergé.
```

---

## 6. Décisions techniques

| Décision | Choix | Justification |
|---|---|---|
| Transport skills | Git | Versionné, merge humain, pas de conflit binaire |
| Transport mémoire | Syncthing | P2P, pas de serveur, chiffré, fonctionne en LAN et WAN |
| Format mémoire | JSONL append | Append = pas de corruption, JSONL = lisible et debugable |
| Vue mémoire | SQLite local | Lecture rapide, compatible Mnemosyne existant |
| Résolution conflits | Last-write-wins | Simple, suffisant pour ce use case (pas de transaction financière) |
| Latence acceptable | 5-30 secondes | La mémoire collective n'est pas un stream temps réel |
| Pas de consensus | Pas de Raft/Paxos | Overkill. Les membres se font confiance. |
| Pas de blockchain | Pas de PoW/PoS | Inutile en environnement de confiance |

---

## 7. Ce qui n'est PAS dans la Phase 1

| Exclu | Pourquoi | Reporté à |
|---|---|---|
| Clusters | Complexité inutile sans la base | Phase 2 |
| Pondération | Pas de clusters = pas de pondération | Phase 2 |
| Permissions par rôle | Tous égaux dans un HiveMind plat | Phase 2 |
| Multi-tenancy (plusieurs orgs) | Un seul groupe = un seul HiveMind | Phase 2+ |
| Héritage global↔cluster | Pas de hiérarchie | Phase 2 |
| .env partagé | Risque sécurité | Jamais |

---

## 8. Prochaines étapes

1. **Prototype Merge Engine** — script Python de base
2. **Test Syncthing multi-writer** — 2 machines écrivent simultanément
3. **Intégration Mnemosyne** — adapter l'API Mnemosyne pour écrire dans l'Event Log
4. **CLI d'onboarding** — `hermes hivemind join <org>`
5. **Test réel** — 3 utilisateurs, 1 semaine
