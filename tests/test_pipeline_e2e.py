from __future__ import annotations

from pathlib import Path

from journie_data.artifacts.store import ArtifactStore
from journie_data.config import BBox, Region, load_pipeline_config
from journie_data.pipeline import DataPipeline
from journie_data.sources import FoursquareAdapter, OSMAdapter, OvertureAdapter, VisitHCMCAdapter
from journie_data.sources.base import FetchResult, FetchStatus, read_records

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures"


def local_fetch(adapter: object, fixture: Path) -> None:
    records = read_records(fixture)

    def fetch(region: object, snapshot_date: object) -> FetchResult:
        return FetchResult(adapter.source_name, FetchStatus.SUCCESS, records)

    adapter.fetch = fetch


def test_fixture_pipeline_builds_versioned_outputs_without_network(tmp_path, monkeypatch) -> None:
    config = load_pipeline_config(PROJECT_ROOT / "configs")
    polygon = tmp_path / "hcm.geojson"
    polygon.write_text('{"type":"Polygon","coordinates":[]}', encoding="ascii")
    region = Region(
        id="hcm",
        name="Ho Chi Minh City",
        country_code="VN",
        timezone="Asia/Ho_Chi_Minh",
        bbox=BBox(min_lon=106.3498, min_lat=10.3510, max_lon=107.0175, max_lat=11.1602),
        polygon_file=polygon,
        boundary_source="test-fixture",
    )
    monkeypatch.chdir(tmp_path)
    store = ArtifactStore(tmp_path / "data")
    pipeline = DataPipeline(store, region, "fixture-run", config)
    adapters = [
        (OSMAdapter(), FIXTURES / "osm.json"),
        (OvertureAdapter(), FIXTURES / "overture.json"),
        (FoursquareAdapter(), FIXTURES / "foursquare.json"),
        (VisitHCMCAdapter(), FIXTURES / "visithcmc.json"),
    ]

    staging = []
    for adapter, fixture in adapters:
        local_fetch(adapter, fixture)
        staging.extend(pipeline.ingest(adapter, "2026-09-01"))
    outputs = pipeline.build(staging)

    assert len(staging) == 6
    assert pipeline.outputs.source_statuses == {
        "osm": "success",
        "overture": "success",
        "foursquare": "success",
        "visithcmc": "success",
    }
    artifacts = {
        (manifest.artifact_type, path.stem): (path, manifest)
        for path, manifest in outputs.artifacts
    }
    pois = store.read_table(artifacts[("canonical", "pois")][0])
    memberships = store.read_table(artifacts[("canonical", "poi_sources")][0])
    evidence = store.read_table(artifacts[("canonical", "poi_field_evidence")][0])
    rag = store.read_table(artifacts[("derived", "rag_pois")][0])
    csp = store.read_table(artifacts[("derived", "csp_pois")][0])

    assert len(pois) == 3
    assert sorted(poi["source_count"] for poi in pois) == [1, 2, 3]
    assert len(memberships) == 6
    assert len(rag) == 3
    assert len(csp) == 1
    assert csp[0]["category"] == "attraction.museum"
    assert any(
        row["field"] == "expected_visit_duration_min"
        and row["method"] == "heuristic"
        and row["transformation_version"] == "journie_category_prior_v1"
        for row in evidence
    )
    assert all(path.exists() and manifest.content_sha256 for path, manifest in outputs.artifacts)
    staging_ids = {
        manifest.artifact_id
        for _, manifest in outputs.artifacts
        if manifest.artifact_type == "staging"
    }
    canonical_ids = {
        manifest.artifact_id
        for _, manifest in outputs.artifacts
        if manifest.artifact_type == "canonical"
    }
    assert all(
        set(manifest.parent_artifact_ids) == staging_ids
        for _, manifest in outputs.artifacts
        if manifest.artifact_type == "canonical"
    )
    assert all(
        canonical_ids & set(manifest.parent_artifact_ids)
        for _, manifest in outputs.artifacts
        if manifest.artifact_type == "derived"
    )

    resumed = DataPipeline(store, region, "fixture-run", config, resume=True)
    resumed_staging = []
    for adapter, _ in adapters:
        resumed_staging.extend(resumed.ingest(adapter, "2026-09-01"))
    resumed_outputs = resumed.build(resumed_staging)
    assert len(resumed_staging) == 6
    assert {manifest.artifact_id for _, manifest in resumed_outputs.artifacts} == {
        manifest.artifact_id for _, manifest in outputs.artifacts
    }


def test_resume_builds_missing_staging_from_raw_without_fetch(tmp_path, monkeypatch) -> None:
    config = load_pipeline_config(PROJECT_ROOT / "configs")
    polygon = tmp_path / "hcm.geojson"
    polygon.write_text('{"type":"Polygon","coordinates":[]}', encoding="ascii")
    region = Region(
        id="hcm",
        name="Ho Chi Minh City",
        country_code="VN",
        timezone="Asia/Ho_Chi_Minh",
        bbox=BBox(min_lon=106.3, min_lat=10.3, max_lon=107.1, max_lat=11.2),
        polygon_file=polygon,
        boundary_source="test-fixture",
    )
    monkeypatch.chdir(tmp_path)
    store = ArtifactStore(tmp_path / "data")
    raw_records = [
        {
            "type": "node",
            "id": 1,
            "lat": 10.77916,
            "lon": 106.69979,
            "tags": {"name": "Museum", "tourism": "museum"},
        }
    ]
    raw_path = store.write_raw_jsonl("hcm", "snapshot-1", "osm", raw_records)
    store.write_raw_manifest(
        region="hcm",
        snapshot_id="snapshot-1",
        source="osm",
        raw_path=raw_path,
        metadata={
            "adapter_version": "osm/v1",
            "status": "success",
            "record_count": 1,
            "retrieved_at": "2026-09-01T00:00:00+00:00",
        },
    )
    adapter = OSMAdapter()

    def unexpected_fetch(*args: object) -> object:
        raise AssertionError("resume should not fetch when the raw snapshot exists")

    monkeypatch.setattr(adapter, "fetch", unexpected_fetch)
    pipeline = DataPipeline(store, region, "resumed-run", config, resume=True)

    records = pipeline.ingest(adapter, "snapshot-1")

    assert len(records) == 1
    assert records[0].source_record_id == "node/1"
    assert pipeline.outputs.source_statuses == {"osm": "success"}
