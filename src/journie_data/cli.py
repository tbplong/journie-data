from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer

from journie_data.artifacts.manifest import ArtifactManifest
from journie_data.artifacts.store import ArtifactStore
from journie_data.config import load_pipeline_config, load_region, load_yaml
from journie_data.pipeline import DataPipeline
from journie_data.schemas.registry import SCHEMAS
from journie_data.sources.foursquare import FoursquareAdapter
from journie_data.sources.osm import OSMAdapter
from journie_data.sources.overture import OvertureAdapter
from journie_data.sources.visithcmc import VisitHCMCAdapter

app = typer.Typer(help="Versioned POI ingestion pipeline for Journie.", no_args_is_help=True)
fetch_app = typer.Typer(help="Fetch and normalize one source.", no_args_is_help=True)
crawl_app = typer.Typer(help="Crawl an HTML source.", no_args_is_help=True)
config_app = typer.Typer(help="Validate pipeline configuration.", no_args_is_help=True)
schema_app = typer.Typer(help="Inspect versioned schemas.", no_args_is_help=True)
source_app = typer.Typer(help="Inspect source capabilities.", no_args_is_help=True)
release_app = typer.Typer(help="Create immutable dataset releases.", no_args_is_help=True)
app.add_typer(fetch_app, name="fetch")
app.add_typer(crawl_app, name="crawl")
app.add_typer(config_app, name="config")
app.add_typer(schema_app, name="schema")
app.add_typer(source_app, name="source")
app.add_typer(release_app, name="release")


def _new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _adapter(
    name: str,
    settings: dict[str, Any],
    client: httpx.Client,
    input_path: Path | None = None,
):
    local_path = input_path or settings.get("local_path")
    mapping_config = load_yaml(f"configs/categories/mappings/{name}.v1.yaml")
    category_map = mapping_config.get("mappings") or mapping_config.get("keyword_mappings") or {}
    if name == "overture":
        return OvertureAdapter(local_path=local_path, client=client, category_map=category_map)
    if name == "osm":
        return OSMAdapter(
            local_path=local_path,
            overpass_url=settings.get("overpass_url", "https://overpass-api.de/api/interpreter"),
            client=client,
            category_map=category_map,
        )
    if name == "foursquare":
        return FoursquareAdapter(local_path=local_path, client=client, category_map=category_map)
    if name == "visithcmc":
        return VisitHCMCAdapter(
            local_path=local_path,
            start_urls=settings.get("start_urls", []),
            client=client,
            category_map=category_map,
        )
    raise typer.BadParameter(f"unknown source: {name}")


def _single_source(
    source: str,
    region_id: str,
    snapshot: str,
    run_id: str,
    input_path: Path | None,
    data_root: Path,
) -> None:
    region = load_region(region_id)
    config = load_pipeline_config()
    settings = config["sources.yaml"].get("sources", {}).get(source, {})
    with httpx.Client(timeout=120) as client:
        adapter = _adapter(source, settings, client, input_path)
        pipeline = DataPipeline(ArtifactStore(data_root), region, run_id, config)
        records = pipeline.ingest(adapter, snapshot)
    typer.echo(json.dumps({"source": source, "records": len(records), "run_id": run_id}))


def _fetch_command(source: str):
    def command(
        region: Annotated[str, typer.Option("--region")] = "hcm",
        snapshot: Annotated[str, typer.Option("--snapshot")] = "latest",
        run_id: Annotated[str | None, typer.Option("--run-id")] = None,
        input_path: Annotated[Path | None, typer.Option("--input")] = None,
        data_root: Annotated[Path, typer.Option("--data-root")] = Path("data"),
    ) -> None:
        _single_source(source, region, snapshot, run_id or _new_run_id(), input_path, data_root)

    return command


fetch_app.command("overture")(_fetch_command("overture"))
fetch_app.command("osm")(_fetch_command("osm"))
fetch_app.command("foursquare")(_fetch_command("foursquare"))
crawl_app.command("visithcmc")(_fetch_command("visithcmc"))


@config_app.command("validate")
def validate_config(
    region: Annotated[str, typer.Option("--region")] = "hcm",
) -> None:
    loaded_region = load_region(region)
    config = load_pipeline_config()
    typer.echo(
        json.dumps(
            {"valid": True, "region": loaded_region.id, "config_files": sorted(config)},
            ensure_ascii=False,
        )
    )


@schema_app.command("list")
def list_schemas() -> None:
    for schema in sorted(SCHEMAS):
        typer.echo(schema)


@source_app.command("check")
def check_sources(
    region: Annotated[str, typer.Option("--region")] = "hcm",
) -> None:
    load_region(region)
    settings = load_yaml("configs/sources.yaml").get("sources", {})
    result = []
    for name, source_config in sorted(settings.items()):
        configured = bool(
            source_config.get("enabled")
            and (
                name in {"osm", "overture"}
                or source_config.get("local_path")
                or source_config.get("start_urls")
            )
        )
        result.append(
            {
                "source": name,
                "enabled": bool(source_config.get("enabled")),
                "configured": configured,
                "license": source_config.get("license"),
                "redistribution_allowed": source_config.get("redistribution_allowed"),
            }
        )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command("run")
