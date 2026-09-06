"""Background worker that polls non-terminal OpenRouter batches.

Every POLL_INTERVAL seconds we look for BatchJob rows that aren't terminal and
(1) refresh their status/results via GET /beta/batches/{id}, and (2) when a
batch completes, mirror the answers into a kind="batch" conversation so the
results show up in the normal chat UI.
"""

import json
import logging
import threading
import time

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import SessionLocal
from app.models import BatchItem, BatchJob, Conversation, Message, utcnow
from app.services.openrouter import (
    OpenRouterError,
    extract_batch_answer,
    get_batch,
    is_batch_error,
    is_batch_terminal,
)

log = logging.getLogger("batch_worker")

POLL_INTERVAL_SECONDS = 30


def poll_job(job_id: int) -> None:
    """One-shot synchronous poll + finalize for a single job. Safe to call from
    FastAPI BackgroundTasks or the worker loop."""
    db = SessionLocal()
    try:
        job = db.scalar(
            select(BatchJob).options(selectinload(BatchJob.items))
            .where(BatchJob.id == job_id)
        )
        if job is None or job.external_id is None:
            return
        if is_batch_terminal(job.status):
            return

        batch = get_batch(job.external_id)
        job.status = batch.get("status", job.status)
        job.results_json = json.dumps(batch, ensure_ascii=False)

        counts = batch.get("request_counts") or {}
        job.completed_items = int(counts.get("completed", 0))
        job.failed_items = int(counts.get("failed", 0))

        if is_batch_terminal(job.status):
            job.finalized_at = utcnow()
            job.error = None
            if is_batch_error(job.status):
                err = batch.get("error")
                job.error = err if isinstance(err, str) else str(err)
            _finalize_items(job, batch)
            if job.status == "completed":
                _create_conversation(db, job)
        db.commit()
    except OpenRouterError as exc:
        log.warning("poll job %s failed: %s", job_id, exc)
    except Exception:  # noqa: BLE001
        log.exception("poll job %s crashed", job_id)
    finally:
        db.close()


def _finalize_items(job: BatchJob, batch: dict) -> None:
    """Copy per-line results from the OpenRouter batch into BatchItem rows."""
    by_custom_id = {job_item.custom_id: job_item for job_item in job.items}
    for result in (batch.get("results") or []):
        if not isinstance(result, dict):
            continue
        custom_id = str(result.get("custom_id") or "")
        item = by_custom_id.get(custom_id)
        if item is None:
            continue
        status, answer, error = extract_batch_answer(result)
        item.status = status
        item.answer = answer
        item.error = error


def _create_conversation(db, job: BatchJob) -> None:
    """Mirror a completed batch into a kind="batch" conversation so it shows up
    in the normal conversation list (like imported phone batches). Uses the
    caller's open session `db`."""
    if job.conversation_id is not None:
        return
    conv = Conversation(
        external_id=job.external_id,
        kind="batch",
        model=job.model,
        title=job.title,
        account_id=job.account_id,
    )
    db.add(conv)
    db.flush()
    for item in job.items:
        if item.status == "pending":
            continue
        db.add(Message(
            conversation_id=conv.id,
            role="user",
            content=item.prompt or item.custom_id,
        ))
        if item.status == "completed" and item.answer:
            db.add(Message(
                conversation_id=conv.id,
                role="assistant",
                content=item.answer,
                model=job.model,
            ))
        elif item.error:
            db.add(Message(
                conversation_id=conv.id,
                role="assistant",
                content=f"[error] {item.error}",
                model=job.model,
            ))
    job.conversation_id = conv.id


def _poll_due() -> None:
    db = SessionLocal()
    try:
        jobs = db.scalars(
            select(BatchJob).where(
                BatchJob.external_id.is_not(None),
                BatchJob.status.notin_(("completed", "failed", "expired", "cancelled")),
            )
        ).all()
        ids = [job.id for job in jobs]
    finally:
        db.close()

    for job_id in ids:
        poll_job(job_id)


def start_batch_worker() -> None:
    """Start the daemon polling thread (idempotent)."""
    if getattr(start_batch_worker, "_started", False):
        return
    start_batch_worker._started = True

    def loop() -> None:
        log.info("batch worker started (every %ss)", POLL_INTERVAL_SECONDS)
        time.sleep(5)  # small startup delay
        while True:
            try:
                _poll_due()
            except Exception:  # noqa: BLE001
                log.exception("batch worker tick failed")
            time.sleep(POLL_INTERVAL_SECONDS)

    thread = threading.Thread(target=loop, name="batch-worker", daemon=True)
    thread.start()