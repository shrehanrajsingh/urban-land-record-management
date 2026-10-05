.PHONY: up down seed test api web benchmark

up:
	docker compose up --build

down:
	docker compose down

db:
	docker compose up -d db

seed:
	python scripts/seed_benchmark.py

api:
	uvicorn backend.api.main:app --reload --port 8000

web:
	cd web && npm install && npm run dev

test:
	pytest -q

benchmark:
	python scripts/run_benchmark.py
