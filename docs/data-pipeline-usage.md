# Journie Data Pipeline: Usage and Maintenance

Use this guide to:

1. Run the complete pipeline offline.
2. Understand how source records become canonical POIs.
3. Run real data without confusing snapshots, boundaries, and releases.
4. Find the correct extension point for a new source, parser, or schema.

> **Current scope:** the pipeline ingests and normalizes POIs, resolves duplicate
> entities, records field-level provenance, and builds RAG-base and CSP-base
> datasets. It does not create LLM enrichment, embeddings, or vector indexes.

For a file-by-file schema and report reference, see
[`dataset-reference.md`](dataset-reference.md).

## 1. Run It in Five Minutes

Run these commands from the repository root:

```bash
uv sync --extra dev
uv run journie-data config validate --region hcm
uv run journie-data run --region hcm --snapshot fixture-v1 --run-id fixture-run-v1 --fixture --data-root data
```

The last command reads all four files in `tests/fixtures/`. It does not access the
network or create a release. Its main outputs are:

```text
data/artifacts/hcm/fixture-run-v1/
|-- staging/       # one normalized table per source
|-- canonical/     # POIs after cross-source entity resolution
|-- derived/       # eligible subsets for RAG and CSP
`-- reports/       # quality reports in Parquet and CSV
```

Inspect the canonical POIs:

```bash
uv run python -c "import polars as pl; print(pl.read_parquet('data/artifacts/hcm/fixture-run-v1/canonical/pois/pois.parquet').select('poi_id', 'name', 'category', 'csp_eligible'))"
```

Use `--resume` to continue the same run. If code, input, or configuration changed,
use a new `run-id` instead.

Fixture data tests the processing flow only. Do not report fixture metrics as real
Ho Chi Minh City coverage.

## 2. What the Pipeline Produces

```text
external source or local file
  -> raw: records acquired by an adapter
  -> staging: records converted to one shared schema
  -> canonical: source records resolved into POIs
  -> derived: POIs filtered for RAG and CSP use
  -> reports: data-quality measurements
  -> release: immutable package created only when requested
```

Core terms:

- **POI (Point of Interest):** a place such as a museum, restaurant, or park.
- **Entity resolution:** deciding whether records from different sources describe
  the same POI, then selecting a representative value for each field.
- **Field evidence:** provenance showing where a canonical value came from and
  whether it was direct, parsed, manually supplied, or estimated by a heuristic.

The pipeline does not invent missing values. Unknown opening hours do not mean
24/7, and an unknown price does not mean free.

## 3. Bounding Box and Boundary Polygon

These are separate concepts:

| Concept | Shape | Current use |
|---|---|---|
| `bbox` | Rectangle defined by `min_lon`, `min_lat`, `max_lon`, and `max_lat` | Limits live OSM and Overture queries |
| Boundary polygon | GeoJSON `Polygon` or `MultiPolygon` | Tests each normalized coordinate and sets `outside_region` |

The current spatial flow is:

1. Live OSM and Overture queries fetch candidates within the `bbox`.
2. Normalization tests each `(longitude, latitude)` point against the polygon.
3. Points outside the polygon remain in staging and canonical data with
   `outside_region=true`.
4. RAG-base and CSP-base exclude POIs carrying that flag.

Local inputs are not pre-filtered by `bbox`. The polygon still marks their records
when valid coordinates are available.

Boundary configuration lives in:

```text
configs/regions/hcm.yaml   # bbox, polygon path, and boundary metadata
configs/geo/hcm.geojson   # GeoJSON geometry
```

### Current Boundary Status

`configs/geo/hcm.geojson` is currently a rectangular polygon generated from the
bbox (`bootstrap_bbox_v1`). It is useful for development but is not an
administrative boundary suitable for a real release.

Before publishing real data:

1. Replace it with an authoritative administrative `Polygon` or `MultiPolygon`.
2. Pin the source version or retrieval date in `boundary_source`.
3. Keep GeoJSON coordinates in WGS84 `[longitude, latitude]` order.
4. Visually inspect concave areas, islands, and polygon holes.

`config validate` currently checks that the file exists and has an accepted
top-level GeoJSON type. It does not validate non-empty geometry, topology, or
administrative accuracy.

## 4. IDs and Versions

### `run-id`

A run ID identifies one transformation run and its directory under
`data/artifacts/<region>/<run-id>/`. If omitted, the CLI creates a UTC ID in
`YYYYMMDDTHHMMSSZ` format.

Use a new run ID after changing code, parsers, schemas, taxonomy, mappings,
resolution logic, or configuration.

### `snapshot`

The current `run` command passes one shared `--snapshot` string to every selected
source. Its actual meaning depends on the adapter:

| Source | Does `--snapshot` select the input? |
|---|---|
| Live Overture | Yes. It selects the remote release; `latest` is rejected. |
| Live OSM | No. Overpass returns current data; the string is stored only as the snapshot ID. |
| Foursquare, Visit HCMC, or local files | No. The configured file or URL selects the input; the string is an output label. |

Therefore, `--snapshot 2026-09-13` does not create a synchronized multi-source
snapshot for that date. For benchmarks and releases, record each source's release
or file checksum and each live query's retrieval time separately.

Do not reuse a snapshot ID for mutable live data. The pipeline fetches first and
then rejects content that conflicts with an existing raw snapshot under that ID.

### `dataset-version`

`--dataset-version` creates
`data/releases/journie-<region>-v<version>/`. Dataset versions are independent of
schema, taxonomy, adapter, and resolver versions. Assign one only after reviewing
quality reports and redistribution rights.

### `artifact-id`

An artifact ID is derived from the stage, table name, schema label, and Parquet
checksum. Its upstream relationships are stored in `parent_artifact_ids`.

## 5. Choose a Run Mode

### Offline Fixture Run

```bash
uv run journie-data run \
  --region hcm \
  --snapshot fixture-v2 \
  --run-id fixture-run-v2 \
  --fixture \
  --data-root data
