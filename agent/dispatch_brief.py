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

A turn that carries no envelope is left alone: ``memory_query`` is the identity there.
"""

from __future__ import annotations

import re
from typing import Any

# A query, not a document: long enough for "KEY-123 <title> — <scope>", short enough that no
# provider's length cap bites (gbrain rejects a question over 2000 chars outright).
_MAX_SUBJECT_CHARS = 500
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
    a dispatch envelope, ``text`` itself otherwise (``""`` for non-text)."""
    if not isinstance(text, str):
        return ""
    return task_subject(text) or text
