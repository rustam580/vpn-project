.PHONY: lint test check
PYTHON ?= python

lint:
	$(PYTHON) -m ruff check --no-cache bot.py app_texts.py tests

test:
	$(PYTHON) -m pytest -q -p no:cacheprovider

check:
	$(PYTHON) -B -m py_compile bot.py app_texts.py
	$(PYTHON) -m ruff check --no-cache bot.py app_texts.py tests
	$(PYTHON) -m pytest -q -p no:cacheprovider
