# Rejoindre le HiveMind — Guide nouveau membre

> Temps estimé : 10 minutes.  
> Prérequis : Hermes Agent installé, Git, Syncthing.

---

## Étape 1 : Cloner le repo

```bash
git clone git@github.com:rtsitola/hivemind.git ~/.hermes/profiles/cabinet-ascent/
```

Si tu vois `Permission denied (publickey)`, configure d'abord ta clé SSH sur GitHub :
```bash
ssh-keygen -t ed25519 -C "ton-email@ascent.mg"
cat ~/.ssh/id_ed25519.pub   # → à ajouter dans GitHub > Settings > SSH Keys
```

---

## Étape 2 : Configurer tes clés API

```bash
cp ~/.hermes/profiles/cabinet-ascent/.env.example ~/.hermes/profiles/cabinet-ascent/.env
nano ~/.hermes/profiles/cabinet-ascent/.env
```

Remplis avec **tes** clés API personnelles. Le `.env` n'est **jamais** sync — chaque membre a ses propres clés.

---

## Étape 3 : Accepter le partage Syncthing

1. Ouvre l'interface Syncthing : http://localhost:8384
2. Accepte le partage `hivemind-cabinet-ascent-memory`
3. Vérifie que le dossier local pointe vers :
   ```
   ~/.hermes/profiles/cabinet-ascent/memory
   ```
4. Attends que la synchro initiale se termine (premier cercle vert)

---

## Étape 4 : Premier bootstrap — importer ta mémoire

```bash
cd ~/.hermes/profiles/cabinet-ascent
python3 core/hivemind_mnemosyne.py --agent ton-prenom bootstrap
```

Ceci exporte **toutes tes mémoires Mnemosyne existantes** vers l'Event Log du cabinet. Après cette étape, le groupe voit ce que tu sais déjà.

---

## Étape 5 : Premier merge — voir la mémoire collective

```bash
python3 core/hivemind_mnemosyne.py merge
python3 core/hivemind_mnemosyne.py stats
```

Tu devrais voir des centaines de mémoires — celles du groupe entier.

---

## Étape 6 : Démarrer le watcher

```bash
python3 core/hivemind_cli.py serve
```

Le watcher surveille `memory/events/` et merge automatiquement les nouveaux souvenirs des collègues. Laisse-le tourner.

---

## Étape 7 : Définir le profil par défaut

```bash
hermes config set profile cabinet-ascent
```

Ton Hermes utilise maintenant l'intelligence du cabinet par défaut.

---

## Vérification

```bash
# Checker que le profil est actif
hermes config get profile

# Voir les stats de la mémoire collective
python3 core/hivemind_mnemosyne.py stats

# Tester une recherche
python3 core/hivemind_mnemosyne.py recall "seuil matérialité"
```

---

## Workflow quotidien

- **Tu écris une mémoire** → Hermes l'ajoute automatiquement dans ton `.jsonl` → Syncthing la partage aux collègues
- **Un collègue écrit une mémoire** → Syncthing la dépose dans ton `events/` → ton watcher merge → ta `consolidated.db` est à jour
- **Tu corriges un skill** → `git add`, `commit`, `push` → les autres font `git pull`

---

## En cas de problème

| Symptôme | Solution |
|---|---|
| Mémoires non visibles | `python3 core/hivemind_mnemosyne.py merge` (merge manuel) |
| Syncthing bloqué | Vérifier que le dossier `memory` est bien partagé et que le device est connecté |
| Conflit Git sur un skill | Résoudre manuellement — ne jamais forcer `push --force` |
| `consolidated.db` corrompu | Supprimer et re-merger : `rm memory/consolidated.db && python3 core/hivemind_mnemosyne.py merge` |
