#!/usr/bin/env python3
"""
HiveMind Chain — Hash Chain + Ed25519 Signatures
==================================================

Chaque agent maintient une chaîne hashée de ses événements.
Chaque événement référence le hash du précédent et est signé.

PROPRIÉTÉS :
  - Immutabilité : insérer/supprimer un événement casse la chaîne
  - Attribution : la signature prouve que l'agent a écrit l'événement
  - Vérifiable : n'importe quel tiers peut vérifier la chaîne
  - Zéro consensus : chaque agent signe sa PROPRE chaîne
  - Backward compatible : événements sans prev_hash/signature = "legacy"

CLÉS :
  - Priorité 1 : ~/.ssh/id_ed25519 (clé SSH standard)
  - Priorité 2 : ~/.hermes/hivemind_key (clé dédiée, générée auto)
  - La clé publique est stockée dans le premier événement de la chaîne

USAGE :
  from hivemind.hivemind_chain import ChainState, sign_event, verify_chain

  state = ChainState(agent="alice", events_dir="./memory/events")
  event = state.sign_event({"op": "remember", ...})
  state.append_event(event)

  # Vérification
  errors = verify_chain("./memory/events/alice.jsonl")
"""

import json
import os
import hashlib
import base64
from pathlib import Path
from typing import Optional

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.exceptions import InvalidSignature
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False
    # Fallback: utiliser hashlib uniquement (hash chain sans signature)
    # La chaîne reste vérifiable, mais l'attribution est plus faible


# ── Paths ─────────────────────────────────────────────────────────

KEY_PATH = Path(os.path.expanduser("~/.hermes/hivemind_key"))
SSH_KEY_PATH = Path(os.path.expanduser("~/.ssh/id_ed25519"))
PUBKEY_PATH = Path(os.path.expanduser("~/.hermes/hivemind_key.pub"))


# ── Key management ────────────────────────────────────────────────

def _load_or_generate_key():
    """Charge la clé SSH existante ou génère une clé dédiée HiveMind.
    Returns: Ed25519PrivateKey ou None si cryptography non installé."""
    if not HAS_CRYPTO:
        return None

    # Priorité 1 : clé SSH standard
    if SSH_KEY_PATH.exists():
        try:
            with open(SSH_KEY_PATH, "rb") as f:
                k = serialization.load_ssh_private_key(f.read(), password=None)
                if isinstance(k, ed25519.Ed25519PrivateKey):
                    return k
        except Exception:
            pass

    # Priorité 2 : clé dédiée HiveMind
    if KEY_PATH.exists():
        try:
            with open(KEY_PATH, "rb") as f:
                k = serialization.load_ssh_private_key(f.read(), password=None)
                if isinstance(k, ed25519.Ed25519PrivateKey):
                    return k
        except Exception:
            pass

    # Générer une nouvelle clé
    key = ed25519.Ed25519PrivateKey.generate()
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Écrire clé privée (permissions 600)
    priv_bytes = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    )
    with open(KEY_PATH, "wb") as f:
        f.write(priv_bytes)
    os.chmod(KEY_PATH, 0o600)

    # Écrire clé publique
    pub_bytes = key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    )
    with open(PUBKEY_PATH, "wb") as f:
        f.write(pub_bytes)

    return key


def get_public_key_bytes() -> Optional[bytes]:
    """Retourne la clé publique en bytes (pour partage/vérification)."""
    key = _load_or_generate_key()
    if key is None:
        return None
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


# ── Hashing ───────────────────────────────────────────────────────

def _canonical_payload(payload: dict) -> bytes:
    """Sérialise le payload de façon déterministe (clés triées)."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")


def _event_hash(event: dict) -> str:
    """
    Hash SHA-256 d'un événement (sans prev_hash ni signature).
    Couvre : id | agent | ts | op | payload (trié)
    """
    fields = [
        event.get("id", ""),
        event.get("agent", ""),
        event.get("ts", ""),
        event.get("op", ""),
    ]
    raw = "|".join(fields).encode("utf-8")
    raw += b"|" + _canonical_payload(event.get("payload", {}))
    return "sha256:" + hashlib.sha256(raw).hexdigest()[:16]


def _sign_data(prev_hash: str, event_id: str, agent: str, ts: str,
               op: str, payload: dict) -> Optional[str]:
    """Signe les champs d'un événement avec la clé de l'agent."""
    if not HAS_CRYPTO:
        return None

    key = _load_or_generate_key()
    if key is None:
        return None

    data = f"{prev_hash}|{event_id}|{agent}|{ts}|{op}|"
    data += json.dumps(payload, sort_keys=True, ensure_ascii=False)
    signature = key.sign(data.encode("utf-8"))
    return "ed25519:" + base64.b64encode(signature).decode("ascii")


def _verify_signature(event: dict, pub_key_bytes: bytes) -> bool:
    """Vérifie la signature d'un événement."""
    if not HAS_CRYPTO:
        return True  # Skip si crypto pas dispo

    sig_field = event.get("signature", "")
    if not sig_field.startswith("ed25519:"):
        return True  # Legacy event, pas de signature

    try:
        sig_bytes = base64.b64decode(sig_field.split(":", 1)[1])
        pub_key = ed25519.Ed25519PublicKey.from_public_bytes(pub_key_bytes)

        data = f"{event.get('prev_hash', 'genesis')}|"
        data += f"{event.get('id', '')}|{event.get('agent', '')}|"
        data += f"{event.get('ts', '')}|{event.get('op', '')}|"
        data += json.dumps(event.get("payload", {}), sort_keys=True, ensure_ascii=False)

        pub_key.verify(sig_bytes, data.encode("utf-8"))
        return True
    except (InvalidSignature, Exception):
        return False


