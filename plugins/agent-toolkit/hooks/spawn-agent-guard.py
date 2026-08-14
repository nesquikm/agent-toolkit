#!/usr/bin/env python3
"""PreToolUse guard for SendMessage — a session may message only peers it spawned.

Wired as a PreToolUse hook with matcher "SendMessage" by this plugin's own
`hooks/hooks.json`. Reads the hook payload on stdin and decides one of two things:

  pass through (no output, exit 0)   the target is not a peer Claude Code session
                                     at all, or it is one this session spawned, or
                                     it is the supervisor that spawned this session
  ask                                a live peer session that nobody here can prove
                                     ownership of -- the user is shown the target
                                     and confirms or cancels

It never emits "allow". Emitting allow would short-circuit the normal permission
flow for a call this hook has no opinion about; staying silent leaves that flow
exactly as it was.

Measured 2026-08-13: an `ask` from this hook still fires after the user picks
"Yes, and don't ask again for SendMessage commands in <dir>" and a
`permissions.allow` rule for SendMessage is written. A PreToolUse decision runs
ahead of the allow rules, so the allowlist cannot switch this guard off.

IT SCOPES ITSELF TO MACHINES THAT SPAWN, AND IT CHECKS THAT BEFORE IT LOOKS AT
THE TARGET.

This ships with the plugin, so it is installed by everyone who installs the
marketplace for the spawn skill -- including everyone who never runs it. With no
`.owner` sidecar anywhere in the ledger directory there is no ownership record on
this machine at all, which means every branch below that could return a pass is
unreachable: the guard is structurally incapable of doing anything but adding
prompts. So it returns 0 as soon as it knows the tool is SendMessage, before it
resolves any session -- `main()`'s posix check, its stdin parse and its tool-name
check are the only things ahead of it.

**The predicate is "while a sidecar exists", not "once one ever did".** Teardown
removes the ledger and its sidecar together, so a machine that spawns constantly
is unguarded in the gaps between runs, and the state is entered and left many
times rather than latched once. That is the deliberate trade: this hook is the
second line, and `owned.py` plus the skill's own rules are the first.

The gate is the **sidecar**, not the `.tsv`. A ledger with no sidecar beside it
proves nothing about any row in it -- `owned.py` exits 5 on exactly that state --
so a `.tsv`-keyed gate would arm the guard in the one configuration where it can
still only ever ask. Measured 2026-08-14 before the gate existed: with TMPDIR
pointed at an empty directory and a stranger's payload aimed at a live local
session, the guard answered `ask`; that is what any user with the plugin and no
spawn history was getting on every SendMessage, with no per-hook off switch.

OWNERSHIP COMES FROM THE SPAWN LEDGER, NOT FROM A NAME.

spawn-agent writes one ledger per caller slot at

    ${TMPDIR:-/tmp}/spawn-agent/<slot>.tsv     name loc1 loc2 state session_id pid
    ${TMPDIR:-/tmp}/spawn-agent/<slot>.owner   the spawning session's own id

Column 5 is a uuid the supervisor MINTED and passed to `claude --session-id`, so a
session carries it only if this run created it -- a human never passes that flag,
and a hand-started session takes a random id and auto-names itself. Column 6 is
the pid captured once that id identified the session.

Both keys are needed, and both were measured on 2026-08-13:
  - `--session-id` reserves nothing: two live sessions registered the same minted
    uuid with no error. So a match set larger than one is real, and is refused.
  - `/clear` rotates the session id in place -- same pid, same name, same socket,
    fresh id within 400ms. Joining on the id alone would disown a live worker the
    moment a user cleared its tab, so the pid is what the row is held by after
    readiness.

A name proves nothing: names are freed when a session exits and can then be taken
by anyone, and a hand-started session auto-names itself `<dirname>-<2 hex>` in a
256-wide space.

Both directions are allowed, and the sidecar is what makes the second one cheap:

  supervisor -> worker   my id is a <slot>.owner; targets = that ledger's workers
  worker -> supervisor   my id is one of that ledger's workers; target = its .owner

A 4-column ledger (the pre-ownership format) proves nothing and grants nothing.
That is deliberate: an un-updated spawn-agent genuinely cannot show it spawned
anything, so it gets asked.
"""

import glob
import json
import os
import sys


