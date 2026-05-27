#!/usr/bin/env python3
"""
HiveMind Inbox Writer — Phase 2
================================

Envoie un message d'un cluster à un autre (cross-cluster communication).

ARCHITECTURE :
  ┌──────────────────┐         ┌──────────────────────┐
  │  Cluster AUDIT   │         │  Dossier partagé      │
  │                  │         │  hivemind-cross/      │
  │  inbox_writer.py │────────►│  fiscal/              │
  │  --to fiscal     │         │    from-audit.jsonl   │── Syncthing ──►
  │                  │         │                       │    Cluster
  └──────────────────┘         └──────────────────────┘    FISCAL
                                                           le lit

FORMAT D'UN MESSAGE :
  {
    "op": "message",
    "id": "msg-abc123",
    "agent": "cluster:audit",
    "ts": "2026-05-26T12:00:00Z",
    "payload": {
      "to_cluster": "fiscal",
      "from_cluster": "audit",
      "from_agent": "alice",
      "content": "Ce montage est-il conforme à la convention fiscale ?",
      "priority": "normal",
      "scope": "cross-cluster",
      "reply_to": "msg-xyz"   // optionnel
    }
  }

Le cluster destinataire lit ce fichier via son merge engine local.
Le scope "cross-cluster" le distingue des mémoires locales.

USAGE (CLI) :
  python3 inbox_writer.py \
    --from audit --from-agent alice \
    --to fiscal \
    --cross-dir ./cross-cluster \
    "Ce montage est-il conforme ?"

USAGE (module) :
  from inbox_writer import send_message
  send_message(cross_dir, from_cluster, "alice", to_cluster, "Message...")
"""

import json
import os
import sys
import uuid
import argparse
from datetime import datetime, timezone
from pathlib import Path


# ── Helpers ────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _msg_id() -> str:
    return f"msg-{uuid.uuid4().hex[:8]}"


# ── Send ────────────────────────────────────────────────────────────

def send_message(
    cross_dir: str,
    from_cluster: str,
    from_agent: str,
    to_cluster: str,
    content: str,
    priority: str = "normal",
    reply_to: str = None,
) -> str:
    """
    Envoie un message d'un cluster à un autre.

    Écrit dans : <cross_dir>/<to_cluster>/from-<from_cluster>.jsonl

    Args:
        cross_dir: Dossier partagé inter-cluster
        from_cluster: Cluster expéditeur (ex: "audit")
        from_agent: Agent expéditeur (ex: "alice")
        to_cluster: Cluster destinataire (ex: "fiscal")
        content: Contenu du message
        priority: "low", "normal", "high", "urgent"
        reply_to: ID du message auquel on répond (optionnel)

    Returns: message_id
    """
    cross_path = Path(cross_dir)

    # Dossier cible : <cross_dir>/fiscal/from-audit.jsonl
    target_dir = cross_path / to_cluster
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / f"from-{from_cluster}.jsonl"

    msg_id = _msg_id()
    message = {
        "op": "message",
        "id": msg_id,
        "agent": f"cluster:{from_cluster}",
        "ts": _now_iso(),
        "payload": {
            "to_cluster": to_cluster,
            "from_cluster": from_cluster,
            "from_agent": from_agent,
            "content": content,
            "priority": priority,
            "scope": "cross-cluster",
        },
    }

    if reply_to:
        message["payload"]["reply_to"] = reply_to

    with open(target_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(message, ensure_ascii=False) + "\n")

    return msg_id


# ── Read inbox ──────────────────────────────────────────────────────

def read_inbox(
    cross_dir: str,
    cluster_name: str,
    unread_only: bool = False,
) -> list[dict]:
    """
    Lit les messages reçus par un cluster.

    Lit : <cross_dir>/<cluster_name>/from-*.jsonl

    Args:
        cross_dir: Dossier partagé inter-cluster
        cluster_name: Nom du cluster qui lit ses messages
        unread_only: Si True, seulement les messages non lus

    Returns: liste de messages
    """
    inbox_dir = Path(cross_dir) / cluster_name

    if not inbox_dir.exists():
        return []

    messages = []
    for f in sorted(inbox_dir.glob("from-*.jsonl")):
        with open(f, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    messages.append(msg)
                except json.JSONDecodeError:
                    pass

    messages.sort(key=lambda m: m.get("ts", ""))
    return messages


def read_inbox_summary(cross_dir: str, cluster_name: str) -> dict:
    """
    Résumé de l'inbox : combien de messages, de qui, priorités.
    """
    messages = read_inbox(cross_dir, cluster_name)

    if not messages:
        return {"total": 0, "by_from": {}, "by_priority": {}, "unread": 0}

    by_from = {}
    by_priority = {}
    unread = 0

    for msg in messages:
        p = msg.get("payload", {})
        from_cluster = p.get("from_cluster", "unknown")
        priority = p.get("priority", "normal")

        by_from[from_cluster] = by_from.get(from_cluster, 0) + 1
        by_priority[priority] = by_priority.get(priority, 0) + 1

        if not p.get("read_at"):
            unread += 1

    return {
        "total": len(messages),
        "by_from": by_from,
        "by_priority": by_priority,
        "unread": unread,
        "latest_ts": messages[-1].get("ts") if messages else None,
    }


# ── CLI ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="HiveMind Inbox — communication inter-cluster"
    )
    parser.add_argument(
        "--cross-dir", default="./cross-cluster",
        help="Dossier partagé inter-cluster (défaut: ./cross-cluster)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # send
    p_send = sub.add_parser("send", help="Envoyer un message à un autre cluster")
    p_send.add_argument("--from-cluster", required=True, help="Cluster expéditeur")
    p_send.add_argument("--from-agent", default="unknown", help="Agent expéditeur")
    p_send.add_argument("--to", required=True, help="Cluster destinataire")
    p_send.add_argument("--priority", choices=["low", "normal", "high", "urgent"],
                        default="normal")
    p_send.add_argument("--reply-to", help="ID du message auquel répondre")
    p_send.add_argument("content", help="Contenu du message")

    # read
    p_read = sub.add_parser("read", help="Lire les messages reçus")
    p_read.add_argument("--cluster", required=True, help="Cluster qui lit")
    p_read.add_argument("--summary", action="store_true", help="Résumé uniquement")

    args = parser.parse_args()

    if args.command == "send":
        msg_id = send_message(
            cross_dir=args.cross_dir,
            from_cluster=args.from_cluster,
            from_agent=args.from_agent,
            to_cluster=args.to,
            content=args.content,
            priority=args.priority,
            reply_to=args.reply_to,
        )
        print(json.dumps({
            "sent": True,
            "message_id": msg_id,
            "from": f"cluster:{args.from_cluster}",
            "to": f"cluster:{args.to}",
        }, indent=2))

    elif args.command == "read":
        if args.summary:
            summary = read_inbox_summary(args.cross_dir, args.cluster)
            print(json.dumps(summary, indent=2))
        else:
            messages = read_inbox(args.cross_dir, args.cluster)
            print(json.dumps(messages, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