```

Add `--dataset-version 0.0.0-fixture.2` to test release packaging. The release will
have `release_kind: fixture`.

### Enabled Sources

```bash
uv run journie-data source check --region hcm
uv run journie-data run --region hcm --snapshot <shared-label> --run-id <new-run-id> --data-root data
```

`run` selects only sources with `enabled: true` in `configs/sources.yaml`. The
defaults are Overture and OSM. The default configuration cannot publish a real
release because Overture redistribution is unverified and the boundary is still a
bootstrap polygon.

Partial success is allowed by default. Require every selected source to fetch
successfully or return a valid empty result with:

```bash
uv run journie-data run --region hcm --snapshot <shared-label> --run-id <new-run-id> --require-all-sources
```

This gate checks fetch status, not whether records survive normalization. The
command still exits with code 2 if all sources produce no staging records.

### One Source

```bash
uv run journie-data fetch overture --region hcm --snapshot <release> --run-id <run-id> --input <places.parquet>
uv run journie-data fetch osm --region hcm --snapshot <label> --run-id <run-id> --input <overpass.json>
uv run journie-data fetch foursquare --region hcm --snapshot <label> --run-id <run-id> --input <places.parquet>
uv run journie-data crawl visithcmc --region hcm --snapshot <label> --run-id <run-id> --input <records.json>
```

Supported local formats are `.json`, `.jsonl`, `.ndjson`, `.parquet`, and `.pq`.

Single-source commands create raw and staging outputs only. Use `journie-data run`
to create canonical, derived, and report artifacts. Because `run` has no per-source
`--input` option, set each source's `local_path` in `configs/sources.yaml` for a
multi-source local build.

### Adapter Capabilities

| Source | Local | Live or remote |
|---|---|---|
| Overture | Yes | Queries a pinned S3 release through DuckDB, limited by bbox |
| OSM | Yes | Queries current Overpass data, limited by bbox |
| Foursquare | Yes | Not implemented; returns `deferred` without a local input |
| Visit HCMC | Yes | Fetches configured `start_urls` with robots checks; does not crawl discovered links |

## 6. Resume a Run

```bash
uv run journie-data run --region hcm --snapshot <same-label> --run-id <same-run-id> --resume
```

Use resume only to continue the same interrupted run:

| Situation | Action |
|---|---|
| Interrupted run with unchanged input, code, and config | Reuse the IDs with `--resume` |
| Changed input or snapshot | Use a new run ID |
| Changed parser, mapping, taxonomy, schema, or config | Use a new run ID |
| Comparing two transformations of the same raw data | Use two different run IDs |

Resume currently reuses an artifact when its Parquet file and manifest exist. It
does not compare the requested snapshot, current config hash, schema, or parent
artifacts. If staging is missing but a matching raw file and manifest exist, resume
validates the raw checksum and rebuilds staging without fetching the source.

## 7. Output Layout

```text
data/
|-- raw/<region>/<snapshot-id>/<source>/
|   |-- records.jsonl
|   `-- manifest.json
|-- artifacts/<region>/<run-id>/
|   |-- staging/<source>/
|   |-- canonical/<dataset>/
|   |-- derived/<dataset>/
|   `-- reports/<report>/
`-- releases/journie-<region>-v<dataset-version>/
    |-- staging/*.parquet
    |-- canonical/*.parquet
    |-- derived/*.parquet
    |-- reports/*.{parquet,csv}
    `-- manifest.json
