# CareSync: Async Voice-to-PCR Pipeline for EMS

*Writeup template — fill in results and figures before publishing.*

## Abstract

One paragraph on the motivation (paramedic charting latency), the approach
(async FastAPI pipeline + Claude tool-use extraction + SQL warehouse), and the
headline numbers (extraction confidence, queue-to-complete latency, HR
cross-check accuracy from `analytics/05_extraction_quality.sql`).

## 1. Introduction

Paramedics currently finish a run and then spend 15–30 minutes typing up the
Patient Care Report (PCR) from memory or from handwritten notes. The charting
delay compresses every downstream step: ED handoff is partly verbal because
the written record isn't ready, billing lags the encounter by hours or days,
and any analytics on EMS-level patterns sits on stale data. The goal of
CareSync is to reduce the gap between the end of the run and a clean,
structured, queryable PCR by having the medic narrate instead of type, and
doing the structuring automatically.

## 2. System design

High-level: a React SPA records audio, a FastAPI backend runs a bounded async
pipeline (ASR → LLM extraction → SQL write), and analytics run as plain SQL
against a MySQL 8.4 warehouse. See the top-level `README.md` for the
architecture diagram and the full design-decisions section.

### 2.1 Async extraction pipeline

Why an in-process `asyncio.Semaphore` instead of Celery / RQ / a hosted queue.
Walk through the lifecycle of a single job: queued → transcribing → extracting
→ completed, and the failure modes the semaphore prevents.

### 2.2 Structured extraction with Claude tool-use

Why `tool_choice` with an explicit schema beats "please return JSON", with the
clinical failure mode (`spo2: 'ninety four'`) as the motivating example.
Include the system prompt and the tool schema from `backend/app/extract.py`.

### 2.3 Warehouse schema

Why `pcr_reports` and `pcr_extractions` are separate tables. Why vitals are a
narrow time-series table keyed on `(encounter_id, t)` rather than embedded in
the PCR. Trigger for `finalized_at`.

## 3. Evaluation

### 3.1 End-to-end latency

From `analytics/05_extraction_quality.sql`: per-day queued → completed wall
time. Report mean and tail.

### 3.2 Extraction accuracy vs. ground-truth vitals

The HR cross-check: fraction of jobs where LLM-extracted heart rate is within
10 BPM of the median measured HR in the first minute of the vitals stream.
This is the closest thing the system has to a continuous eval, because it
grounds the LLM output in sensor data that was captured independently of the
narration. Discuss limitations (narration timing vs. measurement timing,
confounds when the medic quotes a number off the monitor).

### 3.3 Model vs. regex fallback

Same cross-check, split by the `model` column in `pcr_extractions`. The
regex extractor is a useful floor: it tells you what fraction of the
improvement over baseline is actually attributable to the LLM.

## 4. Related work

EMS documentation automation, Whisper-based clinical scribes, structured
output from LLMs (tool-use vs. JSON mode vs. constrained decoding). Keep it
short; this is an engineering writeup.

## 5. Limitations and future work

- Single-host deployment. The in-process semaphore is the right design for
  the current scale; beyond one replica it needs to move to Redis/NATS.
- ASR quality in-cabin is the biggest source of error and this project
  doesn't try to solve it.
- The HR cross-check is a proxy for accuracy, not ground truth. A real eval
  needs a held-out set of hand-annotated PCRs.

## Acknowledgments

Thanks to Prof. Eran Bendavid for feedback on the evidence-synthesis angle
and for pushing on the evaluation story.
