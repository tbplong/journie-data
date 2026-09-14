# Journie Dataset Reference

This document describes every dataset emitted by `journie-data run`, its logical
schema, and its intended use. It also explains how to inspect the quality reports.

For commands, run modes, boundaries, snapshots, and releases, see
[`data-pipeline-usage.md`](data-pipeline-usage.md).

## 1. Start Here

Set the run directory once in your code:

```python
from pathlib import Path

RUN_ID = "fixture-run-v1"
RUN_ROOT = Path("data/artifacts/hcm") / RUN_ID
```

Choose the dataset based on the task:

| Task | Start with |
|---|---|
| Search, recommendations, or RAG indexing | `derived/rag_pois/rag_pois.parquet` |
| Itinerary optimization or CSP | `derived/csp_pois/csp_pois.parquet` |
| POI API, catalog, or map | `canonical/pois/pois.parquet` |
| Explain a supported resolved field's origin | `canonical/poi_field_evidence/poi_field_evidence.parquet` |
| Debug source ingestion | `staging/<source>/<source>.parquet` |
| Approve or reject a run | `reports/`, starting with `source_status` |

Read a table with Polars:

```python
import polars as pl

pois = pl.read_parquet(RUN_ROOT / "canonical/pois/pois.parquet")
print(pois.schema)
print(pois.head())
```

Read a table with DuckDB:

```sql
SELECT category, count(*) AS poi_count
FROM read_parquet('data/artifacts/hcm/<run-id>/canonical/pois/pois.parquet')
GROUP BY category
ORDER BY poi_count DESC;
```

Parquet is the machine-readable source of truth. Report CSV files exist for manual
inspection only.

## 2. Layout and File Conventions

```text
data/
|-- raw/<region>/<snapshot-id>/<source>/
|   |-- records.jsonl
|   `-- manifest.json
`-- artifacts/<region>/<run-id>/
    |-- staging/<source>/
    |   |-- <source>.parquet
    |   |-- manifest.json
    |   `-- rejected_records.jsonl       # optional
    |-- canonical/<dataset>/
    |   |-- <dataset>.parquet
    |   `-- manifest.json
    |-- derived/<dataset>/
    |   |-- <dataset>.parquet
    |   `-- manifest.json
    `-- reports/<report>/
        |-- <report>.parquet
        |-- <report>.csv
        `-- manifest.json
