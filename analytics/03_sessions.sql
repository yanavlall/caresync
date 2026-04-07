-- Gap-and-islands: continuous hypoxia episodes (SpO2<90) lasting >= 120s
USE caresync;
WITH obs AS (
  SELECT
    encounter_id, t, spo2,
    (spo2 < 90) AS low,
    CASE
      WHEN (spo2 < 90) <> LAG(spo2 < 90,1,0) OVER (PARTITION BY encounter_id ORDER BY t)
      THEN 1 ELSE 0
    END AS boundary
  FROM vitals
),
tag AS (
  SELECT *,
         SUM(boundary) OVER (PARTITION BY encounter_id ORDER BY t) AS grp
  FROM obs
),
episodes AS (
  SELECT encounter_id,
         MIN(t) AS start_t,
         MAX(t) AS end_t,
         TIMESTAMPDIFF(SECOND, MIN(t), MAX(t)) + 1 AS seconds
  FROM tag
  WHERE low = 1
  GROUP BY encounter_id, grp
)
SELECT * FROM episodes
WHERE seconds >= 120
ORDER BY encounter_id, start_t;
