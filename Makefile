.PHONY: install install-dev run test verify demo benchmark connectors-smoke ai-smoke clean

PYTHON ?= python

install:
	$(PYTHON) -m pip install -e .

install-dev:
	$(PYTHON) -m pip install -e '.[dev]'

run:
	$(PYTHON) -m uvicorn app.main:app --host 127.0.0.1 --port $${PORT:-8000} --reload

test:
	$(PYTHON) -m pytest -q

verify: test
	$(PYTHON) -m compileall -q app tests scripts
	node --check app/static/app.js
	$(PYTHON) scripts/demo_sqlite_roundtrip.py
	$(PYTHON) -m app.cli --help

demo:
	./scripts/demo.sh

benchmark:
	$(PYTHON) scripts/benchmark_million.py

connectors-smoke:
	$(PYTHON) scripts/verify_external_connectors.py

ai-smoke:
	$(PYTHON) scripts/verify_ai_agent.py

clean:
	find . -type d -name '__pycache__' -prune -exec rm -rf {} +
	rm -rf .pytest_cache build dist *.egg-info