```

Every artifact directory has one Parquet file and one manifest. Reports also have
a CSV copy. Only staging can have `rejected_records.jsonl`.

### Logical Types vs. Physical Parquet Types

The schemas below are logical application contracts from the Pydantic models.
Current writes use Polars schema inference rather than an explicit Arrow schema.
Consequently:

- A nullable field containing only nulls in one run can have physical type `Null`.
- Model datetimes are currently serialized into Parquet as ISO 8601 strings.
- An empty report can be a valid zero-row, zero-column Parquet file.
- List and struct element types can vary when every collection is empty.

Consumers should select columns defensively and use manifest `schema_version` as a
contract label, not as proof that every physical Arrow type is fixed. The registered
schemas are `staging/v1`, `poi/v1`, `poi-source/v1`, and `field-evidence/v1`.
`identity-history/v1` and `report/v1` are currently manifest labels only.

### Type Notation

| Notation | Meaning |
|---|---|
| `string?` | Nullable string |
| `int?`, `float?`, `bool?` | Nullable scalar |
| `datetime?` | Nullable logical datetime; currently stored as an ISO string |
| `list<T>` | List of values of type `T` |
| `map` | JSON-like object; staging raw maps are stored as JSON strings |

## 3. Raw Acquisition Records

### `raw/<region>/<snapshot-id>/<source>/records.jsonl`

One JSON object per line containing the records returned by the source adapter.
Use it to inspect acquisition behavior or reprocess source records through a
single-source command with `--input`.

This is not guaranteed to be the byte-for-byte upstream response. For example,
OSM stores the response's `elements`, while remote Overture removes geometry and
adds centroid coordinates derived from each feature bbox.

### Raw `manifest.json`

| Field | Type | Meaning |
|---|---|---|
| `manifest_version` | string | Raw manifest format, currently `v1` |
| `artifact_id` | string | Content-derived raw artifact ID |
| `artifact_type` | string | Always `raw` |
| `region` | string | Region ID |
| `snapshot_id` | string | Adapter-specific release ID or caller-supplied label |
| `source` | string | Source adapter name |
| `content_sha256` | string | SHA-256 of `records.jsonl` |
| `adapter_version` | string | Adapter implementation version |
| `status` | string | Acquisition status, normally `success` or `empty` here |
| `record_count` | int | Number of acquired records |
| `retrieved_at` | datetime | Time recorded for acquisition |
| `license` | string? | Configured license statement |
| `attribution_required` | bool? | Configured attribution requirement |
| `redistribution_allowed` | bool? | Configured redistribution decision |
| `terms_checked_at` | string? | Date on which terms were last reviewed |

## 4. Staging Datasets

Staging provides one normalized row per accepted source record. All adapters use
the same logical `staging/v1` schema.

Overture and OSM first select records matching at least one configured category
mapping. Their raw snapshots retain every acquired record, but their staging tables
contain only the mapped travel-domain subset. The run log records both
`records_acquired` and `records_selected`.

### Staging Files

| File | Source-specific role |
|---|---|
| `staging/overture/overture.parquet` | Overture places normalized from a local export or pinned remote release |
| `staging/osm/osm.parquet` | OSM nodes, ways, and relations returned by a local file or Overpass |
| `staging/foursquare/foursquare.parquet` | Foursquare places from a local JSON/JSONL/Parquet export |
| `staging/visithcmc/visithcmc.parquet` | Parsed Visit HCMC records from local data or configured page URLs |

Only selected sources appear in a run. Fixture mode selects all four sources.

### `staging/v1` Schema

**Version and identity**

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | string | Always `staging/v1` |
| `taxonomy_version` | string | Currently `journie-taxonomy/v1` |
| `adapter_version` | string | Adapter version that normalized the row |
| `source` | string | `overture`, `osm`, `foursquare`, or `visithcmc` |
| `source_record_id` | string | Stable identifier within the source |
| `source_snapshot_id` | string? | Snapshot ID or label supplied to the pipeline |
| `source_url` | string? | Upstream page or feature URL |
| `retrieved_at` | datetime | Time at which the record was normalized |
| `source_updated_at` | datetime/string? | Upstream update timestamp when supplied |
| `first_seen_at` | datetime? | First observed time; not populated by current adapters |
| `last_seen_at` | datetime? | Last observed time; not populated by current adapters |

**Names and location**

| Field | Type | Meaning |
|---|---|---|
| `name` | string | Required source display name |
| `name_en` | string? | English name when supplied separately |
| `normalized_name` | string? | Matching key that preserves Unicode |
| `normalized_name_ascii` | string? | ASCII-folded matching key |
| `latitude` | float? | WGS84 latitude |
| `longitude` | float? | WGS84 longitude |
| `outside_region` | bool | Whether the point is outside the configured polygon |
| `address` | string? | Source address text |
| `district` | string? | District or neighborhood |
| `city` | string? | City or locality |

**Contact and classification**

| Field | Type | Meaning |
|---|---|---|
| `website` | string? | Source website value |
| `website_domain` | string? | Normalized website domain |
| `phone` | string? | Source phone value |
| `normalized_phone` | string? | Normalized phone value |
| `category` | string? | Journie taxonomy category |
| `source_categories` | list<string> | Original provider category labels |

**Hours, cost, and visit data**

| Field | Type | Meaning |
|---|---|---|
| `opening_hours_raw` | string? | Unmodified source hours expression |
| `opening_hours_normalized` | weekly-hours? | Parsed weekly schedule |
| `price_raw` | string? | Unmodified source price expression |
| `cost_min_vnd` | int? | Parsed minimum cost in VND |
| `cost_typical_vnd` | int? | Parsed typical cost in VND |
| `cost_max_vnd` | int? | Parsed maximum cost in VND |
| `cost_type` | enum | `ticket`, `expected_spend`, `free`, or `unknown` |
| `expected_visit_duration_min` | int? | Source duration; current adapters normally leave it null |

**Content and provenance**

| Field | Type | Meaning |
|---|---|---|
| `description` | string? | Source description |
| `operating_status` | string? | Source operating or closure status |
| `wikidata` | string? | Wikidata identifier |
| `wikipedia` | string? | Wikipedia identifier or tag |
| `content_hash` | string? | Source content checksum, used by Visit HCMC |
| `confidence` | float | Source-record confidence, default `0.8` |
| `method` | enum | `direct`, `parsed`, `heuristic`, `llm_generated`, or `manual` |
| `raw_tags` | JSON string | Preserved source-native tags, primarily OSM |
| `raw_data` | JSON string | Preserved adapter acquisition record |

The logical model allows a string-keyed map of interval lists. Output produced by
the current opening-hours parser uses a struct with `mon` through `sun`, where each
day contains zero or more `[start, end]` 24-hour time pairs:

```json
{
  "mon": [["09:00", "17:00"]],
  "tue": [["09:00", "17:00"]],
  "wed": [],
  "thu": [],
  "fri": [],
  "sat": [],
  "sun": []
}
```

### Staging Usage

Use staging to:

- Debug source adapters, category mappings, and parsers.
- Compare provider coverage before entity resolution.
- Rebuild canonical data after resolver changes.
- Inspect source-native data retained in `raw_data` and `raw_tags`.

Do not use staging directly as a public POI catalog because it contains duplicate
entities across sources.

### `rejected_records.jsonl`

This optional file contains one object per rejected acquisition record:

| Field | Type | Meaning |
|---|---|---|
| `index` | int | Position in the acquired record list |
| `reason` | string | `source_filter` or validation/normalization error |
| `raw` | object | Rejected acquisition record |

Review it when selected records do not survive normalization. A raw-to-staging
count difference can also come from Overture/OSM domain selection and does not imply
rejection. This file has no separate manifest and is not copied into releases.

## 5. Canonical Datasets

Canonical tables describe resolved POIs and their lineage. Every canonical table
has all staging artifact IDs as manifest parents.

### `canonical/pois/pois.parquet`

One row per resolved POI. This is the main general-purpose catalog.

**Contract and identity**

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | string | Always `poi/v1` |
| `taxonomy_version` | string | Journie taxonomy version |
| `resolver_version` | string | Entity resolver version |
| `eligibility_version` | string | Eligibility rule label |
| `poi_id` | string | UUIDv5 derived from sorted source identities in the cluster |
| `source_count` | int | Number of distinct source names in the cluster |
| `first_seen_at` | datetime? | Reserved first-observation time; currently normally null |
| `last_seen_at` | datetime? | Reserved latest-observation time; currently normally null |

**Resolved POI values**

| Field | Type | Meaning |
|---|---|---|
| `name` | string | Selected display name |
| `normalized_name` | string | Unicode matching key; do not display it |
| `normalized_name_ascii` | string? | ASCII matching key; null when the name has no ASCII equivalent |
| `category` | string? | Selected Journie category |
| `latitude` | float? | Selected WGS84 latitude |
| `longitude` | float? | Selected WGS84 longitude |
| `outside_region` | bool | Selected boundary flag |
| `address` | string? | Selected address |
| `district` | string? | Selected district |
| `city` | string? | Selected city |
| `website` | string? | Selected website |
| `normalized_website` | string? | Normalized website domain |
| `phone` | string? | Selected phone number |
| `normalized_phone` | string? | Normalized phone number |
| `opening_hours_raw` | string? | Selected source hours expression |
| `opening_hours_normalized` | weekly-hours? | Selected parsed weekly schedule |
| `cost_min_vnd` | int? | Minimum direct, parsed, or estimated cost |
| `cost_typical_vnd` | int? | Typical direct, parsed, or estimated cost |
| `cost_max_vnd` | int? | Maximum direct, parsed, or estimated cost |
| `cost_type` | enum | `ticket`, `expected_spend`, `free`, or `unknown` |
| `expected_visit_duration_min` | int? | Direct or category-estimated duration |
| `description` | string? | Selected description |
| `operating_status` | string? | Selected operating status |
| `semantic_tags` | list<string> | Reserved for semantic enrichment; currently empty |

**Eligibility**

| Field | Type | Meaning |
|---|---|---|
| `rag_eligible` | bool | Whether the POI appears in `rag_pois` |
| `csp_eligible` | bool | Whether the POI appears in `csp_pois` |
| `rag_ineligibility_reasons` | list<string> | Failed RAG requirements |
| `csp_ineligibility_reasons` | list<string> | Failed CSP requirements |

Use `pois` for POI APIs, maps, analytics, manual review, or use cases that need all
resolved records rather than only an eligible subset.

### `canonical/poi_sources/poi_sources.parquet`

One row per canonical-to-source membership. A POI with records from three sources
has three rows.

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | string | Always `poi-source/v1` |
| `poi_id` | string | Canonical POI foreign key |
| `source` | string | Source name |
| `source_record_id` | string | Source-native stable ID |
| `source_url` | string? | Source page or feature URL |
| `retrieved_at` | datetime? | Source retrieval time |
| `first_seen_at` | datetime? | First observation when available |
| `last_seen_at` | datetime? | Latest observation when available |

Use it to trace a POI back to providers, display attribution links, count source
support, or join canonical records back to staging.

### `canonical/poi_field_evidence/poi_field_evidence.parquet`

One row per available supported field value per source record, plus rows for
generated cost and duration heuristics. Several evidence rows can exist for one POI
and field.

Evidence is currently emitted for names, category, address fields, contact fields,
coordinates, hours, costs, duration, description, operating status,
`outside_region`, and the staging-only `price_raw` value. It is not emitted for
computed fields such as normalized matching keys, semantic tags, `source_count`,
observation timestamps, or eligibility fields.

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | string | Always `field-evidence/v1` |
| `poi_id` | string | Canonical POI foreign key |
| `field` | string | Supported canonical field, or staging `price_raw` |
| `value_raw` | scalar/string? | Source or heuristic value; structured values are JSON strings |
| `value_normalized` | scalar/string? | Canonical value when this evidence was selected |
| `source` | string | Provider or heuristic configuration version |
| `source_record_id` | string | Source record ID or heuristic category key |
| `source_url` | string? | Upstream URL |
| `retrieved_at` | datetime? | Retrieval time |
| `effective_from` | datetime? | Evidence validity start, currently normally null |
| `effective_to` | datetime? | Evidence validity end, currently normally null |
| `method` | enum | `direct`, `parsed`, `heuristic`, `manual`, or `llm_generated` |
| `confidence` | float | Confidence from 0 to 1 |
| `selected` | bool | Whether field resolution selected this evidence |
| `transformation_version` | string? | Parser or heuristic version |

Use it to explain selected values, audit heuristic cost/duration, compare conflicting
sources, and implement confidence filters. No current pipeline step creates
`llm_generated` evidence.

### `canonical/identity_history/identity_history.parquet`

One row per canonical POI. This is an extension point for ID lifecycle tracking.

| Field | Type | Meaning |
|---|---|---|
| `poi_id` | string | Canonical POI ID |
| `status` | string | Currently always `active` |
| `created_in_run` | string | Run ID that emitted this row |
| `redirect_to` | string? | Future merge redirect target; currently null |
| `split_from` | string? | Future split parent; currently null |

Do not treat it as a complete cross-release identity ledger yet. Merge, split, and
redirect workflows are not implemented.

## 6. Derived Datasets

Both derived datasets use the complete `poi/v1` schema described under
`canonical/pois`. They contain filtered copies, not new document or solver schemas.

### `derived/rag_pois/rag_pois.parquet`

Contains canonical POIs for which all of these are true:

- `name` is non-blank.
- `latitude` and `longitude` are valid WGS84 coordinates.
- `category` is non-blank.
- `outside_region` is false.

Use it as the input to a separate document-building, embedding, and indexing step.
This repository does not create embeddings or a vector database. Keep `poi_id` as
the document ID so retrieval results can join back to canonical and CSP data.

Suggested RAG document fields are `name`, `category`, `description`, `address`,
`district`, `opening_hours_raw`, and cost fields. Do not expose normalized matching
keys or raw provider blobs as user-facing text.

### `derived/csp_pois/csp_pois.parquet`

Contains canonical POIs satisfying the RAG-style identity requirements plus:

- `expected_visit_duration_min` is a positive integer.
- `opening_hours_normalized` has all seven days and at least one valid interval.
- Cost type is `free`, `ticket`, or `expected_spend`.
- At least one cost value is present with direct, parsed, heuristic, or manual evidence.
- `outside_region` is false.

Use it as candidate input to a separate itinerary solver. The table supplies POI
duration, availability, cost, and coordinates, but it does not supply:

- User time windows, budget, preferences, or start/end locations.
- Travel times or distances between POIs.
- Decision variables, constraints, or an optimization objective.
- A routing engine or CSP/CP-SAT implementation.

A typical application retrieves relevant POIs from `rag_pois`, intersects their
IDs with `csp_pois`, requests a travel-time matrix, and then runs a solver.

## 7. Report Files and Schemas

Every report directory contains equivalent `.parquet` and `.csv` data plus an
artifact manifest. `entity_resolution` and `dedup_report` currently contain the
same rows.

### `reports/source_status/source_status.parquet`

| Field | Type | Meaning |
|---|---|---|
| `source` | string | Selected source name |
| `status` | string | `success`, `empty`, `not_configured`, `unsupported`, `deferred`, `blocked`, or `failed` |

Use this first. A source without `success` or `empty` has no raw or staging artifact.
The report includes selected sources only, not every entry in `sources.yaml`.

### `reports/source_quality/source_quality.parquet`

One row per source that produced accepted staging records.

| Field | Type | Meaning |
|---|---|---|
| `source` | string | Source name |
| `record_count` | int | Accepted staging rows |
| `named_pois` | int | Rows with non-blank names |
| `restaurant_count` | int | Rows categorized as `food.restaurant` |
| `cafe_count` | int | Rows categorized as `food.cafe` |
| `attraction_count` | int | Rows whose category starts with `attraction.` |
| `name_coverage` | float | Fraction with a name |
| `category_coverage` | float | Fraction with a Journie category |
| `address_coverage` | float | Fraction with an address |
| `website_coverage` | float | Fraction with a website |
| `opening_hours_coverage` | float | Fraction with raw opening hours |
| `price_coverage` | float | Fraction with raw price data |
| `operating_status_coverage` | float | Fraction with operating status |
| `outside_region_count` | int | Rows marked outside the polygon |
| `duplicate_candidate_count` | int | Current per-source duplicate candidate count |

Coverage values range from 0 to 1. Compare them to an earlier accepted run rather
than assuming a universal threshold.

Current limitation: `duplicate_candidate_count` is calculated on one source at a
time, while candidate generation excludes same-source pairs. It is therefore
expected to remain zero in the current implementation.

### `reports/category_distribution/category_distribution.parquet`

| Field | Type | Meaning |
|---|---|---|
| `source` | string | Source name |
| `category` | string | Journie category or `<unmapped>` |
| `count` | int | Staging rows in the source/category group |

Use it to detect sudden category shifts, compare source specialization, and verify
taxonomy mapping changes.

### `reports/missing_values/missing_values.parquet`

| Field | Type | Meaning |
|---|---|---|
| `source` | string | Source name |
| `field` | string | One of the nine measured fields |
| `missing_count` | int | Rows where the value is null or empty |
| `record_count` | int | Accepted staging rows for that source |

Measured fields are `name`, `category`, `latitude`, `longitude`, `address`,
`website`, `opening_hours_raw`, `price_raw`, and `description`.

Compute missing rate as `missing_count / record_count`. Do not divide when
`record_count` is zero.

### `reports/unmapped_categories/unmapped_categories.parquet`

| Field | Type | Meaning |
|---|---|---|
| `source` | string | Source name |
| `source_category` | string | Unmapped provider label or `<missing>` |
| `count` | int | Affected staging rows |

Use it to prioritize additions to `configs/categories/mappings/*.v1.yaml`. Review
high-count values first. Do not automatically map unknown values to `other`.
Overture and OSM records filtered by domain selection never enter staging, so this
report cannot discover unmapped categories excluded by those selectors.

### `reports/entity_resolution/entity_resolution.parquet`

### `reports/dedup_report/dedup_report.parquet`

Both files currently contain one row for each cross-source candidate pair generated
because it is within 150 metres or shares a strong identifier.

| Field | Type | Meaning |
|---|---|---|
| `left_source`, `right_source` | string | Candidate source names |
| `left_id`, `right_id` | string | Candidate source record IDs |
| `distance_m` | float? | Haversine distance when both coordinates are valid |
| `score` | float | Weighted match score from 0 to 1 |
| `name_similarity` | float | Name component from 0 to 1 |
| `geo_similarity` | float | Distance component from 0 to 1 |
| `category_similarity` | float | Category component: 0, 0.5, or 1 |
| `strong_match` | bool | Whether a normalized phone, website, or other strong ID matched |
| `decision` | string | `merge`, `review`, or `separate` |

Current thresholds are `merge >= 0.90` and `review >= 0.75`. A strong identifier
can force score 1 unless coordinates contradict it by more than 1,000 metres.

`decision` is the pairwise scoring result, not proof that clustering applied the
merge. Final clustering can reject a scored merge to avoid two records from the
same source in one cluster or a cluster spanning more than 300 metres. The current
reports do not record that final applied/rejected outcome.

Inspect all `review` rows, then inspect `merge` rows near 0.90. Chains and business
branches deserve special attention; confirm final membership in `poi_sources`.

### `reports/opening_hours_parse_failures/opening_hours_parse_failures.parquet`

### `reports/price_parse_failures/price_parse_failures.parquet`

Each populated parser-failure report has:

| Field | Type | Meaning |
|---|---|---|
| `source` | string | Source name |
| `source_record_id` | string | Failed staging record ID |
| `value_raw` | string | Input the parser could not normalize |

A row appears only when a raw value exists but normalization failed. Group by
`value_raw` before changing a parser; fix common patterns and add regression tests.

### `reports/csp_eligibility/csp_eligibility.parquet`

| Field | Type | Meaning |
|---|---|---|
| `poi_id` | string | Canonical POI ID |
| `eligible` | bool | Whether it appears in `csp_pois` |
| `reasons` | list<string> | Zero or more exclusion reason codes |

Possible reason codes are:

| Reason | Meaning |
|---|---|
| `missing_name` | Name is null or blank |
| `missing_or_invalid_coordinates` | Coordinates are absent or outside WGS84 bounds |
| `missing_category` | Journie category is absent |
| `missing_duration` | Visit duration is absent, non-integer, or not positive |
| `unknown_or_invalid_opening_hours` | Weekly schedule is absent or invalid |
| `missing_cost_with_provenance` | Cost is absent, unknown, or lacks accepted evidence |
| `outside_region` | POI is marked outside the boundary polygon |

Explode `reasons` and count each code to identify the largest CSP bottleneck.

## 8. How to Review Reports

Review one run in this order. Stop when an earlier stage is invalid; downstream
percentages are not meaningful until source acquisition is understood.

### Step 1: Check Source Status

```python
import polars as pl

status = pl.read_parquet(RUN_ROOT / "reports/source_status/source_status.parquet")
print(status)
```

For a complete benchmark, investigate every status other than `success`. Decide
explicitly whether `empty` is expected. If partial success is acceptable, record
the missing sources with the benchmark result.

### Step 2: Check Volume, Coverage, and Boundary Noise

```python
quality = pl.read_parquet(RUN_ROOT / "reports/source_quality/source_quality.parquet")
print(
    quality.select(
        "source",
        "record_count",
        "category_coverage",
        "opening_hours_coverage",
        "price_coverage",
        "outside_region_count",
    )
)
```

Compare `record_count` and coverage fields to the previous accepted snapshot.
Large changes require a source, adapter, mapping, or boundary explanation.

### Step 3: Check Rejections and Mapping Gaps

```python
unmapped_path = RUN_ROOT / "reports/unmapped_categories/unmapped_categories.parquet"
unmapped = pl.read_parquet(unmapped_path)

if unmapped.width:
    print(unmapped.sort("count", descending=True))
else:
    print("No unmapped category rows")
```

Also inspect every existing `staging/*/rejected_records.jsonl`. Reconcile:

```text
raw record_count
  -> domain-selected records (see the run log)
  -> accepted staging rows + rejected record rows
