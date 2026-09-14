from __future__ import annotations

import json

import pytest

from journie_data.artifacts.manifest import DatasetManifest
from journie_data.artifacts.store import ArtifactStore, file_hash, stable_hash


def test_raw_snapshot_is_idempotent_but_rejects_conflicting_content(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    records = [{"id": 1, "name": "Museum"}]

    path = store.write_raw_jsonl("hcm", "2026-09-01", "fixture", records)
    original = path.read_bytes()
    assert store.write_raw_jsonl("hcm", "2026-09-01", "fixture", records) == path
    assert path.read_bytes() == original

    with pytest.raises(FileExistsError, match="immutable"):
        store.write_raw_jsonl("hcm", "2026-09-01", "fixture", [{"id": 2}])
    assert path.read_bytes() == original


def test_table_artifact_is_immutable_and_manifest_matches_bytes(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    config = {"threshold": 0.9, "sources": ["osm"]}

    path, manifest = store.write_table(
        region="hcm",
        run_id="run-1",
        stage="staging",
        name="osm",
        rows=[{"id": "node/1", "name": "Museum"}],
        schema_version="staging/v1",
        config=config,
        source="osm",
        parents=["raw-1"],
    )

    on_disk = json.loads((path.parent / "manifest.json").read_text(encoding="utf-8"))
    assert manifest.content_sha256 == file_hash(path)
    assert manifest.config_sha256 == stable_hash(config)
    assert manifest.row_count == 1
    assert manifest.parent_artifact_ids == ["raw-1"]
    assert on_disk["artifact_id"] == manifest.artifact_id
    assert store.read_table(path) == [{"id": "node/1", "name": "Museum"}]

    with pytest.raises(FileExistsError, match="immutable"):
        store.write_table(
            region="hcm",
            run_id="run-1",
            stage="staging",
            name="osm",
            rows=[],
            schema_version="staging/v1",
            config=config,
        )


def test_table_infers_sparse_columns_beyond_the_default_sample(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    rows = [{"id": index, "operating_status": None} for index in range(100)]
    rows.append({"id": 100, "operating_status": "open"})

    path, _ = store.write_table(
        region="hcm",
        run_id="run-sparse",
        stage="staging",
        name="overture",
        rows=rows,
        schema_version="staging/v1",
        config={},
    )

    assert store.read_table(path)[-1]["operating_status"] == "open"


def test_release_contains_dataset_manifest_and_cannot_be_replaced(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    artifact = store.write_table(
        region="hcm",
        run_id="run-1",
        stage="canonical",
        name="pois",
        rows=[{"poi_id": "p1"}],
        schema_version="poi/v1",
        config={},
    )

    release = store.create_release(
        dataset_version="0.1",
        region="hcm",
        run_id="run-1",
        artifacts=[artifact],
        sources={"osm": "2026-09-01"},
    )
    manifest = DatasetManifest.model_validate_json(
        (release / "manifest.json").read_text(encoding="utf-8")
    )

    assert manifest.dataset_version == "0.1"
    assert manifest.artifacts[0].content_sha256 == file_hash(release / "canonical" / "pois.parquet")
    with pytest.raises(FileExistsError, match="release already exists"):
        store.create_release(
            dataset_version="0.1",
            region="hcm",
            run_id="run-1",
            artifacts=[artifact],
            sources={},
        )
