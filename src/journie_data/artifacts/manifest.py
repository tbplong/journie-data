from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


class ArtifactManifest(BaseModel):
    manifest_version: str = "v1"
    artifact_id: str
    artifact_type: str
    schema_version: str
    region: str
    run_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: str | None = None
    source_release: str | None = None
    adapter_version: str | None = None
    taxonomy_version: str | None = None
    resolver_version: str | None = None
    eligibility_version: str | None = None
    parent_artifact_ids: list[str] = Field(default_factory=list)
    content_sha256: str
    config_sha256: str
    pipeline_git_commit: str | None = None
    row_count: int
    status: Literal["succeeded", "succeeded_empty"]
    license: str | None = None
    redistribution_allowed: bool | None = None


class DatasetManifest(BaseModel):
    manifest_version: str = "v1"
    release_kind: Literal["dataset", "fixture"] = "dataset"
    dataset_version: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    region: str
    run_id: str
    artifacts: list[ArtifactManifest]
    sources: dict[str, str]
    pipeline_git_commit: str | None = None
