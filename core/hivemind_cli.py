#!/usr/bin/env python3
"""
HiveMind CLI — Onboarding
==========================

Crée ou rejoint un HiveMind en une commande.

COMMANDS:
  init   <org>              Crée un nouveau profil HiveMind
  join   <org> --git-url    Rejoint un HiveMind existant
  status                    Affiche l'état du HiveMind actif
  serve                     Démarre le watcher en arrière-plan

STRUCTURE CRÉÉE PAR init :
  ~/.hermes/profiles/<org>/
  ├── skills/               ← Git tracké
  ├── config.yaml           ← Config Hermes standardisée
  ├── USER.md               ← Personnalité du groupe
  ├── .gitignore
  ├── .env.example          ← Template clés API
  └── memory/
      └── events/           ← Dossier Syncthing

USAGE TYPIQUE :
  # Créer un HiveMind
  hivemind init cabinet-dupond

  # Configurer Syncthing (instructions affichées)
  # → Ajouter ~/.hermes/profiles/cabinet-dupond/memory/ dans Syncthing

  # Démarrer le watcher
  hivemind serve

  # Un collègue rejoint
  hivemind join cabinet-dupond --git-url git@github.com:dupont/cabinet-hivemind.git
  # → Clone le repo, accepte le partage Syncthing, bootstrap, watcher
"""

import os
import sys
import json
import argparse
import subprocess
import shutil
import socket
import textwrap
from pathlib import Path
from datetime import datetime


# ── Paths ───────────────────────────────────────────────────────────

HERMES_HOME = Path(os.path.expanduser("~/.hermes"))
PROFILES_DIR = HERMES_HOME / "profiles"
PROTOTYPE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))


# ── Templates ───────────────────────────────────────────────────────

CONFIG_TEMPLATE = """# HiveMind Profile: {org}
# Config partagée — tous les membres utilisent la même.

model:
  provider: deepseek
  model: deepseek-v4-pro

# Outils standard du groupe
tools:
  - terminal
  - web_search
  - web_extract
  - file
  - skills

# Mémoire : utilise l'adaptateur HiveMind
memory:
  provider: hivemind
  events_dir: ~/.hermes/profiles/{org}/memory/events
  consolidated_db: ~/.hermes/profiles/{org}/memory/consolidated.db
"""

USERMD_TEMPLATE = """# {org}

## Qui nous sommes

(groupe, organisation, communauté)

## Notre manière de travailler

(style, méthodes, principes)

## Nos outils

(standards, préférences)

## À savoir

(règles implicites, préférences que tout le monde doit connaître)
"""

GITIGNORE_TEMPLATE = """# HiveMind — ne jamais partager
.env
memory/consolidated.db
memory/events/
*.swp
.DS_Store
"""

ENV_EXAMPLE = """# Clés API — NE PAS COMMIT, NE PAS SYNCTHING
# Copier ce fichier en .env et remplir avec VOS clés

# OPENAI_API_KEY=sk-...
# ANTHROPIC_API_KEY=sk-ant-...
# DEEPSEEK_API_KEY=sk-...
"""


# ── Init ────────────────────────────────────────────────────────────

