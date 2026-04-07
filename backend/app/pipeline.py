"""
Async extraction pipeline.

When a paramedic finishes recording, the /encounters/{id}/extract endpoint
enqueues an extraction_jobs row and kicks off a background worker. The
worker:

  1. Updates job status to 'transcribing' and calls the ASR backend.
  2. Updates job status to 'extracting' and calls the LLM extractor.
  3. Writes the structured fields into pcr_extractions, promotes the PCR
     to 'submitted', and marks the job 'completed'.

A bounded asyncio.Semaphore limits how many jobs run concurrently. This
matters because each job holds an LLM API call open, and without a cap
you could exhaust your rate limit or connection pool under load.

The frontend polls GET /jobs/{job_id} to follow progress.
"""

import asyncio
import logging
from typing import Any

from . import asr, extract, db
from .config import settings
from .schemas import PCRExtraction

logger = logging.getLogger(__name__)

# Cap concurrent extraction jobs. Protects downstream APIs and the DB pool.
_sem = asyncio.Semaphore(settings.max_concurrent_jobs)


async def create_job(encounter_id: int, audio_bytes: bytes) -> int:
    """Enqueue a new extraction job and return its id."""
    # Persist the audio to the job row as a path reference. In production this
    # would go to object storage (S3 / GCS) and the path would be a URI; here
    # we keep it simple and just record the byte count.
    audio_path = f"inline:{len(audio_bytes)}bytes"
    job_id = await db.execute(
        """
        INSERT INTO extraction_jobs (encounter_id, status, audio_path)
        VALUES (%s, 'queued', %s)
        """,
        (encounter_id, audio_path),
    )
    logger.info("Job %d enqueued for encounter %d", job_id, encounter_id)
    return job_id


async def _set_status(job_id: int, status: str, **extra: Any) -> None:
    """Update a job's status, optionally setting additional columns."""
    sets = ["status=%s"]
    params: list[Any] = [status]
    for k, v in extra.items():
        sets.append(f"{k}=%s")
        params.append(v)
    params.append(job_id)
    await db.execute(
        f"UPDATE extraction_jobs SET {', '.join(sets)} WHERE job_id=%s",
        tuple(params),
    )


async def _get_pcr_id_for_encounter(encounter_id: int) -> int:
    row = await db.fetch_one(
        "SELECT pcr_id FROM pcr_reports WHERE encounter_id=%s",
        (encounter_id,),
    )
    if row is None:
        raise RuntimeError(f"No PCR report exists for encounter {encounter_id}")
    return int(row["pcr_id"])


async def _persist_extraction(pcr_id: int, job_id: int, extraction: PCRExtraction) -> None:
    """Write the structured extraction into pcr_extractions."""
    await db.execute(
        """
        INSERT INTO pcr_extractions (
            pcr_id, job_id,
            patient_name, patient_age, patient_sex,
            blood_pressure, heart_rate, respiratory_rate, spo2, temperature, gcs,
            chief_complaint, hpi, assessment, treatment,
            model, confidence
        ) VALUES (
            %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s
        )
        """,
        (
            pcr_id, job_id,
            extraction.patient_name, extraction.patient_age, extraction.patient_sex,
            extraction.blood_pressure, extraction.heart_rate, extraction.respiratory_rate,
            extraction.spo2, extraction.temperature, extraction.gcs,
            extraction.chief_complaint, extraction.hpi, extraction.assessment, extraction.treatment,
            settings.anthropic_model if settings.anthropic_api_key else "regex-fallback",
            float(extraction.confidence),
        ),
    )
    # Promote the PCR from draft to submitted now that it has structured content.
    await db.execute(
        "UPDATE pcr_reports SET status='submitted' WHERE pcr_id=%s AND status='draft'",
        (pcr_id,),
    )


async def run_job(job_id: int, encounter_id: int, audio_bytes: bytes) -> None:
    """
    Run one job end-to-end: transcribe, extract, persist.

    Runs under the concurrency semaphore. Catches and records all exceptions
    so a single bad job cannot crash the worker pool.
    """
    async with _sem:
        try:
            # 1. Transcribe.
            await _set_status(job_id, "transcribing")
            transcript = await asr.transcribe(audio_bytes)
            await _set_status(job_id, "extracting", transcript=transcript)

            # 2. Extract structured fields.
            extraction = await extract.extract(transcript)

            # 3. Persist into the warehouse.
            pcr_id = await _get_pcr_id_for_encounter(encounter_id)
            await _persist_extraction(pcr_id, job_id, extraction)

            await _set_status(job_id, "completed")
            logger.info("Job %d completed (pcr_id=%d, confidence=%.2f)", job_id, pcr_id, extraction.confidence)

        except Exception as e:
            logger.exception("Job %d failed: %s", job_id, e)
            await _set_status(job_id, "failed", error=str(e)[:2000])


def schedule_job(job_id: int, encounter_id: int, audio_bytes: bytes) -> asyncio.Task:
    """Fire-and-forget scheduling. Returns the Task for test hooks."""
    return asyncio.create_task(run_job(job_id, encounter_id, audio_bytes))
