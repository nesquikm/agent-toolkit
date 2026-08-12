#!/usr/bin/env python3
"""Look a spawned worker up in Claude Code's peer registry, by name.

Usage: peer.py <name> [field]

Prints one field of the live session called <name>, or nothing (exit 1) when no
live session has that name. Default field is `address`.

  address      uds:<socket>, the string SendMessage takes as `to`
  pid status waitingFor sessionId cwd name   the raw registry fields

The registry is `<config-dir>/sessions/<pid>.json`. Set CLAUDE_CONFIG_DIR to read
a non-default profile; colon-separate several to search them all.

This exists as a file rather than a shell function because every Bash tool call
runs in a fresh shell: a function defined in one call is gone by the next, so a
skill that defines one and then uses it further down silently asks for the whole
definition to be pasted again every time.
"""

import glob
import json
import os
import sys


def alive(pid):
    # os.kill(-1, 0) addresses every process the user may signal and answers
    # yes, so a record with no usable pid must be rejected before signalling.
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def find(name):
    root = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    # Drop empty segments, as watch-workers.py does. A trailing or doubled colon
    # — what a shell hook appending a profile path produces — otherwise leaves
    # d == "" and the glob degrades to the relative `sessions/*.json`, read from
    # whatever directory the caller happened to be standing in.
    for d in [d for d in root.split(":") if d]:
        for path in glob.glob(os.path.join(d, "sessions", "*.json")):
            try:
                with open(path) as fh:
                    rec = json.load(fh)
            except (OSError, ValueError):
                continue
            if rec.get("name") == name and alive(rec.get("pid")):
                return rec
    return None


def main():
    if not 2 <= len(sys.argv) <= 3:
        sys.exit("usage: peer.py <name> [field]")
    field = sys.argv[2] if len(sys.argv) > 2 else "address"

    rec = find(sys.argv[1])
    if rec is None:
        return 1

    if field == "address":
        # No socket means registered but unreachable — a session older than
        # v2.1.224. Absent output says "cannot be messaged", same as absent.
        sock = rec.get("messagingSocketPath")
        if not sock:
            return 1
        print("uds:" + sock)
        return 0

    value = rec.get(field)
    if value is None:
        return 1
    print(value)
    return 0


if __name__ == "__main__":
    sys.exit(main())
