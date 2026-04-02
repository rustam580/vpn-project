.PHONY: lint test check
PYTHON ?= python

lint:
	$(PYTHON) -m ruff check --no-cache .

test:
	$(PYTHON) -m pytest -q -p no:cacheprovider

check:
	$(PYTHON) scripts/compile_all.py
	$(PYTHON) -m ruff check --no-cache .
	$(PYTHON) -m pytest -q -p no:cacheprovider
