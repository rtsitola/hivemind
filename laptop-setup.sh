#!/bin/bash
# HiveMind — Setup laptop
# À exécuter SUR le laptop (GalaxyBookTt)

set -e

echo "🐝 HiveMind — Setup Laptop"
echo "=========================="

# 1. Clone/pull le code HiveMind
if [ -d ~/hivemind ]; then
    echo "📦 Mise à jour du repo HiveMind..."
    cd ~/hivemind && git pull
else
    echo "📦 Clonage du repo HiveMind..."
    git clone git@github.com:rtsitola/hivemind.git ~/hivemind
fi

# 2. Créer le profil cabinet-ascent (si pas déjà fait)
if [ -d ~/.hermes/profiles/cabinet-ascent ]; then
    echo "✅ Profil cabinet-ascent existe déjà"
else
    echo "🏗️  Création du profil..."
    cd ~/hivemind && python3 core/hivemind_cli.py init cabinet-ascent
fi

# 3. Copier USER.md du desktop (version la plus récente)
#    → Le USER.md est dans le code repo maintenant
cp ~/hivemind/core/USER.md ~/.hermes/profiles/cabinet-ascent/USER.md 2>/dev/null || echo "⚠️  USER.md non trouvé dans le repo — à copier manuellement"

# 4. Configurer .env
if [ ! -s ~/.hermes/profiles/cabinet-ascent/.env ] || [ $(wc -l < ~/.hermes/profiles/cabinet-ascent/.env) -lt 10 ]; then
    echo "🔑 .env à remplir manuellement :"
    echo "   nano ~/.hermes/profiles/cabinet-ascent/.env"
    echo "   → Copier tes clés API depuis ~/.hermes/.env"
fi

# 5. Accepter le partage Syncthing
echo ""
echo "📡 Vérifie Syncthing :"
echo "   1. Ouvre http://localhost:8384"
echo "   2. Accepte le partage 'hivemind-cabinet-ascent-memory'"
echo "   3. Path : ~/.hermes/profiles/cabinet-ascent/memory"
echo ""

# 6. Définir le profil par défaut
hermes config set profile cabinet-ascent
echo "✅ Profil cabinet-ascent = défaut"

# 7. Premier merge
cd ~/hivemind
python3 core/hivemind_mnemosyne.py merge --events-dir ~/.hermes/profiles/cabinet-ascent/memory/events --db ~/.hermes/profiles/cabinet-ascent/memory/consolidated.db 2>/dev/null || echo "⚠️  Merge initial — pas d'événements (normal)"

echo ""
echo "=========================="
echo "✅ Laptop prêt pour HiveMind"
echo ""
echo "   Profil  : cabinet-ascent"
echo "   Mémoire : ~/.hermes/profiles/cabinet-ascent/memory/"
echo "   Watcher : cd ~/hivemind && python3 core/hivemind_cli.py serve"