def cmd_init(org: str, git_remote: str = None):
    """Crée un nouveau profil HiveMind."""
    profile_dir = PROFILES_DIR / org

    if profile_dir.exists():
        print(f"❌ Le profil '{org}' existe déjà : {profile_dir}")
        print(f"   Utilisez 'hivemind join' pour rejoindre un HiveMind existant.")
        sys.exit(1)

    print(f"\n🐝 Création du HiveMind : {org}")
    print(f"   Dossier : {profile_dir}")
    print()

    # ── Structure ─────────────────────────────────────────────
    dirs = [
        profile_dir / "skills",
        profile_dir / "memory" / "events",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
        print(f"   ✅ {d.relative_to(HERMES_HOME)}")

    # ── Fichiers ──────────────────────────────────────────────
    files = {
        "config.yaml": CONFIG_TEMPLATE.format(org=org),
        "USER.md": USERMD_TEMPLATE.format(org=org),
        ".gitignore": GITIGNORE_TEMPLATE,
        ".env.example": ENV_EXAMPLE,
    }
    for name, content in files.items():
        path = profile_dir / name
        path.write_text(content.lstrip())
        print(f"   ✅ {name}")

    # ── Git init ──────────────────────────────────────────────
    print(f"\n📦 Initialisation Git...")

    # Config git locale si pas déjà configurée globalement
    git_name = _run(["git", "config", "--global", "user.name"], capture=True).stdout.strip()
    git_email = _run(["git", "config", "--global", "user.email"], capture=True).stdout.strip()

    if not git_name:
        _run(["git", "config", "user.name", f"HiveMind {org}"], cwd=profile_dir)
    if not git_email:
        _run(["git", "config", "user.email", f"hivemind+{org}@local"], cwd=profile_dir)

    _run(["git", "init", "-b", "main"], cwd=profile_dir)
    _run(["git", "add", "."], cwd=profile_dir)
    result = _run(["git", "commit", "-m", f"feat: init HiveMind {org}"], cwd=profile_dir)

    if result.returncode == 0:
        print(f"   ✅ Git initialisé — 1 commit")
    else:
        print(f"   ⚠️  Git commit ignoré (vérifiez votre config git)")

    if git_remote:
        _run(["git", "remote", "add", "origin", git_remote], cwd=profile_dir)
        print(f"   ✅ Remote ajouté : {git_remote}")
        print(f"   ⚠️  Faire 'git push -u origin main' après setup")

    # ── .env ──────────────────────────────────────────────────
    env_path = profile_dir / ".env"
    if not env_path.exists():
        shutil.copy(profile_dir / ".env.example", env_path)
        print(f"\n🔑 Fichier .env créé à partir de .env.example")
        print(f"   ⚠️  Éditer {env_path} avec VOS clés API")

    # ── Instructions Syncthing ────────────────────────────────
    _print_syncthing_instructions(org, profile_dir)

    # ── Résumé ────────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print(f"  ✅ HiveMind '{org}' créé !")
    print(f"{'─'*60}")
    print(f"\n  Prochaines étapes :")
    print(f"  1. Configurer Syncthing (voir ci-dessus)")
    print(f"  2. Éditer {profile_dir / '.env'} avec vos clés API")
    print(f"  3. Éditer {profile_dir / 'USER.md'} avec la personnalité du groupe")
    print(f"  4. Lancer le watcher : hivemind serve")
    print(f"  5. git push (si remote configuré)")
    print(f"  6. Les autres membres : hivemind join {org} --git-url <url>")


def _print_syncthing_instructions(org: str, profile_dir: Path):
    """Affiche les instructions pour configurer Syncthing."""
    memory_dir = profile_dir / "memory"

    print(f"""
{'─'*60}
  📡 Configuration Syncthing
{'─'*60}

  Dans l'interface Syncthing (http://localhost:8384) :

  1. Add Folder
     Folder ID    : hivemind-{org}-memory
     Folder Label : HiveMind {org} — Mémoire
     Folder Path  : {memory_dir}

  2. Partager avec les autres membres
     → Onglet Sharing → Ajouter leurs Device ID

  3. Sur les machines des membres :
     → Accepter le partage
     → Corriger le Path vers : {memory_dir}

  ⚠️  Le dossier memory/events/ est partagé.
  Le fichier consolidated.db est LOCAL (jamais sync).
""")


# ── Join ────────────────────────────────────────────────────────────

def cmd_join(org: str, git_url: str, skip_bootstrap: bool = False):
    """Rejoint un HiveMind existant."""
    profile_dir = PROFILES_DIR / org

    if profile_dir.exists():
        print(f"❌ Le dossier {profile_dir} existe déjà.")
        print(f"   Si vous avez déjà rejoint, utilisez 'hivemind status'.")
        sys.exit(1)

    print(f"\n🐝 Rejoindre le HiveMind : {org}")
    print(f"   Git remote : {git_url}")

    # ── Git clone ─────────────────────────────────────────────
    print(f"\n📦 Clonage du dépôt...")
    result = _run(["git", "clone", git_url, str(profile_dir)])
    if result.returncode != 0:
        print(f"❌ Échec du clone. Vérifiez l'URL et vos accès Git.")
        sys.exit(1)

    # ── Créer memory/events/ si absent ────────────────────────
    events_dir = profile_dir / "memory" / "events"
    events_dir.mkdir(parents=True, exist_ok=True)

    # ── .env ──────────────────────────────────────────────────
    env_example = profile_dir / ".env.example"
    env_path = profile_dir / ".env"
    if env_example.exists() and not env_path.exists():
        shutil.copy(env_example, env_path)
        print(f"\n🔑 .env créé — éditez-le avec vos clés API")

    # ── Bootstrap ─────────────────────────────────────────────
    if not skip_bootstrap:
        print(f"\n📤 Bootstrap : export de votre mémoire Mnemosyne existante...")
        bootstrap_script = PROTOTYPE_DIR / "hivemind_mnemosyne.py"
        if bootstrap_script.exists():
            agent_name = socket.gethostname()
            result = _run([
                sys.executable, str(bootstrap_script),
                "--agent", f"{agent_name}",
                "--events-dir", str(events_dir),
                "--db", str(profile_dir / "memory" / "consolidated.db"),
                "bootstrap",
            ])
            if result.returncode == 0:
                print(f"   ✅ Bootstrap terminé — vos mémoires rejoignent le groupe")
            else:
                print(f"   ⚠️  Bootstrap optionnel — vous pouvez le faire plus tard")
        else:
            print(f"   ⚠️  Script de bootstrap introuvable — ignoré")

    # ── Syncthing ─────────────────────────────────────────────
    _print_syncthing_instructions(org, profile_dir)

    # ── Résumé ────────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print(f"  ✅ Vous avez rejoint le HiveMind '{org}' !")
    print(f"{'─'*60}")
    print(f"\n  Prochaines étapes :")
    print(f"  1. Configurer Syncthing (voir ci-dessus)")
    print(f"  2. Éditer {profile_dir / '.env'} avec vos clés API")
    print(f"  3. Lancer le watcher : hivemind serve")


# ── Status ──────────────────────────────────────────────────────────

def cmd_status(org: str = None):
    """Affiche l'état du HiveMind."""
    if org:
        profile_dir = PROFILES_DIR / org
        if not profile_dir.exists():
            print(f"❌ Profil '{org}' introuvable")
            sys.exit(1)
        profiles = {org: profile_dir}
    else:
        if not PROFILES_DIR.exists():
            print("Aucun profil HiveMind trouvé.")
            return
        profiles = {
            d.name: d for d in PROFILES_DIR.iterdir()
            if d.is_dir() and (d / "memory").exists()
        }

    if not profiles:
        print("Aucun profil HiveMind trouvé.\nUtilisez 'hivemind init <org>' pour en créer un.")
        return

    for org, profile_dir in sorted(profiles.items()):
        print(f"\n🐝 HiveMind : {org}")
        print(f"   Dossier   : {profile_dir}")

        # Git
        git_dir = profile_dir / ".git"
        if git_dir.exists():
            remote = _run(["git", "-C", str(profile_dir), "remote", "get-url", "origin"],
                         capture=True)
            print(f"   Git       : {remote.stdout.strip() if remote.returncode == 0 else 'pas de remote'}")

        # Mémoire
        consolidated = profile_dir / "memory" / "consolidated.db"
        events_dir = profile_dir / "memory" / "events"
        if events_dir.exists():
            event_files = list(events_dir.glob("*.jsonl"))
            print(f"   Events    : {len(event_files)} journaux")
            for ef in event_files[:5]:
                lines = sum(1 for _ in open(ef))
                print(f"     {ef.name} ({lines} événements)")
            if len(event_files) > 5:
                print(f"     ... et {len(event_files) - 5} autres")

        # DB
        if consolidated.exists():
            import sqlite3
            conn = sqlite3.connect(str(consolidated))
            total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            conn.close()
            print(f"   Mémoires  : {total}")
        else:
            print(f"   Mémoires  : pas encore consolidé")

        # Watcher
        watcher_pid = _find_watcher(org)
        if watcher_pid:
            print(f"   Watcher   : ✅ actif (PID {watcher_pid})")
        else:
            print(f"   Watcher   : ❌ inactif → 'hivemind serve'")

    print()


def _find_watcher(org: str) -> str:
    """Trouve le PID du watcher pour un HiveMind donné."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", f"watcher.py.*{org}"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip().split("\n")[0]
    except FileNotFoundError:
        # pgrep pas dispo, fallback ps
        try:
            result = subprocess.run(
                ["ps", "aux"], capture_output=True, text=True
            )
            for line in result.stdout.splitlines():
                if "watcher.py" in line and org in line:
                    return line.split()[1]
        except Exception:
            pass
    return None


# ── Serve ───────────────────────────────────────────────────────────

def cmd_serve(org: str):
    """Démarre le watcher pour un HiveMind."""
    profile_dir = PROFILES_DIR / org
    if not profile_dir.exists():
        print(f"❌ Profil '{org}' introuvable. Faites 'hivemind init {org}' d'abord.")
        sys.exit(1)

    events_dir = profile_dir / "memory" / "events"
    events_dir.mkdir(parents=True, exist_ok=True)

    db_path = profile_dir / "memory" / "consolidated.db"
    watcher_script = PROTOTYPE_DIR / "watcher.py"

    if not watcher_script.exists():
        print(f"❌ watcher.py introuvable dans {PROTOTYPE_DIR}")
        sys.exit(1)

    pid = _find_watcher(org)
    if pid:
        print(f"⚠️  Un watcher est déjà actif pour '{org}' (PID {pid})")
        return

    print(f"🐝 Démarrage du watcher pour '{org}'...")
    print(f"   Events : {events_dir}")
    print(f"   DB     : {db_path}")
    print(f"   Logs   : {profile_dir / 'watcher.log'}")
    print()

    log_file = open(profile_dir / "watcher.log", "a")
    process = subprocess.Popen(
        [sys.executable, str(watcher_script),
         "--events-dir", str(events_dir),
         "--db", str(db_path),
         "--interval", "2",
         "--debounce", "3"],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    print(f"   ✅ Watcher démarré (PID {process.pid})")
    print(f"   Pour l'arrêter : kill {process.pid}")


# ── Helpers ─────────────────────────────────────────────────────────

def _run(cmd: list, cwd=None, capture=False):
    """Lance une commande, affiche la sortie."""
    try:
        if capture:
            return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
        else:
            return subprocess.run(cmd, cwd=cwd)
    except FileNotFoundError:
        print(f"   ⚠️  Commande introuvable : {' '.join(cmd)}")
        return subprocess.CompletedProcess(cmd, 1, "", "")


# ── CLI ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="HiveMind CLI — onboarding et gestion",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        Exemples :
          hivemind init cabinet-dupont
          hivemind join cabinet-dupont --git-url git@github.com:dupont/hivemind.git
          hivemind status
          hivemind serve cabinet-dupont
        """),
    )

    sub = parser.add_subparsers(dest="command")

    # init
    p_init = sub.add_parser("init", help="Créer un nouveau HiveMind")
    p_init.add_argument("org", help="Nom de l'organisation/groupe")
    p_init.add_argument("--git-url", help="URL du dépôt Git (optionnel)")

    # join
    p_join = sub.add_parser("join", help="Rejoindre un HiveMind existant")
    p_join.add_argument("org", help="Nom de l'organisation/groupe")
    p_join.add_argument("--git-url", required=True, help="URL du dépôt Git")
    p_join.add_argument("--skip-bootstrap", action="store_true",
                        help="Ne pas exporter les mémoires Mnemosyne existantes")

    # status
    p_status = sub.add_parser("status", help="Afficher l'état du HiveMind")
    p_status.add_argument("org", nargs="?", help="Nom du HiveMind (optionnel)")

    # serve
    p_serve = sub.add_parser("serve", help="Démarrer le watcher")
    p_serve.add_argument("org", help="Nom du HiveMind")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "init":
        cmd_init(args.org, args.git_url)
    elif args.command == "join":
        cmd_join(args.org, args.git_url, args.skip_bootstrap)
    elif args.command == "status":
        cmd_status(args.org)
    elif args.command == "serve":
        cmd_serve(args.org)


if __name__ == "__main__":
    main()
