.PHONY: test docs docs-check docs-serve docs-deploy install-dev

PY ?= .venv/bin/python
PIP ?= .venv/bin/pip

install-dev:
	$(PIP) install -e '.[dev,docs]'

test:
	$(PY) -m pytest

docs:
	$(PY) scripts/gen_docs.py

docs-check:
	$(PY) scripts/gen_docs.py --check

docs-serve: docs
	$(PY) -m mkdocs serve

docs-deploy: docs
	$(PY) -m mike deploy --push --update-aliases $(VERSION) latest
