from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from journie_data.schemas.models import StagingRecord
from journie_data.schemas.registry import MIGRATIONS, migrate_row, validate_rows


def staging_row(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "schema_version": "staging/v1",
        "source": "fixture",
        "source_record_id": "1",
        "retrieved_at": datetime(2026, 9, 1, tzinfo=UTC),
        "name": "Fixture POI",
        "latitude": 10.7,
        "longitude": 106.7,
    }
    row.update(updates)
    return row


def test_staging_schema_rejects_bad_values_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        StagingRecord.model_validate(staging_row(name=" "))
    with pytest.raises(ValidationError):
        StagingRecord.model_validate(staging_row(latitude=91))
    with pytest.raises(ValidationError):
        StagingRecord.model_validate(staging_row(unexpected=True))


def test_model_rejects_a_mismatched_schema_version() -> None:
    with pytest.raises(ValidationError):
        StagingRecord.model_validate(staging_row(schema_version="staging/v999"))


def test_registry_rejects_row_version_mismatch() -> None:
    with pytest.raises(ValidationError):
        validate_rows("staging/v1", [staging_row(schema_version="poi/v1")])


def test_unknown_schema_and_unregistered_migration_are_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported schema"):
        validate_rows("staging/v999", [staging_row()])
    with pytest.raises(ValueError, match="no migration"):
        migrate_row(staging_row(schema_version="staging/v0"), "staging/v1")


def test_migration_output_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(MIGRATIONS, ("staging/v0", "staging/v1"), lambda row: {"name": ""})

    with pytest.raises(ValidationError):
        migrate_row(staging_row(schema_version="staging/v0"), "staging/v1")
