# Journie — Data Crawling & Ingestion Implementation Plan

> **Scope V0:** TP.HCM  
> **Goal:** xây pipeline thu thập, chuẩn hóa, hợp nhất và versioning dữ liệu POI cho Journie, phục vụ **RAG / preference retrieval**, **CSP itinerary optimization**, và **evaluation**.
>
> Kiến trúc này bám theo hướng của DACN: LLM xử lý natural-language preference và soft constraints; retrieval chọn candidate places; optimization đảm bảo feasibility theo time, budget, opening hours, visit duration và travel distance/time.

---

# 1. Mục tiêu của Data V0

Data V0 không cần hoàn hảo. Nó cần:

- đủ lớn để retrieval/RAG có ý nghĩa;
- đủ đơn giản để xử lý bằng pipeline reproducible;
- có provenance để biết mỗi field đến từ đâu;
- có một subset đủ tốt cho CSP;
- có thể refresh hoặc mở rộng sang thành phố khác sau này.

Từ một canonical dataset, build hai output:

```text
Canonical POI Dataset
        │
        ├── RAG Dataset
        │     broad coverage
        │
        └── CSP Dataset
              stricter constraints
```

## Target ban đầu

Không coi đây là hard requirement:

- **Raw POI catalog:** 3,000–10,000 records.
- **Canonical RAG POIs:** 2,000–8,000.
- **CSP-usable POIs:** 300–1,000.
- **Scope:** TP.HCM.

---

# 2. Nguồn dữ liệu ưu tiên

Không khóa cứng một nguồn là "primary" trước khi benchmark coverage thật ở TP.HCM.

## Source candidates

### 2.1 Overture Maps

Vai trò dự kiến:

- discovery / broad POI catalog;
- name;
- coordinates;
- categories;
- address;
- website/social metadata;
- operating status;
- source/confidence metadata.

### 2.2 OpenStreetMap

Vai trò:

- POI discovery bổ sung;
- coordinates;
- categories/tags;
- opening hours nếu có;
- fee/charge nếu có;
- Wikidata/Wikipedia linkage;
- road network cho OSRM.

### 2.3 Foursquare Open Source Places

Vai trò dự kiến:

- bổ sung business/restaurant/cafe coverage;
- category taxonomy;
- contact/website;
- freshness/closed indicators;
- cross-check với Overture/OSM.

Không để Foursquare trở thành dependency bắt buộc của V0 nếu access friction cao.

### 2.4 Visit HCMC / centralized tourism portals

Vai trò:

- enrich attraction/cultural POIs;
- description;
- address;
- opening hours;
- reference ticket price;
- curated tourism metadata.

Không crawl từng website chính thức của từng POI trong V0.

---

# 3. Nguyên tắc quan trọng

## 3.1 Raw data là immutable

Mỗi crawl/download phải tạo snapshot:

```text
data/raw/2026-09-09/overture/
data/raw/2026-09-09/osm/
data/raw/2026-09-09/foursquare/
data/raw/2026-09-09/visithcmc/
```

Không sửa raw snapshot.

Nếu parser thay đổi:

```text
raw snapshot
   ↓
re-run parser
   ↓
new staging/canonical dataset
```

Không crawl lại nếu không cần.

---

## 3.2 Luôn giữ provenance

Một canonical POI có thể được merge từ nhiều nguồn.

Không chỉ lưu:

```json
{
  "opening_hours": "08:00-17:00"
}
```

Mà phải biết:

```json
{
  "field": "opening_hours",
  "value_raw": "08:00-17:00",
  "source": "osm",
  "source_record_id": "node/123",
  "retrieved_at": "2026-09-09T10:00:00Z",
  "method": "direct",
  "confidence": 0.8
}
```

`method`:

```text
direct
parsed
heuristic
llm_generated
manual
```

---

## 3.3 Không hallucinate missing constraints

Ví dụ:

```text
opening_hours = null
```

không được biến thành:

```text
24/7
```

Tương tự, unknown price không được tự động biến thành 0.

---

