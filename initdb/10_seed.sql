USE caresync;

-- Users
INSERT INTO users (name, role) VALUES
  ('Paramedic A','medic'),
  ('Paramedic B','medic'),
  ('ED Physician','physician')
ON DUPLICATE KEY UPDATE role=VALUES(role);

-- Patients (100)
WITH RECURSIVE s(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM s WHERE n < 100)
INSERT INTO patients (dob, sex, zipcode)
SELECT DATE_SUB(CURDATE(), INTERVAL FLOOR(RAND()*365*90) DAY),
       ELT(1+FLOOR(RAND()*3), 'F','M','O'),
       LPAD(FLOOR(RAND()*99999),5,'0')
FROM s;

-- Encounters (200)
WITH RECURSIVE e(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM e WHERE n < 200)
INSERT INTO encounters (patient_id, ambulance_id, started_at, ended_at, chief_complaint, severity)
SELECT FLOOR(RAND()*100)+1,
       CONCAT('AMB', LPAD(FLOOR(RAND()*50),3,'0')),
       DATE_SUB(NOW(), INTERVAL FLOOR(RAND()*20) DAY),
       DATE_ADD(DATE_SUB(NOW(), INTERVAL FLOOR(RAND()*20) DAY), INTERVAL 20 MINUTE),
       ELT(1+FLOOR(RAND()*5),'chest pain','SOB','stroke','trauma','fever'),
       1+FLOOR(RAND()*5);

-- PCRs (draft initially)
INSERT INTO pcr_reports (encounter_id, author_id, status, created_at)
SELECT encounter_id, 1, 'draft', started_at FROM encounters
ON DUPLICATE KEY UPDATE status=VALUES(status);

-- 1Hz vitals for first 10 minutes of each encounter (600 rows per encounter)
WITH RECURSIVE secs(s) AS (SELECT 0 UNION ALL SELECT s+1 FROM secs WHERE s < 600)
INSERT INTO vitals (encounter_id, t, spo2, hr, rr)
SELECT e.encounter_id,
       DATE_ADD(e.started_at, INTERVAL secs.s SECOND),
       GREATEST(80, LEAST(100, 95 - FLOOR(RAND()*8))), -- mostly 87..100
       60 + FLOOR(RAND()*40),
       12 + FLOOR(RAND()*8)
FROM encounters e
JOIN secs;

-- Transcripts
INSERT INTO transcripts (encounter_id, t, speaker, text)
SELECT e.encounter_id, e.started_at, 'medic',
       CONCAT('Patient reports ', ELT(1+FLOOR(RAND()*4),'chest pain','dizziness','SOB','numbness'),
              ' for ', FLOOR(RAND()*30),' minutes.')
FROM encounters e
LIMIT 140;

-- Mark ~2/3 PCRs as finalized, spaced after encounter end
UPDATE pcr_reports pr
JOIN encounters e ON pr.encounter_id = e.encounter_id
SET pr.status = CASE WHEN (e.encounter_id % 3) <> 0 THEN 'finalized' ELSE 'submitted' END,
    pr.finalized_at = CASE WHEN (e.encounter_id % 3) <> 0
                      THEN DATE_ADD(e.ended_at, INTERVAL (10 + (e.encounter_id % 180)) MINUTE)
                      ELSE pr.finalized_at
                 END;

-- Simple alerts: first time each encounter dips below 88 SpO2
INSERT INTO alerts (encounter_id, t, type, reason)
SELECT encounter_id, MIN(t) AS t, 'critical_spo2', 'SpO2 < 88'
FROM vitals
WHERE spo2 < 88
GROUP BY encounter_id;
