"""Check chart denominators without Matplotlib or network access."""

from plot_report import OUTCOMES, aggregate


def test_report_outcomes_and_denominators():
    rows = []
    for i in range(5):
        rows.append(
            {
                "test_case_id": f"case-{i}",
                "failure": "empty_response" if i == 0 else None,
                "json_parse_valid": i >= 2,
                "schema_valid": i >= 3,
                "latency_seconds": float(i + 1),
                "metrics": {
                    "complete_case_accuracy": int(i == 4),
                    "hallucinated_case": int(i == 3),
                    "populated_field_correct": int(i == 4),
                    "populated_field_total": 1,
                    "empty_field_correct": int(i >= 3),
                    "empty_field_total": 2,
                },
            }
        )
    stats = aggregate(rows)
    assert stats["outcomes"] == dict.fromkeys(OUTCOMES, 1)
    assert sum(stats["outcomes"].values()) == stats["requests"] == 5
    assert stats["schema_valid_rate"] == 2 / 5
    assert stats["complete_case_accuracy"] == 1 / 5
    assert stats["hallucination_rate"] == 1 / 2
    assert stats["p50_seconds"] == 3 and stats["p95_seconds"] == 4.8
    assert stats["populated_field_accuracy"] == 1 / 5
    assert stats["empty_field_accuracy"] == 2 / 10
    assert aggregate(rows[:3])["hallucination_rate"] is None
