# NovaMart convenience targets. Run from the shopping/ directory.
PY := backend/.venv/bin/python

.PHONY: install dev backend frontend test probe typecheck build bugs reset-bugs reset-data

install:
	python3 -m venv backend/.venv
	$(PY) -m pip install -r backend/requirements.txt
	cd frontend && npm ci

dev:
	python3 run_servers.py

backend:
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8100

frontend:
	cd frontend && npm run dev

test:
	cd backend && .venv/bin/pytest

probe:
	cd backend && .venv/bin/python -m app.probe

typecheck:
	cd frontend && npm run typecheck

build:
	cd frontend && npm run build

bugs:
	$(PY) inject_bug.py status

reset-bugs:
	$(PY) inject_bug.py reset all

reset-data:
	rm -f backend/data/novamart-store.json