## 3.4 LLM enrichment chạy sau dedup

Sai:

```text
10,000 raw records
→ LLM enrichment
→ dedup
```

Đúng:

```text
raw
→ normalize
→ entity resolution
→ canonical POIs
→ LLM semantic enrichment
```

Tránh tốn token cho duplicate records.

---

# 4. Tech stack

## Core

- Python 3.12+
- `uv` hoặc Poetry
- DuckDB
- Polars hoặc Pandas
- Parquet
- PostgreSQL + PostGIS khi cần persistence/server-side queries

## Crawling

- `httpx`
- `selectolax` hoặc BeautifulSoup
- `tenacity`
- `pydantic`
- `orjson`

Chỉ dùng Playwright khi site bắt buộc JavaScript rendering.

## Geo

- `shapely`
- `geopandas` nếu cần
- PostGIS cho production
- Haversine/geodesic distance cho matching

---

# 5. Repository structure

```text
journie-data/
├── pyproject.toml
├── README.md
├── .env.example
│
├── configs/
│   ├── hcm.yaml
│   ├── categories.yaml
│   └── sources.yaml
│
├── src/
│   └── journie_data/
│       ├── cli.py
│       │
│       ├── sources/
│       │   ├── base.py
│       │   ├── overture.py
│       │   ├── osm.py
│       │   ├── foursquare.py
│       │   └── visithcmc.py
│       │
│       ├── parsing/
│       │   ├── opening_hours.py
│       │   ├── prices.py
│       │   ├── address.py
│       │   └── text.py
│       │
│       ├── normalization/
│       │   ├── names.py
│       │   ├── categories.py
│       │   ├── contact.py
│       │   └── geo.py
│       │
│       ├── resolution/
│       │   ├── candidates.py
│       │   ├── scoring.py
│       │   └── merge.py
│       │
│       ├── enrichment/
│       │   ├── duration.py
│       │   ├── cost.py
│       │   └── semantic.py
│       │
│       ├── quality/
│       │   ├── metrics.py
│       │   └── reports.py
│       │
│       └── models/
│           ├── raw.py
│           ├── staging.py
│           └── poi.py
│
├── tests/
│   ├── fixtures/
│   ├── test_normalization.py
│   ├── test_resolution.py
│   └── test_parsers.py
│
└── data/
    ├── raw/
    ├── staging/
    ├── canonical/
    ├── derived/
    └── reports/
```

---

# 6. Geographic configuration

Không hard-code TP.HCM trực tiếp vào crawlers.

`configs/hcm.yaml`:

```yaml
region:
  id: hcm
  name: Ho Chi Minh City
  country_code: VN

  bbox:
    min_lon: null
    min_lat: null
    max_lon: null
    max_lat: null

  polygon_file: configs/geo/hcm.geojson
```

Ưu tiên polygon nếu source hỗ trợ spatial filtering.

BBox chỉ là fallback.

---

# 7. Journie category taxonomy

Không để application phụ thuộc trực tiếp vào taxonomy của từng provider.

Ví dụ V0:

```yaml
attraction:
  - landmark
  - museum
  - gallery
  - historic_site
  - viewpoint
  - zoo
  - theme_park

nature:
  - park
  - garden

food:
  - restaurant
  - cafe
  - food_court
  - street_food

shopping:
  - market
  - mall
  - department_store

culture_religion:
  - temple
  - church
  - pagoda
  - mosque
  - shrine

entertainment:
  - cinema
  - performing_arts
  - nightlife
```

Mỗi source có mapping riêng:

```text
OSM tourism=museum
        ↓
attraction.museum

Overture source category X
        ↓
food.cafe

FSQ category Y
        ↓
shopping.mall
```

Unknown categories phải đi vào report:

```text
unmapped_categories.csv
```

Không silently map tất cả thành `other`.

---

# 8. Canonical POI schema V0

