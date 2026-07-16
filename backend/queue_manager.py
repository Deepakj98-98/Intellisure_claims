"""
queue_manager.py — Handles multiple claims submitted at (close to) the
same time, in FIFO order.

WHY THIS EXISTS — THE QUESTION IT ANSWERS:
"What happens when we get two or more PDFs at once?" Without this
module, two concurrent requests to /upload would race each other
through shared resources (same S3 client, same Bedrock session
handling, same logger) with no defined ordering — not incorrect,
necessarily, but not PREDICTABLE either, which matters when you want
to demo "here's what happens with a batch of claims" and get the same
answer every time.

HOW THIS WORKS:
An in-memory asyncio.Queue holds pending claims. A single background
worker task consumes the queue ONE AT A TIME, in the exact order
claims were enqueued (FIFO — First In, First Out). This is a
deliberate simplicity choice for a hackathon build: predictable,
easy to explain and demo ("claims are processed in the order they
arrive, nothing is lost or race-conditioned"), and correct.

HOW YOU'D SCALE THIS FOR REAL CONCURRENT PROCESSING LATER:
Each claim already gets its own unique session_id and claim_id, and
DynamoDB/S3 both handle concurrent writes safely — there's no
correctness reason you couldn't run N worker tasks instead of 1,
consuming from the same queue concurrently (see WORKER_COUNT below).
FIFO ordering across the whole batch is only guaranteed with exactly
1 worker; with N workers, claims still each get processed correctly,
just not necessarily in strict submission order. Set WORKER_COUNT via
environment variable if you want to demonstrate that tradeoff
explicitly — default is 1 (strict FIFO) because predictability is more
useful for a demo than raw throughput at hackathon scale.

WHAT'S IN THE QUEUE, AND WHAT CLAIM STATE LOOKS LIKE WHILE WAITING:
Each queue item is a dict: {"claim_id": ..., "file_bytes": ...,
"filename": ..., "future": asyncio.Future}. The future is what lets
the API endpoint that enqueued the claim `await` its eventual result
without blocking the queue itself — the HTTP request stays open,
waiting, while OTHER claims can still be enqueued behind it.
"""

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)

WORKER_COUNT = int(os.getenv("QUEUE_WORKER_COUNT", "1"))  # 1 = strict FIFO; >1 = concurrent, order not guaranteed


@dataclass
class QueuedClaim:
    claim_id: str
    filename: str
    file_bytes: bytes
    enqueued_at: float
    future: "asyncio.Future" = field(default=None, repr=False)


class ClaimQueue:
    """Wraps an asyncio.Queue with a background worker pool that
    processes claims by calling a provided `process_fn` — this class
    doesn't know or care what "processing a claim" actually involves
    (that's app.py's pipeline function), it only guarantees ordering
    and that every enqueued claim eventually gets processed exactly
    once, with its result delivered back via its Future."""

    def __init__(self, process_fn: Callable[[str, str, bytes], "asyncio.Future"]):
        """
        Args:
            process_fn: an async function with signature
                        (claim_id, filename, file_bytes) -> dict,
                        which runs the actual 6-stage agent pipeline
                        for one claim. Injected rather than imported
                        directly, so this module has no dependency on
                        app.py — it can be tested standalone (see the
                        __main__ block at the bottom of this file).
        """
        self._queue: "asyncio.Queue[QueuedClaim]" = asyncio.Queue()
        self._process_fn = process_fn
        self._workers: list = []
        self._status_board: dict = {}  # claim_id -> status string, for GET /claims/{id}/status polling

    async def start_workers(self):
        """Call this once, at FastAPI startup (see app.py's startup
        event). Spins up WORKER_COUNT background tasks that pull from
        the queue forever."""
        for i in range(WORKER_COUNT):
            worker_task = asyncio.create_task(self._worker_loop(worker_id=i))
            self._workers.append(worker_task)
        logger.info(f"Claim queue started with {WORKER_COUNT} worker(s) (FIFO guaranteed only when WORKER_COUNT=1)")

    async def enqueue(self, filename: str, file_bytes: bytes) -> str:
        """Adds a claim to the FIFO queue. Returns immediately with a
        claim_id — the actual processing happens asynchronously in the
        background worker(s). Callers who need the RESULT (not just
        the claim_id) should use enqueue_and_wait() instead."""
        claim_id = f"CLAIM-{uuid.uuid4().hex[:12].upper()}"
        item = QueuedClaim(
            claim_id=claim_id,
            filename=filename,
            file_bytes=file_bytes,
            enqueued_at=asyncio.get_event_loop().time(),
            future=asyncio.get_event_loop().create_future(),
        )
        self._status_board[claim_id] = "QUEUED"
        await self._queue.put(item)
        logger.info(f"Enqueued claim {claim_id} ({filename}) — queue depth now {self._queue.qsize()}")
        return claim_id, item.future

    async def enqueue_and_wait(self, filename: str, file_bytes: bytes) -> dict:
        """Convenience method for the single-file /upload endpoint,
        which wants to enqueue AND get the result back in one call —
        still goes through the same FIFO queue as batch uploads, so
        single-file and batch uploads behave identically under the
        hood, just with different calling conventions."""
        claim_id, future = await self.enqueue(filename, file_bytes)
        result = await future
        return result

    def get_status(self, claim_id: str) -> str:
        return self._status_board.get(claim_id, "NOT_FOUND")

    def queue_depth(self) -> int:
        """How many claims are currently waiting (not yet started) —
        useful to surface in the UI so users understand WHY their
        claim might take a moment during a batch upload."""
        return self._queue.qsize()

    async def _worker_loop(self, worker_id: int):
        """The actual FIFO consumer. Runs forever, pulling one claim
        at a time and running it through process_fn. Any exception
        from process_fn is caught HERE (not just inside app.py) as a
        last line of defense — a single claim's processing failure
        must never kill the worker loop itself, or every claim queued
        behind it would be stuck forever."""
        while True:
            item: QueuedClaim = await self._queue.get()
            self._status_board[item.claim_id] = "PROCESSING"
            logger.info(f"[Worker {worker_id}] Starting claim {item.claim_id} ({item.filename})")

            try:
                result = await self._process_fn(item.claim_id, item.filename, item.file_bytes)
                self._status_board[item.claim_id] = result.get("routing_decision", "COMPLETE")
                if not item.future.done():
                    item.future.set_result(result)
            except Exception as e:
                logger.error(f"[Worker {worker_id}] Claim {item.claim_id} failed: {e}")
                self._status_board[item.claim_id] = "FAILED"
                if not item.future.done():
                    item.future.set_exception(e)
            finally:
                self._queue.task_done()
