#!/usr/bin/env python3
"""Answer "is any live session already using this name" — the pre-launch check.

Usage: peer.py <name> [field]

Prints one field of a live session called <name>, or nothing (exit 1) when no live
session has that name. Default field is `address`.

  address      uds:<socket>, the string SendMessage takes as `to`
  pid status waitingFor sessionId cwd name   the raw registry fields

THIS IS NOT THE TOOL FOR TALKING TO A WORKER. A name identifies a session only
among the live ones at a single instant, and not at all over time: names are freed
when a session exits and can then be taken by anyone, including the user. Once a
worker is launched, resolve it with `owned.py`, which joins on what this run
minted rather than on a string anybody may claim. What is left here is the one
question that is legitimately about sessions you do not own — is this name taken.

It sweeps EVERY profile, not just the active one. Discovery is profile-scoped but
the name namespace and `SendMessage` are machine-wide, so a check that read one
profile would call a name free while another profile held it — measured on this
machine 2026-08-13, where 15 live sessions spanned two profiles and the default
one could see 9 of them. Spawning into that gap puts two live sessions under one
name, which is the mis-delivered task this check exists to prevent.

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


def roots():
    """Every profile on this machine, active one first, deduplicated.

    Drop empty segments, as watch-workers.py does. A trailing or doubled colon
    — what a shell hook appending a profile path produces — otherwise leaves
    d == "" and the glob degrades to the relative `sessions/*.json`, read from
    whatever directory the caller happened to be standing in.
    """
    out = [d for d in (os.environ.get("CLAUDE_CONFIG_DIR") or "").split(":") if d]
    out.append(os.path.expanduser("~/.claude"))
    out.extend(sorted(glob.glob(os.path.expanduser("~/.claude-*"))))
    seen, keep = set(), []
    for d in out:
        real = os.path.realpath(d)
        if real not in seen and os.path.isdir(os.path.join(d, "sessions")):
            seen.add(real)
            keep.append(d)
    return keep


def find(name):
    hits = []
    for d in roots():
        for path in glob.glob(os.path.join(d, "sessions", "*.json")):
            try:
                with open(path) as fh:
                    rec = json.load(fh)
            except (OSError, ValueError):
                continue
            if isinstance(rec, dict) and rec.get("name") == name and alive(rec.get("pid")):
                hits.append(rec)
    if len(hits) > 1:
        # Still a hit, and louder than one: for the question this script asks —
        # "is the name taken" — two holders is the most emphatic possible yes.
        # Naming them matters because the state is invisible from a single
        # profile, which is how it arises in the first place.
        pids = ", ".join(str(r.get("pid")) for r in hits)
        print(
            f"peer: WARNING {len(hits)} live sessions are named {name!r} "
            f"(pids {pids}) -- this name cannot address any of them reliably",
            file=sys.stderr,
        )
    return hits[0] if hits else None


# Fields this script will serve. `address` and `messagingSocketPath` are absent on
# purpose, and their absence is the point: this script resolves by NAME, so anything
# it printed would be an address for whatever session holds that name right now —
# including a session started by the user. Handing one out is the reported incident
# in a single command, and sweeping every profile (see roots) widened the reach
# rather than narrowing it. `owned.py` serves addresses, because it resolves through
# a ledger row instead of through a string anybody may hold.
ANSWERABLE = ("name", "pid", "status", "waitingFor", "cwd", "sessionId")


def main():
    if not 2 <= len(sys.argv) <= 3:
        sys.exit("usage: peer.py <name> [%s]" % " | ".join(ANSWERABLE))
    field = sys.argv[2] if len(sys.argv) > 2 else "name"

    if field in ("address", "messagingSocketPath"):
        sys.exit(
            "peer.py does not hand out addresses: it resolves by name, so the "
            "socket it printed could belong to any session holding that name, "
            "including the user's own. Use owned.py <ledger> <name> instead."
        )
    if field not in ANSWERABLE:
        sys.exit("peer.py: unknown field %r (want one of: %s)" % (field, ", ".join(ANSWERABLE)))

    rec = find(sys.argv[1])
    if rec is None:
        return 1

    value = rec.get(field)
    if value is None:
        return 1
    print(value)
    return 0


if __name__ == "__main__":
    sys.exit(main())
