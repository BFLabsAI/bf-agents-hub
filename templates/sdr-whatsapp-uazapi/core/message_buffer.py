"""Debounce buffer for bursty WhatsApp messages.

WhatsApp users often send several short messages in a row. MessageBuffer
coalesces them: each new message resets a debounce timer; when the window
elapses with no new message, the accumulated text is flushed via a callback.

Pure, deterministic logic — time is INJECTED (pass `now`, or an injectable
clock). No real timers / asyncio here so tests are fully deterministic. The
app layer is responsible for actually scheduling the flush after `window`
seconds of inactivity (e.g. via an asyncio task that calls `due()` / `flush`).
"""
from __future__ import annotations

from typing import Callable


class MessageBuffer:
    """Per-sender debounce buffer.

    Args:
        window_seconds: debounce window; flush fires after this much silence.
        on_flush: callback invoked as on_flush(sender, combined_text) when a
            buffer is flushed. May be sync or async (implementer decides).
    """

    def __init__(
        self,
        window_seconds: float = 8.0,
        on_flush: Callable[[str, str], object] | None = None,
    ) -> None:
        self.window_seconds = window_seconds
        self.on_flush = on_flush
        # sender -> {"parts": [str], "last_ts": float}
        self._buffers: dict[str, dict] = {}

    def push(self, sender: str, msg: str, now: float) -> None:
        """Append `msg` for `sender` and (re)start the debounce window at `now`.

        `now` is an injected monotonic-ish timestamp (float seconds).
        """
        buf = self._buffers.get(sender)
        if buf is None:
            buf = {"parts": [], "last_ts": now}
            self._buffers[sender] = buf
        buf["parts"].append(msg)
        buf["last_ts"] = now

    def due(self, now: float) -> list[str]:
        """Return senders whose debounce window has elapsed as of `now`.

        (i.e. now - last_ts >= window_seconds). Does not mutate state.
        """
        return [
            sender
            for sender, buf in self._buffers.items()
            if now - buf["last_ts"] >= self.window_seconds
        ]

    def flush(self, sender: str) -> str | None:
        """Pop and return the combined buffered text for `sender`.

        Removes the sender's buffer and (if configured) invokes on_flush.
        Returns None if nothing is buffered for that sender.
        """
        buf = self._buffers.pop(sender, None)
        if buf is None:
            return None
        combined = "\n".join(buf["parts"])
        if self.on_flush is not None:
            self.on_flush(sender, combined)
        return combined
