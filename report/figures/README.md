# Figures

Drop PNG/PDF figures here and reference them from `../writeup.md` and the
top-level `README.md`. Suggested figures:

- `architecture.png` — rendered version of the ASCII diagram in the README
- `pipeline_timeline.png` — job lifecycle on a wall-clock axis (queued →
  transcribing → extracting → completed)
- `hr_crosscheck.png` — scatter of LLM-extracted HR vs. median measured HR
  from the first minute of vitals, colored by model
- `latency_hist.png` — histogram of queued → completed wall time

The analytics queries under `../../analytics/` produce the data for the
last two; export the results to CSV and plot with matplotlib or a notebook
under `../../notebooks/`.