```json
{
  "poi_id": "uuid",

  "name": "Bảo tàng ...",
  "normalized_name": "bao tang ...",

  "category": "attraction.museum",

  "latitude": 10.0,
  "longitude": 106.0,

  "address": null,
  "district": null,
  "city": "Ho Chi Minh City",

  "website": null,
  "phone": null,

  "opening_hours_raw": null,
  "opening_hours_normalized": null,

  "cost_min_vnd": null,
  "cost_typical_vnd": null,
  "cost_max_vnd": null,
  "cost_type": "unknown",

  "expected_visit_duration_min": null,

  "description": null,
  "operating_status": null,

  "semantic_tags": [],

  "source_count": 1,

  "first_seen_at": "2026-09-09T00:00:00Z",
  "last_seen_at": "2026-09-09T00:00:00Z",

  "rag_eligible": true,
  "csp_eligible": false
}
```

---

# 9. Evidence table

Không nhét toàn bộ provenance vào `pois`.

Tạo:

```text
poi_field_evidence.parquet
```

Schema:

```json
{
  "poi_id": "uuid",

  "field": "opening_hours",
  "value_raw": "Mo-Su 08:00-17:00",
  "value_normalized": null,

  "source": "osm",
  "source_record_id": "node/123",

  "source_url": null,

  "retrieved_at": "2026-09-09T10:00:00Z",
  "effective_from": null,
  "effective_to": null,

  "method": "direct",
  "confidence": 0.8
}
```

Một field có thể có nhiều evidence rows.

---

# 10. Base source adapter

Mọi source adapter dùng cùng interface:

```python
class SourceAdapter:
    source_name: str

    def fetch(self, region, snapshot_date): ...

    def normalize(self, raw_path): ...

    def validate(self, dataframe): ...

    def summarize(self, dataframe): ...
```

Output chuẩn:

```text
raw source data
      ↓
source adapter
      ↓
staging/<source>.parquet
```

---

# 11. Phase A — Overture ingestion

## Mục tiêu

Lấy broad POI catalog cho TP.HCM.

Fields cần ưu tiên:

```text
source record id
name
coordinates
source categories
address
website
social/contact metadata
operating status
source/confidence metadata
```

## Flow

```text
remote dataset
     ↓
spatial filter TP.HCM
     ↓
category filter
     ↓
raw snapshot
     ↓
normalize
     ↓
staging/overture.parquet
```

Không load global dataset vào RAM.

## Acceptance criteria

- [ ] Fetch theo region.
- [ ] Có raw snapshot.
- [ ] Có stable source record ID.
- [ ] Mapping được category cơ bản.
- [ ] Xuất quality summary.
- [ ] Có thể chạy lại reproducibly.

---

# 12. Phase B — OpenStreetMap ingestion

## Access strategy

Development:

```text
Overpass
```

Khi cần reproducibility/scale hơn:

```text
regional .osm.pbf
→ local processing
```

## Tags cần lấy

```text
tourism
historic
leisure
amenity
shop
```

Fields ưu tiên:

```text
osm_type
osm_id

name
name:en

lat
lon

opening_hours
fee
charge

website
contact:website
phone

wikidata
wikipedia

cuisine
wheelchair
```

Giữ toàn bộ tag map trong:

```text
raw_tags
```

để không mất metadata khi schema thay đổi.

## Rules

- Node/way/relation đều phải được đưa về representative coordinate.
- Loại record không có `name` khỏi POI catalog V0.
- Missing opening hours giữ `null`.
- Không tự động convert `fee=yes` thành một số tiền.

---

# 13. Phase C — Foursquare Open Source ingestion

## Mục tiêu

Benchmark thêm coverage cho:

```text
restaurants
cafes
shopping
entertainment/business POIs
```

Fields:

```text
fsq_place_id
name
latitude
longitude

address
locality
region

website
telephone

category IDs
category labels

date_refreshed
date_closed

quality/unresolved flags
```

## Rule

Nếu việc access source này làm block pipeline:

```text
status = deferred
```

Milestone đầu vẫn tiếp tục với Overture + OSM.

---

# 14. Phase D — Visit HCMC crawler

Đây là HTML crawler đầu tiên nên làm.

## Mục tiêu

Enrich:

