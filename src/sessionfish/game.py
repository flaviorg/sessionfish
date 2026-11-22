"""The GAME graph: one thread per session, one loop iteration per move.

    START -> prepare -> eval_position -> apply_move -> eval_position -> ... -> END

``prepare`` checks out the base (sha plus dirty patch) in a scratch clone and measures it.
``apply_move`` replays one ply. ``eval_position`` scores the resulting tree and is cached by
tree hash (``CachePolicy``), so Reads, test runs and any move that returns to a known tree cost
nothing. Every checkpoint written after ``eval_position`` is a position, so the checkpoint
history of the thread *is* the game record: ``main_line`` and ``line_from_fork`` just walk it.

Alternative lines are forks of the same thread: ``fork`` calls ``update_state(..., as_node=
"eval_position")`` on the checkpoint before a critical ply with a new list of moves and a fresh
scratch directory, and the same two nodes replay them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

from langgraph.cache.memory import InMemoryCache
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import CachePolicy

from sessionfish import scratch
from sessionfish.evaluate import (
    EvalSettings,
    assert_lines,
    evaluate_position,
    is_test_path,
    measure,
    score,
    token_penalty,
)
from sessionfish.replay import ReplayError
from sessionfish.replay import apply_move as replay_move


class GameState(TypedDict, total=False):
    source: dict[str, Any]  # repo, sha, patch, scratch_root
    setup: dict[str, Any]  # base_tree, protected, base_facts (set by prepare, then constant)
    moves: list[dict[str, Any]]  # the plies this line will play
    cursor: int  # index into moves of the next ply
    workdir: str  # scratch checkout this line plays in
    line: str  # "main" or "alt:<ply>:<n>" / "rule:<ply>"
    strict: bool  # alternative lines: a move that cannot be applied fails the line
    ply: int
    entry: dict[str, Any] | None  # the ply just played
    status: str  # applied | noop | unreplayable | start
    detail: str
    tokens: int  # tokens spent up to and including this ply
    tree: str
    eval_key: str
    position: dict[str, Any]


def eval_cache_key(state: GameState) -> str:
    """Tree hash + settings digest, plus whether moves remain.

    A cache hit replays *all* writes of the cached run, including the routing of the
    conditional edge after the node. Without the last part, a cached "go to apply_move" would
    be replayed on the final ply. Routing depends on nothing else, so this keeps hits exact.
    """
    more = state.get("cursor", 0) < len(state.get("moves", []))
    return f"{state.get('eval_key', '')}:{'more' if more else 'end'}"


def _ensure_checkout(state: GameState) -> Path:
    """Make ``workdir`` hold ``state['tree']``; a fresh fork directory is cloned on demand."""
    workdir = Path(state["workdir"])
    src = state["source"]
    if not (workdir / ".git").exists():
        scratch.clone_scratch(Path(src["scratch_root"]), workdir, src["sha"])
    if scratch.write_tree(workdir) != state["tree"]:
        scratch.restore_tree(workdir, state["tree"])
    return workdir


def build_game_graph(
    settings: EvalSettings,
    *,
    checkpointer: Any | None = None,
    cache: Any | None = None,
) -> Any:
    digest = settings.digest()

    def prepare(state: GameState) -> dict[str, Any]:
        src = state["source"]
        root = Path(src["scratch_root"])
        scratch.clone_scratch(Path(src["repo"]), root, src["sha"])
        scratch.apply_patch(root, src.get("patch", ""))
        tree = scratch.write_tree(root)
        protected = {
            p: assert_lines(scratch.show(root, tree, p)) for p in scratch.list_files(root, tree) if is_test_path(p)
        }
        facts = measure(root, settings)
        return {
            "setup": {"base_tree": tree, "protected": protected, "base_facts": facts},
            "workdir": str(root),
            "tree": tree,
            "eval_key": f"{tree}:{digest}",
            "ply": 0,
            "entry": None,
            "status": "start",
            "detail": "base position" + (" (sha + dirty patch)" if src.get("patch", "").strip() else ""),
            "tokens": 0,
        }

    def apply_move(state: GameState) -> dict[str, Any]:
        entry = state["moves"][state["cursor"]]
        workdir = _ensure_checkout(state)
        if entry.get("move") is None:
            status, detail = "unreplayable", entry.get("problem") or "move could not be parsed"
        elif entry.get("errored"):
            status, detail = "noop", "the tool call failed in the session, so it changed nothing"
        else:
            try:
                result = replay_move(workdir, entry["move"])
                status, detail = result.status, result.detail
            except ReplayError as exc:
                if state.get("strict"):
                    raise
                status, detail = "unreplayable", str(exc)
        tree = scratch.write_tree(workdir)
        return {
            "cursor": state["cursor"] + 1,
            "ply": entry["ply"],
            "entry": entry,
            "status": status,
            "detail": detail,
            "tokens": state.get("tokens", 0) + int(entry.get("tokens") or 0),
            "tree": tree,
            "eval_key": f"{tree}:{digest}",
        }

    def eval_position(state: GameState) -> dict[str, Any]:
        setup = state["setup"]
        if state["tree"] == setup["base_tree"]:  # the base was measured by prepare
            facts = setup["base_facts"]
            position = score(facts, facts, changed={}, protected={}, current_asserts={}, settings=settings)
            position.update(tree=state["tree"], facts=facts)
        else:
            workdir = _ensure_checkout(state)
            position = evaluate_position(workdir, state["tree"], setup, settings)
        return {"position": position}

    def after_eval(state: GameState) -> str:
        return "apply_move" if state.get("cursor", 0) < len(state.get("moves", [])) else END

    builder = StateGraph(GameState)
    builder.add_node("prepare", prepare)
    builder.add_node("apply_move", apply_move)
    builder.add_node("eval_position", eval_position, cache_policy=CachePolicy(key_func=eval_cache_key))
    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "eval_position")
    builder.add_edge("apply_move", "eval_position")
    builder.add_conditional_edges("eval_position", after_eval, ["apply_move", END])
    return builder.compile(
        checkpointer=checkpointer if checkpointer is not None else InMemorySaver(),
        cache=cache if cache is not None else InMemoryCache(),
        name="game",
    )


# --------------------------------------------------------------------------- the record


def run_config(thread_id: str, n_moves: int) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 2 * n_moves + 10}


def _position_entry(snap: Any, weights: Any) -> dict[str, Any]:
    v = snap.values
    entry = v.get("entry") or {}
    position = v["position"]
    return {
        "ply": v.get("ply", 0),
        "line": v.get("line", "main"),
        "move": entry.get("move"),
        "raw_tool": entry.get("tool"),
        "label": entry.get("label", "start"),
        "status": v.get("status", "start"),
        "detail": v.get("detail", ""),
        "tokens": v.get("tokens", 0),
        "tree": v["tree"],
        "position": position,
        "score": position["cp"] + token_penalty(v.get("tokens", 0), weights),
        "checkpoint": snap.config,
    }


def _is_position(snap: Any) -> bool:
    """A checkpoint written by eval_position (or a fork placed as if eval_position wrote it)."""
    return "position" in snap.values and snap.next in ((), ("apply_move",)) and bool(snap.values.get("tree"))


def _walk(app: Any, thread_cfg: dict[str, Any], leaf: Any) -> list[Any]:
    history = {s.config["configurable"]["checkpoint_id"]: s for s in app.get_state_history(thread_cfg)}
    chain, snap = [], leaf
    while snap is not None:
        chain.append(snap)
        parent = snap.parent_config
        snap = history.get(parent["configurable"]["checkpoint_id"]) if parent else None
    return list(reversed(chain))


def main_line(
    app: Any, thread_cfg: dict[str, Any], weights: Any, leaf_cfg: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """The game record: one entry per position on the main line, ply 0 first.

    Without ``leaf_cfg`` the latest checkpoint of the thread is the leaf, which is only the
    main line until the first fork; pass the leaf saved after the game to read it later.
    """
    leaf = app.get_state(leaf_cfg or thread_cfg)
    chain = _walk(app, thread_cfg, leaf)
    out: list[dict[str, Any]] = []
    for snap in chain:
        if _is_position(snap) and snap.metadata.get("source") == "loop":
            out.append(_position_entry(snap, weights))
    return out


def line_from_fork(
    app: Any, thread_cfg: dict[str, Any], fork_cfg: dict[str, Any], weights: Any
) -> list[dict[str, Any]]:
    """Positions of a forked line: the fork point (the position it was played from) and every
    position after it. Lines fork from the main line only, so descendants form a chain."""
    snaps = list(app.get_state_history(thread_cfg))
    by_id = {s.config["configurable"]["checkpoint_id"]: s for s in snaps}
    children: dict[str, list[Any]] = {}
    for s in snaps:
        if s.parent_config:
            children.setdefault(s.parent_config["configurable"]["checkpoint_id"], []).append(s)
    node = by_id[fork_cfg["configurable"]["checkpoint_id"]]
    chain = [node]
    while kids := children.get(node.config["configurable"]["checkpoint_id"]):
        node = kids[0]
        chain.append(node)
    return [_position_entry(s, weights) for s in chain if _is_position(s)]


def fork(
    app: Any,
    at: dict[str, Any],
    moves: list[dict[str, Any]],
    *,
    workdir: Path,
    line: str,
) -> dict[str, Any]:
    """Fork the game at checkpoint ``at`` to play ``moves`` instead; returns the fork's config."""
    return app.update_state(
        at,
        {"moves": moves, "cursor": 0, "workdir": str(workdir), "line": line, "strict": True},
        as_node="eval_position",
    )
