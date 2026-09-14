from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Task:
    name: str
    run: Callable[[], Any]
    dependencies: tuple[str, ...] = ()


@dataclass(slots=True)
class Dag:
    tasks: dict[str, Task] = field(default_factory=dict)

    def add(self, task: Task) -> None:
        if task.name in self.tasks:
            raise ValueError(f"duplicate task: {task.name}")
        self.tasks[task.name] = task

    def order(self) -> list[str]:
        result: list[str] = []
        visiting: set[str] = set()

        def visit(name: str) -> None:
            if name in result:
                return
            if name in visiting:
                raise ValueError(f"task dependency cycle at {name}")
            if name not in self.tasks:
                raise ValueError(f"unknown dependency: {name}")
            visiting.add(name)
            for dependency in self.tasks[name].dependencies:
                visit(dependency)
            visiting.remove(name)
            result.append(name)

        for task_name in self.tasks:
            visit(task_name)
        return result

    def execute(self) -> dict[str, Any]:
        return {name: self.tasks[name].run() for name in self.order()}
