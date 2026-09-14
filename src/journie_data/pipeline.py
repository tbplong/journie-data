from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from journie_data.artifacts.manifest import ArtifactManifest
from journie_data.artifacts.store import ArtifactStore, file_hash
from journie_data.config import Region
from journie_data.derived.csp import build_csp_base, csp_ineligibility_reasons
from journie_data.derived.rag import build_rag_base, rag_ineligibility_reasons
from journie_data.enrichment.cost import estimate_cost
from journie_data.enrichment.duration import estimate_duration
from journie_data.logging import RunLogger
from journie_data.normalization.contact import normalize_phone, normalize_website
from journie_data.normalization.geo import geojson_has_polygon, point_in_geojson, valid_coordinates
from journie_data.normalization.names import normalized_name_keys
from journie_data.parsing.opening_hours import parse_opening_hours
from journie_data.parsing.prices import parse_price
from journie_data.quality.metrics import (
    benchmark_sources,
    category_distribution,
    missing_values,
    parser_failures,
    unmapped_categories,
)
from journie_data.resolution.candidates import generate_candidate_pairs
from journie_data.resolution.clustering import constrained_clusters
from journie_data.resolution.merge import merge_records
from journie_data.resolution.scoring import score_candidate
from journie_data.schemas.models import CanonicalPoi, FieldEvidence, PoiSource, StagingRecord
from journie_data.sources.base import SourceAdapter, read_records


@dataclass(slots=True)
class PipelineOutputs:
    artifacts: list[tuple[Path, ArtifactManifest]]
    source_statuses: dict[str, str]


def prepare_staging(record: StagingRecord) -> StagingRecord:
    normalized_name, normalized_name_ascii = normalized_name_keys(record.name)
    hours = parse_opening_hours(record.opening_hours_raw)
    price = parse_price(record.price_raw)
    update: dict[str, Any] = {
        "normalized_name": normalized_name,
        "normalized_name_ascii": normalized_name_ascii,
        "website_domain": normalize_website(record.website),
        "normalized_phone": normalize_phone(record.phone),
        "opening_hours_normalized": hours,
    }
    if price.type != "unknown":
        update.update(
            cost_min_vnd=price.min_vnd,
            cost_typical_vnd=price.typical_vnd,
            cost_max_vnd=price.max_vnd,
            cost_type=price.type,
        )
    return record.model_copy(update=update)


def normalize_records(
    adapter: SourceAdapter,
    records: Iterable[Mapping[str, Any]],
    retrieved_at: datetime | None = None,
    region_geojson: Mapping[str, Any] | None = None,
) -> tuple[list[StagingRecord], list[dict[str, Any]]]:
    timestamp = retrieved_at or datetime.now(UTC)
    accepted: list[StagingRecord] = []
    rejected: list[dict[str, Any]] = []
    for index, raw in enumerate(records):
        if not adapter.include_record(raw):
            rejected.append({"index": index, "reason": "source_filter", "raw": dict(raw)})
            continue
        try:
            record = prepare_staging(adapter.normalize_record(raw, retrieved_at=timestamp))
            if (
                region_geojson
                and geojson_has_polygon(region_geojson)
                and valid_coordinates(record.latitude, record.longitude)
            ):
                longitude = cast(float, record.longitude)
                latitude = cast(float, record.latitude)
                record = record.model_copy(
                    update={
                        "outside_region": not point_in_geojson(longitude, latitude, region_geojson)
                    }
                )
            accepted.append(record)
        except (TypeError, ValueError) as exc:
            rejected.append({"index": index, "reason": str(exc), "raw": dict(raw)})
    return accepted, rejected


