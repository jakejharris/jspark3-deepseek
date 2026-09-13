"""Frozen held-out functional checks; execute only inside the code sandbox."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import runpy


def check_merge_config_layers(module):
    function = module["merge_config_layers"]
    cases = [
        ([], {}),
        ([{"a": 1}], {"a": 1}),
        ([{"a": {"x": 1, "y": 2}}, {"a": {"y": 7, "z": 9}}], {"a": {"x": 1, "y": 7, "z": 9}}),
        ([{"a": {"x": 1}}, {"a": 3}, {"a": {"q": 4}}], {"a": {"q": 4}}),
        ([{"x": [1, 2], "z": None}, {"x": [3], "ok": False}], {"x": [3], "z": None, "ok": False}),
    ]
    for layers, expected in cases:
        before = copy.deepcopy(layers)
        actual = function(layers)
        assert actual == expected, (actual, expected)
        assert layers == before, "input mutated"
        assert actual is not expected
    alias_layers = [{"a": {"items": [1]}}, {"b": [2]}]
    alias_before = copy.deepcopy(alias_layers)
    alias_result = function(alias_layers)
    alias_result["a"]["items"].append(7)
    alias_result["b"].append(8)
    assert alias_layers == alias_before, "output aliases an input container"
    invalid_values = (None, {}, [1], [{"a": {1: "bad"}}], [{"a": [{1: "bad"}]}])
    for invalid in invalid_values:
        try:
            function(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError((invalid, "expected ValueError"))
    return len(cases) + 1 + len(invalid_values)


def check_allocate_quota(module):
    function = module["allocate_quota"]
    cases = [
        ((0, {"a": 1}), {"a": 0}),
        ((7, {"b": 1, "a": 1}), {"a": 4, "b": 3}),
        ((10, {"a": 1, "b": 2, "c": 1}), {"a": 3, "b": 5, "c": 2}),
        ((3, {"z": 5, "a": 5, "m": 5}), {"a": 1, "m": 1, "z": 1}),
        ((2, {"a": 1, "b": 100}), {"a": 0, "b": 2}),
    ]
    for (total, weights), expected in cases:
        before = copy.deepcopy(weights)
        actual = function(total, weights)
        assert actual == expected, (actual, expected)
        assert list(actual) == sorted(actual), "keys must be lexicographic"
        assert sum(actual.values()) == total
        assert weights == before, "input mutated"
    invalid = [
        (-1, {"a": 1}),
        (1.5, {"a": 1}),
        (True, {"a": 1}),
        (1, {}),
        (1, {"a": 0}),
        (1, {"a": True}),
        (1, {1: 2}),
    ]
    for total, weights in invalid:
        try:
            function(total, weights)
        except ValueError:
            pass
        else:
            raise AssertionError(((total, weights), "expected ValueError"))
    return len(cases) + len(invalid)


def check_event_windows(module):
    function = module["event_windows"]
    cases = [
        (([], 3), []),
        (([("a", 0)], 1), [{"start": 0, "end": 1, "labels": ["a"]}]),
        (
            ([('c', 5), ('a', 0), ('b', 2), ('d', 5), ('e', 9)], 4),
            [
                {"start": 0, "end": 4, "labels": ["a", "b"]},
                {"start": 5, "end": 9, "labels": ["c", "d"]},
                {"start": 9, "end": 13, "labels": ["e"]},
            ],
        ),
        (([("x", -3), ("y", 1), ("z", 4)], 4), [
            {"start": -3, "end": 1, "labels": ["x"]},
            {"start": 1, "end": 5, "labels": ["y", "z"]},
        ]),
    ]
    for (events, width), expected in cases:
        before = copy.deepcopy(events)
        actual = function(events, width)
        assert actual == expected, (actual, expected)
        assert events == before, "input mutated"
    invalid = [
        ([], 0),
        ([], 1.5),
        ([], True),
        (None, 3),
        ([("a", 1), ("a", 2)], 3),
        ([(1, 2)], 3),
        ([("a", True)], 3),
    ]
    for events, width in invalid:
        try:
            function(events, width)
        except ValueError:
            pass
        else:
            raise AssertionError(((events, width), "expected ValueError"))
    return len(cases) + len(invalid)


CHECKS = {
    "held-code-merge-config": check_merge_config_layers,
    "held-code-allocate-quota": check_allocate_quota,
    "held-code-event-windows": check_event_windows,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("task", choices=sorted(CHECKS))
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    module = runpy.run_path(args.source)
    cases = CHECKS[args.task](module)
    print(json.dumps({"state": "PASSED", "task": args.task, "cases": cases}), flush=True)


if __name__ == "__main__":
    main()
