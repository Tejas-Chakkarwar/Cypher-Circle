PYTHON ?= .venv/bin/python

.PHONY: up down data load reset run web verify-env smoke-check test test-integration slice-check report zip demo-info

up:
	@if [ ! -f .env ]; then cp .env.example .env && chmod 600 .env; fi
	docker compose up -d

down:
	docker compose down

data:
	$(PYTHON) scripts/download_dataset.py
	$(PYTHON) scripts/preprocess.py

load:
	$(PYTHON) scripts/load_data.py

reset:
	$(PYTHON) scripts/load_data.py --reset

run:
	$(PYTHON) -m cyphercircle

web:
	$(PYTHON) -m cyphercircle.web

verify-env:
	$(PYTHON) scripts/verify_env.py

smoke-check:
	$(PYTHON) scripts/smoke_check.py

test:
	$(PYTHON) -m pytest -m "not integration"

test-integration:
	$(PYTHON) -m pytest -m integration --run-integration

slice-check:
	$(PYTHON) scripts/slice_check.py

report:
	PYTHON="$(PYTHON)" ./build_report.sh

zip: report
	$(PYTHON) scripts/package_project.py

demo-info:
	$(PYTHON) scripts/demo_info.py

