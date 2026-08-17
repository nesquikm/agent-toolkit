#!/usr/bin/env python3
"""Resolve a worker this run spawned — and refuse to resolve anything else.

Usage: owned.py <ledger.tsv> <name> [field]

`peer.py` answers "is there a live session with this name". That is the wrong
question for every use after launch, because a name identifies a session only
among the live ones at one instant and not at all over time. This asks the
question that actually matters: **is the live session behind this ledger row the
one this run started?**

  exit 0   the field, for a session proven to be ours
  exit 1   nothing live for this row — never started, exited or killed; also a live
           session with no inbox socket, and a field its record does not carry
  exit 2   no ledger at that path, or no row in it named this
  exit 3   HARD STOP, never retry — a live session holds this NAME but is not ours,
           or `.owner` names another session, or the row has no minted id
  exit 4   ambiguous: more than one live session answers — HARD STOP
  exit 5   no `.owner` beside the ledger — ownership is unprovable, HARD STOP

A malformed *invocation* exits 1 too (`sys.exit` with a usage string), so read the
table above as the answers to a well-formed call.

Fields: address (default), pid, status, waitingFor, sessionId, cwd, name.

WHY A ROW CARRIES TWO IDENTIFIERS

Column 5 is the session id the supervisor MINTED and passed to `claude
--session-id` before launching. A human never passes that flag — a hand-started
session auto-names itself and takes a random id — so a minted id can never select
one. That is what makes the *first* identification trustworthy.

It is not enough on its own, and both reasons were measured on 2026-08-13:

- **`--session-id` reserves nothing.** Two live interactive sessions were started
  with the same minted uuid and both registered, with no error from either. So a
  minted id is unforgeable-in-practice but not unique-by-construction, and more
  than one match is a real state that must stop the run rather than pick one.
  `peer.py` returns the first glob match; that shape is exactly what must not be
  inherited here.
- **`/clear` rotates the session id.** Same pid, same name, same socket, a fresh
  `sessionId` within 400 ms. Since this skill exists to put workers in visible
  slots a human can click into and take over, a user typing `/clear` in a worker
  tab is ordinary use — and a pin on the session id alone would disown that worker
  permanently, reporting a live agent as dead.

So column 6 holds the **pid**, captured at readiness once the minted id has
identified the session, and the pid is what the row is joined on afterwards. The
minted id establishes the binding; the pid survives it. A row is matched by pid
only when the name still agrees, so a recycled pid cannot quietly inherit
ownership of a slot.

Before the pin, column 6 holds a literal `-` rather than an empty field -- column 7
now names the host that wrote the row, and an empty INTERIOR column collapses under
shell field splitting where a trailing one does not. Nothing here changes for it:
the pid re-join is gated on `.isdigit()`, which is false for `-` exactly as it was
for the empty string, and every index in this file is length-guarded, so 6- and
7-column ledgers both resolve. Column 7 is never read here -- ownership is a
question about a session, and the host a row was written on has no bearing on it.
"""

import glob
import json
import os
import subprocess
import sys

USAGE = "usage: owned.py <ledger.tsv> <name> [field]"


def alive(pid):
    # os.kill(-1, 0) addresses every process this user may signal and answers yes,
    # so a record with no usable pid must be rejected before signalling.
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def registry_roots():
    """Every profile a session may register in, empty segments dropped.

    A trailing or doubled colon — what a shell hook appending a profile path
    produces — would otherwise leave d == "" and degrade the glob to the relative
    `sessions/*.json`, read from whatever directory the caller stood in.
    """
    raw = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    return [d for d in raw.split(":") if d]


def live_records():
    out = []
    for d in registry_roots():
        for path in glob.glob(os.path.join(d, "sessions", "*.json")):
            try:
                with open(path) as fh:
                    rec = json.load(fh)
            except (OSError, ValueError):
                continue  # absent, or caught mid-rewrite
            if isinstance(rec, dict) and alive(rec.get("pid")):
                out.append(rec)
    return out


def row_for(ledger, name):
    """(minted_session_id, pid) for `name`, or None when the row is absent."""
    try:
        with open(ledger) as fh:
            for line in fh:
                cols = line.rstrip("\n").split("\t")
                if cols and cols[0].strip() == name:
                    sid = cols[4].strip() if len(cols) >= 5 else ""
                    pid = cols[5].strip() if len(cols) >= 6 else ""
                    return sid, pid
    except OSError:
        return None
    return None