```

Do not edit `data/raw/` or `data/releases/` directly. Raw snapshots and releases
are immutable; new input requires a new snapshot ID or dataset version.

### Raw

`records.jsonl` contains records acquired by the adapter. It can be inspected or
passed back to an adapter through `--input`, but it is not always the byte-for-byte
source response. OSM stores only `elements`; remote Overture removes geometry and
adds coordinates calculated from each feature bbox.

The raw manifest records its checksum, record count, adapter version, retrieval
time, license, and redistribution status at run time.

### Staging

Each source gets a `staging/v1` table with source identity, names, coordinates,
categories, contact details, hours, prices, and retained source fields in
`raw_tags` or `raw_data`.

Overture and OSM apply their configured category mappings as a domain-selection
step before normalization. Their raw snapshots retain all acquired records, while
staging contains only records matching at least one mapped travel category.

Records rejected by a source filter or schema validation are written to
`staging/<source>/rejected_records.jsonl` and do not enter canonical data.

### Canonical

| Dataset | Question it answers |
|---|---|
| `pois` | What is the resolved POI and its representative data? |
| `poi_sources` | Which source records were merged into this POI? |
| `poi_field_evidence` | Where did each field value come from? |
| `identity_history` | What is the canonical ID status in this run? |

`pois.poi_id` remains stable while its source-record cluster remains unchanged.
Changing clustering logic can change the ID. `identity_history` currently creates
only `active` rows; cross-release merge, split, and redirect workflows are not yet
fully implemented.

### Derived

- `rag_pois` requires a name, valid coordinates, a Journie category, and an
  in-region result.
- `csp_pois` additionally requires a positive visit duration, parsed opening hours,
  and cost data with provenance.

These are canonical subsets, not a complete vector index or solver output.
Eligibility rules are currently implemented directly in code;
`configs/eligibility.v1.yaml` is loaded but does not control them yet.

### Reports

Use `reports/<name>/<name>.csv` for manual review and Parquet for code. Review in
this order:

1. `source_status`: determine which sources actually succeeded or failed.
2. `source_quality` and `missing_values`: check row counts and field coverage.
3. `unmapped_categories` and rejected records: find adapter or mapping gaps.
4. `entity_resolution`: inspect likely false merges and false splits.
5. Parser-failure and `csp_eligibility` reports: explain CSP exclusions.

`source_status` includes only sources selected for the run. A `failed`, `blocked`,
`deferred`, or `not_configured` source has no raw or staging artifact.

## 8. Source Statuses

| Status | Meaning |
|---|---|
| `success` | Fetch returned records; normalization may still reject some or all of them |
| `empty` | Fetch completed successfully with no records |
| `not_configured` | A required client, URL, local file, or release ID is missing |
| `unsupported` | The adapter does not support the requested access mode |
| `deferred` | The source was postponed, such as Foursquare without a local snapshot |
| `blocked` | Robots rules or another policy prevented access |
| `failed` | An HTTP, file, response-parsing, or runtime error occurred |

Do not interpret zero rows as “the source has no POIs” until you inspect its status
and rejected records.

## 9. Create a Release

Use the run command to apply the available automated gates:

```bash
uv run journie-data run \
  --region hcm \
  --snapshot <shared-label> \
  --run-id <reviewed-run-id> \
  --require-all-sources \
  --dataset-version <version>
