import asyncio
import logging
from typing import Callable, Awaitable

logger = logging.getLogger("italo.buffer")

BUFFER_DELAY = 10.0  # seconds to wait after last message


class MessageBuffer:
    """
    Per-user debounce buffer. Collects incoming messages and flushes them
    as a single combined text once BUFFER_DELAY seconds pass with no new message.
    """

    def __init__(self, delay: float = BUFFER_DELAY) -> None:
        self._delay = delay
        self._buffers: dict[str, list] = {}   # from_number -> [msg, ...]
        self._tasks: dict[str, asyncio.Task] = {}  # from_number -> pending flush task
        self._lock = asyncio.Lock()

    async def add(
        self,
        msg,
        callback: Callable[[object], Awaitable[None]],
    ) -> None:
        """
        Add a message to the buffer for msg.from_number.
        Resets the flush timer. callback receives a synthetic msg with combined text.
        """
        key = msg.from_number
        async with self._lock:
            if key not in self._buffers:
                self._buffers[key] = []
            self._buffers[key].append(msg)

            # Cancel existing timer
            existing = self._tasks.get(key)
            if existing and not existing.done():
                existing.cancel()

            self._tasks[key] = asyncio.create_task(
                self._flush(key, callback)
            )
            count = len(self._buffers[key])

        if count > 1:
            logger.info("buffer | %s — %d messages buffered, timer reset", key, count)

    async def _flush(self, key: str, callback: Callable[[object], Awaitable[None]]) -> None:
        try:
            await asyncio.sleep(self._delay)
        except asyncio.CancelledError:
            return  # timer was reset by a new message

        async with self._lock:
            messages = self._buffers.pop(key, [])
            self._tasks.pop(key, None)

        if not messages:
            return

        if len(messages) == 1:
            combined_msg = messages[0]
        else:
            combined_text = "\n".join(m.text for m in messages)
            logger.info("buffer | %s — flushing %d messages: %r", key, len(messages), combined_text[:120])
            # Use first msg as base, override text and wamid (last message for reply-to)
            combined_msg = _CombinedMessage(messages, combined_text)

        await callback(combined_msg)


class _CombinedMessage:
    """Wraps multiple messages into one, combining their text."""

    def __init__(self, messages: list, combined_text: str) -> None:
        first = messages[0]
        last = messages[-1]
        self.from_number = first.from_number
        self.phone_number_id = first.phone_number_id
        self.sender_name = first.sender_name
        self.wamid = last.wamid
        self.text = combined_text
        self.raw_payload = last.raw_payload


message_buffer = MessageBuffer()
