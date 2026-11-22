# Contributing

Thanks for taking a look. Issues and pull requests are welcome.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
make check        # ruff, format check, tests, graph and fixture drift checks
```

Everything runs offline: the tests use the scripted fake model in `src/sessionfish/llm/` and the
fixture repo in `examples/discount_shop`.

## Ground rules

- **The model proposes; code decides.** Classification lives in `annotate.py`, scoring in
  `evaluate.py`, line selection in `analysis.pick_best`, rule verification in
  `analysis.check_rule`. A model never decides whether a move was bad or which line is best.
- **Keep the fake deterministic.** Fake-model rules match prompt text, never call order, so
  concurrent lines and resumed interrupts get the same answers.
- **Fixtures are synthetic.** Never commit a real session transcript. Change the demo session in
  `scripts/make_fixture.py` and run `python scripts/make_fixture.py`.
- **If you change a graph,** run `python scripts/gen_graph.py` so the README and
  `docs/architecture.md` show the new diagram (CI checks this).
- **If you change the viewer,** run `make gif` to re-record `docs/media/demo.gif` (needs
  playwright and ffmpeg, not project dependencies).
- Keep pull requests focused, and add a line to `CHANGELOG.md` under an `Unreleased` heading.
