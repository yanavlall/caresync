"""
CareSync backend entrypoint.

Routes:
  GET  /health                         liveness check
  POST /encounters/                    start a new encounter + empty PCR
  POST /encounters/{id}/extract        upload audio, kick off pipeline, return job_id
  GET  /jobs/{job_id}                  get job status + transcript + extraction (for polling)
  GET  /pcrs/                          list recent PCRs with their latest extraction

Run with:
  uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
"""

import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, Path, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from . import db, pipeline
from .config import settings
from .schemas import (
    CreateEncounterRequest,
    CreateEncounterResponse,
    JobResponse,
    PCRExtraction,
    PCRListItem,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logger = logging.getLogger("caresync")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    logger.info("CareSync backend started")
    yield
    await db.close_pool()
    logger.info("CareSync backend stopped")


app = FastAPI(title="CareSync Backend", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "caresync-backend", "version": "0.2.0"}


@app.post("/encounters/", response_model=CreateEncounterResponse)
async def create_encounter(req: CreateEncounterRequest):
    """
    Start a new encounter and create a draft PCR for it.

    In a real deployment the patient would be looked up or created separately;
    for the demo we pick a random seeded patient so the foreign key is valid.
    """
    patient = await db.fetch_one(
        "SELECT patient_id FROM patients ORDER BY RAND() LIMIT 1"
    )
    if patient is None:
        raise HTTPException(500, "No patients in database; seed the warehouse first")

    encounter_id = await db.execute(
        """
        INSERT INTO encounters (patient_id, ambulance_id, started_at, chief_complaint, severity)
        VALUES (%s, %s, NOW(), %s, %s)
        """,
        (patient["patient_id"], req.ambulance_id, req.chief_complaint, req.severity),
    )

    # Every encounter gets a draft PCR. Author is hardcoded to seeded user 1.
    pcr_id = await db.execute(
        """
        INSERT INTO pcr_reports (encounter_id, author_id, status, created_at)
        VALUES (%s, 1, 'draft', NOW())
        """,
        (encounter_id,),
    )

    row = await db.fetch_one(
        "SELECT started_at FROM encounters WHERE encounter_id=%s",
        (encounter_id,),
    )
    return CreateEncounterResponse(
        encounter_id=encounter_id,
        pcr_id=pcr_id,
        started_at=row["started_at"],  # type: ignore[index]
    )


@app.post("/encounters/{encounter_id}/extract")
async def extract_encounter(
    encounter_id: int = Path(..., gt=0),
    audio: UploadFile = File(...),
):
    """
    Accept an audio blob, enqueue a transcribe+extract job, return job_id.

    This endpoint returns immediately. The client should poll /jobs/{job_id}
    until status is 'completed' or 'failed'.
    """
    # Make sure the encounter exists and has a PCR.
    enc = await db.fetch_one(
        "SELECT encounter_id FROM encounters WHERE encounter_id=%s",
        (encounter_id,),
    )
    if enc is None:
        raise HTTPException(404, f"Encounter {encounter_id} not found")

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(400, "Empty audio upload")

    job_id = await pipeline.create_job(encounter_id, audio_bytes)
    pipeline.schedule_job(job_id, encounter_id, audio_bytes)

    return {"job_id": job_id, "encounter_id": encounter_id, "status": "queued"}


@app.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: int = Path(..., gt=0)):
    """Fetch job status and (if completed) the extracted PCR fields."""
    job = await db.fetch_one(
        "SELECT * FROM extraction_jobs WHERE job_id=%s",
        (job_id,),
    )
    if job is None:
        raise HTTPException(404, f"Job {job_id} not found")

    extraction: PCRExtraction | None = None
    if job["status"] == "completed":
        row = await db.fetch_one(
            """
            SELECT patient_name, patient_age, patient_sex,
                   blood_pressure, heart_rate, respiratory_rate, spo2, temperature, gcs,
                   chief_complaint, hpi, assessment, treatment, confidence
            FROM pcr_extractions
            WHERE job_id=%s
            ORDER BY extraction_id DESC LIMIT 1
            """,
            (job_id,),
        )
        if row is not None:
            extraction = PCRExtraction(**{k: v for k, v in row.items() if v is not None})

    return JobResponse(
        job_id=job["job_id"],
        encounter_id=job["encounter_id"],
        status=job["status"],
        transcript=job.get("transcript"),
        extraction=extraction,
        error=job.get("error"),
        created_at=job["created_at"],
        updated_at=job["updated_at"],
    )


@app.get("/pcrs/", response_model=list[PCRListItem])
async def list_pcrs(limit: int = Query(20, ge=1, le=100)):
    """List recent PCRs with their most recent extraction (if any)."""
    rows = await db.fetch_all(
        """
        SELECT
            p.pcr_id,
            p.encounter_id,
            p.status,
            e.chief_complaint,
            p.created_at,
            ex.patient_name,
            ex.extracted_at
        FROM pcr_reports p
        JOIN encounters e ON e.encounter_id = p.encounter_id
        LEFT JOIN (
            SELECT x1.*
            FROM pcr_extractions x1
            JOIN (
                SELECT pcr_id, MAX(extraction_id) AS max_id
                FROM pcr_extractions
                GROUP BY pcr_id
            ) x2 ON x2.max_id = x1.extraction_id
        ) ex ON ex.pcr_id = p.pcr_id
        ORDER BY p.created_at DESC
        LIMIT %s
        """,
        (limit,),
    )
    return [PCRListItem(**row) for row in rows]
