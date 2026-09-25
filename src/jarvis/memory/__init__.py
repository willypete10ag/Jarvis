"""Durable memory for Jarvis: the task store and its audit log.

The design goal here is simple and non-negotiable: **a task you give Jarvis does
not disappear.** That is enforced three ways:

1. SQLite in WAL mode  -> crash-resilient structured storage (`jarvis.db`).
2. An append-only event log -> every change is recorded and never overwritten.
3. A markdown mirror + rolling backups -> a human-readable copy you can open
   with no software running, plus timestamped DB snapshots.
"""