def canonicalize(
    records: list[StagingRecord],
    field_priority: Mapping[str, list[str]] | None = None,
    duration_config: Mapping[str, Any] | None = None,
    cost_config: Mapping[str, Any] | None = None,
) -> tuple[list[CanonicalPoi], list[FieldEvidence], list[PoiSource], list[dict[str, Any]]]:
    candidates = generate_candidate_pairs(records)
    scores = [score_candidate(candidate) for candidate in candidates]
    review = [
        {
            "left_source": cast(StagingRecord, pair.left).source,
            "left_id": cast(StagingRecord, pair.left).source_record_id,
            "right_source": cast(StagingRecord, pair.right).source,
            "right_id": cast(StagingRecord, pair.right).source_record_id,
            "distance_m": pair.distance_m,
            **asdict(score),
        }
        for pair, score in zip(candidates, scores, strict=True)
    ]
    clusters = constrained_clusters(records, candidates)
    pois: list[CanonicalPoi] = []
    evidence: list[FieldEvidence] = []
    memberships: list[PoiSource] = []
    for cluster in clusters:
        merged = merge_records(cluster, dict(field_priority or {}))
        poi = merged.poi
        poi_evidence = list(merged.evidence)
        if not isinstance(poi, CanonicalPoi):
            poi = CanonicalPoi.model_validate(poi)
        update: dict[str, Any] = {}
        duration = estimate_duration(
            poi.category,
            (duration_config or {}).get("duration_minutes", {}),
            source=str((duration_config or {}).get("version", "journie_category_prior_v1")),
            confidence=float((duration_config or {}).get("confidence", 0.5)),
        )
        if duration and poi.expected_visit_duration_min is None:
            update["expected_visit_duration_min"] = duration.value
            poi_evidence.append(
                FieldEvidence(
                    poi_id=poi.poi_id,
                    field="expected_visit_duration_min",
                    value_raw=duration.value,
                    value_normalized=duration.value,
                    source=duration.source,
                    source_record_id=poi.category or "unknown",
                    method="heuristic",
                    confidence=duration.confidence,
                    selected=True,
                    transformation_version=duration.source,
                )
            )
        cost = estimate_cost(
            poi.category,
            (cost_config or {}).get("estimated_spend", {}),
            source=str((cost_config or {}).get("version", "journie_category_prior_v1")),
            confidence=float((cost_config or {}).get("confidence", 0.4)),
        )
        if cost and poi.cost_type == "unknown":
            update.update(
                cost_min_vnd=cost.min_vnd,
                cost_typical_vnd=cost.typical_vnd,
                cost_max_vnd=cost.max_vnd,
                cost_type=cost.type,
            )
            for field, value in (
                ("cost_min_vnd", cost.min_vnd),
                ("cost_typical_vnd", cost.typical_vnd),
                ("cost_max_vnd", cost.max_vnd),
                ("cost_type", cost.type),
            ):
                poi_evidence.append(
                    FieldEvidence(
                        poi_id=poi.poi_id,
                        field=field,
                        value_raw=value,
                        value_normalized=value,
                        source=cost.source,
                        source_record_id=poi.category or "unknown",
                        method="heuristic",
                        confidence=cost.confidence,
                        selected=True,
                        transformation_version=cost.source,
                    )
                )
        poi = poi.model_copy(update=update)
        poi = poi.model_copy(
            update={
                "rag_eligible": not rag_ineligibility_reasons(poi),
                "csp_eligible": not csp_ineligibility_reasons(poi, poi_evidence),
                "rag_ineligibility_reasons": rag_ineligibility_reasons(poi),
                "csp_ineligibility_reasons": csp_ineligibility_reasons(poi, poi_evidence),
            }
        )
        pois.append(poi)
        evidence.extend(cast(list[FieldEvidence], poi_evidence))
        memberships.extend(cast(tuple[PoiSource, ...], merged.sources))
    return sorted(pois, key=lambda row: row.poi_id), evidence, memberships, review