```

For a real run, the CLI automatically blocks only these conditions:

1. `boundary_source` starts with `bootstrap`.
2. A staging artifact does not have `redistribution_allowed: true`.

You must still verify polygon accuracy, source completeness, quality reports,
current licenses, the Git commit, and version uniqueness. Omitting
`--require-all-sources` can still create a partial release.

Package an existing run with:

```bash
uv run journie-data release create --version <version> --run-id <run-id> --region hcm
```

This standalone command does **not** run the boundary or license gates. Use it only
after manual review. A release flattens files by stage; per-artifact manifests are
embedded in the release-level `manifest.json` instead of copied as separate files.

## 10. Manifests and Reproducibility

Each run artifact has a `manifest.json` containing:

| Field | Meaning |
|---|---|
| `artifact_id`, `content_sha256` | Content identity and Parquet checksum |
| `schema_version` | Artifact schema label |
| `run_id`, `region` | Producing run and region |
| `source`, `source_release` | Source metadata for staging artifacts |
| `parent_artifact_ids` | Upstream artifact IDs |
| `config_sha256` | Hash of the loaded pipeline-config subset |
| `pipeline_git_commit` | Current commit, or `null` outside a Git commit |
| `row_count`, `status` | Row count and `succeeded` or `succeeded_empty` |

Important reproducibility limits:

- `config_sha256` covers sources, field priorities, duration, cost, and eligibility.
  It does not cover region YAML, polygon bytes, taxonomy, or category mappings.
- `schema list` registers only `staging/v1`, `poi/v1`, `poi-source/v1`, and
  `field-evidence/v1`. Some report and identity tables have only manifest labels.
- Live OSM is not historical, and raw records are not always original responses.

For a benchmark, preserve the release manifest, Git commit, complete configuration,
boundary polygon, and source input metadata or checksums.

## 11. Maintain and Extend the Pipeline

### Add a Source

1. Implement `SourceAdapter` under `src/journie_data/sources/`.
2. Declare access and license metadata in `configs/sources.yaml`, then register the
   adapter in the CLI.
3. Add a versioned category mapping under `configs/categories/mappings/`.
4. Map records to `StagingRecord`; retain unmapped source fields in `raw_data` or
   `raw_tags`.
5. Add a fixture and contract tests, then run the end-to-end pipeline with a new ID.

### Change a Parser, Taxonomy, or Resolver

Do not modify the raw snapshot. Increase the relevant version when semantics
change, process the old raw input under a new run ID, and compare reports between
the two runs.

### Add a Schema Version

1. Add a new model instead of changing the meaning of an old release.
2. Register the schema and explicit migration in `journie_data.schemas.registry`.
3. Update producers, consumers, manifest versions, and tests together.
4. Rebuild from raw data with a new run ID and dataset version.

## 12. Troubleshooting

| Error or symptom | Cause and action |
|---|---|
| `immutable artifact already exists` | The run ID already has output; resume the same run or use a new ID after changes |
| `immutable raw snapshot has conflicting content` | The snapshot ID refers to different content; use a new snapshot ID |
| Overture rejects `latest` | Supply a pinned release ID or a local file through `--input` |
| Foursquare is `deferred` | Configure `local_path` or use the single-source command with `--input` |
| Visit HCMC is `blocked` | Do not bypass robots or CAPTCHA; use an allowed snapshot and preserve the status |
| CSP contains few rows | Inspect `csp_eligibility`, opening-hours failures, and price failures before changing rules |

## 13. Verify Changes Before Merge

```bash
uv run ruff check .
uv run mypy src/journie_data
uv run pytest
uv run journie-data run --region hcm --snapshot fixture-check-v1 --run-id fixture-check-v1 --fixture --data-root data
```

Change the final command's IDs if they already exist. Use `--resume` only when
continuing that exact verification run.
