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

  ASK   <name>  suspended on AskUserQuestion — will not resolve without a human
  ATTN  <name>  suspended on anything else a human must clear: a permission
                prompt, a plan approval, a held peer message, a Claude Code
                dialog — likewise. Also the default for a `waitingFor` this
                file does not recognise, and the line then carries that
                literal value so the next new one documents itself.
  DONE  <name>  went from working to idle, so a turn ended
  CLEAR <name>  stopped being blocked without a turn running — nothing to collect
  GONE  <name>  its process is no longer running (clean exit, crash, or kill)
  WARN  <text>  the watcher itself is deaf — it matches nothing, so it will
                never report anything. Emitted at most once per run.
"""

import glob
import json
import os
import signal
import sys
import time

USAGE = "usage: watch-workers.py <ledger.tsv> [poll_seconds]"

# How long a watcher may match nothing before it says so. Not zero: the ledger
# row is written *before* its worker is launched, so "no match yet" is the
# normal state for the seconds a `claude` spends booting and registering, and
# warning on the first poll would cry wolf on every healthy run.
WARN_AFTER = 30.0

# Which signal a blocked worker is due, keyed by the registry's `waitingFor`.
# Every value here was measured on this machine: `input needed` and
# `permission prompt` on 2026-08-09, `dialog open` on 2026-08-17 — that last on
# a session-limit dialog ("You've hit your session limit"), which is neither a
# tool prompt nor a question and had no entry to fall into.
#
# An explicit map rather than a substring test. The predecessor was
# `"ask" if "input" in waiting_for else "attn"`, which routed `dialog open`
# correctly only because that string happens not to contain the word: a later
# value like "input needed for permission" would have sent a permission prompt
# to ASK with nothing anywhere reporting a fault. ASK is the narrower claim —
# *this* worker is sitting on an AskUserQuestion — so it is the one that must
# never be guessed. See snapshot() for what becomes of a value absent from this
# table.
WAITING_STATES = {
    "input needed": "ask",
    "permission prompt": "attn",
    "dialog open": "attn",
}


def registry_dirs():
    raw = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    return [os.path.join(d, "sessions") for d in raw.split(":") if d]


def wanted(ledger):
    """Names this run owns, or None when the ledger cannot be read at all.

    Missing/short lines are skipped, not fatal. The None matters: returning an
    empty set for an unreadable ledger makes "the path is wrong" look exactly
    like "no workers yet", and both then look like a healthy quiet watcher.
    Only main() has the context to say which, so hand it the difference.
    """
    try:
        with open(ledger) as fh:
            return {ln.split("\t")[0].strip() for ln in fh if ln.strip()}
    except OSError:
        return None


def alive(pid):
    # Guard the value before signalling. os.kill(-1, 0) does not mean "no such
    # process" — it addresses *every* process the user may signal, and answers
    # yes, so a record with no usable pid would read as a healthy worker forever.
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_record(path):
    """(record, torn) for one registry file.

    A record is rewritten in place — read, modify, write to the live path, no
    tmp file and no rename — so a reader can catch one mid-write. What it sees
    then depends on the runtime: bun does not pass O_TRUNC, so a *shortening*
    write leaves fresh JSON followed by a stale tail (`...}10,"waitingFor":...`,
    measured at a 0.65 us window, 563 failures in 2.07 M reads); node does pass
    it, so the file is genuinely empty for ~11 us on *every* write. Both land
    here as ValueError, and a retry microseconds later clears it.

    `torn` distinguishes the two failures that must not be treated alike: a file
    that will not parse *while its process is still alive* is a session in an
    unknown state, while a file that is not there — or one whose process is gone —
    is a session that is gone. That liveness qualifier is what keeps a permanent
    tear from reading as an immortal worker; see the comment on the fall-through.
    """
    for _ in range(2):
        try:
            with open(path) as fh:
                rec = json.load(fh)
        except OSError:
            return None, False  # deleted between the glob and the open
        except ValueError:
            continue  # caught mid-write; read it again immediately
        # A fragment can parse and still not be a record. Anything that is not
        # a mapping would reach rec.get() below and take the whole watcher down
        # with an AttributeError, so treat it as another torn read.
        if isinstance(rec, dict):
            return rec, False

    # Falling out of the loop is *no information about the state*, which is not the
    # same as evidence of life — though the two look identical while the process
    # lives. A worker killed between the O_TRUNC and its write leaves a file that
    # will never parse again, and SIGKILL skips the exit handler that would unlink
    # it, so the corpse stays torn on disk forever. Called torn, that name is held
    # by the absence loop, never counts a miss, and never gets its GONE — the one
    # signal a dead worker cannot send for itself. So resolve it against the pid,
    # which on this path lives only in the filename: the body is unparseable here
    # by definition, so there is no second source to prefer over it.
    try:
        pid = int(os.path.basename(path).split(".")[0])
    except ValueError:
        return None, True  # not a pid-named file; nothing to check it against
    # alive() answers True on PermissionError, so a pid we may not signal is held
    # rather than reported dead. Deliberate: this path wants "provably alive" while
    # the record path wants "not provably dead", and alive() spells only the second.
    # The gap needs pid reuse across a user boundary *and* a permanent tear, so it
    # is named here rather than engineered around — silence beats a false GONE.
    return (None, True) if alive(pid) else (None, False)


def snapshot(names, paths):
    """(name -> state, torn names, the memo for the next poll, unknown-value notes).

    `paths` maps a registry file to the wanted name last read out of it. It is
    what makes a torn read attributable at all: the file is named for the pid,
    so the name lives *inside* the bytes that failed to parse, and under node
    there are none left to salvage it from.

    It is rebuilt from this poll's glob rather than added to, so a watcher armed
    for hours cannot accumulate an entry for every session file that has ever
    existed on the machine — it holds at most one per wanted, live worker.

    `notes` carries the literal `waitingFor` of any worker blocked on a value
    WAITING_STATES does not list. It is a *fourth* return rather than a richer
    state because the state is what gets compared between polls; see the
    routing below.
    """
    out = {}
    torn = set()
    fresh = {}
    notes = {}
    for d in registry_dirs():
        for path in glob.glob(os.path.join(d, "*.json")):
            rec, was_torn = read_record(path)
            if rec is None:
                # Neither a state nor an absence. Keep the name so the caller can
                # leave its prior state standing, and keep the memo entry with it —
                # dropping it here would strand the name on a second torn poll. A
                # file never yet read has no name to keep, so it is skipped entirely
                # and picked up whole on the next poll. The `in names` re-check is
                # against the *current* ledger: a name dropped from it since the memo
                # was written must not ride a further poll on a stale entry.
                name = paths.get(path)
                if was_torn and name in names:
                    fresh[path] = name
                    torn.add(name)
                continue
            name = rec.get("name")
            if name not in names or not alive(rec.get("pid")):
                continue
            fresh[path] = name
            status = rec.get("status")
            if status == "waiting":
                waiting_for = rec.get("waitingFor") or ""
                state = WAITING_STATES.get(waiting_for)
                if state is None:
                    # The same collapse as the `status` tail below, for the same
                    # reason: an unrecognised value must not become a state of
                    # its own, or the transition *out* of it goes unrecognised
                    # and the DONE is lost. `attn` is the honest default — it
                    # claims only "a human is needed", which is true of every
                    # blocked state — and it is where a value with no entry
                    # belongs whether it is new, empty, or missing entirely.
                    #
                    # The literal rides in `notes`, *beside* the state and never
                    # inside it, so two different unknown values still compare
                    # equal poll to poll while the line reporting the first one
                    # still names it. Fold it into the state instead and the
                    # collapse above is undone the moment a second unknown value
                    # exists.
                    state = "attn"
                    notes[name] = waiting_for
                out[name] = state
            elif status == "idle":
                out[name] = "idle"
            else:
                # Everything that is not idle and not waiting means working.
                # Collapse rather than pass through: the registry emits at least
                # `busy` and `shell`, and a value this file has not seen would
                # otherwise become a state of its own, so the -> idle transition
                # out of it would not be recognised and the DONE would be lost.
                out[name] = "busy"
    return out, torn, fresh, notes


def main():
    # Monitor closes the pipe when it stops reading, and CPython turns that into
    # a BrokenPipeError plus an "Exception ignored in: <stdout>" dump at
    # interpreter shutdown. Nothing is lost — the watcher was being torn down
    # anyway — but the tail of a Monitor ends on what reads as a crash. Dying
    # from SIGPIPE like any other filter says the same thing silently.
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    if len(sys.argv) < 2:
        sys.exit(USAGE)
    ledger = sys.argv[1]

    # Parsed here, not at import: as a module-level `float(sys.argv[2])` this
    # ran sixty lines before the check above could reject it, so the usage
    # message was unreachable and a bad interval was a ValueError traceback in
    # the supervisor's notification stream instead. Non-positive is rejected for
    # the same reason — it reaches time.sleep and dies there.
    poll = 2.0
    if len(sys.argv) > 2:
        try:
            poll = float(sys.argv[2])
        except ValueError:
            sys.exit(USAGE)
        if not 0 < poll < 86400:  # excludes nan and inf, which sleep rejects
            sys.exit(USAGE)

    seen = {}
    misses = {}
    paths = {}  # registry file -> its name, so a torn read is still attributable
    # A watcher that matches nothing *would* print nothing forever, which is
    # exactly what a healthy watcher with busy workers looks like — that is the
    # problem this clock exists to solve, not the behaviour still in effect. It
    # is reachable today: the ledger path is keyed by the caller's slot (a cmux
    # surface, a herdr pane), which outlives any one `claude`, so a restarted
    # session inherits the old rows.
    # Say it once. None once anything has matched, which also makes the warning
    # one-shot without a second flag.
    deaf_since = time.monotonic()

    while True:
        names = wanted(ledger)
        if names:
            now, torn, paths, notes = snapshot(names, paths)
        else:
            now, torn, notes = {}, set(), {}

        if now or torn:
            deaf_since = None
        elif deaf_since is not None and time.monotonic() - deaf_since >= WARN_AFTER:
            if names is None:
                print(f"WARN ledger unreadable, watching nothing: {ledger}", flush=True)
            elif not names:
                # Zero rows is its own case: "none match" reads as a mismatch,
                # and there is nothing here to mismatch.
                print(f"WARN ledger is empty, watching nothing: {ledger}", flush=True)
            else:
                print(
                    f"WARN ledger has {len(names)} row(s), none match a live "
                    f"session name: {ledger}",
                    flush=True,
                )
            deaf_since = None

        for name, state in now.items():
            misses.pop(name, None)
            was = seen.get(name)
            if was == state:
                continue
            # A worker already blocked when first seen still needs a human, so
            # report it. A worker merely busy or idle at that point does not.
            if state == "ask":
                print(f"ASK  {name}", flush=True)
            elif state == "attn":
                # An unrecognised `waitingFor` is reported as itself here and
                # nowhere else. The state was collapsed to keep DONE working, so
                # this line is the only place a new registry value can surface —
                # without it the string that fell through arrives invisibly and
                # the next reader has no evidence it ever existed.
                extra = (
                    f" -- unrecognised waitingFor {notes[name]!r}"
                    if name in notes
                    else ""
                )
                print(f"ATTN {name}{extra}", flush=True)
            elif state == "idle" and was == "busy":
                print(f"DONE {name}", flush=True)
            elif state == "idle" and was in ("ask", "attn"):
                # Unblocked with no working state in between, so no turn ran.
                # A terminal UI overlay opening and closing over an already-idle
                # worker lands here, and so does a held peer message that was
                # denied. Reporting DONE for either would hand a supervisor a
                # completion for a worker that did nothing, which is worse than
                # saying less: DONE is the one line it is told to trust as an
                # ending. Observed 2026-08-09 as a spurious ATTN->DONE pair.
                print(f"CLEAR {name}", flush=True)
            seen[name] = state

        for name in [n for n in seen if n not in now]:
            # Only ever for a worker seen alive at least once, so a name in the
            # ledger before its `claude` has registered is not reported dead.
            if name in torn:
                # Its file is right there, it just could not be parsed this
                # tick, which says nothing about the worker. Hold the last known
                # state and say nothing. The state is what matters: dropping the
                # name here would `del seen[name]` below, and the sighting after
                # that would count as a *first* sighting — first sight of idle
                # is silent by design, so a worker that finished during the gap
                # would lose its DONE and never get another. That is the one
                # line a supervisor is told to treat as an ending, for the one
                # worker that cannot report itself. And the transitions this
                # window can eat are exactly the ones worth keeping: a torn read
                # only happens on a *shortening* write, so busy->idle (same
                # length) is safe, but shell->idle is a DONE and waiting->idle
                # is a CLEAR, and both shrink.
                # `continue` also leaves any pending miss count standing rather
                # than resetting it. Holding and resetting are indistinguishable
                # in practice — nothing writes a dead session's file, so a worker
                # cannot tear repeatedly on its way out — and the torn hazard that
                # is actually reachable is the *permanent* tear, which read_record
                # now resolves to an absence rather than leaving to this loop.
                continue
            # Two consecutive absences, not one. Insurance rather than a fix for
            # something observed — 8.8 M reads produced no transient absence,
            # and no code path makes one — so it costs a real death one poll
            # (~2 s) of latency to cover a glob/stat race nobody has measured.
            # Cheap in the right direction: a GONE worker is already dead and
            # nothing races on it. Strictly consecutive, reset on every sighting
            # above, because a cumulative count would eventually reach 2 on a
            # name that merely missed twice over a long run and kill it.
            misses[name] = misses.get(name, 0) + 1
            if misses[name] < 2:
                continue
            print(f"GONE {name}", flush=True)
            del seen[name]
            del misses[name]

        time.sleep(poll)


if __name__ == "__main__":
    main()
