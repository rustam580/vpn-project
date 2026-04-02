.PHONY: lint test check clean
PYTHON ?= python

lint:
	$(PYTHON) -m ruff check --no-cache .

test:
	$(PYTHON) -m pytest -q -p no:cacheprovider

check:
	$(PYTHON) scripts/compile_all.py
	$(PYTHON) -m ruff check --no-cache .
	$(PYTHON) -m pytest -q -p no:cacheprovider

clean:
	@if [ -x scripts/clean.sh ]; then bash scripts/clean.sh; else echo "scripts/clean.sh not found"; fi