```text
attractions
museums
historic places
cultural/religious POIs
tourism landmarks
```

Fields mong muốn:

```text
name
description
address
opening_hours_raw
price_raw
source_url
category/context
crawl_timestamp
```

## Discovery strategy

Ưu tiên:

1. sitemap;
2. category/list pages;
3. pagination;
4. internal links.

Không dùng search engine làm discovery mechanism chính.

## Fetch strategy

```text
GET page
  ↓
store HTML snapshot
  ↓
content hash
  ↓
parse
```

Nếu content hash không đổi, parser có thể chạy lại nhưng không cần fetch lại thường xuyên.

## Crawl etiquette

- kiểm tra robots.txt;
- rate limit;
- retry có giới hạn;
- user-agent rõ ràng;
- không bypass CAPTCHA/auth;
- không request lại unnecessarily;
- ghi lại crawl timestamp.

---

# 15. Normalization

Normalization phải diễn ra trước entity resolution.

## 15.1 Name

Canonical display name giữ nguyên Unicode.

Tạo auxiliary keys:

```text
normalized_name
normalized_name_ascii
```

Operations:

```text
unicode normalization
lowercase
trim whitespace
collapse repeated whitespace
normalize punctuation
optional Vietnamese abbreviation normalization
```

Không overwrite original name.

---

## 15.2 Website

Normalize về domain:

```text
https://www.example.com/path
http://example.com/
        ↓
example.com
```

Giữ original URL riêng nếu cần.

---

## 15.3 Phone

Normalize về một representation nhất quán, ưu tiên international format nếu parse được.

---

## 15.4 Coordinates

Validate:

```text
-90 <= lat <= 90
-180 <= lon <= 180
```

POI nằm ngoài HCMC polygon phải được flag.

---

# 16. Entity resolution

Một physical POI có thể xuất hiện ở nhiều source.

Không được coi:

```text
OSM record
Overture record
Visit HCMC record
```

là ba places khác nhau nếu chúng cùng đại diện một POI.

## Candidate generation

Chỉ so sánh các record gần nhau:

```text
distance <= 150 m
```

Có thể tune sau.

## Strong identifiers

Auto-match candidate nếu có:

```text
same Wikidata ID
same normalized website domain
same normalized phone
```

và geographic position không mâu thuẫn rõ rệt.

## Geo + name score

V0:

```text
score =
    0.55 * name_similarity
  + 0.30 * geo_similarity
  + 0.15 * category_similarity
```

Threshold khởi đầu:

```text
>= 0.90    auto merge
0.75-0.90  review/conservative path
< 0.75     keep separate
```

Threshold không phải final.

Phải tune dựa trên sample thủ công.

## Important rule

Không merge chain branches chỉ vì cùng tên.

```text
Highlands Coffee A
Highlands Coffee B
```

là hai POIs nếu tọa độ khác nhau.

---

# 17. Entity-resolution evaluation

Random sample khoảng 100–200 candidate pairs.

Đánh label:

```text
same POI
different POI
uncertain
```

Metrics quan trọng:

```text
precision
false merge count
missed duplicate count
```

Ưu tiên **precision**.

False merge thường nguy hiểm hơn missed duplicate.

---

# 18. Field resolution

Sau merge, canonical POI có thể có nhiều values cho một field.

V0 resolver:

```text
source priority
+
freshness
+
parse confidence
```

Priority phải theo field.

Ví dụ:

```yaml
field_priority:

  coordinates:
    - osm
    - overture
    - foursquare

  opening_hours:
    - tourism_portal
    - osm

  description:
    - tourism_portal

  website:
    - tourism_portal
    - overture
    - osm
    - foursquare
```

Không có một global source ranking duy nhất.

---

# 19. Opening-hours parsing

Tách parser khỏi crawler.

Input examples:

```text
Mo-Su 08:00-17:00
7h30 - 17h00
Thứ 2 - Chủ nhật: 8:00 - 17:00
```

Canonical target:

