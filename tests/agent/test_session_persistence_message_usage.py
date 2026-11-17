"""The per-message usage stamp (PER-1045): the assistant row for a call carries that call's usage,
consumed once so a later synthetic row cannot inherit it."""

from types import SimpleNamespace

from agent.session_persistence import _db_flush_row


def _agent():
    return SimpleNamespace(_pending_message_usage=None)


def test_the_assistant_row_takes_the_pending_usage_and_clears_it():
    agent = _agent()
    agent._pending_message_usage = {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4,
                                    "cost_status": "unknown"}
    row = _db_flush_row(agent, {"role": "assistant", "content": "hi"}, False)
    assert row["display_metadata"]["usage"]["total_tokens"] == 4
    assert row["token_count"] == 4
    assert agent._pending_message_usage is None
    later = _db_flush_row(agent, {"role": "assistant", "content": "next"}, False)
    assert not (later.get("display_metadata") or {}).get("usage")


def test_a_row_without_pending_usage_keeps_its_metadata():
    agent = _agent()
    row = _db_flush_row(agent, {"role": "assistant", "content": "hi",
                                "display_metadata": {"reasoning_tokens": 7}}, False)
    assert row["display_metadata"] == {"reasoning_tokens": 7}


def test_a_user_row_does_not_consume_the_pending_usage():
    agent = _agent()
    agent._pending_message_usage = {"total_tokens": 4}
    _db_flush_row(agent, {"role": "user", "content": "hi"}, False)
    assert agent._pending_message_usage == {"total_tokens": 4}
