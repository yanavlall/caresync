.PHONY: up down reset logs-db logs-backend mysql seed-demo analytics help

# Default target
help:
	@echo "CareSync — make targets"
	@echo ""
	@echo "  make up            Start db + backend (detached)"
	@echo "  make down          Stop all services"
	@echo "  make reset         Stop and wipe the database volume"
	@echo "  make logs-db       Tail MySQL logs"
	@echo "  make logs-backend  Tail backend logs"
	@echo "  make mysql         Open a MySQL shell inside the db container"
	@echo "  make seed-demo     POST a demo encounter + extraction via curl"
	@echo "  make analytics     Run every SQL file under analytics/ and print results"
	@echo ""

up:
	docker compose up -d --build
	@echo ""
	@echo "Backend:  http://localhost:8000/health"
	@echo "Frontend: cd frontend && npm install && npm start   # http://localhost:3000"

down:
	docker compose down

reset:
	docker compose down -v

logs-db:
	docker compose logs -f db

logs-backend:
	docker compose logs -f backend

mysql:
	docker compose exec db mysql -uroot -pdev caresync

# End-to-end smoke test. Creates an encounter, uploads a tiny dummy audio blob,
# polls the job until it completes, then prints the extracted PCR fields.
seed-demo:
	@echo "==> Waiting for backend to be healthy..."
	@for i in $$(seq 1 30); do \
		curl -fsS http://localhost:8000/health >/dev/null 2>&1 && break; \
		sleep 1; \
	done
	@echo "==> Creating encounter"
	@ENC=$$(curl -sS -X POST http://localhost:8000/encounters/ \
		-H "Content-Type: application/json" \
		-d '{"ambulance_id":"AMB042","chief_complaint":"chest pain","severity":3}' \
		| python3 -c "import sys,json; print(json.load(sys.stdin)['encounter_id'])"); \
	echo "    encounter_id=$$ENC"; \
	echo "==> Uploading dummy audio (mock ASR returns a fixed transcript)"; \
	printf 'dummy-audio-bytes' > /tmp/caresync_demo.wav; \
	JOB=$$(curl -sS -X POST http://localhost:8000/encounters/$$ENC/extract \
		-F "audio=@/tmp/caresync_demo.wav;type=audio/wav" \
		| python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])"); \
	echo "    job_id=$$JOB"; \
	echo "==> Polling job until complete"; \
	for i in $$(seq 1 40); do \
		STATUS=$$(curl -sS http://localhost:8000/jobs/$$JOB | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])"); \
		echo "    [$$i] status=$$STATUS"; \
		[ "$$STATUS" = "completed" ] && break; \
		[ "$$STATUS" = "failed" ] && break; \
		sleep 1; \
	done; \
	echo "==> Final job payload:"; \
	curl -sS http://localhost:8000/jobs/$$JOB | python3 -m json.tool

# Runs every .sql under analytics/ against the live db container and prints
# the result. Useful for demoing the warehouse side of the project.
analytics:
	@for f in analytics/*.sql; do \
		echo ""; \
		echo "================================================================"; \
		echo "  $$f"; \
		echo "================================================================"; \
		docker compose exec -T db mysql -uroot -pdev --table caresync < $$f; \
	done
