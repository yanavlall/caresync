-- Extraction jobs and structured PCR fields populated by the LLM pipeline.
USE caresync;

-- Async job queue for transcribe + extract pipeline.
CREATE TABLE IF NOT EXISTS extraction_jobs (
  job_id BIGINT PRIMARY KEY AUTO_INCREMENT,
  encounter_id BIGINT NOT NULL,
  status ENUM('queued','transcribing','extracting','completed','failed') NOT NULL DEFAULT 'queued',
  audio_path VARCHAR(255),
  transcript TEXT,
  error TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (encounter_id) REFERENCES encounters(encounter_id),
  INDEX idx_job_status_created (status, created_at),
  INDEX idx_job_encounter (encounter_id)
) ENGINE=InnoDB;

-- Structured PCR fields extracted from transcripts by the LLM.
-- Separate from pcr_reports (which tracks the document lifecycle) so that
-- multiple extraction attempts per PCR can be tracked and compared.
CREATE TABLE IF NOT EXISTS pcr_extractions (
  extraction_id BIGINT PRIMARY KEY AUTO_INCREMENT,
  pcr_id BIGINT NOT NULL,
  job_id BIGINT,
  -- Patient info
  patient_name VARCHAR(200),
  patient_age SMALLINT,
  patient_sex ENUM('F','M','O'),
  -- Vitals (snapshot at narration time)
  blood_pressure VARCHAR(20),
  heart_rate SMALLINT,
  respiratory_rate SMALLINT,
  spo2 TINYINT,
  temperature DECIMAL(4,1),
  gcs TINYINT,
  -- Narrative fields
  chief_complaint VARCHAR(500),
  hpi TEXT,
  assessment TEXT,
  treatment TEXT,
  -- Metadata
  model VARCHAR(64),
  confidence DECIMAL(3,2),
  extracted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (pcr_id) REFERENCES pcr_reports(pcr_id),
  FOREIGN KEY (job_id) REFERENCES extraction_jobs(job_id),
  INDEX idx_extraction_pcr (pcr_id),
  INDEX idx_extraction_extracted_at (extracted_at)
) ENGINE=InnoDB;
