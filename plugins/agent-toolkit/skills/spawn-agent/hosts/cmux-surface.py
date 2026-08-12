#!/usr/bin/env python3
"""Resolve one field of a cmux surface, by ref or by uuid.

Usage: surface.py <surface ref|uuid> <field>

Fields: id, ref, pane_id, pane_ref, tty, title.

Prints nothing (exit 1) when no such surface exists — which is how you tell that
a tab has been closed, and the only trustworthy way to confirm a close, since the
`OK surface:N` that `cmux close-surface` prints back is a re-enumerated ref and
not the surface you just closed.

Targets $CMUX_WORKSPACE_ID. Like peer.py this is a file rather than a shell
function because every Bash tool call runs in a fresh shell, so a function
defined in one call is gone by the next.
"""

import json
import os
import subprocess
import sys


def walk(node, key):
    if isinstance(node, dict):
        if str(node.get("ref", "")).startswith("surface:") and key in (
            node.get("ref"),
            node.get("id"),
        ):
            return node
        for value in node.values():
            found = walk(value, key)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = walk(value, key)
            if found is not None:
                return found
    return None


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: surface.py <surface ref|uuid> <field>")
    key, field = sys.argv[1], sys.argv[2]

    workspace = os.environ.get("CMUX_WORKSPACE_ID")
    if not workspace:
        sys.exit("CMUX_WORKSPACE_ID is not set; refusing to guess a workspace")

    proc = subprocess.run(
        ["cmux", "--json", "--id-format", "both", "tree", "--workspace", workspace],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.exit(proc.stderr.strip() or "cmux tree failed")

    try:
        tree = json.loads(proc.stdout)["windows"]
    except (ValueError, KeyError):
        sys.exit("could not parse cmux tree output")

    surface = walk(tree, key)
    if surface is None:
        return 1
    value = surface.get(field)
    if value in (None, ""):
        return 1
    print(value)
    return 0


if __name__ == "__main__":
    sys.exit(main())