def config_roots():
    """Every profile a session may be registered in, most specific first.

    CLAUDE_CONFIG_DIR is colon-separated, and three things happen to it. Each is a
    failure that was measured, not tidiness:

      - **empty segments are dropped.** A shell hook that appends a profile path
        leaves a trailing colon, and an empty segment would turn the join below
        into the relative `sessions/*.json`, read from whatever directory this hook
        happened to be spawned in.
      - **every segment is expanduser'd.** A literal `~/.claude` really is passed
        by launchd jobs, systemd units and containers, and an unexpanded tilde
        names a directory that does not exist -- so the guard resolves no session,
        interposes on nothing, and stays blind for the life of that install with no
        error anywhere.
      - **a segment still not absolute after that is dropped**, for the same
        reason: it would be read relative to the cwd.

    Those three are the same class of bug as `ledger_dir()`'s TMPDIR trap one
    function below -- an environment variable trusted without normalising it.

    THE SWEEP IS A UNION, AND THE UNION IS THE WHOLE POINT.

    `~/.claude` and every `~/.claude-*` are appended **unconditionally**, on top of
    whatever CLAUDE_CONFIG_DIR named. The caller and its target need not share a
    profile: discovery is profile-scoped but a `uds:` send is not, so a guard that
    read only the caller's own profile would fail to resolve the very cross-profile
    target it most needs to see.

    Measured 2026-08-14, and this is why treating the variable as authoritative is
    not an option here. Of 11 live `claude` processes on this machine, **3 carried
    `CLAUDE_CONFIG_DIR=/Users/ns/.claude-st` and 8 carried none** -- the second
    profile sets it by design, since the user's statusline reads it to print which
    profile a session is in. A briefly-committed version of this function honoured
    only that variable, and one of those 3 messaging a session registered solely in
    `~/.claude` was then **passed through in silence** where this version answers
    `ask`. That is a fail-open in the exact direction this guard exists to close,
    so the reach is wider than `lib/owned.py`'s on purpose: `owned.py` resolves a
    row the caller already owns, while this must recognise a stranger anywhere on
    the machine.
    """
    roots = []
    for d in os.environ.get("CLAUDE_CONFIG_DIR", "").split(":"):
        if not d:
            continue
        d = os.path.expanduser(d)
        if os.path.isabs(d):
            roots.append(d)
    home = glob.escape(os.path.expanduser("~"))
    roots.append(os.path.expanduser("~/.claude"))
    roots.extend(sorted(glob.glob(os.path.join(home, ".claude-*"))))
    seen, out = set(), []
    for d in roots:
        real = os.path.realpath(d)
        if real not in seen and os.path.isdir(os.path.join(d, "sessions")):
            seen.add(real)
            out.append(d)
    return out


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


def live_sessions():
    """Every live registered session, across every profile."""
    out = []
    for root in config_roots():
        for path in glob.glob(os.path.join(glob.escape(root), "sessions", "*.json")):
            try:
                with open(path) as fh:
                    rec = json.load(fh)
            except (OSError, ValueError):
                continue  # absent, or caught mid-rewrite; nothing to salvage
            if isinstance(rec, dict) and alive(rec.get("pid")):
                out.append(rec)
    return out


def target_record(to, sessions):
    """(record, label) for a `to`, or (None, reason) when it is not a peer.

    "Not a peer" is the common case and must stay silent: an in-process subagent,
    a teammate, or a cloud session has no local registry record, and gating those
    would put a prompt in front of ordinary work this guard has no view on.
    """
    if not isinstance(to, str) or not to.strip():
        return None, "no target"
    to = to.strip()

    if to.startswith("uds:"):
        # /tmp/cc-socks/<pid>.sock -- verified on this machine as pid == socket
        # basename == registry filename for every live session.
        #
        # THAT FILENAME CONVENTION IS LOAD-BEARING, AND IT IS AN ASSUMPTION.
        # The pid is recovered by string-parsing the basename, not by asking the
        # filesystem what the socket is, so anything that breaks the convention --
        # a symlink or alias to the socket under another name, a bind mount, a
        # future release that keys sockets by session id instead -- falls to
        # "unparseable socket path" or to no match, and this guard passes the send
        # through in silence. There is no error and nothing in the decision looks
        # different from a legitimate non-peer target.
        #
        # The robust form is to stat the `to` path and match st_dev/st_ino against
        # each record's messagingSocketPath, which is immune to naming entirely.
        # It is deliberately NOT done here: it costs a stat per live session on
        # every SendMessage, and the convention has held for every session on this
        # machine. Recorded so the next person sees a choice rather than a fact.
        stem = os.path.basename(to[4:]).split(".")[0]
        try:
            pid = int(stem)
        except ValueError:
            return None, "unparseable socket path"
        for rec in sessions:
            if rec.get("pid") == pid:
                return rec, rec.get("name") or f"pid {pid}"
        return None, "no live session behind that socket"

    # A bare name, or the `name [ref]` form the refusal message hands back.
    name = to.split(" [")[0].strip()
    hits = [r for r in sessions if r.get("name") == name]
    if not hits:
        return None, "not a peer session"
    if len(hits) > 1:
        # Two live sessions share a name. Nothing can say which was meant, so the
        # send is exactly as likely to land on a stranger as on the intended one.
        return hits[0], f"{name} (ambiguous -- {len(hits)} live sessions share it)"
    return hits[0], name


