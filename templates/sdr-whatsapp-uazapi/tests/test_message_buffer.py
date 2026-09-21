"""Tests for core.message_buffer.MessageBuffer (debounce buffer)."""
from __future__ import annotations

from core.message_buffer import MessageBuffer


def test_push_then_flush_after_window_returns_message():
    buf = MessageBuffer(window_seconds=8.0)
    buf.push("sender_a", "hello", now=100.0)
    # window elapsed -> sender is due
    assert buf.due(now=108.0) == ["sender_a"]
    assert buf.flush("sender_a") == "hello"


def test_second_push_before_window_resets_timer():
    buf = MessageBuffer(window_seconds=8.0)
    buf.push("sender_a", "hello", now=100.0)
    # 5s later (still inside window) another message arrives
    buf.push("sender_a", "world", now=105.0)
    # at 108 it would have been due relative to first push, but timer reset
    assert buf.due(now=108.0) == []
    # only after window from the *second* push
    assert buf.due(now=113.0) == ["sender_a"]


def test_flush_returns_all_buffered_messages_in_order():
    buf = MessageBuffer(window_seconds=8.0)
    buf.push("sender_a", "one", now=100.0)
    buf.push("sender_a", "two", now=101.0)
    buf.push("sender_a", "three", now=102.0)
    assert buf.due(now=110.0) == ["sender_a"]
    assert buf.flush("sender_a") == "one\ntwo\nthree"
    # flush is destructive: nothing left
    assert buf.flush("sender_a") is None
    assert buf.due(now=200.0) == []


def test_separate_senders_have_independent_buffers():
    buf = MessageBuffer(window_seconds=8.0)
    buf.push("sender_a", "a-one", now=100.0)
    buf.push("sender_b", "b-one", now=102.0)
    buf.push("sender_a", "a-two", now=103.0)
    # sender_a last_ts=103, sender_b last_ts=102
    # at 110: a -> 7s (not due), b -> 8s (due)
    assert buf.due(now=110.0) == ["sender_b"]
    # at 111: a -> 8s (due), b -> 9s (due)
    assert set(buf.due(now=111.0)) == {"sender_a", "sender_b"}
    # flushing one leaves the other intact
    assert buf.flush("sender_a") == "a-one\na-two"
    assert buf.flush("sender_b") == "b-one"


def test_flush_invokes_on_flush_callback():
    calls: list[tuple[str, str]] = []
    buf = MessageBuffer(window_seconds=8.0, on_flush=lambda s, t: calls.append((s, t)))
    buf.push("sender_a", "hi", now=100.0)
    buf.push("sender_a", "there", now=101.0)
    buf.flush("sender_a")
    assert calls == [("sender_a", "hi\nthere")]
    # flushing empty sender does NOT invoke callback
    calls.clear()
    assert buf.flush("nobody") is None
    assert calls == []
