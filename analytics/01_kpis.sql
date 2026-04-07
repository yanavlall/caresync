-- KPI: Chart-close time (ended_at -> finalized_at) by ISO week with avg and approx P90
USE caresync;
WITH durations AS (
  SELECT
    e.encounter_id,
    e.ended_at,
    TIMESTAMPDIFF(MINUTE, e.ended_at, p.finalized_at) AS minutes_to_close
  FROM encounters e
  JOIN pcr_reports p ON p.encounter_id = e.encounter_id
  WHERE p.status = 'finalized' AND e.ended_at IS NOT NULL AND p.finalized_at IS NOT NULL
),
wk AS (
  SELECT
    YEARWEEK(ended_at, 3) AS yw,
    minutes_to_close,
    NTILE(10) OVER (PARTITION BY YEARWEEK(ended_at,3) ORDER BY minutes_to_close) AS decile
  FROM durations
)
SELECT yw AS yearweek,
       ROUND(AVG(minutes_to_close),1) AS avg_close_min,
       MAX(CASE WHEN decile = 9 THEN minutes_to_close END) AS p90_approx
FROM wk
GROUP BY yearweek
ORDER BY yearweek DESC;