def run_pipeline(
    region: Annotated[str, typer.Option("--region")] = "hcm",
    snapshot: Annotated[str, typer.Option("--snapshot")] = "latest",
    dataset_version: Annotated[str | None, typer.Option("--dataset-version")] = None,
    run_id: Annotated[str | None, typer.Option("--run-id")] = None,
    fixture: Annotated[bool, typer.Option("--fixture")] = False,
    allow_partial: Annotated[
        bool, typer.Option("--allow-partial/--require-all-sources")
    ] = True,
    resume: Annotated[bool, typer.Option("--resume")] = False,
    data_root: Annotated[Path, typer.Option("--data-root")] = Path("data"),
) -> None:
    actual_run_id = run_id or _new_run_id()
    loaded_region = load_region(region)
    config = load_pipeline_config()
    source_configs = config["sources.yaml"].get("sources", {})
    fixture_paths = {
        name: Path("tests/fixtures") / f"{name}.json"
        for name in ("overture", "osm", "foursquare", "visithcmc")
    }
    selected = (
        list(fixture_paths)
        if fixture
        else [name for name, value in source_configs.items() if value.get("enabled")]
    )
    pipeline = DataPipeline(
        ArtifactStore(data_root), loaded_region, actual_run_id, config, resume=resume
    )
    staging = []
    with httpx.Client(timeout=120) as client:
        for source in selected:
            adapter = _adapter(
                source,
                source_configs.get(source, {}),
                client,
                fixture_paths[source] if fixture else None,
            )
            staging.extend(pipeline.ingest(adapter, snapshot))
    successful = [
        status
        for status in pipeline.outputs.source_statuses.values()
        if status in {"success", "empty"}
    ]
    if not staging:
        typer.echo("No staging records were produced", err=True)
        raise typer.Exit(code=2)
    if not allow_partial and len(successful) != len(selected):
        typer.echo("One or more required sources failed", err=True)
        raise typer.Exit(code=2)
    outputs = pipeline.build(staging)
    release_path = None
    if dataset_version:
        if not fixture:
            if loaded_region.boundary_source.startswith("bootstrap"):
                typer.echo(
                    "Release blocked: replace the bootstrap HCMC boundary with a pinned "
                    "authoritative polygon",
                    err=True,
                )
                raise typer.Exit(code=2)
            blocked = [
                manifest.source
                for _, manifest in outputs.artifacts
                if manifest.artifact_type == "staging"
                and manifest.redistribution_allowed is not True
                and manifest.source is not None
            ]
            if blocked:
                typer.echo(
                    f"Release blocked by unverified redistribution terms: {', '.join(blocked)}",
                    err=True,
                )
                raise typer.Exit(code=2)
        expected_release = data_root / "releases" / f"journie-{region}-v{dataset_version}"
        if resume and expected_release.exists():
            release_path = expected_release
        else:
            release_path = pipeline.store.create_release(
                dataset_version=dataset_version,
                region=region,
                run_id=actual_run_id,
                artifacts=outputs.artifacts,
                sources=outputs.source_statuses,
                release_kind="fixture" if fixture else "dataset",
            )
    typer.echo(
        json.dumps(
            {
                "run_id": actual_run_id,
                "sources": outputs.source_statuses,
                "artifacts": len(outputs.artifacts),
                "release": str(release_path) if release_path else None,
            },
            ensure_ascii=False,
        )
    )


@release_app.command("create")
def create_release(
    version: Annotated[str, typer.Option("--version")],
    run_id: Annotated[str, typer.Option("--run-id")],
    region: Annotated[str, typer.Option("--region")] = "hcm",
    data_root: Annotated[Path, typer.Option("--data-root")] = Path("data"),
) -> None:
    store = ArtifactStore(data_root)
    run_dir = store.run_dir(region, run_id)
    artifacts: list[tuple[Path, ArtifactManifest]] = []
    for manifest_path in run_dir.glob("**/manifest.json"):
        manifest = ArtifactManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        parquet_files = list(manifest_path.parent.glob("*.parquet"))
        if len(parquet_files) != 1:
            raise typer.BadParameter(f"invalid artifact directory: {manifest_path.parent}")
        artifacts.append((parquet_files[0], manifest))
    if not artifacts:
        raise typer.BadParameter(f"no artifacts found for run {run_id}")
    status_path = (
        store.artifact_dir(region, run_id, "reports", "source_status") / "source_status.parquet"
    )
    sources = (
        {row["source"]: row["status"] for row in store.read_table(status_path)}
        if status_path.exists()
        else {}
    )
    release = store.create_release(
        dataset_version=version,
        region=region,
        run_id=run_id,
        artifacts=artifacts,
        sources=sources,
    )
    typer.echo(str(release))


if __name__ == "__main__":
    app()
