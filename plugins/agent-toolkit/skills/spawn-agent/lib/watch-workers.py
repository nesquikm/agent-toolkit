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
  GATE  <name>  the same transition, but the last thing the worker said puts
                something to a human — a question, or a request phrased as a
                statement ("Tell me which", "Waiting on your go-ahead"). The
                statement half matters more than it looks: a worker told to stop
                and report rather than decide is steered away from question
                marks, so punctuation alone would miss exactly the workers most
                worth catching. The registry cannot see any of this: a worker
                that asks in prose and ends its turn is byte-identical to one
                that finished, so the discriminator is its transcript, and the
                line carries the closing words so a supervisor can route the
                gate without reading a screen. DONE and GATE are the two
                renderings of one transition and never both fire for it.
  CLEAR <name>  stopped being blocked without a turn running — nothing to collect
  GONE  <name>  its process is no longer running (clean exit, crash, or kill)
  WARN  <text>  the watcher reporting on itself, never on a worker. Two kinds,
                each emitted at most once per run: it is deaf — armed on a
                ledger that matches nothing, so it will never report anything —
                or its gate predicate raised and every GATE is degrading to a
                bare DONE until it is fixed.
"""

import glob
import json
import os
import re
import signal
import string
import sys

# The profile list is `peer.py`'s, not a second copy of it — see `registry_dirs`.
# Imported by path because this file is run as a script from anywhere, and
# sys.path[0] is only the lib directory when the interpreter was pointed at it.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import peer  # noqa: E402
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

# --- the third state: a worker that asked in prose and ended its turn ---------
#
# The registry has two states for a worker that is not working: `idle` (a turn
# ended) and `waiting` (a human is needed, and `waitingFor` says which dialog).
# It has no state for the third thing that actually happens: a worker asks its
# question **in prose** and ends its turn, which registers `idle` and is
# byte-for-byte identical on disk to a worker that finished its work. Measured
# five times in one session on 2026-09-04 — two commit gates, two release gates,
# one more commit gate — each reported as a bare DONE, each read as turn churn,
# and each discovered only by reading the worker's screen.
#
# It cannot be fixed by telling workers to raise gates through AskUserQuestion so
# that `waitingFor` fires: the gates come out of skill contracts that *mandate*
# prose, one shipped skill requiring literally `Apply commit "<subject>"? [y / n /
# edit]`. A worker obeying its skill asks in prose, so the discriminator has to be
# something the watcher can observe rather than something the worker must change.
#
# The transcript is that thing, and it is the only one: same config dir, keyed by
# the `cwd` and `sessionId` the registry record already carries, no host command
# and no terminal — which is what keeps this file host-agnostic. Read only on the
# transition, never on a poll.

# How far back from the end of a transcript to look for the final assistant
# record. Two attempts because a single tool result can be larger than the first
# window; both are cheap because this runs once per turn-end, not once per poll.
TAIL_BYTES = (1 << 20, 1 << 23)

# How much of the worker's closing line rides in the GATE line.
EXCERPT = 200

# `<config-dir>/projects/<esc-cwd>/<sessionId>.jsonl`, where esc-cwd replaces
# every non-alphanumeric character with `-`. ASCII deliberately: str.isalnum() is
# true for accented letters and would leave them in place, so a cwd outside ASCII
# would build a path that does not exist and silently lose every GATE under it.
SAFE = frozenset(string.ascii_letters + string.digits)

# The three predicates below were each measured against this machine's own
# transcripts, on 2026-09-07, across both profiles (`~/.claude` and
# `~/.claude-st`). Quote the effective numbers, not the file count: the walk
# started from 4,829 `.jsonl`, but 4,089 of those are `agent-*.jsonl` sub-agent
# transcripts that `transcript_path` can never address and `final_assistant_text`
# skips, so only **608 sessions** contributed anything. Those 608 yield **5,083
# turn-ending closing lines** — the exact strings `gate_line` is handed, and the
# denominator every rate below is over.
#
# The corpus also grows while you read it (4,829 at the start of the run, 4,845
# a few minutes later), which is why this is pinned by date. An earlier pin of
# "611 transcripts" named the per-transcript shape of the same corpus, one
# closing line per file; that shape reads 601 lines today. No count here is a
# constant. Re-measure before quoting any of them.

# A question mark that closes a word, not any question mark anywhere. The
# difference is measured: a bare `"?" in line` test also fired on git's `??`
# shorthand inside backticks and on a URL query string, and this form fires on
# neither.
QMARK = re.compile(r"[\w\"'’)\]]\?")

# The other shape a prose gate takes: a trailing bracketed option list, as in
# `Apply commit "…"? [y / n / edit]`. Deliberately narrow — square brackets only,
# short word-ish tokens only. The loose first version accepted any parenthesised
# text containing a slash, which made `· resets 1pm (Asia/Tbilisi)` and every
# markdown link `](https://…)` a gate; this one fires on **3** closing lines in
# the corpus, and all 3 also match QMARK, so it has never been the reason a GATE
# was reported. It is kept for the mandated form above rather than for anything
# it has uniquely caught. (An earlier comment here claimed zero. It was wrong,
# and it survived a re-measurement of every number around it.)
CHOICE = re.compile(r"\[\s*\w[\w -]{0,11}(?:\s*[/|]\s*\w[\w -]{0,11}){1,4}\s*\]\s*$")

# The third shape, and the one neither of the above can see: a request for the
# operator's input written as a statement. Terminal punctuation was the sole
# discriminator before this — flipping the "." to a "?" on the same sentence
# turned a DONE into a GATE — and it is the wrong discriminator, because a
# worker under instruction to report rather than decide avoids question marks.
#
# Every alternative is clause-anchored: it must open a clause, after sentence
# punctuation or at the start of the line, with up to two connectives allowed
# in front. That anchor is the whole precision, and it was chosen on the corpus
# rather than by eye — unanchored, `tell me` also matches "an invitation to tell
# me the declines are wrong" and `your call` matches "per your call", both of
# which are prose *about* a decision already taken. Clause-anchoring keeps 39 of
# 45 `tell me` hits and 17 of 22 `waiting` hits while killing every one of those.
# Strict `^` anchoring was tried and rejected: it keeps only 6 of each.
#
# `[^A-Za-z0-9]{0,4}` at line start is load-bearing, not cosmetic — `gate_line`'s
# own strip does not remove a leading "-", so a bullet arrives as
# `- **Still waiting on you:`. The connectives are load-bearing too: without
# `still` the two real `Still waiting on you` gates are lost, without `or`/`just`
# so are `Or tell me plainly what's off` and `Just tell me what's on screen`.
#
# One candidate alternative was measured and **dropped outright**: `say the word`
# added 76 hits, almost all post-completion courtesy offers ("say the word and
# I'll clean it up"), and anchoring does not separate them from the genuine ones
# — 75 of the 76 survive clause-anchoring, because the offers already sit at a
# clause start. It was refused on its false-positive RATE, not on any claim that
# a wrong GATE loses a transition: hand-read, roughly three quarters of its hits
# are invitations, against roughly a quarter to a third for the arms above. That
# gap is the line. The cost of the refusal is real and worth naming — of those 76
# lines, the minority that are genuine blocks stay invisible, and no tightening
# was found that keeps them.
#
# One known gap, measured and deliberately not closed: the anchor accepts an em
# or en dash but not a spaced ASCII `--`. Across the 5,083 lines that costs
# nothing — spaced ASCII dashes appear in 3 lines, none carrying a trigger — so
# the pattern is left alone rather than widened for a case the corpus does not
# contain.
REQUEST = re.compile(
    r"(?i)"
    r"(?:^[^A-Za-z0-9]{0,4}|[.!?:;,—–]\s*)"
    r"(?:(?:or|and|but|so|then|just|still|otherwise|instead|now)\s+){0,2}"
    r"(?:tell me\b"
    r"|let me know (?:whether|which|what|when|how)\b"
    r"|your call\b"
    r"|confirm(?: and| whether| which)\b"
    r"|waiting (?:on|for) (?:you|your)\b"
    r"|I need (?:a|your) (?:decision|answer|call|ruling)\b"
    r"|(?:one line|a line|one word|a word) from you\b"
    r"|(?:one line|a line|one word|a word|your (?:word|answer|go-?ahead|call|decision|sign-?off|reply))"
    r"(?: from you)? unblocks? this\b"
    r")"
)


def registry_dirs():
    """(config dir, its sessions dir) for every profile being watched.

    Both halves are needed: the record is read out of `sessions/`, and the
    transcript that answers "was that an ending or a question" lives beside it
    under `projects/` in the same profile — which is why this cannot simply
    return `peer.roots()` the way `owned.py` does. The root list is still
    peer.py's; only the pair shape is built here, because forking the list is
    how this drifted apart in the first place.

    It used to read CLAUDE_CONFIG_DIR and fall back to `~/.claude` only when it
    was unset, so it watched ONE profile. A spawned worker does not register
    where its supervisor watches — measured 2026-09-07, supervisor under
    `~/.claude-st`, worker's record in `~/.claude/sessions/`, roots
    `['/Users/ns/.claude-st']`, worker visible to the watcher: **False**. The
    watcher armed, emitted its 30 s deafness WARN, and then nothing, ever, for a
    worker that was replying normally the whole time.

    That failure is worse than it sounds, and worse than it used to be. While
    `owned.py` was narrow too the run died loudly at readiness — "never became
    addressable" — and somebody investigated. Fix only that half and the
    supervisor resolves its worker, calls it healthy, and is then simply never
    notified again. The deafness WARN does not cover it: it fires once, around
    30 s, during exactly the window when a worker still on its trust gate
    legitimately matches nothing, which is the line an operator learns to ignore.
    """
    return [(d, os.path.join(d, "sessions")) for d in peer.roots()]


def wanted(ledger):
    """{name: (minted session id, pinned pid)} this run owns, or None.

    None when the ledger cannot be read at all. That matters: returning an empty
    mapping for an unreadable ledger makes "the path is wrong" look exactly like
    "no workers yet", and both then look like a healthy quiet watcher. Only
    main() has the context to say which, so hand it the difference.

    Missing or short lines are skipped, not fatal, and a row that carries neither
    identifier still yields an entry — the name alone is what this watched before
    ownership existed, and a legacy ledger must keep working.

    The two extra columns are carried because `registry_dirs` now sweeps every
    profile, and a name is only unique among the live sessions of one instant.
    `snapshot` joins on them; see the note there for why that is not optional.
    """
    out = {}
    try:
        with open(ledger) as fh:
            for ln in fh:
                if not ln.strip():
                    continue
                cols = ln.rstrip("\n").split("\t")
                name = cols[0].strip()
                if not name:
                    continue
                out[name] = (
                    cols[4].strip() if len(cols) >= 5 else "",
                    cols[5].strip() if len(cols) >= 6 else "",
                )
    except OSError:
        return None
    return out


def is_ours(rec, ident):
    """Does this registry record belong to the ledger row that claims the name?

    Widening `registry_dirs` to every profile made this necessary, and the risk
    it answers is *substitution*, not duplication: the watcher keys everything by
    name, so one session found twice collapses to one entry harmlessly, but a
    STRANGER session in another profile that happens to carry a worker's name
    would previously have been invisible and is now in range. Reporting its
    states as our worker's is the failure to prevent.

    So join the way `owned.py` does, on the session id this run MINTED, which
    nobody else can produce. The pid fallback is not decoration: `/clear` rotates
    a session's id in place, and without it a worker that cleared its context
    would stop matching, be reported `GONE`, and never be heard from again — a
    likelier event than a cross-profile name collision, so a strict id-only join
    would trade one silent loss for another.

    Two shapes deserve naming, because "legacy" is not one thing. The both-falsy
    branch is the FOUR-column row, which carries no identifier at all and is
    accepted on its name exactly as before. A FIVE-column row is not covered by
    it: column 5 is read as a minted id, and on those ledgers it holds a pid, so
    neither branch fires and the row matches nothing. That costs nothing here —
    column 1 of a five-column ledger holds a session uuid rather than a name, so
    it matched nothing before this join existed either, and the setup block
    refuses any ledger that is not exactly seven columns. Do not "fix" it by
    sniffing whether column 5 looks like a uuid: this join should be boring and
    TOTAL, and a heuristic about a format nobody writes any more is one nobody
    can falsify.

    Note also that `-` is TRUTHY, so an unpinned row takes the minted-id path
    rather than the legacy one and its worker stops matching if it later runs
    `/clear`. That is RARE rather than unreachable, and the difference was a
    measured mistake: this said "a row is unpinned only until its worker
    registers, and a rotation cannot precede that", which holds for every worker
    that clears readiness and for no other. A worker parked on the folder-trust
    gate registers nothing, so the pin at the end of the readiness block is
    skipped on its empty pid, and until 2026-09-16 nothing re-pinned it -- the
    rescue procedure sent a keystroke and never came back to column 6. Such a row
    carries `-` for the life of the run, and a `/clear` after that is the ordinary
    reachable case, not an impossible one.

    SKILL.md now re-pins at the end of the gate-clearing procedure, which makes it
    rare again. Do not read that as restoring the old claim: the re-pin is
    advisory by design (an unregistered worker is simply not pinned yet), so a
    row can still reach here unpinned and this join must keep handling it.
    """
    minted, pinned = ident
    if not minted and not pinned:
        return True
    if minted and (rec.get("sessionId") or "").lower() == minted.lower():
        return True
    return bool(pinned) and str(rec.get("pid")) == pinned


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


def transcript_path(config_dir, cwd, session_id):
    if not config_dir or not cwd or not session_id:
        return None
    esc = "".join(c if c in SAFE else "-" for c in cwd)
    return os.path.join(config_dir, "projects", esc, session_id + ".jsonl")


def tail_records(path, nbytes):
    """(records, whole_file) from the last nbytes of a .jsonl transcript.

    The leading fragment is dropped whenever the read started mid-file, and an
    unparseable line is skipped rather than fatal — a transcript is appended to
    while this reads it, so the last line can be half-written.
    """
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        start = max(0, fh.tell() - nbytes)
        fh.seek(start)
        blob = fh.read()
    lines = blob.decode("utf-8", "replace").split("\n")
    if start:
        lines = lines[1:]
    out = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out, start == 0


def final_assistant_text(records):
    """The text of the LAST thing this session said, or None if it said nothing.

    It returns at the first assistant record it meets scanning backwards and
    never walks past it, which is the whole staleness guarantee. Walk further and
    a turn that ended on a tool call — a worker whose last act was `SendMessage`
    and no prose — would be described by whatever it said in some *earlier* turn,
    and a question from ten minutes ago would be reported as a gate that is not
    open. An empty string is therefore a real answer, distinct from None: this
    turn ended without the worker saying anything.

    Sub-agent records are interleaved into the same file, so a session whose last
    act was spawning one would otherwise be described by its sub-agent's closing
    words rather than its own.
    """
    for rec in reversed(records):
        if rec.get("type") != "assistant" or rec.get("isSidechain"):
            continue
        msg = rec.get("message")
        if not isinstance(msg, dict):
            return None
        content = msg.get("content")
        if isinstance(content, str):
            return content
        parts = []
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "text":
                    continue
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return None


def gate_line(text):
    """The closing line, when it puts something to a human. Else None.

    Only the last non-empty line is ever tested, and the test stops there rather
    than searching upwards. A report body is full of questions it answers itself;
    a gate is the last thing on screen, and that asymmetry is the precision.

    Measured over the corpus pinned above, on 2026-09-07: `QMARK` and `CHOICE`
    together fire on 271 of 5,083 turn-ending lines (5.3%) — real questions put
    to a human ("Want me to push and open a draft PR?", "May I commit?"). Adding
    `REQUEST` brings 66 more, for 337 (6.6%). All 66 were read by hand. Every one
    is a second-person request addressed to the operator, and none is a category
    error — no prose about a sub-agent, nothing backward-looking, no line where
    the subject of "tell me" is a machine.

    A quarter to a third of them are invitations rather than live blocks, and the
    spread is the honest part, because the boundary is a judgement and not a
    measurement. Counting only lines where the worker says outright that it is not
    waiting — "not blocking yet", "I'll keep driving unless", "otherwise I'll
    relay when it lands", "tell me if you want that restored" — gives 19 of 66.
    Including lines merely parked on an event the operator will report gives 24.
    An independent hand-read of the same corpus put it at 14. Any of those is
    several times the "six" this docstring first claimed, and the error ran in the
    direction that flattered the change.

    They ship anyway. Ten candidate guards were built and scored: the best removes
    twelve of them and strands a real gate — a worker holding a PR open on
    "Tell me if you'd rather hold `d3612af` out of #86". Exactly 7 corpus lines
    are both question-mark gates and REQUEST matches, and the guards were caught
    firing inside those too; they survive today only because they happen to carry
    a question mark, which is the luck this predicate exists to stop depending on.
    A handful of extra screen-reads is the cheaper error.
    """
    for raw in reversed(text.split("\n")):
        line = raw.strip().strip("*_`> ").strip()
        if not line:
            continue
        if QMARK.search(line) or CHOICE.search(line) or REQUEST.search(line):
            return line
        return None
    return None


def excerpt(line):
    clean = " ".join("".join(c if c.isprintable() else " " for c in line).split())
    return clean if len(clean) <= EXCERPT else clean[: EXCERPT - 3].rstrip() + "..."


# Set once, by the guard in gate_excerpt, so its WARN fires on the first failure
# rather than on every transition after it.
_PREDICATE_WARNED = False


def gate_excerpt(source):
    """The GATE line's payload for a worker whose turn just ended, or None.

    None for every failure to read as well as for a genuine ending, and that is
    deliberate: a missing transcript, an unreadable one, a cwd that has moved, a
    file with no assistant record in range all fall back to exactly today's DONE.
    This can add information to a transition; it can never take a DONE away.

    That last sentence is why `gate_line` is called inside a bare `except`. It is
    the one frame here that runs a regex over arbitrary worker prose, and it sits
    on the only path that reads a transcript at all: `main` has no broad handler,
    so an exception raised here does not lose one GATE, it kills the watcher and
    with it every DONE, ASK, ATTN, CLEAR and GONE for every worker in the run.
    A predicate that throws must degrade to today's DONE, not to silence.
    """
    path = transcript_path(*source) if source else None
    if not path:
        return None
    for nbytes in TAIL_BYTES:
        try:
            records, whole = tail_records(path, nbytes)
        except OSError:
            return None
        text = final_assistant_text(records)
        if text is not None:
            try:
                line = gate_line(text)
                return excerpt(line) if line else None
            except Exception as exc:
                # Degrading to DONE is the right failure. Doing it *quietly* is
                # not: a predicate that throws on most prose would turn every
                # gate on this machine back into a completion, indefinitely, and
                # look exactly like a week in which nobody happened to ask
                # anything. So it says so once, through the line this file
                # already reserves for reporting on itself.
                global _PREDICATE_WARNED
                if not _PREDICATE_WARNED:
                    _PREDICATE_WARNED = True
                    print(
                        f"WARN gate predicate raised, GATE degrades to DONE "
                        f"until fixed: {exc!r}",
                        flush=True,
                    )
                return None
        if whole:
            break  # already read the entire file; a bigger window finds no more
    return None


def snapshot(names, paths):
    """(name -> state, torn names, next poll's memo, unknown-value notes, sources).

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

    `sources` carries what it takes to find a worker's transcript — its profile,
    its `cwd` and its `sessionId`, all three straight off the record this poll has
    already read. It is collected for every live worker and *used* for none of
    them here: the read happens in main(), once, on the transition, because doing
    it in this loop would open a transcript for every idle worker on every poll.
    """
    out = {}
    torn = set()
    fresh = {}
    notes = {}
    sources = {}
    for config_dir, d in registry_dirs():
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
            if not is_ours(rec, names[name]):
                continue  # same name, another profile, not our session
            fresh[path] = name
            sources[name] = (config_dir, rec.get("cwd"), rec.get("sessionId"))
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
    return out, torn, fresh, notes, sources


def main():
    # Monitor closes the pipe when it stops reading, and CPython turns that into
    # a BrokenPipeError plus an "Exception ignored in: <stdout>" dump at
    # interpreter shutdown. Nothing is lost — the watcher was being torn down
    # anyway — but the tail of a Monitor ends on what reads as a crash. Dying
    # from SIGPIPE like any other filter says the same thing silently.
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    # Every line this file prints can carry text it did not write — a GATE
    # excerpt is the worker's own prose, an ATTN carries a registry value, a WARN
    # carries a path — and on a stdout that cannot encode it, `print` raises.
    # There is no handler above the poll loop, so that is not a mangled line, it
    # is a dead watcher, and a dead watcher is indistinguishable from a healthy
    # quiet one: there is no mid-run liveness check. Reproduced with
    # PYTHONIOENCODING=ascii and a real GATE excerpt carrying an em-dash —
    # UnicodeEncodeError at the GATE print, rc=1, and every later transition for
    # every worker lost. A replacement character costs a supervisor nothing.
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):
        pass  # a stdout that is not a reconfigurable text stream

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
            now, torn, paths, notes, sources = snapshot(names, paths)
        else:
            now, torn, notes, sources = {}, set(), {}, {}

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
                # One transition, two renderings. The registry says only that a
                # turn ended; the transcript says whether the worker ended it by
                # asking a human something, which is the case a bare DONE has
                # been reporting as a completion. GATE is that DONE with the
                # question attached, never a second line about the same event —
                # so a supervisor that sees GATE has been told the turn ended
                # too, and a wrong reading costs it no transition. What a wrong
                # reading does cost is spent in aggregate: enough false GATEs and
                # the line stops discriminating at all. That is why the
                # predicates are graded on their false-positive rate rather than
                # on the price of any single line.
                gate = gate_excerpt(sources.get(name))
                if gate:
                    # The last-resort net under the reconfigure above. "It can
                    # never take a DONE away" is a claim about this line, so it
                    # is enforced on this line: whatever stops the excerpt from
                    # reaching stdout, the transition is still reported.
                    try:
                        print(f'GATE {name} -- "{gate}"', flush=True)
                    except Exception:
                        print(f"DONE {name}", flush=True)
                else:
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
