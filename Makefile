.PHONY: lint test check
PYTHON ?= python

lint:
	$(PYTHON) -m ruff check --no-cache bot.py tests

test:
	$(PYTHON) -m pytest -q -p no:cacheprovider

check:
	$(PYTHON) -B -m py_compile bot.py
	$(PYTHON) -m ruff check --no-cache bot.py tests
	$(PYTHON) -m pytest -q -p no:cacheprovider
