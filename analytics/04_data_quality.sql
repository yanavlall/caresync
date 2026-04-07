-- Data-quality: overdue encounters (no finalized PCR after 24h of end time)
USE caresync;
SELECT e.encounter_id, e.ended_at
FROM encounters e
LEFT JOIN pcr_reports p
  ON p.encounter_id = e.encounter_id AND p.status = 'finalized'
WHERE e.ended_at IS NOT NULL
  AND e.ended_at < NOW() - INTERVAL 24 HOUR
  AND p.pcr_id IS NULL
ORDER BY e.ended_at DESC;
