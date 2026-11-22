# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-22

### Added

- Claude Code transcript parser: `Read`, `Edit`, `MultiEdit` (expanded), `Write` and `Bash`
  tool calls become plies; token usage is counted once per API message; failed tool calls and
  paths outside the session cwd are marked.
- `SessionStart` hook (`sessionfish hook session-start`, `sessionfish hook install`) that
  records HEAD plus a patch of every uncommitted change, so a session replays from its exact
  starting position.
- GAME graph (`prepare → eval_position → apply_move → eval_position …`): one thread per
  session, Edit/Write replayed in a `git clone --shared` scratch checkout, Bash replayed only for
  pytest/ruff (everything else is unreplayable), checkpoint history as the game record.
- Code-only eval per position, cached with `CachePolicy` by git tree hash: tests fixed and
  regressed, intent-aware expected red for new failing tests, edits to pre-existing test files,
  changed assertion lines, lint delta, optional mypy delta, lines outside the declared scope,
  and token spend.
- Move classification `??` / `?` / `?!` from fixed win-chance thresholds with a recovery
  window, plus motifs (`weakened-assertion`, `edited-existing-test`, `regression`, …) and a
  Lichess-style accuracy.
- ANALYSIS graph: for the top 3 critical plies, forks the game thread with
  `update_state(as_node="eval_position")`, `Send`s up to 3 schema-validated alternative lines
  (depth ≤ 5), replays them with a timeout and an error handler, and picks the best one in a
  `defer=True` fan-in.
- Puzzles: each pre-`??` position frozen as base sha + dirty patch + transcript prefix + eval;
  `sessionfish puzzle verify` rebuilds it and checks a CLAUDE.md rule against only that
  position, with and without the rule.
- CLAUDE.md rule proposals reviewed through `interrupt(response_schema=RuleDecision)`.
- Static HTML viewer with eval bar, advantage chart, move list, diffs and analysis; Markdown and
  JSON reports; secret and home-path redaction on everything written out.
- `sessionfish demo`: fixture repo plus a synthetic transcript where the agent weakens a test
  assertion to go green, with scripted alternatives, fully offline.
- LLM port with a LangChain adapter (Anthropic, OpenAI-compatible, OpenRouter) and a stateless
  scripted fake model; CI on Python 3.11 to 3.13.

### Planned

- Store-backed motifs across sessions (`langgraph` Store), Codex transcript support.

[0.1.0]: https://github.com/flaviorg/sessionfish/releases/tag/v0.1.0
