-- Extraction quality: cross-check LLM-extracted PCR fields against the
-- ground-truth vitals stream, per day.
--
-- For each extraction_jobs row that completed, we compute:
--   * the median heart rate measured in the FIRST MINUTE of the encounter's
--     vitals stream (our "gold" value),
--   * the LLM-extracted heart_rate from pcr_extractions,
--   * the absolute difference between the two.
--
-- Then we group by the day the job was created and report:
--   * job_count               how many extraction jobs ran that day
--   * avg_confidence          mean self-reported model confidence
--   * avg_minutes_to_complete wall-clock job latency (queued -> completed)
--   * hr_within_10_bpm        count of jobs where LLM HR was within 10 BPM
--                             of the median measured HR in the first minute
--   * hr_compared             denominator for the above (jobs where we had
--                             both an LLM HR and at least one vitals sample
--                             in the first minute, so the check is defined)
--
-- MySQL 8.4 has neither MEDIAN() nor PERCENTILE_CONT, so we compute the
-- median per encounter via ROW_NUMBER()/COUNT() and average the middle
-- value(s). For odd N we take the single middle row; for even N we average
-- the two middle rows.
USE caresync;

WITH first_minute_vitals AS (
  SELECT
    v.encounter_id,
    v.hr
  FROM vitals v
  JOIN encounters e ON e.encounter_id = v.encounter_id
  WHERE v.t >= e.started_at
    AND v.t <  e.started_at + INTERVAL 60 SECOND
    AND v.hr IS NOT NULL
),
ordered AS (
  SELECT
    encounter_id,
    hr,
    ROW_NUMBER() OVER (PARTITION BY encounter_id ORDER BY hr) AS rn,
    COUNT(*)    OVER (PARTITION BY encounter_id)              AS cnt
  FROM first_minute_vitals
),
median_hr_per_enc AS (
  SELECT
    encounter_id,
    AVG(hr) AS median_hr
  FROM ordered
  WHERE rn IN (FLOOR((cnt + 1) / 2), FLOOR((cnt + 2) / 2))
  GROUP BY encounter_id
),
joined AS (
  SELECT
    j.job_id,
    DATE(j.created_at)                                       AS day,
    j.status,
    TIMESTAMPDIFF(SECOND, j.created_at, j.updated_at) / 60.0 AS minutes_to_complete,
    x.confidence                                             AS llm_confidence,
    x.heart_rate                                             AS llm_hr,
    m.median_hr                                              AS measured_hr
  FROM extraction_jobs j
  LEFT JOIN pcr_extractions  x ON x.job_id = j.job_id
  LEFT JOIN median_hr_per_enc m ON m.encounter_id = j.encounter_id
  WHERE j.status = 'completed'
)
SELECT
  day,
  COUNT(*)                                              AS job_count,
  ROUND(AVG(llm_confidence), 3)                         AS avg_confidence,
  ROUND(AVG(minutes_to_complete), 2)                    AS avg_minutes_to_complete,
  SUM(
    CASE
      WHEN llm_hr IS NOT NULL
       AND measured_hr IS NOT NULL
       AND ABS(llm_hr - measured_hr) <= 10
      THEN 1 ELSE 0
    END
  )                                                     AS hr_within_10_bpm,
  SUM(
    CASE
      WHEN llm_hr IS NOT NULL AND measured_hr IS NOT NULL
      THEN 1 ELSE 0
    END
  )                                                     AS hr_compared
FROM joined
GROUP BY day
ORDER BY day DESC;
