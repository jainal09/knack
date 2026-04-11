#!/usr/bin/env python3
"""Helpers for high-throughput JetStream publishing with explicit ack handling."""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import nats


NATS_JS_API_TIMEOUT = float(os.environ.get("NATS_JS_API_TIMEOUT", "30"))
NATS_JS_ASYNC_MAX_PENDING = int(os.environ.get("NATS_JS_ASYNC_MAX_PENDING", "4000"))
NATS_JS_STALL_TIMEOUT = float(os.environ.get("NATS_JS_STALL_TIMEOUT", "30"))
NATS_JS_ACK_TIMEOUT = float(os.environ.get("NATS_JS_ACK_TIMEOUT", "30"))


class JetStreamAsyncPublisher:
    """Bounded async publisher that tracks enqueue and ack failures explicitly."""

    def __init__(
        self,
        js: nats.js.JetStreamContext,
        subject: str,
        *,
        payload: bytes,
        ack_timeout: float = NATS_JS_ACK_TIMEOUT,
        max_pending: int = NATS_JS_ASYNC_MAX_PENDING,
        wait_stall: float = NATS_JS_STALL_TIMEOUT,
        error_prefix: str = "publish",
        error_log_limit: int = 5,
    ) -> None:
        self._js = js
        self._subject = subject
        self._payload = payload
        self._ack_timeout = ack_timeout
        self._max_pending = max_pending
        self._wait_stall = wait_stall
        self._error_prefix = error_prefix
        self._error_log_limit = error_log_limit
        self._logged_errors = 0
        self._pending: set[asyncio.Future[Any]] = set()

        self.sent = 0
        self.accepted = 0
        self.enqueue_errors = 0
        self.ack_errors = 0

    @property
    def total_errors(self) -> int:
        return self.enqueue_errors + self.ack_errors

    async def submit(self, payload: bytes | None = None) -> None:
        if len(self._pending) >= self._max_pending:
            await self._wait_for_progress()

        try:
            future = await self._js.publish_async(
                self._subject,
                payload or self._payload,
                wait_stall=self._wait_stall,
            )
        except nats.js.errors.TooManyStalledMsgsError as exc:
            self.enqueue_errors += 1
            self._log_error(f"publish enqueue stalled: {exc}")
            return
        except Exception as exc:
            self.enqueue_errors += 1
            self._log_error(f"publish enqueue failed: {exc}")
            return

        self._pending.add(future)
        self.accepted += 1
        if len(self._pending) >= max(32, self._max_pending // 4):
            self._drain_ready()

    async def submit_many(self, count: int, payload: bytes | None = None) -> None:
        for _ in range(count):
            await self.submit(payload)
        self._drain_ready()

    async def flush(self) -> None:
        while self._pending:
            done, pending = await asyncio.wait(
                self._pending,
                timeout=self._ack_timeout,
                return_when=asyncio.ALL_COMPLETED,
            )
            self._consume_done(done)
            if pending:
                self._expire_pending(
                    pending, f"publish ack timed out after {self._ack_timeout:.1f}s"
                )
                break

    def _drain_ready(self) -> None:
        done = {future for future in self._pending if future.done()}
        self._consume_done(done)

    async def _wait_for_progress(self) -> None:
        if not self._pending:
            return

        done, pending = await asyncio.wait(
            self._pending,
            timeout=self._ack_timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        self._consume_done(done)
        if not done and pending:
            self._expire_pending(
                pending, f"publish ack timed out after {self._ack_timeout:.1f}s"
            )

    def _consume_done(self, done: set[asyncio.Future[Any]]) -> None:
        for future in done:
            self._pending.discard(future)
            try:
                future.result()
            except Exception as exc:
                self.ack_errors += 1
                self._log_error(f"publish ack failed: {exc}")
            else:
                self.sent += 1

    def _expire_pending(
        self, pending: set[asyncio.Future[Any]], message: str
    ) -> None:
        self._log_error(message)
        for future in pending:
            if future not in self._pending:
                continue
            future.cancel()
            self._pending.discard(future)
            self.ack_errors += 1

    def _log_error(self, message: str) -> None:
        if self._logged_errors >= self._error_log_limit:
            return
        print(f"{self._error_prefix}: {message}", file=sys.stderr)
        self._logged_errors += 1
