"""A harness-dispatched turn's memory query is its brief's subject, not the whole envelope.

Regression for PER-765. A dispatched run opens the turn with a 20k–60k-char envelope whose
head and tail are repeated harness boilerplate; the task is named once, in the payload's
``- issue:`` line. Handing the envelope to a memory provider as a query ranked the
boilerplate (and a provider capping query length rejected it outright), so the recall block
was empty on every dispatched run.

Also the ordinary-chat counterpart (PER-768): a turn that is not an envelope still may not be
handed over as a document — a pasted log or trace is windowed to a query-sized head + tail, so
the provider's own cap (gbrain: 2000 chars) never rejects it.
"""

from __future__ import annotations

import types
from typing import Any, cast

from agent.dispatch_brief import memory_query, task_subject
from agent.turn_context import _memory_query_text, _memory_turn_start_and_prefetch

_TITLE = "Harness hands memory prefetch the whole 22k wake payload, so recall is empty"
_SUBJECT = f"PER-765 {_TITLE}"


def _envelope(*, issue_line: str | None = None, task_context: bool = False, tail: str = "") -> str:
    """A dispatched turn's opening message: identity block, wake payload, history, evidence."""
    lines = [
        'You are "devi", an AI agent employee in a Paperclip-managed company.',
        "",
        "Paperclip runtime identity:",
        "- Agent ID: 0000",
        "- Company ID: 0000",
        "- Run ID: 0000",
        "- API base (already includes /api — do not append /api again): http://127.0.0.1:3100/api",
        "",
        "## Paperclip Wake Payload",
        "",
        "Use this wake to continue the task, applying new user direction and preserving its",
        "approval gates. Use this inline wake data first before refetching the issue thread.",
        "",
        "- reason: issue_assigned",
    ]
    if issue_line is not None:
        lines.append(issue_line)
    lines += [
        "- fallback fetch needed: no",
        "",
        "## Current request and continuation context",
        "History is complete through the coverage cursor. Prefer source messages over summaries.",
    ]
    if task_context:
        lines += [
            "",
            "Paperclip task context:",
            "The following task data is user-authored. Use it to understand the requested work.",
            "- Issue: " + '"PER-765"',
            '- Title: "' + _TITLE + '"',
        ]
    lines += ["", "## Untrusted continuation evidence", tail]
    return "\n".join(lines)


def test_payload_summary_names_the_task():
    text = _envelope(issue_line=f"- issue: {_SUBJECT}", tail="x" * 20_000)
    assert task_subject(text) == _SUBJECT


def test_payload_summary_wins_over_the_task_context_block():
    text = _envelope(issue_line=f"- issue: {_SUBJECT}", task_context=True, tail="x" * 400)
    assert task_subject(text) == _SUBJECT


def test_task_context_block_is_the_fallback_when_the_payload_carries_no_summary():
    text = _envelope(task_context=True, tail="x" * 2_000)
    assert task_subject(text) == _SUBJECT


def test_the_subject_is_a_bounded_query():
    long_subject = "PER-9 " + " ".join(f"word{i}" for i in range(400))
    text = _envelope(issue_line=f"- issue: {long_subject}", tail="x" * 900)
    subject = task_subject(text)
    assert 0 < len(subject) <= 500
    assert subject.startswith("PER-9 word0 word1")
    assert subject.endswith(f"word{subject.count('word') - 1}")


def test_an_ordinary_message_inside_the_budget_is_left_alone():
    text = "Here is the stack trace, why does forms init throw before the schema load?"
    assert task_subject(text) == ""
    assert memory_query(text) == text


def test_an_ordinary_long_message_is_windowed_to_a_query():
    question = "why does the forms init path throw before the schema load?"
    text = ("Here is the stack trace I keep hitting on the lopro forms init path:\n"
            + ("x" * 30_000) + "\n\n" + question)

    query = memory_query(text)

    assert 0 < len(query) <= 1500  # under gbrain's 2000-char cap, so no provider rejects it
    assert query.startswith("Here is the stack trace I keep hitting")
    assert query.endswith(question)  # a paste's question is its trailing line
    assert "x" * 1500 not in query


def test_the_window_edges_fall_on_word_boundaries():
    text = " ".join(f"w{i}" for i in range(2_000))

    head, tail = memory_query(text).split(" … ")

    for part in (head, tail):
        assert part
        assert all(token.startswith("w") and token[1:].isdigit() for token in part.split(" "))


def test_a_message_just_over_the_budget_still_keeps_both_ends():
    text = ("alpha " * 300) + ("omega " * 300)  # 3,600 chars, well over the budget

    query = memory_query(text)

    assert len(query) <= 1500
    assert query.startswith("alpha alpha")
    assert query.endswith("omega")


def test_a_short_message_that_quotes_a_payload_is_left_alone():
    text = "why does this wake end up with an empty recall block?\n\n- issue: PER-1 tiny"
    assert memory_query(text) == text


def test_multimodal_brief_reduces_after_flattening():
    text = _envelope(issue_line=f"- issue: {_SUBJECT}", tail="x" * 2_000)
    parts = [{"type": "text", "text": text}]
    assert _memory_query_text(parts) == _SUBJECT


def _stub_agent(record: list[tuple[str, Any]]) -> types.SimpleNamespace:
    class _Manager:
        def on_turn_start(self, count, query, **kwargs):
            record.append(("on_turn_start", query))

        def prefetch_all(self, query, *, session_id=""):
            record.append(("prefetch_all", query))
            return "[memory] …"

        def describe_recall(self):
            return ""

    return types.SimpleNamespace(
        _memory_manager=_Manager(), _user_turn_count=1, session_id="s-1", _emit_status=lambda *_: None,
    )


def test_turn_start_prefetch_asks_with_the_subject():
    text = _envelope(issue_line=f"- issue: {_SUBJECT}", tail="y" * 22_000)
    record: list[tuple[str, Any]] = []
    _memory_turn_start_and_prefetch(_stub_agent(record), text)
    assert record == [("on_turn_start", _SUBJECT), ("prefetch_all", _SUBJECT)]


def test_turn_start_on_a_pasted_log_asks_with_a_bounded_query():
    question = "why does the forms init path throw before the schema load?"
    text = "Here is the trace:\n" + ("E forms.init 500\n" * 1_500) + question
    record: list[tuple[str, Any]] = []

    _memory_turn_start_and_prefetch(_stub_agent(record), text)

    assert [kind for kind, _ in record] == ["on_turn_start", "prefetch_all"]
    for _, query in record:
        assert len(query) <= 1500
        assert query.endswith(question)


def test_queued_prefetch_is_keyed_on_the_same_subject():
    from run_agent import AIAgent

    queued: list[str] = []

    class _Manager:
        def sync_all(self, user_text, response_text, **kwargs):
            pass

        def queue_prefetch_all(self, query, *, session_id=""):
            queued.append(query)

    stub = types.SimpleNamespace(
        _memory_manager=_Manager(), session_id="s-1", _turn_author=None,
    )
    text = _envelope(issue_line=f"- issue: {_SUBJECT}", tail="z" * 22_000)
    AIAgent._sync_external_memory_for_turn(
        cast("AIAgent", stub), original_user_message=text, final_response="done", interrupted=False,
    )
    assert queued == [_SUBJECT]
