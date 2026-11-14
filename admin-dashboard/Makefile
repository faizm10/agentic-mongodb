# OpsRelay uptime monitoring. Run from admin-dashboard/.
PY := backend/.venv/bin/python

.PHONY: install serve worker api dashboard dashboard-dev test typecheck build status check-now reset-data

install:
	python3 -m venv backend/.venv
	$(PY) -m pip install -r backend/requirements.txt
	cd web && npm ci

serve:          ## API on :8300 with the monitoring worker embedded
	cd backend && .venv/bin/python -m uptime serve

worker:         ## monitoring worker only
	cd backend && .venv/bin/python -m uptime worker

api:            ## read API only (pair with `make worker`)
	cd backend && .venv/bin/python -m uptime api

dashboard:      ## production dashboard on :3300
	cd web && npm run build && npm run start

dashboard-dev:
	cd web && npm run dev

test:
	cd backend && .venv/bin/python -m pytest

typecheck:
	cd web && npm run typecheck

build:
	cd web && npm run build

status:
	cd backend && .venv/bin/python -m uptime status

check-now:      ## one immediate cycle, printed (does not need the worker)
	cd backend && .venv/bin/python -m uptime check

reset-data:     ## delete LOCAL development history only (never touches MongoDB)
	rm -f backend/data/uptime-dev.db backend/data/uptime-dev.db-shm backend/data/uptime-dev.db-wal