class DataPipeline:
    def __init__(
        self,
        store: ArtifactStore,
        region: Region,
        run_id: str,
        config: Mapping[str, Any],
        *,
        resume: bool = False,
    ):
        self.store = store
        self.region = region
        self.run_id = run_id
        self.config = dict(config)
        self.logger = RunLogger(run_id)
        self.outputs = PipelineOutputs([], {})
        self.resume = resume

    def _region_geojson(self) -> dict[str, Any]:
        polygon = self.region.polygon_file
        if not polygon.is_absolute():
            polygon = Path.cwd() / polygon
        return json.loads(polygon.read_text(encoding="utf-8"))

    def ingest(self, adapter: SourceAdapter, snapshot_id: str) -> list[StagingRecord]:
        directory = self.store.artifact_dir(
            self.region.id, self.run_id, "staging", adapter.source_name
        )
        existing_path = directory / f"{adapter.source_name}.parquet"
        existing_manifest = directory / "manifest.json"
        if self.resume and existing_path.exists() and existing_manifest.exists():
            manifest = ArtifactManifest.model_validate_json(
                existing_manifest.read_text(encoding="utf-8")
            )
            rows = self.store.read_table(existing_path)
            for row in rows:
                for field in ("raw_tags", "raw_data"):
                    if isinstance(row.get(field), str):
                        row[field] = json.loads(row[field])
            records = [StagingRecord.model_validate(row) for row in rows]
            self.outputs.artifacts.append((existing_path, manifest))
            self.outputs.source_statuses[adapter.source_name] = (
                "empty" if manifest.status == "succeeded_empty" else "success"
            )
            self.logger.event(adapter.source_name, "artifact_resumed", records=len(records))
            return records
        raw_directory = self.store.raw_dir(self.region.id, snapshot_id, adapter.source_name)
        raw_path = raw_directory / "records.jsonl"
        raw_manifest_path = raw_directory / "manifest.json"
        raw_records: list[dict[str, Any]]
        retrieved_at: datetime
        if self.resume and raw_path.exists() and raw_manifest_path.exists():
            raw_manifest = json.loads(raw_manifest_path.read_text(encoding="utf-8"))
            if raw_manifest.get("source") != adapter.source_name:
                raise ValueError(f"raw manifest source does not match {adapter.source_name}")
            if raw_manifest.get("snapshot_id") != snapshot_id:
                raise ValueError(f"raw manifest snapshot does not match {snapshot_id}")
            if raw_manifest.get("content_sha256") != file_hash(raw_path):
                raise ValueError(f"raw snapshot checksum does not match manifest: {raw_path}")
            status = str(raw_manifest.get("status"))
            if status not in {"success", "empty"}:
                raise ValueError(f"raw snapshot has non-reusable status {status!r}: {raw_path}")
            raw_artifact_id = str(raw_manifest["artifact_id"])
            raw_records = read_records(raw_path)
            retrieved_at = datetime.fromisoformat(str(raw_manifest["retrieved_at"]))
            self.outputs.source_statuses[adapter.source_name] = status
            self.logger.event(
                adapter.source_name,
                "raw_resumed",
                snapshot_id=snapshot_id,
                records=len(raw_records),
            )
        else:
            self.logger.event(adapter.source_name, "fetch_started", snapshot_id=snapshot_id)
            result = adapter.fetch(self.region.model_dump(), snapshot_id)
            self.outputs.source_statuses[adapter.source_name] = result.status.value
            self.logger.event(
                adapter.source_name,
                "fetch_finished",
                status=result.status.value,
                records_downloaded=len(result.records),
                http_attempts=result.http_attempts,
                message=result.message,
            )
            if not result.ok:
                return []
            raw_records = result.records
            raw_path = self.store.write_raw_jsonl(
                self.region.id, snapshot_id, adapter.source_name, raw_records
            )
            retrieved_at = datetime.fromtimestamp(raw_path.stat().st_mtime, UTC)
            source_config = (
                self.config.get("sources.yaml", {}).get("sources", {}).get(adapter.source_name, {})
            )
            _, raw_artifact_id = self.store.write_raw_manifest(
                region=self.region.id,
                snapshot_id=snapshot_id,
                source=adapter.source_name,
                raw_path=raw_path,
                metadata={
                    "adapter_version": adapter.adapter_version,
                    "status": result.status.value,
                    "record_count": len(raw_records),
                    "retrieved_at": retrieved_at.isoformat(),
                    "license": source_config.get("license"),
                    "attribution_required": source_config.get("attribution_required"),
                    "redistribution_allowed": source_config.get("redistribution_allowed"),
                    "terms_checked_at": source_config.get("terms_checked_at"),
                },
            )
        source_config = (
            self.config.get("sources.yaml", {}).get("sources", {}).get(adapter.source_name, {})
        )
        selected_raw_records = adapter.select_records(raw_records)
        self.logger.event(
            adapter.source_name,
            "records_selected",
            records_acquired=len(raw_records),
            records_selected=len(selected_raw_records),
        )
        records, rejected = normalize_records(
            adapter,
            selected_raw_records,
            retrieved_at=retrieved_at,
            region_geojson=self._region_geojson(),
        )
        records = [
            record.model_copy(update={"source_snapshot_id": snapshot_id}) for record in records
        ]
        path, manifest = self.store.write_table(
            region=self.region.id,
            run_id=self.run_id,
            stage="staging",
            name=adapter.source_name,
            rows=records,
            schema_version="staging/v1",
            config=self.config,
            source=adapter.source_name,
            source_release=snapshot_id,
            adapter_version=adapter.adapter_version,
            license_name=source_config.get("license"),
            redistribution_allowed=source_config.get("redistribution_allowed"),
            taxonomy_version="journie-taxonomy/v1",
            parents=[raw_artifact_id],
        )
        self.outputs.artifacts.append((path, manifest))
        if rejected:
            reject_path = path.parent / "rejected_records.jsonl"
            reject_path.write_text(
                "".join(
                    json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rejected
                ),
                encoding="utf-8",
            )
        self.logger.event(
            adapter.source_name,
            "normalize_finished",
            records_parsed=len(records),
            records_failed=len(rejected),
            raw_path=str(raw_path),
        )
        return records

    def build(self, staging: list[StagingRecord]) -> PipelineOutputs:
        priorities = self.config["field_priorities.v1.yaml"].get("field_priority", {})
        pois, evidence, memberships, resolution_rows = canonicalize(
            staging,
            priorities,
            self.config["duration.v1.yaml"],
            self.config["cost.v1.yaml"],
        )
        parents = [manifest.artifact_id for _, manifest in self.outputs.artifacts]
        tables: list[tuple[str, str, str, Iterable[Any]]] = [
            ("canonical", "pois", "poi/v1", pois),
            ("canonical", "poi_sources", "poi-source/v1", memberships),
            ("canonical", "poi_field_evidence", "field-evidence/v1", evidence),
            (
                "canonical",
                "identity_history",
                "identity-history/v1",
                [
                    {
                        "poi_id": poi.poi_id,
                        "status": "active",
                        "created_in_run": self.run_id,
                        "redirect_to": None,
                        "split_from": None,
                    }
                    for poi in pois
                ],
            ),
            ("derived", "rag_pois", "poi/v1", build_rag_base(pois)),
            ("derived", "csp_pois", "poi/v1", build_csp_base(pois, evidence)),
            ("reports", "source_quality", "report/v1", benchmark_sources(staging)),
            ("reports", "category_distribution", "report/v1", category_distribution(staging)),
            ("reports", "missing_values", "report/v1", missing_values(staging)),
            ("reports", "unmapped_categories", "report/v1", unmapped_categories(staging)),
            ("reports", "entity_resolution", "report/v1", resolution_rows),
            ("reports", "dedup_report", "report/v1", resolution_rows),
            (
                "reports",
                "opening_hours_parse_failures",
                "report/v1",
                parser_failures(staging, "opening_hours"),
            ),
            (
                "reports",
                "price_parse_failures",
                "report/v1",
                parser_failures(staging, "price"),
            ),
            (
                "reports",
                "csp_eligibility",
                "report/v1",
                [
                    {
                        "poi_id": poi.poi_id,
                        "eligible": poi.csp_eligible,
                        "reasons": poi.csp_ineligibility_reasons,
                    }
                    for poi in pois
                ],
            ),
            (
                "reports",
                "source_status",
                "report/v1",
                [
                    {"source": source, "status": status}
                    for source, status in sorted(self.outputs.source_statuses.items())
                ],
            ),
        ]
        canonical_ids: list[str] = []
        for stage, name, schema, rows in tables:
            artifact_parents = canonical_ids if stage == "derived" else parents
            directory = self.store.artifact_dir(self.region.id, self.run_id, stage, name)
            existing_path = directory / f"{name}.parquet"
            existing_manifest = directory / "manifest.json"
            if self.resume and existing_path.exists() and existing_manifest.exists():
                path = existing_path
                manifest = ArtifactManifest.model_validate_json(
                    existing_manifest.read_text(encoding="utf-8")
                )
            else:
                path, manifest = self.store.write_table(
                    region=self.region.id,
                    run_id=self.run_id,
                    stage=stage,
                    name=name,
                    rows=rows,
                    schema_version=schema,
                    config=self.config,
                    parents=artifact_parents,
                    taxonomy_version=(
                        "journie-taxonomy/v1" if stage in {"canonical", "derived"} else None
                    ),
                    resolver_version=(
                        "entity-resolver/v1" if stage in {"canonical", "derived"} else None
                    ),
                    eligibility_version="eligibility/v1" if stage == "derived" else None,
                )
            self.outputs.artifacts.append((path, manifest))
            if stage == "canonical":
                canonical_ids.append(manifest.artifact_id)
        self.logger.event("pipeline", "build_finished", canonical_pois=len(pois))
        return self.outputs
