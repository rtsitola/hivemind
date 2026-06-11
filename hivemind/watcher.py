#!/usr/bin/env python3
"""
HiveMind Watcher — détection de changements + merge automatique
================================================================

Surveille le dossier events/ et déclenche un merge dès qu'un nouveau
fichier .jsonl est ajouté ou modifié (ex: après une sync Syncthing).

UTILISATION :
  # Mode daemon (surveille en continu)
  python3 watcher.py --events-dir ./memory/events --db ./memory/consolidated.db

  # Mode oneshot (merge une fois et sort)
  python3 watcher.py --oneshot

  # Avec intervalle de poll personnalisé
  python3 watcher.py --interval 2.0 --debounce 3.0

FONCTIONNEMENT :
  1. Poll le dossier events/ toutes les N secondes
  2. Compare les tailles/timestamps des fichiers .jsonl
  3. Si changement détecté → reset le timer de debounce
  4. Après debounce (pas de nouveau changement pendant X secondes) → merge
  5. Log chaque merge avec stats
"""

import os
import sys
import time
import signal
import argparse
import subprocess
import fcntl
from pathlib import Path
from datetime import datetime


# ── Helpers ────────────────────────────────────────────────────────

def now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log(msg: str):
    print(f"[{now()}] {msg}", flush=True)


# ── File snapshot ───────────────────────────────────────────────────

class Snapshot:
    """État des fichiers .jsonl à un instant T."""

    def __init__(self, events_dir: str):
        self.events_dir = Path(events_dir)
        self.files: dict[str, tuple[int, float]] = {}  # path → (size, mtime)
        self.refresh()

    def refresh(self):
        """Re-scanne le dossier et met à jour l'état."""
        self.files = {}
        if not self.events_dir.exists():
            return
        for f in sorted(self.events_dir.glob("*.jsonl")):
            try:
                stat = f.stat()
                self.files[str(f)] = (stat.st_size, stat.st_mtime)
            except OSError:
                pass

    def has_changed(self) -> tuple[bool, list[str]]:
        """
        Compare avec l'état précédent.
        Returns (changed, list_of_new_or_modified_files)
        """
        old = self.files.copy()
        self.refresh()
        new = self.files

        changed = []

        # Nouveaux fichiers ou fichiers modifiés
        for path, (size, mtime) in new.items():
            if path not in old:
                changed.append(f"NEW: {os.path.basename(path)}")
            elif old[path] != (size, mtime):
                changed.append(f"MOD: {os.path.basename(path)}")

        # Fichiers supprimés (rare mais possible)
        for path in old:
            if path not in new:
                changed.append(f"DEL: {os.path.basename(path)}")

        return (len(changed) > 0, changed)


# ── Merge trigger ───────────────────────────────────────────────────

def run_merge(events_dir: str, db_path: str) -> bool:
    """
    Lance le merge engine.
    Returns True si succès.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    merge_script = os.path.join(script_dir, "merge_engine.py")

    if not os.path.exists(merge_script):
        log(f"⚠️  Merge engine introuvable: {merge_script}")
        return False

    try:
        result = subprocess.run(
            [sys.executable, merge_script, "--events-dir", events_dir, "--db", db_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            # Extraire le résumé
            for line in result.stdout.splitlines():
                if "Merge terminé" in line or "Événements" in line or "mémoires" in line:
                    log(f"   {line.strip()}")
            return True
        else:
            log(f"⚠️  Merge failed: {result.stderr[:200]}")
            return False
    except subprocess.TimeoutExpired:
        log("⚠️  Merge timeout")
        return False
    except Exception as e:
        log(f"⚠️  Merge error: {e}")
        return False


# ── Watcher ─────────────────────────────────────────────────────────

def watch(
    events_dir: str,
    db_path: str,
    interval: float = 1.0,
    debounce: float = 2.0,
    oneshot: bool = False,
):
    """
    Boucle principale.

    Args:
        events_dir: dossier à surveiller
        db_path: chemin de la DB consolidée
        interval: délai entre deux polls (secondes)
        debounce: délai sans changement avant merge (secondes)
        oneshot: merge une fois et sort (ignore le watch)
    """
    os.makedirs(events_dir, exist_ok=True)

    if oneshot:
        log(f"Mode ONESHOT — merge unique sur {events_dir}")
        run_merge(events_dir, db_path)
        return

    log(f"👁️  Watcher démarré")
    log(f"   Dossier   : {os.path.abspath(events_dir)}")
    log(f"   DB        : {os.path.abspath(db_path)}")
    log(f"   Poll      : toutes les {interval}s")
    log(f"   Debounce  : {debounce}s")
    log(f"   PID       : {os.getpid()}")

    snap = Snapshot(events_dir)
    last_change: float | None = None
    merge_count = 0

    # Verrou pour éviter deux watchers sur la même DB
    lock_path = db_path + ".watcher.lock"
    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log(f"⚠️  Un autre watcher est déjà actif sur {db_path}")
        lock_fd.close()
        return

    # Graceful shutdown
    running = True

    def shutdown(sig, frame):
        nonlocal running
        log(f"\n🛑 Signal reçu, arrêt... ({merge_count} merges effectués)")
        running = False

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    while running:
        try:
            changed, details = snap.has_changed()

            if changed:
                last_change = time.time()
                for d in details[:5]:  # max 5 détails par tick
                    log(f"   🔄 {d}")
                if len(details) > 5:
                    log(f"   ... et {len(details) - 5} autres changements")

            # Si un changement a eu lieu et que le debounce est écoulé
            if last_change and (time.time() - last_change) >= debounce:
                log(f"⚡ Merge #{(merge_count + 1)} déclenché")
                if run_merge(events_dir, db_path):
                    merge_count += 1
                last_change = None  # reset

            time.sleep(interval)

        except Exception as e:
            log(f"⚠️  Erreur: {e}")
            time.sleep(interval)

    log(f"Watcher arrêté — {merge_count} merges effectués")
    fcntl.flock(lock_fd, fcntl.LOCK_UN)
    lock_fd.close()


# ── CLI ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="HiveMind Watcher — merge automatique sur changement"
    )
    parser.add_argument(
        "--events-dir", default="./memory/events",
        help="Dossier à surveiller (défaut: ./memory/events)"
    )
    parser.add_argument(
        "--db", default="./memory/consolidated.db",
        help="DB consolidée (défaut: ./memory/consolidated.db)"
    )
    parser.add_argument(
        "--interval", type=float, default=1.0,
        help="Intervalle de poll en secondes (défaut: 1.0)"
    )
    parser.add_argument(
        "--debounce", type=float, default=2.0,
        help="Délai sans changement avant merge (défaut: 2.0)"
    )
    parser.add_argument(
        "--oneshot", action="store_true",
        help="Merge une fois et sort (pas de watch continu)"
    )

    args = parser.parse_args()
    watch(
        events_dir=args.events_dir,
        db_path=args.db,
        interval=args.interval,
        debounce=args.debounce,
        oneshot=args.oneshot,
    )


if __name__ == "__main__":
    main()
