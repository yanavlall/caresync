# CareSync

**Voice-to-PCR pipeline for EMS.** Paramedics narrate a patient encounter, CareSync transcribes the audio, extracts structured Patient Care Report (PCR) fields with an LLM, and writes the result into a SQL warehouse alongside the raw vitals stream. The point is to cut the time between the end of an ambulance run and a clean, queryable chart, without asking the medic to fill a form while driving.

![status](https://img.shields.io/badge/status-prototype-blue)
![python](https://img.shields.io/badge/python-3.12-blue)
![mysql](https://img.shields.io/badge/mysql-8.4-blue)

## What's in here

- **An async FastAPI backend** that accepts audio uploads, runs a bounded `asyncio.Semaphore`–gated pipeline (transcribe → extract → persist), and exposes a polling job endpoint. The HTTP layer never blocks on LLM latency.
- **A React + TypeScript frontend** built around `MediaRecorder` with a live level meter, a timer, and an 800 ms polling loop against the job endpoint.
- **A MySQL 8.4 warehouse** with a normalized schema for encounters, vitals, transcripts, PCR reports, alerts, extraction jobs, and structured extractions — all loaded and seeded by `make up`.
- **Plain-SQL analytics** for chart-close latency, FULLTEXT search over transcripts, gap-and-islands hypoxia detection, data-quality checks, and a cross-check of LLM-extracted vitals against the measured vitals stream.
- **Claude tool-use extraction** with a regex fallback so the whole system runs end-to-end with no API keys in CI.


## Architecture

```
  ┌──────────────┐   audio blob        ┌─────────────────────────────────┐
  │              │  (multipart POST)   │         FastAPI backend         │
  │  React app   │ ──────────────────▶ │                                 │
  │  (MediaRec.) │                     │  /encounters/{id}/extract       │
  │              │ ◀─ job_id ────────  │     │                           │
  │              │                     │     ▼                           │
  │   polls      │                     │  asyncio.create_task            │
  │ /jobs/{id}   │ ──────────────────▶ │     │  (bounded Semaphore)      │
  │              │ ◀─ status + PCR ──  │     ▼                           │
  └──────────────┘                     │  ASR ──▶ Claude (tool_use) ──▶  │
                                       │                  │              │
                                       │                  ▼              │
                                       │            aiomysql pool        │
                                       └─────────────┬───────────────────┘
                                                     │
                                                     ▼
                                       ┌─────────────────────────────────┐
                                       │          MySQL 8.4              │
                                       │                                 │
                                       │  encounters  vitals  alerts     │
                                       │  pcr_reports  transcripts       │
                                       │  extraction_jobs  pcr_extract.  │
                                       └─────────────────────────────────┘
```

Three moving pieces:

1. **Frontend** (`frontend/`) — a small React + TypeScript SPA that captures audio with `MediaRecorder`, uploads it, and polls a job endpoint. No state management library; the whole flow is a single `App.tsx`.
2. **Backend** (`backend/app/`) — FastAPI + `aiomysql`. The HTTP handlers never block: audio upload returns immediately with a `job_id`, and a background task runs the transcribe → extract → persist pipeline under a bounded `asyncio.Semaphore`.
3. **Warehouse** (`initdb/`, `analytics/`) — MySQL 8.4 with a normalized schema (encounters, vitals, transcripts, PCR reports, alerts) plus two tables for the async layer (`extraction_jobs`, `pcr_extractions`). Analytics queries live as plain SQL files under `analytics/`.

## Quickstart

Requires Docker, `make`, and (for the frontend) Node 20+.

```bash
# Optional: copy the env template and fill in your keys. Without a key
# the backend falls back to a regex extractor so everything still runs.
cp .env.example .env
# then edit .env to set ANTHROPIC_API_KEY=sk-ant-...

# Bring up MySQL + backend. The db is seeded with 100 patients,
# 200 encounters, ~120k vitals rows, and ~140 transcripts.
make up

# Smoke test the async pipeline end-to-end.
make seed-demo

# Run every analytics query against the warehouse.
make analytics

# In a second terminal, start the frontend.
cd frontend
npm install
npm start
# → http://localhost:3000
```

`make down` stops the stack; `make reset` wipes the MySQL volume so the next `make up` re-runs the init scripts.

## Async pipeline

The extraction pipeline is the part of the system most relevant to the "scalable systems" side of the project. The flow:

1. `POST /encounters/{id}/extract` reads the uploaded audio into memory, inserts a row into `extraction_jobs` with status `queued`, and calls `asyncio.create_task(run_job(...))`. The HTTP handler returns `{job_id}` inside a few milliseconds; the client never blocks on transcription or LLM latency.
2. `run_job` enters a module-level `asyncio.Semaphore(max_concurrent_jobs)`. This caps how many jobs are simultaneously holding an LLM connection open. Without it, a burst of uploads would either saturate the Anthropic rate limit or exhaust the aiomysql pool, and neither failure mode is graceful.
3. Inside the semaphore the worker transitions the job through `transcribing` → `extracting` → `completed`, updating `extraction_jobs.status` at each boundary so the polling frontend has something to display. Each transition is a single autocommitted UPDATE through the pool.
4. On success the worker inserts into `pcr_extractions` and promotes `pcr_reports.status` from `draft` to `submitted`. The whole function is wrapped in a try/except that writes any exception into `extraction_jobs.error` and sets `status='failed'`, so one bad job cannot take down the worker pool.
5. The frontend polls `GET /jobs/{job_id}` at 800 ms until the status is terminal. Polling was chosen over websockets because the job timescale is seconds-to-tens-of-seconds and the code stays much simpler.

The choice of an in-process `asyncio.Task` queue instead of Celery/RQ/Arq was deliberate for this scope. The total job count per host is bounded by the semaphore, every worker is the same FastAPI process that already has the DB pool, and there is no need for cross-host fairness or retries-with-backoff at this stage. When the project grows past a single backend replica, the right move is to swap `schedule_job` for an enqueue into Redis/NATS and keep everything else the same; the pipeline function itself is already idempotent at the DB level.

## LLM extraction

Structured extraction uses Claude with tool-use rather than prompting for JSON and parsing text. The relevant code is in `backend/app/extract.py`.

The tool schema mirrors the `PCRExtraction` Pydantic model exactly (patient demographics, vitals as integers with explicit units, narrative fields, and a self-reported confidence in `[0, 1]`). `tool_choice={"type": "tool", "name": "extract_pcr_fields"}` forces Claude to respond via the tool, so the response is guaranteed to be a dict that matches the schema rather than free text that might or might not parse. The Pydantic model then validates types and enum values before the row is written.

A regex fallback extractor lives next to the Claude call. If `ANTHROPIC_API_KEY` is unset, or if the Claude call raises, the pipeline uses the regex extractor and records `model='regex-fallback'` in `pcr_extractions`. This keeps the whole system runnable in development without API keys and gives the analytics side a clean way to filter on model provenance.

System prompt design is conservative. Two rules do most of the work: "only extract values that are explicitly stated" and "missing fields are better than hallucinated ones". Clinical data where the model invents a plausible vital is worse than a null, so the prompt and the confidence score are both tuned toward under-extraction rather than completeness.

## SQL warehouse and analytics

The schema (`initdb/01_schema.sql`) treats vitals as a separate time-series table keyed on `(encounter_id, t)`, with narrative data in `transcripts` (FULLTEXT indexed) and alerts as a derived table. PCR tracking is split across two tables on purpose: `pcr_reports` is the document lifecycle (draft → submitted → finalized, with a BEFORE UPDATE trigger that stamps `finalized_at`), and `pcr_extractions` is an append-only record of every LLM extraction attempt, joined back to the job that produced it. Multiple extractions per PCR are allowed so that re-runs with new models can be compared without overwriting history.

Analytics queries under `analytics/` are hand-written SQL, not an ORM:

- `01_kpis.sql` — chart-close latency (encounter end → PCR finalized) bucketed by ISO week, with mean and approximate p90 via `NTILE(10)`.
- `02_text_fts.sql` — FULLTEXT search over transcripts for encounters mentioning chest pain.
- `03_sessions.sql` — gap-and-islands: continuous hypoxia episodes (SpO2 < 90) lasting ≥ 120 seconds, built with `LAG` + running `SUM(boundary)`.
- `04_data_quality.sql` — encounters more than 24 hours old with no finalized PCR.
- `05_extraction_quality.sql` — per-day job count, mean confidence, mean queued→completed latency, and the count of jobs where the LLM-extracted heart rate is within 10 BPM of the median measured HR in the first minute of that encounter's vitals stream. This is the cross-check between the LLM output and the ground-truth sensor stream; it is the closest thing the system has to a continuous eval.

Run all of them with `make analytics`.

## Swapping ASR backends

ASR is behind a one-function interface in `backend/app/asr.py`: `async def transcribe(audio_bytes: bytes) -> str`. Three backends are implemented and selected via the `ASR_BACKEND` env var:

- `mock` (default) returns a fixed paramedic-narration transcript. This is what the seed demo uses, and it means the whole pipeline can be exercised without any ASR dependency.
- `openai_whisper` calls Whisper-1 via `AsyncOpenAI`. Requires `OPENAI_API_KEY`. Install `openai` from `requirements.txt` (commented out by default to keep the image small).
- `faster_whisper` runs `faster-whisper` locally. The blocking model inference is wrapped in `asyncio.to_thread` so it does not stall the event loop.

Adding a fourth backend is a matter of writing another `async def _xxx_transcribe(audio_bytes)` and registering it in the `_BACKENDS` dict. Nothing upstream of that function needs to know which one ran.

## Design decisions

A few choices are worth calling out explicitly, because they are the ones most likely to come up in review.

**Bounded `asyncio.Semaphore` instead of an unbounded task pool.** FastAPI will happily accept hundreds of concurrent uploads; without a cap, every one of them would race for the Anthropic rate limit and the MySQL pool at the same time, and the failure mode is a cliff (503s + rate-limit errors) rather than graceful queueing. The semaphore turns the cliff into a queue: jobs still get accepted and `job_id`s still get returned immediately, but the work behind them runs at a controlled rate and the frontend polling loop shows `queued` until a worker slot opens up. `max_concurrent_jobs` is an env var (default 4) so it can be tuned per deployment.

**aiomysql pool instead of per-request connections.** Every job does several writes (job status updates at each pipeline stage, one insert into `pcr_extractions`, one update to `pcr_reports`), so the cost of reconnecting per call would dominate. The pool is sized independently of the job semaphore, which matters because the HTTP layer also hits the pool for encounter creation and the PCR list endpoint, and those should not be starved by a burst of extractions.

**Claude tool-use instead of "please return JSON".** The difference is that tool-use gives you a schema the model must respect rather than a prompt it can ignore. In a clinical context "the model returned something that parsed as JSON but had `spo2: 'ninety four'`" is a real failure mode, and the tool schema plus Pydantic validation on the way in removes an entire class of parsing bugs. The regex fallback means the system is still testable end-to-end with no API key, which is useful both in CI and when demoing on a plane.

**Two tables for PCRs (`pcr_reports` + `pcr_extractions`).** The document lifecycle and the extraction history are different concerns with different cardinalities, and conflating them would either lose the history of previous extraction runs or force every UPDATE to the document to touch the extraction row. Keeping them separate is also what makes `05_extraction_quality.sql` easy to write: the join from jobs to extractions to vitals is a straight line.

## Layout

```
caresync/
├── backend/
│   ├── app/
│   │   ├── main.py          FastAPI routes
│   │   ├── pipeline.py      async job worker + semaphore
│   │   ├── extract.py       Claude tool-use + regex fallback
│   │   ├── asr.py           ASR backend dispatch
│   │   ├── db.py            aiomysql pool
│   │   ├── schemas.py       Pydantic models (also the LLM schema)
│   │   └── config.py        env-driven settings
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.tsx          single-page recorder + polling UI
│   │   ├── index.tsx
│   │   └── styles.css
│   ├── public/index.html
│   ├── package.json
│   ├── webpack.config.js
│   ├── tsconfig.json
│   └── .env.example
├── initdb/
│   ├── 01_schema.sql        core tables
│   ├── 02_security.sql      roles + app user
│   ├── 03_triggers.sql      finalized_at stamp
│   ├── 04_extraction.sql    extraction_jobs + pcr_extractions
│   └── 10_seed.sql          demo data
├── analytics/
│   ├── 01_kpis.sql
│   ├── 02_text_fts.sql
│   ├── 03_sessions.sql
│   ├── 04_data_quality.sql
│   └── 05_extraction_quality.sql
├── report/
│   ├── writeup.md           project writeup (template)
│   └── figures/             figures referenced by writeup + README
├── docker-compose.yml
├── Makefile
├── README.md
├── CONTRIBUTING.md
├── LICENSE
├── .env.example
└── .gitignore
```