```json
{
  "mon": [["08:00", "17:00"]],
  "tue": [["08:00", "17:00"]],
  "wed": [["08:00", "17:00"]],
  "thu": [["08:00", "17:00"]],
  "fri": [["08:00", "17:00"]],
  "sat": [["08:00", "17:00"]],
  "sun": [["08:00", "17:00"]]
}
```

V0 behavior:

```text
parse success
→ normalized structure

parse failure
→ keep raw
→ normalized = null
```

Không đoán.

Later:

```text
holiday exceptions
date-specific exceptions
temporary closure
seasonal hours
```

---

# 20. Price normalization

Input examples:

```text
Miễn phí
40.000 đồng/người
50.000 - 100.000 VNĐ
Adult: 80k
```

Canonical:

```json
{
  "min_vnd": 40000,
  "typical_vnd": 40000,
  "max_vnd": 40000,
  "type": "ticket"
}
```

Types:

```text
ticket
expected_spend
free
unknown
```

Parser không được biến price bands hoặc ambiguous text thành exact factual price nếu không đủ evidence.

---

# 21. Expected visit duration

Không cần crawl field này ở V0.

Dùng category heuristic.

`configs/duration.yaml`:

```yaml
duration_minutes:
  attraction.museum: 120
  attraction.landmark: 60
  attraction.viewpoint: 45
  attraction.historic_site: 75

  nature.park: 90

  food.cafe: 60
  food.restaurant: 75

  shopping.market: 90
  shopping.mall: 120

  culture_religion.temple: 45
  culture_religion.church: 45
  culture_religion.pagoda: 45
```

Lưu:

```text
value = 120
method = heuristic
source = journie_category_prior_v1
```

Sau này có thể dùng POI-specific estimate.

---

# 22. Restaurant/cafe cost

Không crawl toàn bộ menus ở V0.

Nếu không có factual data:

```yaml
estimated_spend:
  food.cafe:
    min: 40000
    typical: 70000
    max: 120000

  food.restaurant:
    min: 70000
    typical: 150000
    max: 300000
```

Đây là **estimate**, không phải factual price.

Provenance phải thể hiện điều đó.

Các con số này phải được hiệu chỉnh sau khi sampling data thật, không coi là fixed truth.

---

# 23. RAG eligibility

Rule V0:

```python
rag_eligible = (
    name is not None and latitude is not None and longitude is not None and category is not None
)
```

Ưu tiên có thêm:

```text
description
subcategory
provider labels
semantic tags
website text
```

nhưng không bắt buộc ở V0.

---

# 24. CSP eligibility

Dataset CSP strict hơn.

Required:

```text
✓ name
✓ coordinates
✓ category
✓ expected visit duration
```

Opening hours:

```text
✓ normalized factual hours
OR
✓ explicitly unrestricted/always-open
```

Cost:

```text
✓ factual value
OR
✓ heuristic estimate with provenance
```

## Benchmark mode

```text
unknown opening hours
→ exclude
```

## Product prototype mode

```text
unknown opening hours
→ candidate for on-demand enrichment
→ không mặc định open
```

---

# 25. Semantic enrichment

Chỉ chạy trên canonical POIs.

Controlled vocabulary V0:

```json
{
  "themes": [
    "history",
    "local_culture",
    "food",
    "art",
    "architecture",
    "nature"
  ],

  "experience": [
    "quiet",
    "crowded",
    "romantic",
    "family_friendly",
    "educational",
    "vibrant"
  ],

  "environment": [
    "indoor",
    "outdoor"
  ],

  "traveler_types": [
    "solo",
    "family",
    "couple",
    "foodie"
  ]
}
```

Không cho LLM tự tạo taxonomy không giới hạn.

---

# 26. Embedding document

Không embed raw JSON.

Example:

```text
Name: Bảo tàng ...
Category: Museum
Description: ...
Themes: history, local culture
Experience: indoor, educational
Area: District 1, Ho Chi Minh City
```

Không cần embed:

```text
source hash
crawl timestamp
raw coordinates precision
solver-only internals
```

---

# 27. Output datasets