def main():
    if not 3 <= len(sys.argv) <= 4:
        sys.exit(USAGE)
    ledger, name = sys.argv[1], sys.argv[2]
    field = sys.argv[3] if len(sys.argv) > 3 else "address"

    # Establish that the ledger is THERE before saying anything about ownership.
    # An empty $LEDGER is the ordinary way in -- a Bash call's shell state does not
    # outlive the call, so a block that forgets to re-derive it passes "" -- and it
    # would otherwise be answered as a missing sidecar, whose repair line names a
    # RELATIVE `.owner` and drops one into whatever directory the caller stood in.
    # A path with no file behind it has no rows, so refusing here cannot re-open
    # the hole the sidecar check closes.
    if not ledger or not os.path.isfile(ledger):
        print(f"owned: no ledger at {ledger!r}", file=sys.stderr)
        return 2

    # The ledger says which workers exist; the .owner sidecar says whose they are.
    # Check it before anything else: a slot outlives any one claude, so a session
    # that started in this slot after another one -- or that skipped the setup
    # block entirely -- would otherwise be handed the previous run's workers by a
    # path it never wrote a byte of.
    sidecar = os.path.splitext(ledger)[0] + ".owner"
    me = os.path.join(os.path.dirname(os.path.abspath(__file__)), "me.py")
    owner = ""
    try:
        with open(sidecar) as fh:
            owner = fh.read().strip()
    except OSError:
        pass  # absent reads the same as empty; both are refused below
    if not owner:
        # No sidecar at all, or an empty one. This used to fall through, and a
        # supervisor mid-upgrade -- serving pre-sidecar skill text against these
        # scripts -- wrote rows by hand and got a working address back from a
        # ledger it could prove nothing about, while every SendMessage it made was
        # gated. Refusing here is what makes that visible at the tool the skill
        # sends you to. It is a per-LEDGER verdict, not a per-row one: no sidecar
        # proves nothing about any row in the file, so no field resolves.
        print(
            f"owned: no .owner beside {ledger} -- ownership is unprovable, so "
            "nothing here resolves.\n"
            f"       If these rows are yours:  python3 {me} sessionId > {sidecar}\n"
            "       If they are not:          move the ledger aside.",
            file=sys.stderr,
        )
        return 5

    try:
        here = subprocess.run(
            [sys.executable, me, "sessionId"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:
        here = ""
    # here == "" means me.py could not resolve this session, and the check
    # falls through rather than blocking. That is deliberate but it is a real
    # gap, so know when it opens: me.py reads CLAUDE_CONFIG_DIR, so pointing
    # that at a fixture profile (as the smoke test does) makes it exit
    # "no session record for claude pid N under <fixture>" and this check
    # inert. Under a real profile it resolves, and the mismatch is caught.
    # Blocking on an unresolvable self would make every fixture and every
    # unusual profile layout unusable, which is a worse failure than the one
    # the setup block already prevents by refusing to overwrite .owner. A
    # MISSING sidecar is a different case and is refused above: there the file
    # that would carry the proof does not exist, so nothing can resolve it later.
    if here and here.lower() != owner.lower():
        print(
            f"owned: {ledger} is owned by session {owner}, not by this one "
            "-- those workers belong to another run",
            file=sys.stderr,
        )
        return 3

    row = row_for(ledger, name)
    if row is None:
        print(f"owned: no ledger row named {name!r} in {ledger}", file=sys.stderr)
        return 2
    minted, pinned_pid = row
    if not minted:
        # A 4-column row from before ownership existed, or a row whose launch
        # never got as far as minting. It cannot prove anything, so it does not.
        print(f"owned: row {name!r} carries no minted session id", file=sys.stderr)
        return 3

    records = live_records()

    # Compare case-insensitively: any 8-4-4-4-12 hex string is accepted by the CLI
    # and its case is preserved verbatim into the registry.
    mine = [r for r in records if (r.get("sessionId") or "").lower() == minted.lower()]

    if not mine and pinned_pid.isdigit():
        # The minted id is gone but the process may not be: `/clear` rotates the
        # session id in place. Re-join on the pid, and require the name to still
        # agree so a recycled pid cannot inherit the row.
        mine = [
            r for r in records
            if str(r.get("pid")) == pinned_pid and r.get("name") == name
        ]

    if len(mine) > 1:
        pids = ", ".join(str(r.get("pid")) for r in mine)
        print(
            f"owned: {len(mine)} live sessions answer for {name!r} (pids {pids}) "
            "-- refusing to guess which is ours",
            file=sys.stderr,
        )
        return 4

    if not mine:
        # Nothing of ours is live. Say whether somebody ELSE now holds the name,
        # because that is the case a retry must never paper over.
        if any(r.get("name") == name for r in records):
            print(
                f"owned: a live session is named {name!r} but it is not the one "
                "this run spawned -- do not send to it, do not key it, do not close it",
                file=sys.stderr,
            )
            return 3
        return 1

    rec = mine[0]
    if field == "address":
        sock = rec.get("messagingSocketPath")
        if not sock:
            return 1  # registered but unreachable; same answer as absent
        print("uds:" + sock)
        return 0

    value = rec.get(field)
    if value is None:
        return 1
    print(value)
    return 0


if __name__ == "__main__":
    sys.exit(main())
