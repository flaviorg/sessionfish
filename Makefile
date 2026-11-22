PY ?= .venv/bin/python
BIN := $(dir $(PY))

.PHONY: install lint format test cov demo graph fixture gif docs check clean

install:
	$(or $(PYTHON),python3) -m venv .venv
	$(BIN)pip install -e ".[dev]"

lint:
	$(BIN)ruff check .
	$(BIN)ruff format --check .

format:
	$(BIN)ruff format .
	$(BIN)ruff check --fix .

test:
	$(BIN)pytest

cov:
	$(BIN)pytest --cov --cov-report=term-missing

demo:
	$(BIN)sessionfish demo

graph:
	$(PY) scripts/gen_graph.py

fixture:
	$(PY) scripts/make_fixture.py

gif: demo
	$(PY) scripts/gen_gif.py

docs: graph fixture

check: lint test
	$(PY) scripts/gen_graph.py --check
	$(PY) scripts/make_fixture.py --check

clean:
	rm -rf .sessionfish .pytest_cache .ruff_cache .coverage dist build