```text
data/
├── raw/
│   └── YYYY-MM-DD/
│       ├── overture/
│       ├── osm/
│       ├── foursquare/
│       └── visithcmc/
│
├── staging/
│   ├── overture.parquet
│   ├── osm.parquet
│   ├── foursquare.parquet
│   └── visithcmc.parquet
│
├── canonical/
│   ├── pois.parquet
│   ├── poi_sources.parquet
│   └── poi_field_evidence.parquet
│
├── derived/
│   ├── rag_pois.parquet
│   ├── csp_pois.parquet
│   └── embedding_documents.jsonl
│
└── reports/
    ├── source_quality.csv
    ├── category_distribution.csv
    ├── missing_values.csv
    ├── entity_resolution.csv
    └── dedup_report.csv
```

---

# 28. Source quality report

Mỗi ingestion run phải tạo report như:

| Metric | Overture | OSM | Foursquare | Visit HCMC |
|---|---:|---:|---:|---:|
| Raw records | | | | |
| Named POIs | | | | |
| Unique POIs | | | | |
| Restaurants | | | | |
| Cafes | | | | |
| Attractions | | | | |
| Address coverage | | | | |
| Website coverage | | | | |
| Opening-hours coverage | | | | |
| Price coverage | | | | |
| Closed/status coverage | | | | |
| Duplicate candidate rate | | | | |

Không quyết định source priority cuối cùng trước khi có report này.

---

# 29. Data quality checks

Pipeline/report phải flag:

## Invalid coordinates

```text
lat outside [-90, 90]
lon outside [-180, 180]
```

## Missing name

Reject:

```text
null
empty
whitespace-only
```

## Geographic mismatch

POI ngoài region polygon:

```text
flag / reject depending tolerance
```

## Duplicate candidates

Report:

```text
similar normalized name
+
distance < threshold
```

Không xóa trước entity resolution.

## Unknown categories

Xuất:

```text
unmapped_categories.csv
```

---

# 30. CLI

Target interface:

```bash
journie-data fetch overture --region hcm
journie-data fetch osm --region hcm
journie-data fetch foursquare --region hcm
journie-data crawl visithcmc --region hcm

journie-data normalize all
journie-data resolve

journie-data enrich duration
journie-data enrich cost
journie-data enrich semantic

journie-data build rag
journie-data build csp

journie-data report
```

Full pipeline:

```bash
journie-data run \
  --region hcm \
  --snapshot 2026-09-09
```

---

# 31. Logging

Mỗi run ghi:

```text
run_id
source
snapshot_date

start_time
end_time

records_requested
records_downloaded
records_parsed
records_failed

http_failures
parse_failures
validation_failures
```

Logs:

```text
logs/
└── <run_id>/
    ├── overture.jsonl
    ├── osm.jsonl
    ├── foursquare.jsonl
    └── visithcmc.jsonl
```

---

# 32. Retry policy

HTTP sources:

```text
429
→ exponential backoff

5xx
→ retry

timeout
→ retry

normal 4xx
→ usually no retry
```

Default:

```text
max retries = 3–5
```

Không retry vô hạn.

---

# 33. Dataset versioning

Dataset version:

```text
journie-hcm-v0.1
journie-hcm-v0.2
```

Manifest:

```json
{
  "dataset_version": "0.1",
  "created_at": "2026-09-09",
  "region": "hcm",

  "sources": {
    "overture": "...",
    "osm": "...",
    "foursquare": "...",
    "visithcmc": "..."
  },

  "pipeline_git_commit": "..."
}
```

Experiment phải tham chiếu dataset version.

---

# 34. Licensing / compliance metadata

Mỗi source có config:

```yaml
source:
  name:
  homepage:
  access_method:
  license:
  attribution_required:
  redistribution_allowed:
  terms_checked_at:
  notes:
```

Không coi "crawl được" đồng nghĩa với "được phép redistribute".

---

# 35. Milestones

## Milestone 0 — Project skeleton

