from __future__ import annotations

import re

from .models import ConfigurationError, Issue


DEPENDENCY_LINE = re.compile(r"^Depends-On:\s*(.+?)\s*$", re.IGNORECASE)
DEPENDENCY_LIST = re.compile(r"#(\d+)(?:\s*,\s*#(\d+))*")


def parse_dependencies(issue: Issue) -> tuple[int, ...]:
    """Parse the single explicit `Depends-On: #N, #M` issue-body declaration."""
    declarations = [match.group(1) for line in issue.body.splitlines()
                    if (match := DEPENDENCY_LINE.fullmatch(line.strip()))]
    prefixed = [line for line in issue.body.splitlines() if line.strip().lower().startswith("depends-on:")]
    if len(prefixed) != len(declarations) or len(declarations) > 1:
        raise ConfigurationError("malformed Depends-On declaration; use one line: Depends-On: #10, #11")
    if not declarations:
        return ()
    value = declarations[0]
    if not DEPENDENCY_LIST.fullmatch(value):
        raise ConfigurationError("malformed Depends-On declaration; use: Depends-On: #10, #11")
    numbers = tuple(int(part[1:]) for part in re.split(r"\s*,\s*", value))
    if issue.number in numbers:
        raise ConfigurationError(f"issue #{issue.number} cannot depend on itself")
    return tuple(dict.fromkeys(numbers))


def cycle_members(graph: dict[int, tuple[int, ...]]) -> set[int]:
    visiting: set[int] = set()
    visited: set[int] = set()
    cyclic: set[int] = set()

    def visit(number: int, path: list[int]) -> None:
        if number in visiting:
            cyclic.update(path[path.index(number):])
            return
        if number in visited:
            return
        visiting.add(number); path.append(number)
        for dependency in graph.get(number, ()):
            if dependency in graph:
                visit(dependency, path)
        path.pop(); visiting.remove(number); visited.add(number)

    for number in sorted(graph):
        visit(number, [])
    return cyclic