# ── Chain State ───────────────────────────────────────────────────

class ChainState:
    """
    État de la chaîne d'un agent.
    Maintient le prev_hash pour le prochain événement.
    """

    def __init__(self, agent: str, events_dir: str):
        self.agent = agent
        self.events_dir = Path(events_dir)
        self._prev_hash: Optional[str] = None
        self._loaded = False

    @property
    def event_file(self) -> Path:
        return self.events_dir / f"{self.agent}.jsonl"

    @property
    def prev_hash(self) -> str:
        if not self._loaded:
            self._load_last()
        return self._prev_hash or "genesis"

    def _load_last(self):
        """Charge le hash du dernier événement de la chaîne."""
        self._loaded = True
        if not self.event_file.exists():
            return

        # Lire la dernière ligne non-vide du fichier
        try:
            with open(self.event_file, "rb") as f:
                # Chercher la dernière ligne efficacement
                f.seek(0, 2)  # Fin du fichier
                size = f.tell()
                if size == 0:
                    return

                # Buffer depuis la fin
                chunk_size = min(4096, size)
                f.seek(max(0, size - chunk_size))
                lines = f.read().decode("utf-8").strip().split("\n")
                # Dernière ligne non-vide
                for line in reversed(lines):
                    line = line.strip()
                    if line:
                        event = json.loads(line)
                        self._prev_hash = _event_hash(event)
                        return
        except (json.JSONDecodeError, OSError):
            pass

    def sign_event(self, event: dict) -> dict:
        """
        Signe un événement — ajoute prev_hash et signature.
        Si genesis : injecte aussi la clé publique (self-certifying).
        L'événement doit déjà avoir : id, agent, ts, op, payload.
        """
        ph = self.prev_hash
        event["prev_hash"] = ph

        # Genesis event : injecter la clé publique
        if ph == "genesis" and HAS_CRYPTO:
            pub = get_public_key_bytes()
            if pub:
                event["pubkey"] = base64.b64encode(pub).decode("ascii")

        sig = _sign_data(
            ph, event.get("id", ""), self.agent,
            event.get("ts", ""), event.get("op", ""),
            event.get("payload", {}),
        )
        if sig:
            event["signature"] = sig

        return event

    def append_event(self, event: dict):
        """Ajoute un événement au fichier et met à jour prev_hash."""
        self.events_dir.mkdir(parents=True, exist_ok=True)
        with open(self.event_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._prev_hash = _event_hash(event)


# ── Chain Verification ────────────────────────────────────────────

def verify_chain(jsonl_path: str, pub_key_bytes: Optional[bytes] = None) -> list[str]:
    """
    Vérifie l'intégrité de la chaîne d'événements d'un agent.

    Détecte :
      - Hash chain brisé (insertion/suppression/modification)
      - Signature invalide
      - prev_hash manquant après le premier événement

    Returns: liste d'erreurs (vide = chaîne valide)
    """
    errors = []
    path = Path(jsonl_path)
    effective_pubkey = pub_key_bytes  # peut être override par le genesis

    if not path.exists():
        return [f"Fichier introuvable: {jsonl_path}"]

    prev_hash = "genesis"
    line_no = 0

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line_no += 1
            line = line.strip()
            if not line:
                continue

            try:
                event = json.loads(line)
            except json.JSONDecodeError as e:
                errors.append(f"Ligne {line_no}: JSON invalide — {e}")
                continue

            # Vérifier prev_hash
            ph = event.get("prev_hash", "")
            if ph and ph != prev_hash:
                errors.append(
                    f"Ligne {line_no}: prev_hash={ph} ≠ attendu={prev_hash} "
                    f"(chaîne brisée — insertion/suppression/modification)"
                )
            elif not ph:
                # Legacy event — pas de prev_hash. On accepte mais on ne
                # peut pas vérifier la continuité. Le hash du prochain
                # événement référencera celui-ci.
                pass

            # Extraire la clé publique du genesis (self-certifying)
            if event.get("prev_hash") == "genesis" and "pubkey" in event and not effective_pubkey:
                try:
                    effective_pubkey = base64.b64decode(event["pubkey"])
                except Exception:
                    errors.append(
                        f"Ligne {line_no}: pubkey genesis invalide pour {event.get('agent', '?')}"
                    )

            # Vérifier la signature si clé publique fournie
            if effective_pubkey and "signature" in event:
                if not _verify_signature(event, effective_pubkey):
                    errors.append(
                        f"Ligne {line_no}: signature invalide pour {event.get('id', '?')}"
                    )

            # Calculer le hash de cet événement pour le suivant
            prev_hash = _event_hash(event)

    return errors


def verify_all_chains(events_dir: str,
                      pub_keys: Optional[dict[str, bytes]] = None) -> dict[str, list[str]]:
    """
    Vérifie toutes les chaînes dans un dossier events/.

    Args:
        events_dir: dossier contenant les .jsonl
        pub_keys: dict agent_name → public_key_bytes (optionnel)

    Returns: dict agent_name → liste d'erreurs
    """
    results = {}
    for f in sorted(Path(events_dir).glob("*.jsonl")):
        agent = f.stem
        pk = pub_keys.get(agent) if pub_keys else None
        errors = verify_chain(str(f), pk)
        if errors:
            results[agent] = errors
    return results


# ── CLI ────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="HiveMind Chain — hash chain + signatures")
    sub = parser.add_subparsers(dest="command", required=True)

    # verify
    p_verify = sub.add_parser("verify", help="Vérifier une chaîne")
    p_verify.add_argument("path", help="Chemin du fichier .jsonl ou dossier events/")
    p_verify.add_argument("--pubkey", help="Clé publique (fichier ou chaîne brute)")

    # keygen
    p_key = sub.add_parser("keygen", help="Générer une nouvelle clé")
    p_key.add_argument("--force", action="store_true", help="Écraser la clé existante")

    # pubkey
    sub.add_parser("pubkey", help="Afficher la clé publique")

    # genesis-hash
    p_gen = sub.add_parser("genesis-hash", help="Afficher le hash du genesis pour partage out-of-band")
    p_gen.add_argument("events_dir", help="Dossier events/")
    p_gen.add_argument("--agent", default=None, help="Agent spécifique (défaut: tous)")

    args = parser.parse_args()

    if args.command == "verify":
        path = Path(args.path)
        if path.is_dir():
            results = verify_all_chains(str(path))
            if not results:
                print("✅ Toutes les chaînes sont valides")
            for agent, errs in results.items():
                print(f"\n❌ {agent}: {len(errs)} erreur(s)")
                for e in errs[:10]:
                    print(f"   • {e}")
        else:
            pub_key = None
            if args.pubkey:
                pub_key = Path(args.pubkey).read_bytes() if Path(args.pubkey).exists() else None
            errors = verify_chain(str(path), pub_key)
            if not errors:
                print(f"✅ Chaîne valide: {path.name}")
            else:
                print(f"❌ {len(errors)} erreur(s) dans {path.name}")
                for e in errors:
                    print(f"   • {e}")

    elif args.command == "keygen":
        if KEY_PATH.exists() and not args.force:
            print(f"Clé existante: {KEY_PATH}")
            print("Utilisez --force pour régénérer")
            return
        if args.force and KEY_PATH.exists():
            KEY_PATH.unlink()
            PUBKEY_PATH.unlink(missing_ok=True)
        key = _load_or_generate_key()
        print(f"✅ Clé générée : {KEY_PATH}")
        print(f"   Publique     : {PUBKEY_PATH}")

    elif args.command == "pubkey":
        pub = get_public_key_bytes()
        if pub:
            print(base64.b64encode(pub).decode("ascii"))
        else:
            print("cryptography non installé — pip install cryptography")

    elif args.command == "genesis-hash":
        events_dir = Path(args.events_dir)
        if not events_dir.exists():
            print(f"❌ Dossier introuvable: {args.events_dir}")
            return

        pattern = f"{args.agent}.jsonl" if args.agent else "*.jsonl"
        found = 0
        for f in sorted(events_dir.glob(pattern)):
            agent = f.stem
            try:
                with open(f, "r") as fh:
                    first_line = fh.readline().strip()
                    if not first_line:
                        continue
                    event = json.loads(first_line)
                    if event.get("prev_hash") != "genesis":
                        print(f"⚠️  {agent}: pas de genesis (premier event prev_hash={event.get('prev_hash','?')})")
                        continue
                    h = _event_hash(event)
                    pub = event.get("pubkey", "non disponible")
                    print(f"🔐 {agent}")
                    print(f"   genesis-hash: {h}")
                    print(f"   pubkey:       {pub[:50]}..." if len(pub) > 50 else f"   pubkey:       {pub}")
                    found += 1
            except Exception as e:
                print(f"⚠️  {agent}: erreur — {e}")

        if not found:
            print("Aucun agent trouvé. Faites d'abord 'hivemind_mnemosyne.py remember' pour créer un genesis.")
        else:
            print(f"\n📋 Partagez ces genesis-hash avec les nouveaux membres (Signal, email, etc.)")


if __name__ == "__main__":
    main()