- [ ] Initialize repo.
- [ ] Python environment.
- [ ] CLI skeleton.
- [ ] Pydantic models.
- [ ] HCMC config.
- [ ] Journie category taxonomy.
- [ ] raw/staging/canonical directories.
- [ ] base adapter.
- [ ] logging.

Definition of Done:

```bash
journie-data --help
```

hoạt động.

---

## Milestone 1 — Overture + OSM benchmark

- [ ] Overture ingestion.
- [ ] OSM ingestion.
- [ ] Normalize both to staging schema.
- [ ] Category mappings.
- [ ] Basic coverage report.
- [ ] Optional Foursquare ingestion nếu access thuận lợi.

Questions phải trả lời:

```text
1. Mỗi source có bao nhiêu POIs ở TP.HCM?
2. Category distribution?
3. Restaurant/cafe coverage?
4. Attraction coverage?
5. Address coverage?
6. Website coverage?
7. Opening-hours coverage?
8. Fee/price coverage?
9. Spatial distribution?
10. Duplicate/overlap sơ bộ?
```

**Checkpoint:** dừng lại và xem data trước khi build resolver phức tạp.

---

## Milestone 2 — Canonical POI dataset


- [ ] Name/contact/geo normalization.
- [ ] Candidate generation.
- [ ] Rule-based entity resolution.
- [ ] Canonical POI IDs.
- [ ] Evidence table.
- [ ] Field resolver.
- [ ] `canonical/pois.parquet`.
- [ ] Manual evaluation trên 100–200 candidate pairs.

Definition of Done:

Có thể trả lời:

```text
một canonical POI được tạo từ source records nào?
tại sao field X có value Y?
```

---

## Milestone 3 — Visit HCMC enrichment


- [ ] URL discovery.
- [ ] HTML snapshots.
- [ ] Parser.
- [ ] `description`.
- [ ] `address`.
- [ ] `opening_hours_raw`.
- [ ] `price_raw`.
- [ ] Entity-match vào canonical POIs.
- [ ] Coverage report.

---

## Milestone 4 — CSP dataset


- [ ] Opening-hours parser.
- [ ] Price parser.
- [ ] Duration heuristic.
- [ ] Restaurant/cafe cost heuristic.
- [ ] `csp_eligible`.
- [ ] Export `derived/csp_pois.parquet`.

Sau milestone này có thể bắt đầu CSP prototype.

---

## Milestone 5 — RAG dataset


- [ ] Semantic controlled vocabulary.
- [ ] Batch LLM enrichment.
- [ ] Embedding document generator.
- [ ] `rag_eligible`.
- [ ] Export `embedding_documents.jsonl`.
- [ ] Vector DB ingestion.

Sau milestone này có thể chạy preference retrieval experiments.

---

# 36. Definition of Done cho Data V0

Data V0 hoàn thành khi:

- [ ] Có ít nhất 2 independent POI sources.
- [ ] Có raw snapshots.
- [ ] Có unified staging schema.
- [ ] Có canonical POI IDs.
- [ ] Có dedup/entity resolution.
- [ ] Có provenance/evidence.
- [ ] Có Journie taxonomy.
- [ ] Có missing-value/source-quality reports.
- [ ] Có opening-hours parser cơ bản.
- [ ] Có visit-duration heuristic.
- [ ] Có cost representation.
- [ ] Có `rag_eligible`.
- [ ] Có `csp_eligible`.
- [ ] Có dataset manifest/version.
- [ ] Pipeline chạy lại reproducibly.

---

# 37. Không làm trong V0

Để tránh scope explosion:

- Không crawl từng official website của từng POI.
- Không crawl toàn bộ Google Maps.
- Không xây hotel dataset.
- Không xây flight dataset.
- Không làm booking.
- Không crawl menu của mọi restaurant.
- Không yêu cầu opening hours coverage 100%.
- Không build perfect ontology.
- Không dùng ML entity resolution ngay.
- Không train embedding model từ đầu.
- Không làm live traffic ingestion.
- Không làm weather ingestion.
- Không giải quyết toàn bộ real-time events.

Dynamic events có thể được simulate cho replanning experiment trước.

---

