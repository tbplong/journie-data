from __future__ import annotations

import pytest

from journie_data.runtime.dag import Dag, Task


def test_dag_executes_dependencies_before_dependents_once() -> None:
    calls: list[str] = []
    dag = Dag()
    dag.add(Task("release", lambda: calls.append("release"), ("canonical", "report")))
    dag.add(Task("raw", lambda: calls.append("raw")))
    dag.add(Task("report", lambda: calls.append("report"), ("raw",)))
    dag.add(Task("canonical", lambda: calls.append("canonical"), ("raw",)))

    results = dag.execute()

    assert calls == ["raw", "canonical", "report", "release"]
    assert tuple(results) == ("raw", "canonical", "report", "release")


def test_dag_rejects_cycles_unknown_dependencies_and_duplicates() -> None:
    cyclic = Dag()
    cyclic.add(Task("a", lambda: None, ("b",)))
    cyclic.add(Task("b", lambda: None, ("a",)))
    with pytest.raises(ValueError, match="cycle"):
        cyclic.order()

    unknown = Dag()
    unknown.add(Task("a", lambda: None, ("missing",)))
    with pytest.raises(ValueError, match="unknown dependency: missing"):
        unknown.order()

    duplicate = Dag()
    duplicate.add(Task("a", lambda: None))
    with pytest.raises(ValueError, match="duplicate task"):
        duplicate.add(Task("a", lambda: None))