```

### Step 4: Check Resolution and Parser Failures

```python
resolution = pl.read_parquet(
    RUN_ROOT / "reports/entity_resolution/entity_resolution.parquet"
)
needs_review = resolution.filter(pl.col("decision") == "review")
near_threshold = resolution.filter(
    (pl.col("decision") == "merge") & (pl.col("score") < 0.95)
)

print(needs_review)
print(near_threshold)
```

For parser reports, check `table.width` before selecting columns because a report
with no failures is currently a zero-column table.

### Step 5: Explain CSP Exclusions

```python
csp_report = pl.read_parquet(RUN_ROOT / "reports/csp_eligibility/csp_eligibility.parquet")

reason_counts = (
    csp_report
    .filter(~pl.col("eligible"))
    .explode("reasons")
    .group_by("reasons")
    .len()
    .sort("len", descending=True)
)
print(reason_counts)
```

Join affected IDs to canonical POIs before changing rules:

```python
pois = pl.read_parquet(RUN_ROOT / "canonical/pois/pois.parquet")

excluded = (
    csp_report
    .filter(~pl.col("eligible"))
    .join(pois.select("poi_id", "name", "category"), on="poi_id", how="left")
)
print(excluded)
```

Fix source data, mappings, or parsers before relaxing CSP requirements. Unknown
hours must not be interpreted as 24/7, and unknown prices must not be interpreted
as zero.

## 9. Artifact Manifest

Every staging, canonical, derived, and report artifact has the same manifest model:

| Field | Type | Meaning |
|---|---|---|
| `manifest_version` | string | Manifest format, currently `v1` |
| `artifact_id` | string | Content-derived artifact identifier |
| `artifact_type` | string | `staging`, `canonical`, `derived`, or `reports` |
| `schema_version` | string | Logical schema label |
| `region` | string | Region ID |
| `run_id` | string | Producing run ID |
| `created_at` | datetime | Manifest creation time |
| `source` | string? | Source name for staging |
| `source_release` | string? | Snapshot ID or label for staging |
| `adapter_version` | string? | Adapter version for staging |
| `taxonomy_version` | string? | Taxonomy version where applicable |
| `resolver_version` | string? | Resolver version where applicable |
| `eligibility_version` | string? | Eligibility version for derived tables |
| `parent_artifact_ids` | list<string> | Upstream artifact IDs |
| `content_sha256` | string | Parquet checksum |
| `config_sha256` | string | Hash of the loaded pipeline-config subset |
| `pipeline_git_commit` | string? | Current Git commit when available |
| `row_count` | int | Number of Parquet rows |
| `status` | string | `succeeded` or `succeeded_empty` |
| `license` | string? | Source license for staging |
| `redistribution_allowed` | bool? | Source redistribution decision for staging |

Check the manifest before consuming an artifact. At minimum, verify
`schema_version`, `row_count`, `status`, `content_sha256`, and the expected parent
IDs. For benchmark outputs, also preserve the full configuration and boundary
because `config_sha256` does not currently cover every relevant file.
