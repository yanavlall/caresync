USE caresync;
DELIMITER //
CREATE TRIGGER pcr_finalize_stamp
BEFORE UPDATE ON pcr_reports
FOR EACH ROW
BEGIN
  IF NEW.status = 'finalized' AND (NEW.finalized_at IS NULL) THEN
    SET NEW.finalized_at = CURRENT_TIMESTAMP;
  END IF;
END//
DELIMITER ;