def ledger_dir():
    """The spawn ledger directory, or None when there cannot be one.

    `os.environ.get("TMPDIR", "/tmp")` is wrong here and the failure is silent: a
    default applies only when the key is ABSENT, so a **set-but-empty** TMPDIR --
    what `TMPDIR= claude` or a stripped environment produces -- returns "" and
    leaves the relative path `spawn-agent`, resolved against whatever directory
    this hook was spawned in. Measured 2026-08-14 against the pre-fix file: one
    payload, one profile, `TMPDIR=""`, and the **cwd** as the only variable --
    run from a directory holding `spawn-agent/slot.tsv` + `slot.owner` the guard
    passed a stranger's send through in silence, and run from a sibling directory
    it asked. Files committed to a repo would have granted message-ownership of a
    live session to anyone who checked it out.

    So: `or`, not a default; and a relative directory is not a ledger directory at
    all, it is a cwd, which is why it is refused rather than resolved. This is the
    same trap `config_roots` documents one function above for CLAUDE_CONFIG_DIR
    segments -- an empty string that reads as a path.
    """
    tmp = os.environ.get("TMPDIR") or "/tmp"
    if not os.path.isabs(tmp):
        return None
    return os.path.join(tmp, "spawn-agent")


def machine_spawns():
    """True when some run on this machine has recorded ownership of something.

    A `.owner` sidecar is the only thing that can make any target below provably
    ours. With none anywhere, this guard has exactly one reachable outcome and it
    is `ask`.

    `glob.escape` on the directory, here and in `allowed_ids`, for the same reason
    `ledger_dir` refuses a relative path: TMPDIR is attacker- or accident-supplied
    and a `[` or `*` anywhere in it makes the pattern match nothing. That reads as
    "this machine has never spawned", which silently disarms the guard -- the very
    failure mode the relative-path fix closed, reached by a different route.
    """
    d = ledger_dir()
    return bool(d) and bool(glob.glob(os.path.join(glob.escape(d), "*.owner")))


def allowed_ids(my_id, sessions):
    """Session ids this session may message, from the spawn ledgers on disk."""
    allowed = set()
    d = ledger_dir()
    if not my_id or not d:
        return allowed
    by_pid = {str(r.get("pid")): r for r in sessions}

    for ledger in glob.glob(os.path.join(glob.escape(d), "*.tsv")):
        owner = ""
        try:
            with open(ledger[: -len(".tsv")] + ".owner") as fh:
                owner = fh.read().strip()
        except OSError:
            pass

        workers = set()
        try:
            with open(ledger) as fh:
                for line in fh:
                    cols = line.rstrip("\n").split("\t")
                    # Column 5 only. A 4-column row is the old format and carries
                    # no proof of anything.
                    if len(cols) < 5 or not cols[4].strip():
                        continue
                    workers.add(cols[4].strip().lower())
                    # Column 6, the pid pinned at readiness, re-attaches a worker
                    # whose session id was rotated by /clear. The name must still
                    # agree, so a recycled pid cannot inherit the row.
                    pid = cols[5].strip() if len(cols) >= 6 else ""
                    rec = by_pid.get(pid)
                    if rec is not None and rec.get("name") == cols[0].strip():
                        workers.add((rec.get("sessionId") or "").lower())
        except OSError:
            continue

        workers.discard("")
        if owner and owner.lower() == my_id.lower():
            allowed |= workers          # I spawned these
        if owner and my_id.lower() in workers:
            allowed.add(owner.lower())  # this one spawned me -- let the reply home
    return allowed


def main():
    # UNMEASURED -- there is no Windows host here to reproduce it on. `alive()`
    # calls os.kill(pid, 0), and on Windows CPython os.kill routes every signal
    # other than CTRL_C_EVENT/CTRL_BREAK_EVENT to TerminateProcess. The registry
    # is pid-keyed, so this would walk a stranger's live pids and kill them. One
    # line, and the downside of being wrong about it is nothing worse than a
    # guard that does not run off-posix.
    if os.name != "posix":
        return 0

    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0  # nothing parseable to judge; do not stand in the way

    if payload.get("tool_name") != "SendMessage":
        return 0

    # Nothing on this machine has ever recorded ownership, so nothing here can be
    # proven ours and the only thing this guard could add is a prompt. See the
    # module docstring -- this is the gate that keeps it off machines that install
    # the plugin and never spawn.
    if not machine_spawns():
        return 0

    sessions = live_sessions()
    to = (payload.get("tool_input") or {}).get("to")
    rec, label = target_record(to, sessions)
    if rec is None:
        return 0  # not a peer Claude Code session

    my_id = payload.get("session_id") or ""
    target_id = (rec.get("sessionId") or "").lower()
    if target_id and target_id in allowed_ids(my_id, sessions):
        return 0  # spawned by me, or the session that spawned me

    # `nameSource: derived` means the session auto-named itself, i.e. nobody
    # passed -n, i.e. it was almost certainly started by a human at a prompt.
    hand = rec.get("nameSource") == "derived"
    who = "started by hand (it auto-named itself)" if hand else "not spawned by this session"
    reason = (
        f"'{label}' is a live Claude Code session {who}. Sending to it puts work "
        "into a session someone else -- very possibly the user -- is using. "
        "Confirm only if the user asked for this specific session to be messaged."
    )
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # never let a guard bug wedge every SendMessage
        print(f"spawn-agent-guard: {exc}", file=sys.stderr)
        sys.exit(0)
