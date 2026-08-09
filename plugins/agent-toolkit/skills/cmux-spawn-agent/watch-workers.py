#!/usr/bin/env python3
"""Watch this run's workers in Claude Code's peer registry.

Usage: watch-workers.py <ledger.tsv> [poll_seconds]

The ledger's first column is a worker's session name (`claude -n <name>`). It is
re-read every poll, so workers spawned after the watcher is armed are picked up
without re-arming.

The registry is `<config-dir>/sessions/<pid>.json`, one file per live session,
rewritten by that session whenever its status changes. Set CLAUDE_CONFIG_DIR to
watch a non-default profile; colon-separate several to watch them all at once.

One line per state change, each becoming a chat notification:

  ASK  <name>   suspended on AskUserQuestion  — will not resolve without a human
  ATTN <name>   suspended on a permission prompt or a plan approval — likewise
  DONE <name>   its turn ended
  GONE <name>   its process is no longer running (clean exit, crash, or kill)
"""

import glob
import json
import os
import sys
import time

POLL = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0


def registry_dirs():
    raw = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    return [os.path.join(d, "sessions") for d in raw.split(":") if d]


def wanted(ledger):
    """Names this run owns. Missing/short lines are skipped, not fatal."""
    try:
        with open(ledger) as fh:
            return {ln.split("\t")[0].strip() for ln in fh if ln.strip()}
    except OSError:
        return set()


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def snapshot(names):
    """name -> state, for every wanted name with a live process."""
    out = {}
    for d in registry_dirs():
        for path in glob.glob(os.path.join(d, "*.json")):
            try:
                with open(path) as fh:
                    rec = json.load(fh)
            except (OSError, ValueError):
                continue  # mid-rewrite; the next poll gets it
            name = rec.get("name")
            if name not in names or not alive(rec.get("pid", -1)):
                continue
            status = rec.get("status")
            if status == "waiting":
                waiting_for = rec.get("waitingFor") or ""
                out[name] = "ask" if "input" in waiting_for else "attn"
            else:
                out[name] = status or "idle"
    return out


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: watch-workers.py <ledger.tsv> [poll_seconds]")
    ledger = sys.argv[1]
    seen = {}

    while True:
        names = wanted(ledger)
        now = snapshot(names)

        for name, state in now.items():
            was = seen.get(name)
            if was == state:
                continue
            # A worker already blocked when first seen still needs a human, so
            # report it. A worker merely busy or idle at that point does not.
            if state == "ask":
                print(f"ASK  {name}", flush=True)
            elif state == "attn":
                print(f"ATTN {name}", flush=True)
            elif state == "idle" and was in ("busy", "ask", "attn"):
                print(f"DONE {name}", flush=True)
            seen[name] = state

        for name in [n for n in seen if n not in now]:
            # Only ever for a worker seen alive at least once, so a name in the
            # ledger before its `claude` has registered is not reported dead.
            print(f"GONE {name}", flush=True)
            del seen[name]

        time.sleep(POLL)


if __name__ == "__main__":
    main()
