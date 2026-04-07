-- Schema: core tables
CREATE DATABASE IF NOT EXISTS caresync;
USE caresync;

-- Patients and users
CREATE TABLE IF NOT EXISTS patients (
  patient_id BIGINT PRIMARY KEY AUTO_INCREMENT,
  dob DATE NOT NULL,
  sex ENUM('F','M','O') NOT NULL,
  zipcode VARCHAR(10),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS users (
  user_id BIGINT PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(100) NOT NULL,
  role ENUM('medic','nurse','physician','admin') NOT NULL
) ENGINE=InnoDB;

-- Encounters (one PCR per encounter typically)
CREATE TABLE IF NOT EXISTS encounters (
  encounter_id BIGINT PRIMARY KEY AUTO_INCREMENT,
  patient_id BIGINT NOT NULL,
  ambulance_id VARCHAR(32),
  started_at DATETIME NOT NULL,
  ended_at DATETIME,
  chief_complaint VARCHAR(255),
  severity TINYINT,
  FOREIGN KEY (patient_id) REFERENCES patients(patient_id),
  INDEX idx_enc_patient_started (patient_id, started_at),
  INDEX idx_enc_ambulance_started (ambulance_id, started_at),
  INDEX idx_enc_started (started_at)
) ENGINE=InnoDB;

-- Time-series vitals (SpO2, HR, RR, etc.)
CREATE TABLE IF NOT EXISTS vitals (
  encounter_id BIGINT NOT NULL,
  t DATETIME NOT NULL,
  spo2 TINYINT,
  hr SMALLINT,
  rr SMALLINT,
  PRIMARY KEY (encounter_id, t),
  FOREIGN KEY (encounter_id) REFERENCES encounters(encounter_id)
) ENGINE=InnoDB;

-- PCR tracking
CREATE TABLE IF NOT EXISTS pcr_reports (
  pcr_id BIGINT PRIMARY KEY AUTO_INCREMENT,
  encounter_id BIGINT NOT NULL UNIQUE,
  author_id BIGINT NOT NULL,
  status ENUM('draft','submitted','finalized') NOT NULL DEFAULT 'draft',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finalized_at DATETIME NULL,
  FOREIGN KEY (encounter_id) REFERENCES encounters(encounter_id),
  FOREIGN KEY (author_id) REFERENCES users(user_id),
  INDEX idx_pcr_status_created (status, created_at)
) ENGINE=InnoDB;

-- Transcripts + FTS for symptoms/notes
CREATE TABLE IF NOT EXISTS transcripts (
  transcript_id BIGINT PRIMARY KEY AUTO_INCREMENT,
  encounter_id BIGINT NOT NULL,
  t DATETIME NOT NULL,
  speaker ENUM('medic','patient','other') NOT NULL,
  text TEXT NOT NULL,
  FOREIGN KEY (encounter_id) REFERENCES encounters(encounter_id),
  FULLTEXT KEY ft_text (text)
) ENGINE=InnoDB;

-- Alerts (derived or rule-based)
CREATE TABLE IF NOT EXISTS alerts (
  alert_id BIGINT PRIMARY KEY AUTO_INCREMENT,
  encounter_id BIGINT NOT NULL,
  t DATETIME NOT NULL,
  type VARCHAR(64) NOT NULL,
  reason VARCHAR(255),
  FOREIGN KEY (encounter_id) REFERENCES encounters(encounter_id),
  INDEX idx_alerts (encounter_id, t, type)
) ENGINE=InnoDB;
