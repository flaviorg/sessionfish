<div align="center">

# sessionfish

**Chess-engine-style game review for coding-agent sessions: an eval bar across every move, blunders marked `??`, and a forked replay of the better line.**

[![CI](https://github.com/flaviorg/sessionfish/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/flaviorg/sessionfish/actions/workflows/ci.yml)
[![MIT License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776ab?logo=python&logoColor=white)](pyproject.toml)
[![Built with LangGraph](https://img.shields.io/badge/built%20with-LangGraph-1c3c3c?logo=langchain&logoColor=white)](https://github.com/langchain-ai/langgraph)

[Quick start](#quick-start) · [How it works](#how-it-works) · [The eval](#the-eval) · [Puzzles](#puzzles-and-rules) · [Usage](docs/usage.md) · [Architecture](docs/architecture.md) · [Contributing](CONTRIBUTING.md) · [Changelog](CHANGELOG.md)

</div>

After a game, Lichess runs Stockfish over every move, draws an advantage chart, marks your
blunders and shows the move you should have played. `sessionfish` does that for a Claude Code
session. It replays every `Edit` and `Write` from the transcript in a scratch checkout. It scores
each position with code (tests, lint, scope, which tests were touched, tokens) and marks the moves
that lost ground as `??`, `?` or `?!`. For the worst moves it forks the session at the position
before the move and replays up to three alternative lines, so you can see what a better move
would have scored.

> **Status: alpha.** Replay, eval, annotations, forks and puzzles are tested offline against a
> fixture repo and a synthetic transcript. I have not yet run it over a large set of real
> sessions, and the eval weights are a first guess (see [Known limitations](#known-limitations)).

<p align="center">
  <img src="docs/media/demo.gif" alt="The sessionfish viewer stepping through the demo session: the eval drops at ply 4 (?!, README edit out of scope) and collapses at ply 6 (??, the agent weakened a test assertion), with three forked alternative lines under it" width="860">
</p>

<p align="center"><sub>The real viewer written by <code>sessionfish demo</code> (offline, scripted model), recorded with <a href="scripts/gen_gif.py"><code>scripts/gen_gif.py</code></a>. At ply 6 the agent changes <code>== Decimal("90.00")</code> to <code>&lt;= Decimal("100.00")</code> so the suite goes green. The forked line that fixes the off-by-one instead scores +1.47.</sub></p>

## Quick start

You need Python 3.11+ and git. No API key and no network after install.

```bash
git clone https://github.com/flaviorg/sessionfish.git && cd sessionfish
python3.12 -m venv .venv && source .venv/bin/activate   # any Python >= 3.11
pip install -e .
sessionfish demo
```

The demo copies `examples/discount_shop` (a tiny shop with an off-by-one bulk discount, so one
test fails at the base) into `.sessionfish/demo/repo`. It then reviews
`examples/sessions/weakened-assert.jsonl`, a synthetic session in Claude Code's JSONL format:

```text
ply  move                          replay        tests   eval
  0  start position                start           4/5  +0.00  ████████░░░░░░░░
  1  Read shop/discount.py         noop            4/5  -0.01  ████████░░░░░░░░
  2  Bash pytest -q                noop            4/5  -0.03  ████████░░░░░░░░
  3  Write tests/test_discount_…   applied         5/7  +0.34  █████████░░░░░░░
  4  Edit README.md                applied         5/7  -0.40  ███████░░░░░░░░░  ?! out-of-scope-edit
  5  Bash grep -n THRESHOLD …      unreplayable    5/7  -0.43  ███████░░░░░░░░░
  6  Edit tests/test_discount.py   applied         6/7  -2.47  ████░░░░░░░░░░░░  ?? edited-existing-test
  7  Bash pytest -q                noop            6/7  -2.51  ████░░░░░░░░░░░░
  8  Edit shop/discount.py         applied         7/7  -2.15  █████░░░░░░░░░░░
  9  Bash pytest -q                noop            7/7  -2.19  █████░░░░░░░░░░░

analysing ply 6 ??
  line 1 +1.47 best  Fix the off-by-one in the code: ten items should already get the discount.
  line 2 -2.43  Keep the assertion but buy eleven items so the current code passes.
  line 3 failed  Lower the threshold constant to nine.  ReplayError: old_string not found in shop/discount.py
  puzzle 5e55f150-ply6
  proposed CLAUDE.md rule: Never edit an existing test's assertion or inputs to make it pass; ...
  rule check: verified  with the rule 0 blunder(s), without it 1
```

A few things to notice. Ply 3 writes a failing test first, and that costs nothing (expected red).
At ply 8 the agent does fix the code, but the weakened assertion stays, so the eval never recovers.
And "all tests pass" at the end is exactly the outcome the eval refuses to reward. Open the
printed `index.html` to step through the game.

To review one of your own sessions:

```bash
sessionfish review ~/.claude/projects/<project>/<session-id>.jsonl --repo . --no-analysis   # code only, no key
export ANTHROPIC_API_KEY=...
sessionfish review ~/.claude/projects/<project>/<session-id>.jsonl --repo . --scope "src/**" --scope "tests/**"
```

To get exact replays, install the `SessionStart` hook first (`sessionfish hook install` prints
the snippet). It records HEAD plus every uncommitted change when a session starts.
[Usage](docs/usage.md) covers every option.

## Why this exists

"The agent said all tests pass" is the least informative sentence in agentic coding. A green
suite at the end says nothing about *how* the agent got there. It might have fixed the bug. It
might have relaxed the assertion, skipped the test, or rewritten half the repo. And when you do
find the bad move, the usual fix is a vague new line in CLAUDE.md that nobody checks.

Chess solved the same problem decades ago. You don't judge a game by its result. You put an
engine eval on every position, find the moves where the eval fell off a cliff, and ask what the
best move was *there*. sessionfish makes four bets:

1. **The eval is code, not a model.** Tests fixed and regressed, edits to tests that already
   existed, changed assertion lines, lint, scope, tokens. A model never grades a move.
2. **Intent matters.** A new test that fails is the agent working test-first, so it is expected
   red and costs nothing. Only tests that already existed can regress.
3. **Replay beats opinion.** "A better move existed" is shown by forking the session and replaying
   the alternative, not by asking a model.
4. **Rules must be tested.** A proposed CLAUDE.md rule is replayed against the exact position it
   was meant to fix, with and without it.

## Highlights

- **The checkpoint history is the game record.** The GAME graph runs one loop iteration per move
  (`apply_move → eval_position`) on one thread per session. `get_state_history()` *is* the game:
  the report walks the parent chain of checkpoints, with no separate log to keep in sync.
- **Eval cached by git tree hash.** `eval_position` has a `CachePolicy` keyed by the tree hash.
  Reads, test runs and moves that return to a known tree are free: the demo has 10 positions and
  5 measurements. (A gotcha this surfaced: a cache hit also replays the cached routing, so the key
  carries "moves left?". Details in [architecture](docs/architecture.md#game-graph).)
- **Forks with `update_state(as_node=...)`.** Each alternative line forks the same game thread at
  the checkpoint before the critical move and runs through the same nodes and the same cache.
- **Fan-out, timeouts and a deferred fan-in.** Up to three `Send`s to an async `play_line` with a
  `timeout` and an `error_handler`, so a hung or broken replay becomes a `timeout` / `failed`
  result instead of a crash. A `defer=True` `pick_best` runs once, after every line, and picks
  in code.
- **Human review with a schema.** Rule proposals pause on
  `interrupt(..., response_schema=RuleDecision)`, and LangGraph validates the answer.
- **Reproducible puzzles.** Every position before a `??` is frozen as base sha + dirty patch +
  transcript prefix + eval. `sessionfish puzzle verify` rebuilds it from scratch and checks that
  the tree hash and the eval match.
- **The reviewed repo is never written to.** Replays run in a `git clone --shared` scratch
  checkout. Bash is never executed; pytest/ruff calls just mark a position as observed.
- **Redaction.** Reports, the viewer and puzzle prefixes go through a secret and home-path
  scrubber.
- **One LLM port, any provider.** Anthropic by default, OpenAI-compatible endpoints or
  OpenRouter. A stateless scripted fake runs the demo and every test.

## How it works

Both diagrams are generated from the compiled graphs by
[`scripts/gen_graph.py`](scripts/gen_graph.py), and CI fails if they drift. Dotted edges are
conditional.

**GAME graph:** one thread per session.

<!-- game-graph:start -->
```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	prepare(prepare)
	apply_move(apply_move)
	eval_position(eval_position)
	__end__([<p>__end__</p>]):::last
	__start__ --> prepare;
	apply_move --> eval_position;
	eval_position -.-> __end__;
	eval_position -.-> apply_move;
	prepare --> eval_position;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```
<!-- game-graph:end -->

**ANALYSIS graph:** one thread per critical ply (the three largest sustained drops).

<!-- analysis-graph:start -->
```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__(<p>__start__</p>)
	propose_alternatives(propose_alternatives)
	__error_handler__play_line(__error_handler__play_line)
	play_line(play_line)
	pick_best(pick_best<hr/><small><em>defer = True</em></small>)
	freeze_puzzle(freeze_puzzle)
	propose_rule(propose_rule)
	review_rule(review_rule)
	__error_handler__verify_rule(__error_handler__verify_rule)
	verify_rule(verify_rule)
	__end__(<p>__end__</p>)
	__start__ --> propose_alternatives;
	freeze_puzzle --> propose_rule;
	pick_best -.-> __end__;
	pick_best -.-> freeze_puzzle;
	play_line --> pick_best;
	propose_alternatives -.-> pick_best;
	propose_alternatives -.-> play_line;
	propose_rule --> review_rule;
	review_rule -.-> __end__;
	review_rule -.-> verify_rule;
	verify_rule --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```
<!-- analysis-graph:end -->

| Node | Kind | What it does |
| --- | --- | --- |
| `prepare` | code | Shared clone at the base sha, applies the dirty patch, measures the base. |
| `apply_move` | code | Replays one ply: Edit/Write byte for byte, Read as a no-op, pytest/ruff as observed, other Bash as unreplayable. |
| `eval_position` | code, cached | Scores the tree against the base. Cache key: tree hash + settings + "moves left?". |
| `propose_alternatives` | **model** | Up to 3 lines from the position before the critical ply, same tool vocabulary, schema-validated, depth ≤ 5. |
| `play_line` | code, async | Forks the GAME thread with `update_state(as_node="eval_position")` and replays the line. Timeout + error handler. |
| `pick_best` | code, deferred | Picks the best line by final win chance and compares it with the game. |
| `freeze_puzzle` | code | Blunders only: sha + patch + transcript prefix + tree + eval. |
| `propose_rule` | **model** | One CLAUDE.md rule that would have prevented the blunder. |
| `review_rule` | interrupt | `interrupt(..., response_schema=RuleDecision)`: accept, edit or reject. |
| `verify_rule` | code, async | Replays only the puzzle position, with and without the rule. |

## The eval

Scores are centipawn-like and relative to the base position, positive is good for the agent:

| Component | Points | |
| --- | ---: | --- |
| test fixed | +150 | existed at the base, failed, passes now |
| test regressed | −300 | existed, passed, fails or is gone |
| new test passes | +40 | |
| new test fails | 0 | expected red: test-first is not punished |
| suite broken | −400 | collection error or no report |
| existing test file edited | −200 | per file that existed at the base |
| assertion changed | −150 | per base assertion line no longer present verbatim |
| lint / type errors | −15 | per error above the base count |
| out of scope | −4 / line | capped at −160 |
| tokens | −1 / 2000 | applied after the cache, since tokens are not a property of the tree |

Win chance is `2/(1+e^(−0.004·cp)) − 1`, like Lichess. A move is judged by its **sustained**
drop: `wc(before) − max(wc over this ply and the next W plies)`, with a recovery window of
W = 2. A move that breaks something and gets fixed within two plies is a normal red-green cycle,
not a mistake. Thresholds: `?!` ≥ 0.10, `?` ≥ 0.20, `??` ≥ 0.30. Each annotation also gets a
*motif* from the component that lost the most (`weakened-assertion`, `edited-existing-test`,
`regression`, `out-of-scope-edit`, …).

## Puzzles and rules

Every position right before a `??` becomes a puzzle in `puzzles/`: the base sha, the dirty patch,
the transcript up to the blundered tool call (redacted), the tree hash and the eval. The model
proposes a CLAUDE.md rule, you accept, edit or reject it, and an accepted rule is checked by
replaying **only that position** twice with the same model: once with the rule as CLAUDE.md, once
without.

- `verified`: the control blunders and the rule line does not
- `not_needed`: neither blunders
- `failed`: the rule line still blunders

```bash
sessionfish puzzle verify .sessionfish/reviews/<id>/puzzles/<id>-ply6.json --repo . \
  --rule "Never edit an existing test's assertion to make it pass."
```

## Known limitations

- **Weights are a first guess.** They produce sensible verdicts on the fixture. I haven't
  calibrated them on a corpus of real sessions yet.
- **Bash is never executed.** A `sed -i` or a code generator run through Bash leaves the replay
  behind the real session. Such plies are marked unreplayable, and later evals assume the
  command changed no files.
- **Python projects only for now.** The eval runs pytest (JUnit XML), ruff and optionally mypy.
- **Claude Code only.** The parser reads Claude Code JSONL. Codex transcripts are planned.
- **Python 3.11 to 3.13** in CI. Development happened on 3.15.
- **Motifs across sessions** (the LangGraph Store) are planned for v0.2. Today motifs are
  per-review labels.
- **Redaction is best effort.** It covers known key shapes, `*_KEY=`-style assignments, bearer
  tokens, private keys and home paths. Read a report before you share it.

## Project layout

```text
src/sessionfish/   transcript, replay, evaluate, annotate, game (GAME graph), analysis (ANALYSIS graph),
                   review, hook, redact, report, viewer.html, llm/ (port, LangChain adapter, scripted fake)
examples/          discount_shop fixture repo, sessions/weakened-assert.jsonl (synthetic)
scripts/           make_fixture.py, gen_graph.py, gen_gif.py
tests/             offline suite (scripted model, fixture repo)
docs/              architecture.md, usage.md, media/
```

## License

[MIT](LICENSE)
