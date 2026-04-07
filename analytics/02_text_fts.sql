-- FTS: encounters mentioning "chest pain"
USE caresync;
SELECT e.encounter_id, COUNT(*) AS hits
FROM transcripts t
JOIN encounters e USING (encounter_id)
WHERE MATCH(t.text) AGAINST ('+chest +pain' IN BOOLEAN MODE)
GROUP BY e.encounter_id
ORDER BY hits DESC
LIMIT 20;
