#!/usr/bin/env python3
"""
Event Writer — simule l'écriture d'événements mémoire par un agent.

Usage:
    python3 event_writer.py --agent alice \
        remember "Client Omega : toujours demander le cash-flow" --importance 0.8

    python3 event_writer.py --agent bob \
        forget --memory-id mem-abc123
"""

import json
import os
import argparse
# datetime now imported via hivemind_common


# Imported from hivemind_common below
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hivemind_common import now_iso, event_id


def write_event(events_dir: str, agent: str, event: dict):
    """Ajoute un événement au journal de l'agent."""
    filepath = os.path.join(events_dir, f"{agent}.jsonl")
    os.makedirs(events_dir, exist_ok=True)

    with open(filepath, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")

    print(f"✅ Écrit dans {filepath}")
    print(f"   {json.dumps(event, indent=2, ensure_ascii=False)}")


def cmd_remember(args):
    event = {
        "op": "remember",
        "id": event_id(),
        "agent": args.agent,
        "ts": now_iso(),
        "payload": {
            "content": args.content,
            "importance": args.importance,
            "source": args.source,
            "scope": args.scope,
        },
    }
    write_event(args.events_dir, args.agent, event)


def cmd_update(args):
    event = {
        "op": "update",
        "id": event_id(),
        "agent": args.agent,
        "ts": now_iso(),
        "payload": {
            "memory_id": args.memory_id,
        },
    }
    if args.content:
        event["payload"]["content"] = args.content
    if args.importance is not None:
        event["payload"]["importance"] = args.importance
    if args.source:
        event["payload"]["source"] = args.source
    if args.scope:
        event["payload"]["scope"] = args.scope

    write_event(args.events_dir, args.agent, event)


def cmd_forget(args):
    event = {
        "op": "forget",
        "id": event_id(),
        "agent": args.agent,
        "ts": now_iso(),
        "payload": {
            "memory_id": args.memory_id,
        },
    }
    write_event(args.events_dir, args.agent, event)


def main():
    parser = argparse.ArgumentParser(description="HiveMind Event Writer")
    parser.add_argument(
        "--agent", required=True,
        help="Nom de l'agent qui écrit (ex: alice, bob, desktop-tsitola)"
    )
    parser.add_argument(
        "--events-dir", default="./memory/events",
        help="Dossier des journaux d'événements"
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # remember
    p_rem = sub.add_parser("remember", help="Ajouter une mémoire")
    p_rem.add_argument("content", help="Contenu de la mémoire")
    p_rem.add_argument("--importance", type=float, default=0.5,
                       help="Importance (0.0-1.0, défaut: 0.5)")
    p_rem.add_argument("--source", default="manual",
                       help="Source (défaut: manual)")
    p_rem.add_argument("--scope", default="shared",
                       help="Scope (défaut: shared)")
    p_rem.set_defaults(func=cmd_remember)

    # update
    p_upd = sub.add_parser("update", help="Modifier une mémoire existante")
    p_upd.add_argument("--memory-id", required=True,
                       help="ID de la mémoire à modifier")
    p_upd.add_argument("--content",
                       help="Nouveau contenu")
    p_upd.add_argument("--importance", type=float,
                       help="Nouvelle importance")
    p_upd.add_argument("--source",
                       help="Nouvelle source")
    p_upd.add_argument("--scope",
                       help="Nouveau scope")
    p_upd.set_defaults(func=cmd_update)

    # forget
    p_fgt = sub.add_parser("forget", help="Supprimer une mémoire")
    p_fgt.add_argument("--memory-id", required=True,
                       help="ID de la mémoire à supprimer")
    p_fgt.set_defaults(func=cmd_forget)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
