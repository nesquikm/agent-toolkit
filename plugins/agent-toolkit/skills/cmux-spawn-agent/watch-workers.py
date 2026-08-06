#!/usr/bin/env python3
"""Emit one line per spawned-worker turn end. Stdin: `cmux events` NDJSON.

    cmux events --category agent --no-heartbeat --reconnect \
    | python3 -u watch-workers.py "$LEDGER"

argv[1] = the run's ledger (tsv: sid, surface_uuid, pane_uuid, name, status).

Only session ids present in the ledger are reported. That filter is not an
optimisation -- the cmux event bus is global across workspaces and both config
profiles, so an unfiltered watcher fires on the user's own tabs and on the
orchestrator's own turns.

The ledger is re-read when it changes, so workers spawned after the watcher was
armed are picked up without restarting it.
"""
import json
import os
import sys
from datetime import datetime

LEDGER = sys.argv[1]

# Stop  -> the turn ended.
# PermissionRequest -> blocked mid-turn, NOT an ending.
# SessionEnd -> the worker is gone.
# agent.hook.Notification is deliberately absent: it also fires on every normal
# finish, so it cannot mean "needs attention".
WANTED = {
    "agent.hook.Stop": ("DONE", "turn ended"),
    "agent.hook.PermissionRequest": ("ATTN", "blocked on permission"),
    "agent.hook.SessionEnd": ("EXIT", "session ended"),
}

_cache = (None, {})


def rows():
    """sid -> name, reloaded only when the ledger changes."""
    global _cache
    try:
        stamp = os.stat(LEDGER).st_mtime_ns
    except OSError:
        return {}
    if stamp == _cache[0]:
        return _cache[1]
    out = {}
    with open(LEDGER) as fh:
        for line in fh:
            f = line.rstrip("\n").split("\t")
            if len(f) >= 4 and f[0]:
                out["claude-" + f[0]] = f[3]
    _cache = (stamp, out)
    return out


def ts(s):
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


last = {}
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        event = json.loads(line)
    except ValueError:
        continue
    if event.get("type") != "event":
        continue
    kind = WANTED.get(event.get("name", ""))
    if not kind:
        continue
    sid = (event.get("payload") or {}).get("session_id")
    if not sid:
        continue
    known = rows()
    if sid not in known:
        continue

    # cmux delivers each hook as a received/completed pair, and again ~275ms
    # later once the surface resolves -- 4 frames per turn end. Collapse any
    # repeat of the same (sid, name) inside a 5s window.
    key = (sid, event["name"])
    when = ts(event.get("occurred_at"))
    if when - last.get(key, 0.0) < 5.0:
        continue
    last[key] = when

    tag, what = kind
    print(f"{tag}  {known[sid]}  ({sid[7:]}) {what}", flush=True)
