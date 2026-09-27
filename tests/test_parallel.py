"""Tests for the pure parallel-scheduling helpers (agent.parallel)."""

from __future__ import annotations

from agent.parallel import group_by_files, has_cycle, normalize_deps, plan_batches


class TestNormalizeDeps:
    def test_all_ids_present_even_when_missing(self) -> None:
        deps = normalize_deps({}, ["1", "2", "3"])
        assert deps == {"1": [], "2": [], "3": []}

    def test_unknown_keys_and_targets_dropped(self) -> None:
        raw = {"1": ["2", "99"], "99": ["1"], "2": []}
        deps = normalize_deps(raw, ["1", "2"])
        assert deps == {"1": ["2"], "2": []}

    def test_self_dependency_dropped(self) -> None:
        deps = normalize_deps({"1": ["1"]}, ["1"])
        assert deps == {"1": []}

    def test_duplicate_deps_removed(self) -> None:
        deps = normalize_deps({"2": ["1", "1"]}, ["1", "2"])
        assert deps == {"1": [], "2": ["1"]}

    def test_bare_string_dep_tolerated(self) -> None:
        deps = normalize_deps({"2": "1"}, ["1", "2"])
        assert deps == {"1": [], "2": ["1"]}

    def test_non_dict_degrades_to_all_independent(self) -> None:
        assert normalize_deps(None, ["1", "2"]) == {"1": [], "2": []}
        assert normalize_deps([1, 2, 3], ["1", "2"]) == {"1": [], "2": []}


class TestHasCycle:
    def test_no_cycle(self) -> None:
        assert has_cycle({"1": [], "2": ["1"], "3": ["2"]}) is False

    def test_simple_cycle(self) -> None:
        assert has_cycle({"1": ["2"], "2": ["1"]}) is True

    def test_self_cycle(self) -> None:
        assert has_cycle({"1": ["1"]}) is True

    def test_empty(self) -> None:
        assert has_cycle({}) is False


class TestPlanBatches:
    def test_all_independent_is_one_batch(self) -> None:
        batches = plan_batches(["1", "2", "3"], {}, max_width=4)
        assert batches == [["1", "2", "3"]]

    def test_linear_chain_serialises(self) -> None:
        deps = {"1": [], "2": ["1"], "3": ["2"]}
        batches = plan_batches(["1", "2", "3"], deps, max_width=4)
        assert batches == [["1"], ["2"], ["3"]]

    def test_diamond_dependencies(self) -> None:
        # 1 -> 2, 1 -> 3, (2,3) -> 4
        deps = {"1": [], "2": ["1"], "3": ["1"], "4": ["2", "3"]}
        batches = plan_batches(["1", "2", "3", "4"], deps, max_width=4)
        assert batches == [["1"], ["2", "3"], ["4"]]

    def test_width_cap_splits_wide_level(self) -> None:
        batches = plan_batches(["1", "2", "3", "4", "5"], {}, max_width=2)
        assert batches == [["1", "2"], ["3", "4"], ["5"]]

    def test_input_order_preserved(self) -> None:
        batches = plan_batches(["b", "a", "c"], {}, max_width=4)
        assert batches == [["b", "a", "c"]]

    def test_empty_input(self) -> None:
        assert plan_batches([], {}, max_width=4) == []

    def test_cycle_degrades_to_serial(self) -> None:
        deps = {"1": ["2"], "2": ["1"], "3": []}
        batches = plan_batches(["1", "2", "3"], deps, max_width=4)
        assert batches == [["1"], ["2"], ["3"]]

    def test_width_below_one_treated_as_one(self) -> None:
        batches = plan_batches(["1", "2"], {}, max_width=0)
        assert batches == [["1"], ["2"]]


class TestGroupByFiles:
    def test_disjoint_files_stay_together(self) -> None:
        batches = [["1", "2"]]
        hints = {"1": ["a.py"], "2": ["b.py"]}
        assert group_by_files(batches, hints) == [["1", "2"]]

    def test_overlapping_files_split(self) -> None:
        batches = [["1", "2"]]
        hints = {"1": ["a.py"], "2": ["a.py"]}
        assert group_by_files(batches, hints) == [["1"], ["2"]]

    def test_partial_overlap_splits_only_conflicting(self) -> None:
        batches = [["1", "2", "3"]]
        hints = {"1": ["a.py"], "2": ["b.py"], "3": ["a.py", "c.py"]}
        # 1 claims a.py; 2 is disjoint (joins); 3 conflicts with 1 -> new sub-batch
        assert group_by_files(batches, hints) == [["1", "2"], ["3"]]

    def test_missing_hints_never_conflict(self) -> None:
        batches = [["1", "2"]]
        assert group_by_files(batches, {}) == [["1", "2"]]

    def test_multiple_batches_preserved(self) -> None:
        batches = [["1"], ["2", "3"]]
        hints = {"2": ["x"], "3": ["x"]}
        assert group_by_files(batches, hints) == [["1"], ["2"], ["3"]]
