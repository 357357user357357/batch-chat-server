import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import BatchItem, BatchJob
from app.schemas import BatchItemOut, BatchJobOut, BatchSubmitRequest
from app.security import get_account_id
from app.services.jsonl_batches import JsonlParseError, parse_jsonl
from app.services.openrouter import (
    DEFAULT_BATCH_MODEL,
    OpenRouterError,
    create_batch,
)
from app.services.batch_worker import poll_job

router = APIRouter(prefix="/api/batches", tags=["batches"])


def _job_out(job: BatchJob) -> BatchJobOut:
    return BatchJobOut(
        id=job.id,
        external_id=job.external_id,
        model=job.model,
        title=job.title,
        status=job.status,
        error=job.error,
        total_items=job.total_items,
        completed_items=job.completed_items,
        failed_items=job.failed_items,
        created_at=job.created_at,
        finalized_at=job.finalized_at,
        conversation_id=job.conversation_id,
        items=[BatchItemOut.model_validate(i) for i in job.items],
    )


@router.post("", response_model=BatchJobOut, status_code=201)
def submit_batch(
    payload: BatchSubmitRequest,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    account_id: str = Depends(get_account_id),
) -> BatchJobOut:
    """Parse a .jsonl, submit to the OpenRouter async Batch API, and start a
    background poller so the job finalizes by itself."""
    try:
        requests = parse_jsonl(
            payload.jsonl,
            system=payload.system,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
        )
    except JsonlParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    model = payload.model.strip() or DEFAULT_BATCH_MODEL
    title = (payload.title or requests[0]["custom_id"] or "Batch")[:255]

    job = BatchJob(model=model, title=title, status="pending", account_id=account_id)
    job.requests_json = json.dumps(requests, ensure_ascii=False)
    job.total_items = len(requests)

    # Persist items first so we can reference them after submit
    db.add(job)
    db.flush()
    for req in requests:
        prompt = _first_user_message(req.get("body", {}).get("messages", []))
        db.add(
            BatchItem(
                batch_job_id=job.id,
                custom_id=req["custom_id"],
                prompt=prompt,
            )
        )
    db.commit()

    # Talk to OpenRouter (raises 400 if the key isn't configured)
    try:
        created = create_batch(model, requests)
    except OpenRouterError as exc:
        job.status = "error"
        job.error = str(exc)
        db.commit()
        raise HTTPException(status_code=400, detail=str(exc))

    job.external_id = created.get("id")
    job.status = created.get("status", "pending")
    job.results_json = json.dumps(created, ensure_ascii=False)
    db.commit()
    db.refresh(job)

    # Kick an immediate poll (and the background worker keeps polling later)
    background.add_task(poll_job, job.id)
    return _job_out(job)


@router.get("", response_model=list[BatchJobOut])
def list_batches(
    db: Session = Depends(get_db),
    account_id: str = Depends(get_account_id),
) -> list[BatchJobOut]:
    jobs = db.scalars(
        select(BatchJob)
        .options(selectinload(BatchJob.items))
        .where(BatchJob.account_id == account_id)
        .order_by(BatchJob.id.desc())
    ).all()
    return [_job_out(job) for job in jobs]


@router.get("/{job_id}", response_model=BatchJobOut)
def get_batch_job(
    job_id: int,
    db: Session = Depends(get_db),
    account_id: str = Depends(get_account_id),
) -> BatchJobOut:
    job = _fetch_job(db, job_id, account_id)
    return _job_out(job)


@router.post("/{job_id}/poll", response_model=BatchJobOut)
def poll_batch_job(
    job_id: int,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    account_id: str = Depends(get_account_id),
) -> BatchJobOut:
    """Force a poll of the OpenRouter batch right now (then return)."""
    _fetch_job(db, job_id, account_id)
    background.add_task(poll_job, job_id)
    return _job_out(db.scalar(
        select(BatchJob).options(selectinload(BatchJob.items)).where(BatchJob.id == job_id)
    ))


@router.delete("/{job_id}", status_code=204)
def delete_batch_job(
    job_id: int,
    db: Session = Depends(get_db),
    account_id: str = Depends(get_account_id),
) -> None:
    job = _fetch_job(db, job_id, account_id)
    db.delete(job)
    db.commit()


def _fetch_job(db: Session, job_id: int, account_id: str) -> BatchJob:
    job = db.scalar(
        select(BatchJob)
        .options(selectinload(BatchJob.items))
        .where(
            BatchJob.id == job_id,
            BatchJob.account_id == account_id,
        )
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Batch job not found")
    return job


def _first_user_message(messages) -> str:
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "user":
            return str(msg.get("content") or "")
    if messages and isinstance(messages[0], dict):
        return str(messages[0].get("content") or "")
    return ""