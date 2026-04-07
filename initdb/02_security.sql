-- Optional: roles and an app user with least privilege
USE caresync;

-- MySQL 8 roles
CREATE ROLE IF NOT EXISTS app_readonly;
CREATE ROLE IF NOT EXISTS app_writer;

GRANT SELECT ON caresync.* TO app_readonly;
GRANT INSERT, UPDATE, DELETE ON caresync.* TO app_writer;

-- App user
CREATE USER IF NOT EXISTS 'app'@'%' IDENTIFIED BY 'app_pw';
GRANT app_readonly, app_writer TO 'app'@'%';
SET DEFAULT ROLE app_readonly, app_writer FOR 'app'@'%';
