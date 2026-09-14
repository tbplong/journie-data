from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

import polars as pl

from .manifest import ArtifactManifest, DatasetManifest


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def git_commit() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


class ArtifactStore:
    def __init__(self, root: Path | str = "data") -> None:
        self.root = Path(root)

    def raw_dir(self, region: str, snapshot_id: str, source: str) -> Path:
        return self.root / "raw" / region / snapshot_id / source

    def run_dir(self, region: str, run_id: str) -> Path:
        return self.root / "artifacts" / region / run_id

    def artifact_dir(self, region: str, run_id: str, stage: str, name: str) -> Path:
        return self.run_dir(region, run_id) / stage / name

    def write_raw_jsonl(
        self, region: str, snapshot_id: str, source: str, records: Iterable[Mapping[str, Any]]
    ) -> Path:
        directory = self.raw_dir(region, snapshot_id, source)
        path = directory / "records.jsonl"
        content = "".join(
            json.dumps(dict(record), ensure_ascii=False, sort_keys=True, default=str) + "\n"
            for record in records
        )
        if path.exists():
            if path.read_text(encoding="utf-8") == content:
                return path
            raise FileExistsError(f"immutable raw snapshot has conflicting content: {path}")
        directory.mkdir(parents=True, exist_ok=True)
        self._atomic_text(path, content)
        return path

    def write_raw_manifest(
        self,
        *,
        region: str,
        snapshot_id: str,
        source: str,
        raw_path: Path,
        metadata: Mapping[str, Any],
    ) -> tuple[Path, str]:
        path = self.raw_dir(region, snapshot_id, source) / "manifest.json"
        checksum = file_hash(raw_path)
        artifact_id = stable_hash(
            {
                "artifact_type": "raw",
                "region": region,
                "snapshot_id": snapshot_id,
                "source": source,
                "content_sha256": checksum,
            }
        )[:24]
        value = {
            "manifest_version": "v1",
            "artifact_id": artifact_id,
            "artifact_type": "raw",
            "region": region,
            "snapshot_id": snapshot_id,
            "source": source,
            "content_sha256": checksum,
            **metadata,
        }
        content = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str)
        if path.exists():
            if path.read_text(encoding="utf-8") == content:
                return path, artifact_id
            raise FileExistsError(f"immutable raw manifest has conflicting content: {path}")
        self._atomic_text(path, content)
        return path, artifact_id

    def write_table(
        self,
        *,
        region: str,
        run_id: str,
        stage: str,
        name: str,
        rows: Iterable[BaseException | Mapping[str, Any] | Any],
        schema_version: str,
        config: Mapping[str, Any],
        source: str | None = None,
        source_release: str | None = None,
        adapter_version: str | None = None,
        license_name: str | None = None,
        redistribution_allowed: bool | None = None,
        taxonomy_version: str | None = None,
        resolver_version: str | None = None,
        eligibility_version: str | None = None,
        parents: Iterable[str] = (),
    ) -> tuple[Path, ArtifactManifest]:
        values = [self._row(item) for item in rows]
        directory = self.artifact_dir(region, run_id, stage, name)
        path = directory / f"{name}.parquet"
        manifest_path = directory / "manifest.json"
        if path.exists() or manifest_path.exists():
            raise FileExistsError(f"immutable artifact already exists: {directory}")
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False, dir=directory) as handle:
            temporary = Path(handle.name)
        try:
            pl.DataFrame(values, infer_schema_length=None).write_parquet(temporary)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        checksum = file_hash(path)
        artifact_id = stable_hash(
            {"type": stage, "name": name, "schema": schema_version, "sha256": checksum}
        )[:24]
        manifest = ArtifactManifest(
            artifact_id=artifact_id,
            artifact_type=stage,
            schema_version=schema_version,
            region=region,
            run_id=run_id,
            source=source,
            source_release=source_release,
            adapter_version=adapter_version,
            taxonomy_version=taxonomy_version,
            resolver_version=resolver_version,
            eligibility_version=eligibility_version,
            parent_artifact_ids=list(parents),
            content_sha256=checksum,
            config_sha256=stable_hash(config),
            pipeline_git_commit=git_commit(),
            row_count=len(values),
            status="succeeded" if values else "succeeded_empty",
            license=license_name,
            redistribution_allowed=redistribution_allowed,
        )
        self._atomic_text(manifest_path, manifest.model_dump_json(indent=2))
        if stage == "reports":
            csv_path = directory / f"{name}.csv"
            csv_values = [
                {
                    key: json.dumps(value, ensure_ascii=False, sort_keys=True)
                    if isinstance(value, (dict, list))
                    else value
                    for key, value in row.items()
                }
                for row in values
            ]
            with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, dir=directory) as handle:
                temporary_csv = Path(handle.name)
            try:
                pl.DataFrame(csv_values).write_csv(temporary_csv)
                os.replace(temporary_csv, csv_path)
            finally:
                temporary_csv.unlink(missing_ok=True)
        return path, manifest

    def read_table(self, path: Path | str) -> list[dict[str, Any]]:
        return pl.read_parquet(path).to_dicts()

    def create_release(
        self,
        *,
        dataset_version: str,
        region: str,
        run_id: str,
        artifacts: Iterable[tuple[Path, ArtifactManifest]],
        sources: Mapping[str, str],
        release_kind: Literal["dataset", "fixture"] = "dataset",
    ) -> Path:
        release = self.root / "releases" / f"journie-{region}-v{dataset_version}"
        if release.exists():
            raise FileExistsError(f"release already exists: {release}")
        temporary = release.with_name(f".{release.name}.tmp")
        temporary.mkdir(parents=True)
        artifact_manifests: list[ArtifactManifest] = []
        try:
            for source_path, manifest in artifacts:
                target = temporary / manifest.artifact_type / source_path.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, target)
                csv_source = source_path.with_suffix(".csv")
                if csv_source.exists():
                    shutil.copy2(csv_source, target.with_suffix(".csv"))
                artifact_manifests.append(manifest)
            dataset_manifest = DatasetManifest(
                release_kind=release_kind,
                dataset_version=dataset_version,
                region=region,
                run_id=run_id,
                artifacts=artifact_manifests,
                sources=dict(sources),
                pipeline_git_commit=git_commit(),
            )
            self._atomic_text(
                temporary / "manifest.json", dataset_manifest.model_dump_json(indent=2)
            )
            os.replace(temporary, release)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return release

    @staticmethod
    def _row(item: Any) -> dict[str, Any]:
        if hasattr(item, "model_dump"):
            row = item.model_dump(mode="json")
        elif isinstance(item, Mapping):
            row = dict(item)
        else:
            raise TypeError(f"unsupported table row: {type(item).__name__}")
        # Source-native maps are intentionally lossless JSON blobs, not unstable Parquet structs.
        for field in ("raw_tags", "raw_data"):
            if field in row:
                row[field] = json.dumps(row[field], ensure_ascii=False, sort_keys=True, default=str)
        return row

    @staticmethod
    def _atomic_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
