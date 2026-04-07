# Contributing

This doc is the short version of "how to work on CareSync without stepping on
anything". The top-level `README.md` covers architecture and design; this
file is only the mechanical stuff.

## Local setup

You need Docker, `make`, and Node 20+ (for the frontend dev server).

```bash
cp .env.example .env           # fill in ANTHROPIC_API_KEY if you have one
make up                        # db + backend, detached
make seed-demo                 # end-to-end smoke test via curl
cd frontend && npm install && npm start
```

If the DB schema ever gets out of sync with what you expect, `make reset`
wipes the MySQL volume and the next `make up` re-runs every file under
`initdb/` in alphabetical order.

## Layout

Everything is in one repo; there's no monorepo tooling.

- `backend/app/` — FastAPI app. Start reading at `main.py` → `pipeline.py`
  → `extract.py`. `db.py` is the aiomysql pool, `asr.py` is the ASR
  dispatch, `schemas.py` has the Pydantic models (which double as the LLM
  tool schema).
- `frontend/src/` — React + TypeScript SPA. The whole flow is in `App.tsx`;
  there's no routing or state library.
- `initdb/` — SQL files loaded by MySQL on first container start. Numeric
  prefixes control load order (`01` → `10`), so add new migrations as
  `05_*.sql`, `06_*.sql`, etc.
- `analytics/` — read-only queries. Add a new file and it'll automatically
  get picked up by `make analytics`.
- `report/` — writeup scaffold and figures.

## Conventions

**Python.** 3.12. No formatter is pinned but the code is `ruff`-clean.
Prefer async everywhere — the pipeline is async end-to-end and mixing in
sync DB calls will stall the event loop. If you need to call a blocking
library (e.g. faster-whisper), wrap it in `asyncio.to_thread` the way
`asr.py` does.

**TypeScript.** Strict mode. No state management library; if you find
yourself wanting Redux, reconsider. The polling loop in `App.tsx` is the
template for any long-running operation on the frontend.

**SQL.** MySQL 8.4 dialect. Window functions and CTEs are fair game;
`PERCENTILE_CONT` and `MEDIAN` are not (MySQL doesn't support them — see
`analytics/05_extraction_quality.sql` for the portable median pattern).

**Commits.** Conventional-ish is fine. One logical change per commit;
schema migrations and the code that depends on them should be in the same
commit so `git bisect` doesn't land on a broken state.

## Adding a new ASR backend

1. Add `async def _my_backend_transcribe(audio_bytes: bytes) -> str` in
   `backend/app/asr.py`.
2. Register it in the `_BACKENDS` dict.
3. Add any new env vars to `config.py` and `.env.example`.
4. Set `ASR_BACKEND=my_backend` in your `.env`.

Nothing upstream of `transcribe()` needs to know which backend ran.

## Adding a new analytics query

Drop a `.sql` file under `analytics/` with a numeric prefix. `make
analytics` picks them up automatically. Start the file with `USE caresync;`
so it works standalone against a fresh mysql shell too.

## Tests

There isn't a test suite yet. The closest thing is `make seed-demo`, which
exercises the full pipeline (encounter → upload → poll → extracted PCR)
with mock ASR and the regex fallback extractor, so it runs without any API
keys. Treat a clean `seed-demo` as the minimum bar before pushing.
