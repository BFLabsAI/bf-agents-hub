from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict
from contextvars import ContextVar
from typing import Optional

from app.transaction_log import LogEvent, Transaction, _now
from app.db import get_conn, get_dict_conn

logger = logging.getLogger("italo.monitor")

# Carries the active Transaction for the current async task.
# Each _handle_message task sets its own value — no cross-task leakage.
_current_tx: ContextVar[Optional[Transaction]] = ContextVar("current_tx", default=None)


def _ensure_table() -> None:
    try:
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS agent_transactions (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    from_number TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    status TEXT,
                    user_message TEXT,
                    agent_reply TEXT,
                    events TEXT
                )
            """)
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("Could not create agent_transactions table: %s", exc)


class LogStore:
    def __init__(self, maxsize: int = 200) -> None:
        self._txs: OrderedDict[str, Transaction] = OrderedDict()
        self._maxsize = maxsize
        self._queues: list[asyncio.Queue] = []
        self._db_ready = False

    def _init_db(self) -> None:
        if not self._db_ready:
            _ensure_table()
            self._db_ready = True

    def _persist(self, tx: Transaction) -> None:
        try:
            self._init_db()
            conn = get_conn()
            try:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO agent_transactions
                        (id, session_id, from_number, started_at, completed_at, status, user_message, agent_reply, events)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        session_id   = EXCLUDED.session_id,
                        from_number  = EXCLUDED.from_number,
                        started_at   = EXCLUDED.started_at,
                        completed_at = EXCLUDED.completed_at,
                        status       = EXCLUDED.status,
                        user_message = EXCLUDED.user_message,
                        agent_reply  = EXCLUDED.agent_reply,
                        events       = EXCLUDED.events
                """, (
                    tx.id, tx.session_id, tx.from_number,
                    tx.started_at, tx.completed_at, tx.status,
                    tx.user_message, tx.agent_reply,
                    json.dumps([e.to_dict() for e in tx.events]),
                ))
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("persist_transaction failed: %s", exc)

    # ── Transaction lifecycle ─────────────────────────────────────────────────

    def new_transaction(self, session_id: str, from_number: str, user_message: str) -> Transaction:
        tx = Transaction.new(session_id, from_number, user_message)
        self._store(tx)
        _current_tx.set(tx)
        self._broadcast({"type": "transaction_start", "transaction": tx.to_dict()})
        return tx

    def complete_transaction(self, reply: str) -> None:
        tx = _current_tx.get()
        if tx is None:
            return
        tx.agent_reply = reply
        tx.completed_at = _now()
        tx.status = "completed"
        self._persist(tx)
        self._broadcast({"type": "transaction_complete", "transaction": tx.to_dict()})

    def fail_transaction(self, error: str) -> None:
        tx = _current_tx.get()
        if tx is None:
            return
        tx.status = "error"
        tx.completed_at = _now()
        self.log_event("error", {"message": error})
        self._persist(tx)
        self._broadcast({"type": "transaction_error", "transaction_id": tx.id, "error": error})

    # ── Event logging ─────────────────────────────────────────────────────────

    def log_event(self, type: str, data: dict) -> None:
        tx = _current_tx.get()
        if tx is None:
            return
        event = LogEvent(type=type, timestamp=_now(), data=data)
        tx.events.append(event)
        self._broadcast({
            "type": "event",
            "transaction_id": tx.id,
            "event": event.to_dict(),
        })

    # ── Query ─────────────────────────────────────────────────────────────────

    def get_all(self, session_id: str | None = None) -> list[dict]:
        try:
            conn = get_dict_conn()
            try:
                cur = conn.cursor()
                if session_id:
                    cur.execute(
                        "SELECT * FROM agent_transactions WHERE session_id = %s ORDER BY started_at DESC",
                        (session_id,),
                    )
                else:
                    cur.execute(
                        "SELECT * FROM agent_transactions ORDER BY started_at DESC"
                    )
                rows = cur.fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("get_all failed, falling back to in-memory: %s", exc)
            txs = list(reversed(list(self._txs.values())))
            if session_id:
                txs = [t for t in txs if t.session_id == session_id]
            return [t.to_dict() for t in txs]

    # ── SSE pub/sub ───────────────────────────────────────────────────────────

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._queues.remove(q)
        except ValueError:
            # unsubscribe é idempotente: fila já removida não é erro.
            pass

    # ── Internal ──────────────────────────────────────────────────────────────

    def _store(self, tx: Transaction) -> None:
        self._txs[tx.id] = tx
        if len(self._txs) > self._maxsize:
            self._txs.popitem(last=False)

    def _broadcast(self, payload: dict) -> None:
        dead = []
        for q in self._queues:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self.unsubscribe(q)


# Module-level singleton — imported by itarget_client and whatsapp_api
log_store = LogStore()
