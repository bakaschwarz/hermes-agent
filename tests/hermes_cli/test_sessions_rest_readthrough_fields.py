"""The read-through surface `/api/sessions/{id}/messages` serves back (PER-1045).

A turn re-read from the store must render as richly as a live one: per-turn usage, a tool
call's latency, an agent-authored widget, and the pending question. Every field is additive.
"""

from hermes_cli.web_routers.sessions import _project_for_display


def _assistant(**extra):
    return {"role": "assistant", "content": "hi", **extra}


def _clarify_call(call_id="call-1"):
    return {"id": call_id, "function": {"name": "clarify", "arguments": "{}"}}


def test_usage_is_lifted_to_a_top_level_message_field():
    usage = {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}
    projected = _project_for_display([_assistant(display_metadata={"usage": usage})])[0]
    assert projected["usage"] == usage
    # Old clients keep reading the stored metadata: the shape they saw is unchanged.
    assert projected["display_metadata"] == {"usage": usage}


def test_tool_duration_is_lifted_only_on_a_tool_result_row():
    tool = _project_for_display([{
        "role": "tool", "content": "ok", "tool_name": "terminal", "tool_call_id": "c1",
        "display_metadata": {"duration_ms": 1234},
    }])[0]
    assert tool["duration_ms"] == 1234
    assert "duration_ms" not in _project_for_display([_assistant(display_metadata={"duration_ms": 5})])[0]


def test_a_widget_payload_is_lifted():
    widget = {"id": "w-1", "state": {"count": 0}}
    assert _project_for_display([_assistant(display_metadata={"widget": widget})])[0]["widget"] == widget


def test_a_row_without_metadata_gains_no_new_key():
    projected = _project_for_display([_assistant()])[0]
    for key in ("usage", "widget", "duration_ms", "awaiting_user", "pending_question"):
        assert key not in projected


def test_an_unanswered_clarify_marks_the_turn_awaiting_user():
    rows = [{"role": "user", "content": "do it?"}, _assistant(content="", tool_calls=[_clarify_call()])]
    projected = _project_for_display(rows, open_turn=True)
    assert projected[-1]["awaiting_user"] is True
    assert projected[-1]["pending_question"] == {"tool_call_id": "call-1"}


def test_an_answered_clarify_is_not_awaiting():
    rows = [
        {"role": "user", "content": "do it?"},
        _assistant(content="", tool_calls=[_clarify_call()]),
        {"role": "tool", "content": "yes", "tool_call_id": "call-1"},
    ]
    assert not any(message.get("awaiting_user") for message in _project_for_display(rows, open_turn=True))


def test_a_page_that_is_not_the_open_one_is_not_marked():
    rows = [{"role": "user", "content": "do it?"}, _assistant(content="", tool_calls=[_clarify_call()])]
    assert not any(message.get("awaiting_user") for message in _project_for_display(rows))


def test_a_settled_last_turn_hides_an_older_open_clarify():
    rows = [
        _assistant(content="", tool_calls=[_clarify_call()]),
        _assistant(content="done", finish_reason="stop"),
    ]
    assert not any(message.get("awaiting_user") for message in _project_for_display(rows, open_turn=True))


def test_a_non_clarify_tool_call_is_not_a_pending_question():
    call = {"id": "c9", "function": {"name": "terminal", "arguments": "{}"}}
    rows = [_assistant(content="", tool_calls=[call])]
    assert not any(message.get("awaiting_user") for message in _project_for_display(rows, open_turn=True))
