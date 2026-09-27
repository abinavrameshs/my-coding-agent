"""Parallel plan execution — pure scheduling helpers.

This module contains **no I/O and no LLM calls**. It takes a set of plan items
(identified by opaque string ids) plus a dependency map and computes how to run
them concurrently in dependency-respecting *batches*.

The scheduler is intentionally conservative:

- Items in the same batch never depend on each other (topological levels).
- A batch is never wider than ``max_width`` (concurrency cap).
- Items that declare overlapping *files* are never placed in the same batch —
  parallel editors of the same file would race, so they are serialised.

Everything degrades to "one item per batch, in input order" (i.e. today's
sequential behaviour) when the dependency information is missing or unusable.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "normalize_deps",
    "has_cycle",
    "plan_batches",
    "group_by_files",
]


def normalize_deps(
    raw: Any,
    item_ids: list[str],
) -> dict[str, list[str]]:
    """Validate and normalise a dependency map.

    Args:
        raw: Whatever the classifier returned. Expected to be
            ``{item_id: [dep_id, ...]}`` but may be malformed.
        item_ids: The authoritative list of plan item ids.

    Returns:
        A dict with exactly one key per id in *item_ids*. Unknown ids (both as
        keys and as dependency targets) are dropped. Self-dependencies are
        dropped. On any structural error the map degrades to
        ``{id: [] for id in item_ids}`` (all independent).
    """
    empty: dict[str, list[str]] = {i: [] for i in item_ids}
    if not isinstance(raw, dict):
        return empty

    valid = set(item_ids)
    result: dict[str, list[str]] = {}
    for item_id in item_ids:
        deps_raw = raw.get(item_id, [])
        deps: list[str] = []
        if isinstance(deps_raw, (list, tuple, set)):
            for d in deps_raw:
                if (
                    isinstance(d, str)
                    and d in valid
                    and d != item_id
                    and d not in deps
                ):
                    deps.append(d)
        elif isinstance(deps_raw, str):
            # Tolerate a single id given as a bare string.
            if deps_raw in valid and deps_raw != item_id:
                deps.append(deps_raw)
        result[item_id] = deps
    return result


def has_cycle(deps: dict[str, list[str]]) -> bool:
    """Return True if the dependency graph contains a cycle."""
    # Iterative DFS with colouring: 0=unvisited, 1=on-stack, 2=done.
    colour: dict[str, int] = {node: 0 for node in deps}
    for start in deps:
        if colour.get(start, 0) != 0:
            continue
        stack: list[tuple[str, int]] = [(start, 0)]
        while stack:
            node, idx = stack.pop()
            if idx == 0:
                if colour.get(node, 0) == 1:
                    return True
                if colour.get(node, 0) == 2:
                    continue
                colour[node] = 1
            neighbours = deps.get(node, [])
            if idx < len(neighbours):
                stack.append((node, idx + 1))
                nxt = neighbours[idx]
                if colour.get(nxt, 0) == 1:
                    return True
                if colour.get(nxt, 0) == 0:
                    stack.append((nxt, 0))
            else:
                colour[node] = 2
    return False


def plan_batches(
    item_ids: list[str],
    deps: dict[str, list[str]],
    max_width: int = 4,
) -> list[list[str]]:
    """Group *item_ids* into dependency-respecting parallel batches.

    Each returned batch is a list of ids that can safely run concurrently.
    Batches must be executed in order: a later batch may depend on an earlier
    one, but never within the same batch.

    Args:
        item_ids: Plan item ids in their original order.
        deps: ``{id: [dep_id, ...]}`` dependency map (see :func:`normalize_deps`).
        max_width: Maximum number of items per batch (concurrency cap). A
            value < 1 is treated as 1.

    Returns:
        A list of batches. Ids keep their relative input order. If the graph has
        a cycle, returns a single serial batch (one id per batch, input order)
        so the caller never deadlocks.
    """
    max_width = max(1, int(max_width))
    ids = list(item_ids)
    if not ids:
        return []

    norm = normalize_deps(deps, ids)

    # Cycle → safest possible fallback: fully serial, input order.
    if has_cycle(norm):
        return [[i] for i in ids]

    remaining = set(ids)
    done: set[str] = set()
    batches: list[list[str]] = []

    while remaining:
        # Ready = every dependency already completed.
        ready = [i for i in ids if i in remaining and all(d in done for d in norm[i])]
        if not ready:
            # Defensive: shouldn't happen without a cycle, but never loop forever.
            batches.extend([[i] for i in ids if i in remaining])
            break
        # Chunk the ready level to respect the width cap.
        for start in range(0, len(ready), max_width):
            batches.append(ready[start : start + max_width])
        remaining -= set(ready)
        done |= set(ready)

    return batches


def group_by_files(
    batches: list[list[str]],
    file_hints: dict[str, list[str]],
) -> list[list[str]]:
    """Serialise same-batch items that declare overlapping files.

    Two items that touch the same file must not run concurrently, because
    subagents have isolated histories and cannot merge each other's edits.
    Within each batch, items are walked in order and any item whose file set
    intersects the running "claimed" set is deferred to a new sub-batch.

    Args:
        batches: Output of :func:`plan_batches`.
        file_hints: ``{item_id: [path, ...]}``. Missing ids are treated as
            touching no files (never conflict).

    Returns:
        A new list of batches with conflicting items split apart. Ordering of
        the original batches is preserved.
    """
    result: list[list[str]] = []
    for batch in batches:
        current: list[str] = []
        claimed: set[str] = set()
        for item_id in batch:
            files = set(file_hints.get(item_id, []) or [])
            if files and files & claimed:
                # Conflict — flush the current sub-batch and start a new one.
                if current:
                    result.append(current)
                current = [item_id]
                claimed = set(files)
            else:
                current.append(item_id)
                claimed |= files
        if current:
            result.append(current)
    return result