# 38. Sau V0

## V1

- Generic selective website enrichment.
- Better opening-hours exceptions.
- Better price extraction.
- POI popularity/quality.
- Incremental crawling.
- Better entity resolution.
- More districts/categories.

## V2

- Các thành phố khác tại Việt Nam.
- Live status provider.
- Traffic provider.
- Weather provider.
- Event data.
- Uncertainty-aware CSP.
- Automatic refresh policies.

---

# 40. First implementation checkpoint

Checkpoint đầu tiên không phải:

> "crawl được bao nhiêu POI?"

Mà là:

> **Trong cùng geographic scope TP.HCM, Overture và OSM khác nhau thế nào về coverage và metadata quality?**

Sau khi có:

```text
staging/overture.parquet
staging/osm.parquet
```

tạo report/notebook trả lời:

```text
total POIs
category distribution
name coverage
address coverage
website coverage
opening-hours coverage
price/fee coverage
spatial distribution
duplicate candidates
cross-source overlap
```

Kết quả này quyết định:

```text
source priorities
entity-resolution rules
enrichment strategy
```

---

# 41. Immediate next task

Bắt đầu bằng:

```text
Milestone 0
+
Overture adapter
```

Target CLI:

```bash
journie-data fetch overture --region hcm
```

Phải tạo:

```text
data/raw/<snapshot>/overture/
data/staging/overture.parquet
data/reports/overture_summary.json
```

Example summary:

```json
{
  "record_count": 0,
  "category_counts": {},
  "name_coverage": 0.0,
  "address_coverage": 0.0,
  "website_coverage": 0.0
}
```

Sau đó implement OSM adapter với **cùng staging interface**, rồi mới benchmark hai nguồn.

---

# 42. Mapping với kiến trúc Journie

Data pipeline cần giữ đúng separation of concerns:

```text
User request
    │
    ▼
LLM
├── parse preference
├── parse trip constraints
└── create structured request
    │
    ▼
RAG / Retrieval
└── retrieve candidate POIs
    │
    ▼
Reranking
    │
    ▼
CSP / CP-SAT
├── opening hours
├── visit duration
├── budget
├── activity limits
└── scheduling feasibility
    │
    ▲
    │
OSRM
└── distance/travel-time matrix
```

Priority dữ liệu:

```text
1. POI identity
2. coordinates
3. category
4. opening hours
5. visit duration
6. cost
7. semantic description/tags
8. optional popularity/quality
```

Rating/reviews chưa phải ưu tiên của V0.

---

# 43. Recommended starting point

**Bắt đầu với Overture + OSM.**

Lý do:

1. Có thể benchmark coverage TP.HCM nhanh.
2. Có đủ data để thiết kế staging schema.
3. Cho phép làm entity resolution trên data thật.
4. OSM đồng thời phù hợp với routing stack OSRM.
5. Visit HCMC sau đó đóng vai trò enrichment thay vì POI backbone.
6. Không cần quyết định source chính dựa trên giả định trước khi nhìn data.

Sau Milestone 1, mới quyết định có nên đầu tư thêm vào:

```text
Foursquare ingestion
Visit HCMC crawling depth
selective website crawling
additional Vietnam tourism sources
```

---

# 45. First questions to answer with real data

Sau khi Milestone 1 chạy xong, không tiếp tục theo assumptions. Dùng data trả lời:

1. Overture có bao nhiêu POIs phù hợp Journie ở TP.HCM?
2. OSM có bao nhiêu?
3. Overlap bao nhiêu?
4. Nguồn nào có restaurant/cafe coverage tốt hơn?
5. Nguồn nào có attraction coverage tốt hơn?
6. Opening hours thực tế có đủ để dùng không?
7. Bao nhiêu POI có website để hỗ trợ later enrichment?
8. Duplicate rate có đáng lo không?
9. Geographic noise ngoài TP.HCM có nhiều không?
10. Với rule hiện tại, bao nhiêu POIs có thể trở thành `csp_eligible`?

Đây sẽ là cơ sở để thiết kế Data V0.2.
