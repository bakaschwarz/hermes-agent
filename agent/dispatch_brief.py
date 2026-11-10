"""A harness-dispatched turn's brief, reduced to a bounded memory query.

A wrapper that spawns Hermes for one task (an orchestrator adapter, a cron runner, a CI job)
opens the turn with an envelope: identity boilerplate, a ``## … Wake Payload`` section, the
task's data lines, then pages of history and evidence. The task is named once, in those data
lines; head and tail are scaffolding that repeat on every dispatch of every task.

Handing that envelope to a memory provider as a *query* ranks the scaffolding above anything
about the task, and a provider that caps query length rejects the oversized query outright —
so the recall block comes back empty on precisely the runs that need it most. ``task_subject``
reads the subject the envelope already carries (``- issue: PER-765 <title>``), which is
semantic and inside any provider's budget.

A turn that carries no envelope is its own query — but never an oversized one. Past a
provider's query cap (gbrain rejects a ``--question`` over 2000 chars outright with
``invalid_params``, and after PER-754 the plugin skips such a call instead), recall dies
silently on exactly the turns that paste a log or trace in. ``memory_query`` windows such a
message to its head and its tail — a paste's question is usually the trailing line — while
every message inside the budget passes through untouched.
"""

from __future__ import annotations

import re
from typing import Any

# A query, not a document: long enough for "KEY-123 <title> — <scope>", short enough that no
# provider's length cap bites (gbrain rejects a question over 2000 chars outright).
_MAX_SUBJECT_CHARS = 500
# The same rule for a turn that carries no envelope: a chat message pastes a log or trace and
# asks about it at the end, and a provider past its cap ranks nothing (gbrain: 2000 chars, then
# ``invalid_params``; the plugin skips instead of sending). Budget stays well under that cap.
_MAX_QUERY_CHARS = 1500
# Enough of the opening to keep the framing ("here is the trace — why does X fail?").
_WINDOW_HEAD_CHARS = 500
# Marks the cut so a debug log shows the query was windowed, not that the text was mangled.
_WINDOW_OMITTED = " … "
# Below this an envelope is shorter than the boilerplate it would have to beat, so there is
# nothing to gain from rewriting the turn's text.
_MIN_BRIEF_CHARS = 600

# The section header a dispatching harness writes above the payload: "## <...> Wake Payload".
_WAKE_SECTION_RE = re.compile(r"^##[ \t]+[^\n]{0,80}?\bWake Payload\b[^\n]*$", re.MULTILINE)
# The payload's own one-line summary — the only line of the envelope that names the task.
# Case-sensitive on purpose: the task-context block spells it ``- Issue: `` and is handled below.
_ISSUE_SUBJECT_RE = re.compile(
    r"^[ \t]*[-*][ \t]*issue:[ \t]*(?P<subject>[^\n]*\S)[ \t]*$", re.MULTILINE
)
# Task-context block form, used when the payload summary is absent: ``- Issue: "PER-77"`` with
# the title on one of the next few lines.
_TASK_ISSUE_RE = re.compile(
    r'^[ \t]*[-*][ \t]*Issue:[ \t]*"?(?P<key>[A-Za-z][A-Za-z0-9_]*-\d+)"?[ \t]*$', re.MULTILINE
)
_TASK_TITLE_RE = re.compile(r'^[ \t]*[-*][ \t]*Title:[ \t]*(?P<title>[^\n]*\S)[ \t]*$', re.MULTILINE)
# How far the title may sit below its issue key before the pair is not a task context block.
_TASK_TITLE_WINDOW = 400


def _bound(text: str) -> str:
    """Cut a subject to a query-sized window on a word boundary."""
    text = " ".join(text.split())
    if len(text) <= _MAX_SUBJECT_CHARS:
        return text
    cut = text[:_MAX_SUBJECT_CHARS].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:-—–") or text[:_MAX_SUBJECT_CHARS]


def _window(text: str) -> str:
    """An ordinary message is its own query — unless it is too big to be one.

    Cut to ``_MAX_QUERY_CHARS`` keeping both ends: the head carries what the message is about,
    the tail carries the ask (a pasted log ends in its question, and a "why does this fail?"
    opening is at the head). Both edges land on a word boundary; inside the budget the text is
    returned exactly as it came in.
    """
    if len(text) <= _MAX_QUERY_CHARS:
        return text
    flat = " ".join(text.split())
    head_budget = min(_WINDOW_HEAD_CHARS, (_MAX_QUERY_CHARS - len(_WINDOW_OMITTED)) // 2)
    head = flat[:head_budget]
    if " " in head:
        head = head.rsplit(" ", 1)[0]
    tail_budget = _MAX_QUERY_CHARS - len(_WINDOW_OMITTED) - len(head)
    tail = flat[-tail_budget:]
    if " " in tail:
        tail = tail.split(" ", 1)[1]
    return f"{head}{_WINDOW_OMITTED}{tail}"


def _task_context_subject(text: str) -> str:
    """``- Issue: "PER-77"`` + ``- Title: "…"`` → ``PER-77 …``; ``""`` when absent."""
    issue = _TASK_ISSUE_RE.search(text)
    if issue is None:
        return ""
    title = _TASK_TITLE_RE.search(text, issue.end())
    if title is None or title.start() - issue.end() > _TASK_TITLE_WINDOW:
        return ""
    key = issue.group("key")
    title_text = title.group("title").strip().strip(chr(34)).strip()
    # Some harnesses fold the identifier into the title line; never emit it twice.
    if title_text.lower().startswith(key.lower()):
        return title_text
    return f"{key} {title_text}".strip()


def task_subject(text: Any) -> str:
    """The task subject of a harness-dispatched brief; ``""`` when ``text`` is not one.

    Guarded by the payload's own header plus a length floor, so an ordinary message — however
    long — keeps its own text as the query.
    """
    if not isinstance(text, str) or len(text) < _MIN_BRIEF_CHARS:
        return ""
    if _WAKE_SECTION_RE.search(text) is None:
        return ""
    match = _ISSUE_SUBJECT_RE.search(text)
    subject = match.group("subject").strip().strip('"').strip() if match else ""
    return _bound(subject or _task_context_subject(text))


def memory_query(text: Any) -> str:
    """What a memory provider should rank a turn against: the brief's subject when the turn is
    a dispatch envelope, the turn's own text (windowed to a query-sized head + tail) otherwise,
    and ``""`` for non-text."""
    if not isinstance(text, str):
        return ""
    return task_subject(text) or _window(text)
