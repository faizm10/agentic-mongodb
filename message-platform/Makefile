# NovaConnect shortcuts. Run from message-platform/.
PY := backend/.venv/bin/python

.PHONY: install backend frontend test probe typecheck build bugs reset-bugs reset-data

install:
	python3 -m venv backend/.venv
	$(PY) -m pip install -r backend/requirements.txt
	cd frontend && npm ci

backend:
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8200

frontend:
	cd frontend && npm run dev

test:
	cd backend && .venv/bin/python -m pytest

probe:
	cd backend && .venv/bin/python -m app.monitor

typecheck:
	cd frontend && npm run typecheck

build:
	cd frontend && npm run build

bugs:
	python3 inject_bug.py status

reset-bugs:
	python3 inject_bug.py reset all

reset-data:
	rm -f backend/data/novaconnect.db backend/data/novaconnect.db-shm backend/data/novaconnect.db-wal
