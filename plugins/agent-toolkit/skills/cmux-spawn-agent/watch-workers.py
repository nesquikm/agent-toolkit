#!/usr/bin/env python3
"""Emit one line per spawned-worker turn end or block. Stdin: `cmux events` NDJSON.

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
import time
from datetime import datetime

LEDGER = sys.argv[1]

# A turn that ENDS. Each of these arrives as several frames -- see the collapse
# window below.
#
# Stop means "the turn ended", which is NOT the same as "the work is finished":
# a worker that parks awaiting its own background sub-agent ends its turn too.
ENDINGS = {
    "agent.hook.Stop": ("DONE", "turn ended"),
    "agent.hook.SessionEnd": ("EXIT", "session ended"),
}

# A turn that SUSPENDS, waiting on a human. These do not end the turn, so no
# Stop follows -- watching only for endings is total deafness to them, which is
# the bug this file was rewritten to fix (verified 2026-08-07: a worker sat on
# AskUserQuestion through two consecutive blocks and the watcher said nothing).
#
# cmux gives AskUserQuestion its own event name and routes every other tool's
# approval through agent.hook.PermissionRequest. Both were observed live:
#   agent.hook.AskUserQuestion  tool_name=AskUserQuestion
#   agent.hook.PermissionRequest tool_name=Bash
# Both carry _opencode_request_id "<sid>-PermissionRequest-<Tool>-<ms>".
BLOCKS = {
    "agent.hook.AskUserQuestion": ("ASK", "blocked asking a question"),
    "agent.hook.ExitPlanMode": ("ATTN", "blocked on plan approval"),
    "agent.hook.PermissionRequest": ("ATTN", "blocked on permission"),
}

# Fallback for workers launched with --dangerously-skip-permissions. cmux's own
# wrapper documents that under permission_mode "bypassPermissions" Claude "fires
# no PermissionRequest/Notification" for AskUserQuestion and ExitPlanMode, and
# that PreToolUse is the needs-input fallback there. Every name in BLOCKS is
# carried on that same PermissionRequest hook, so without this a bypass worker is
# silent again -- exactly the bug above, one flag away.
#
# UNPROVEN -- do not upgrade this to "covered" without testing it. Spawning with
# that flag was refused in this environment, so this path rests on the wrapper's
# comments and has never been exercised against a live bypass worker. What IS
# verified is only that it stays inert when it is not needed: in normal mode the
# authoritative event arrives ~17 ms first and suppresses this one.
BLOCKING_TOOLS = {
    "AskUserQuestion": ("ASK", "blocked asking a question"),
    "ExitPlanMode": ("ATTN", "blocked on plan approval"),
}

# agent.hook.Notification is deliberately absent: it fires ~6.3s after a block
# AND after a normal finish, so on its own it cannot mean "needs attention".
# The two BLOCKS names above are what make the distinction, so nothing is lost.

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
    """Event time in epoch seconds, falling back to arrival time.

    The fallback must stay on the same scale as the parsed value, because both
    land in `last` and are subtracted from each other -- so time.time(), not
    time.monotonic().

    Returning a constant here (as this did) is not a degraded mode, it is total
    deafness: `when - last.get(key, 0.0)` becomes 0.0 - 0.0 for the first event
    of every key, which is < 5.0, so the collapse window swallows it and every
    event after it. A watcher that reports nothing looks exactly like a run with
    nothing to report.
    """
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return time.time()


last = {}      # (sid, name) -> when, for the multi-frame endings
seen = {}      # request id -> None, for the single-frame blocks
blocked = {}   # (sid, tool) -> when an authoritative block was reported


def repeat(rid):
    """True if this exact hook occurrence was already reported.

    Blocks arrive as ONE frame carrying a request id unique to that occurrence,
    so identity is the right dedupe key for them and a time window is not: two
    questions eight seconds apart are two questions, and a window wide enough to
    swallow the second is the same silent deafness in a smaller costume.
    """
    if rid in seen:
        return True
    seen[rid] = None
    if len(seen) > 512:                      # bound it; a long run blocks a lot
        for k in list(seen)[:256]:
            del seen[k]
    return False


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
    name = event.get("name", "")
    payload = event.get("payload") or {}
    sid = payload.get("session_id")
    if not sid:
        continue
    known = rows()
    if sid not in known:
        continue

    rid = payload.get("_opencode_request_id") or ""
    tool = payload.get("tool_name") or ""

    when = ts(event.get("occurred_at"))
    # Everything cmux routes through the PermissionRequest hook, whatever it
    # named the event. This is the signal of record for a block.
    authoritative = name in BLOCKS or "-PermissionRequest-" in rid

    if name in BLOCKS:
        tag, what = BLOCKS[name]
    elif "-PermissionRequest-" in rid:
        # An approval for a tool cmux has given its own event name, the way it
        # already does for AskUserQuestion and ExitPlanMode. Report it rather
        # than dropping it: an unrecognised *blocking* event name is precisely
        # how this watcher went silent before, so the unknown case must be loud.
        tag = "ATTN"
        what = "blocked on permission" if tool else "blocked on an unnamed prompt"
    elif name == "agent.hook.PreToolUse" and tool in BLOCKING_TOOLS:
        # Only when the authoritative event did not already report this block.
        # In normal mode it always does, ~17 ms earlier, so this stays quiet.
        if when - blocked.get((sid, tool), 0.0) < 2.0:
            continue
        tag, what = BLOCKING_TOOLS[tool]
    elif name in ENDINGS:
        tag, what = ENDINGS[name]
    else:
        continue

    if tag in ("ASK", "ATTN") and rid:
        if repeat(rid):
            continue
        if authoritative:
            blocked[(sid, tool)] = when
    else:
        # cmux delivers each hook as a received/completed pair, and again ~275ms
        # later once the surface resolves -- 4 frames per turn end. Collapse any
        # repeat of the same (sid, name) inside a 5s window.
        key = (sid, name)
        if when - last.get(key, 0.0) < 5.0:
            continue
        last[key] = when

    extra = f" ({tool})" if tag == "ATTN" and tool else ""
    print(f"{tag}  {known[sid]}  ({sid[7:]}) {what}{extra}", flush=True)
