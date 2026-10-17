# Journie Data

Versioned POI ingestion pipeline for Ho Chi Minh City. The current implementation covers Milestones 0-4: source ingestion, normalization, entity resolution, field provenance, Visit HCMC enrichment contracts, and CSP/RAG-base datasets. Semantic LLM enrichment and vector indexing are intentionally deferred.

## Setup

```bash
uv sync --extra dev
uv run journie-data --help
uv run journie-data config validate --region hcm
```

## Offline End-to-End Run

```bash
uv run journie-data run \
  --region hcm \
  --snapshot fixture-v1 \
  --run-id fixture-run \
  --fixture \
  --dataset-version 0.1.0 \
  --data-root data
```

The fixture mode exercises all four adapters without network access. Fixture metrics must not be represented as real HCMC coverage.

## Live and Snapshot Inputs

```bash
uv run journie-data fetch overture --region hcm --snapshot <release> --input <places.parquet>
uv run journie-data fetch osm --region hcm --snapshot <timestamp>
uv run journie-data fetch foursquare --region hcm --snapshot <release> --input <places.parquet>
uv run journie-data crawl visithcmc --region hcm --snapshot <date>
```

Overture and Foursquare currently consume pinned local JSON/JSONL/Parquet snapshots. OSM supports Overpass or local snapshots. Visit HCMC supports robots-aware HTTP fetching and local parser fixtures. External access failures are recorded explicitly and never emitted as successful empty datasets.

Before publishing a real release, replace the bootstrap HCMC bounding polygon with a pinned authoritative administrative boundary and confirm every source's current license and redistribution terms.

## Versioning

Raw snapshots and release directories are immutable. Dataset, schema, taxonomy, resolver, eligibility, parser, and source adapter versions are independent. Additive or breaking schema changes must be registered explicitly in `journie_data.schemas.registry`; existing release files are never rewritten.

## Verification

```bash
uv run ruff check .
uv run mypy src/journie_data
uv run pytest
```

Documentation:

- [`docs/data-pipeline-usage.md`](docs/data-pipeline-usage.md): setup, run modes, and maintenance.
- [`docs/dataset-reference.md`](docs/dataset-reference.md): dataset schemas, intended usage, and report review.
