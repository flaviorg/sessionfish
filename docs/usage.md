# Usage

## 1. Record the starting point of each session

Replaying a session needs the exact files the agent started from. The `SessionStart` hook stores
HEAD plus a patch of every uncommitted change in `.sessionfish/baselines/<session_id>.json`
(that folder gets its own `.gitignore`).

```bash
sessionfish hook install   # prints the snippet, writes nothing
```

```json
{
  "hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "command": "sessionfish hook session-start"}]}]
  }
}
```

Put it in `.claude/settings.json` (one project) or `~/.claude/settings.json` (all projects). The
hook never fails a session: problems go to stderr and it exits 0. Resumed sessions keep their
original baseline.

Without a baseline, `review` replays from `--base <sha>` or from HEAD (with a warning).

## 2. Review a session

Claude Code keeps transcripts under `~/.claude/projects/<project>/<session_id>.jsonl`.

```bash
# code only: replay, eval bar, annotations; no model, no key
sessionfish review ~/.claude/projects/<project>/<id>.jsonl --repo . --no-analysis

# with analysis of the three worst plies
export ANTHROPIC_API_KEY=...
sessionfish review <id>.jsonl --repo . --scope "src/**" --scope "tests/**"
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--repo` | `.` | Repo the session ran in |
| `--baseline` / `--base` | hook file, else HEAD | Starting position |
| `--scope` | everything | Globs the task may touch; other changed lines cost points |
| `--python` | sessionfish's | Interpreter that runs the repo's tests |
| `--lint/--no-lint` | on | Count ruff errors |
| `--types/--no-types` | off | Count mypy errors (needs mypy for that interpreter) |
| `--analysis/--no-analysis` | on | Fork and replay alternative lines |
| `--max-critical` | 3 | Critical plies to analyse |
| `--recovery-window` | 2 | Plies a drop has to recover |
| `--rules` | `ask` | Rule proposals: `ask`, `accept` or `skip` |
| `--model` | env / fake | `provider:model` (Anthropic, OpenAI, OpenRouter) |
| `--fail-on` | `never` | Exit 1 on `blunder`, `mistake` or `inaccuracy` |
| `--json` | off | Print the report as JSON |

Output goes to `.sessionfish/reviews/<session_id>/`: `index.html` (viewer), `report.md`,
`report.json` and `puzzles/`. Everything written out is redacted (known key shapes, `*_KEY=` /
`*_TOKEN=` assignments, bearer tokens, private keys, home directories). It is best-effort:
read a report before you share it.

## 3. Puzzles

```bash
sessionfish puzzle verify .sessionfish/reviews/<id>/puzzles/<id>-ply6.json \
  --repo . --rule "Never edit an existing test's assertion to make it pass."
```

It rebuilds the position from sha + prefix (and says whether the tree and eval match), then lets
the model continue from it with and without the rule. Exit 0 means reproduced and verified.

## Exit codes

`0` done, `1` `--fail-on` triggered (or a puzzle was not verified), `2` error.
