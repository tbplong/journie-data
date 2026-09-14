from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from selectolax.parser import HTMLParser

from journie_data.schemas.models import StagingRecord

from .base import FetchResult, FetchStatus, SourceAdapter, first_value, read_records

VISIT_CATEGORY_KEYWORDS = (
    ("museum", "attraction.museum"),
    ("bảo tàng", "attraction.museum"),
    ("historic", "attraction.historic_site"),
    ("lịch sử", "attraction.historic_site"),
    ("landmark", "attraction.landmark"),
    ("park", "nature.park"),
    ("công viên", "nature.park"),
    ("market", "shopping.market"),
    ("chợ", "shopping.market"),
    ("pagoda", "culture_religion.pagoda"),
    ("chùa", "culture_religion.pagoda"),
    ("church", "culture_religion.church"),
    ("nhà thờ", "culture_religion.church"),
)


class VisitHCMCAdapter(SourceAdapter):
    source_name = "visithcmc"
    adapter_version = "visithcmc/v1"

    def __init__(
        self,
        *,
        start_urls: Iterable[str] | None = None,
        local_path: Path | str | None = None,
        category_map: Mapping[str, str] | None = None,
        user_agent: str = "JournieDataBot/0.1 (research POI ingestion)",
        min_interval_seconds: float = 0.5,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.start_urls = list(start_urls or [])
        self.local_path = Path(local_path) if local_path else None
        self.category_keywords = tuple((category_map or dict(VISIT_CATEGORY_KEYWORDS)).items())
        self.user_agent = user_agent
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self._last_request_at = 0.0
        self._robots: dict[str, RobotFileParser] = {}

    def fetch(self, region: Mapping[str, Any] | Any, snapshot_date: date | str) -> FetchResult:
        if self.local_path is not None:
            if not self.local_path.exists():
                return FetchResult(
                    self.source_name,
                    FetchStatus.NOT_CONFIGURED,
                    raw_path=self.local_path,
                    message="configured local input does not exist",
                )
            try:
                local_records = read_records(self.local_path)
            except (OSError, ValueError, RuntimeError) as exc:
                return FetchResult(
                    self.source_name,
                    FetchStatus.FAILED,
                    raw_path=self.local_path,
                    message=str(exc),
                )
            return FetchResult(
                self.source_name,
                FetchStatus.SUCCESS if local_records else FetchStatus.EMPTY,
                local_records,
                self.local_path,
            )
        if not self.start_urls or self.client is None:
            return FetchResult(
                self.source_name,
                FetchStatus.NOT_CONFIGURED,
                message="start_urls and an httpx.Client are required for Visit HCMC fetches",
            )
        records: list[dict[str, Any]] = []
        attempts = 0
        for url in self.start_urls:
            result = self.fetch_url(url)
            attempts += result.http_attempts
            if result.status == FetchStatus.BLOCKED:
                return result
            if not result.ok:
                return FetchResult(
                    self.source_name,
                    FetchStatus.FAILED,
                    records,
                    message=result.message,
                    http_attempts=attempts,
                )
            records.extend(result.records)
        return FetchResult(
            self.source_name,
            FetchStatus.SUCCESS if records else FetchStatus.EMPTY,
            records,
            discovered_urls=list(self.start_urls),
            http_attempts=attempts,
        )

    def fetch_url(self, url: str) -> FetchResult:
        try:
            if not self._allowed(url):
                return FetchResult(
                    self.source_name, FetchStatus.BLOCKED, message=f"robots.txt disallows {url}"
                )
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self.min_interval_seconds:
                time.sleep(self.min_interval_seconds - elapsed)
            response, attempts = self._request(
                "GET", url, headers={"User-Agent": self.user_agent}, follow_redirects=True
            )
            self._last_request_at = time.monotonic()
            record = self.parse_html(response.text, str(response.url))
            return FetchResult(
                self.source_name, FetchStatus.SUCCESS, [record], http_attempts=attempts
            )
        except (httpx.HTTPError, ValueError, RuntimeError) as exc:
            return FetchResult(self.source_name, FetchStatus.FAILED, message=str(exc))

    def discover_urls(self, html: str, base_url: str) -> list[str]:
        """Discover same-host detail/list links from fixture or fetched HTML."""
        tree = HTMLParser(html)
        host = urlparse(base_url).netloc.lower()
        urls: set[str] = set()
        for node in tree.css("a[href]"):
            href = node.attributes.get("href")
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            absolute = urljoin(base_url, href).split("#", 1)[0]
            if urlparse(absolute).netloc.lower() == host:
                urls.add(absolute)
        return sorted(urls)

    def parse_html(
        self, html: str, source_url: str, *, retrieved_at: datetime | None = None
    ) -> dict[str, Any]:
        tree = HTMLParser(html)

        def text(selectors: str) -> str | None:
            for selector in selectors.split(","):
                node = tree.css_first(selector.strip())
                if node:
                    value = " ".join(node.text(separator=" ").split())
                    if value:
                        return value
            return None

        title = text("h1, [itemprop='name'], meta[property='og:title']")
        if title is None:
            meta_title = tree.css_first("meta[property='og:title']")
            title = meta_title.attributes.get("content") if meta_title else None
        description = text("[itemprop='description'], .description, .detail-description, article p")
        address = text("[itemprop='address'], .address, [class*='address']")
        opening_hours = text("[itemprop='openingHours'], .opening-hours, [class*='opening-hour']")
        price = text(".price, .ticket-price, [class*='price']")
        if opening_hours is None:
            opening_hours = _labelled_text(
                tree, ("opening hours", "giờ mở cửa", "thời gian mở cửa")
            )
        if price is None:
            price = _labelled_text(tree, ("ticket price", "giá vé", "phí tham quan"))
        timestamp = retrieved_at or datetime.now(UTC)
        return {
            "id": hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:24],
            "name": title,
            "description": description,
            "address": address,
            "opening_hours_raw": opening_hours,
            "price_raw": price,
            "source_url": source_url,
            "crawl_timestamp": timestamp.isoformat(),
            "content_hash": hashlib.sha256(html.encode("utf-8")).hexdigest(),
            "html": html,
        }

    def normalize_record(self, raw: Mapping[str, Any], *, retrieved_at: datetime) -> StagingRecord:
        source_category = first_value(raw, "category", "context")
        category = next(
            (
                mapped
                for keyword, mapped in self.category_keywords
                if source_category and keyword in str(source_category).lower()
            ),
            None,
        )
        source_id = first_value(raw, "id", "source_record_id")
        if source_id is None and raw.get("source_url"):
            source_id = hashlib.sha256(str(raw["source_url"]).encode("utf-8")).hexdigest()[:24]
        if source_id is None:
            raise ValueError("Visit HCMC record requires an ID or source_url")
        payload = {
            "source": self.source_name,
            "source_record_id": str(source_id),
            "source_url": raw.get("source_url"),
            "retrieved_at": raw.get("crawl_timestamp") or retrieved_at,
            "name": raw.get("name"),
            "latitude": first_value(raw, "latitude", "lat"),
            "longitude": first_value(raw, "longitude", "lon", "lng"),
            "address": raw.get("address"),
            "district": raw.get("district"),
            "city": raw.get("city"),
            "website": first_value(raw, "website", "source_url"),
            "category": category,
            "source_categories": raw.get("categories")
            or ([source_category] if source_category else []),
            "opening_hours_raw": raw.get("opening_hours_raw"),
            "price_raw": raw.get("price_raw"),
            "description": raw.get("description"),
            "content_hash": raw.get("content_hash"),
            "raw_data": {key: value for key, value in raw.items() if key != "html"},
        }
        return self._staging_record(payload)

    def _allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            parser = RobotFileParser()
            parser.set_url(urljoin(origin, "/robots.txt"))
            try:
                response, _ = self._request(
                    "GET",
                    urljoin(origin, "/robots.txt"),
                    headers={"User-Agent": self.user_agent},
                    follow_redirects=True,
                )
                parser.parse(response.text.splitlines())
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise
                parser.parse([])
            self._robots[origin] = parser
        return self._robots[origin].can_fetch(self.user_agent, url)


def _labelled_text(tree: HTMLParser, labels: tuple[str, ...]) -> str | None:
    pattern = re.compile("|".join(re.escape(label) for label in labels), re.IGNORECASE)
    for node in tree.css("p, li, div"):
        value = " ".join(node.text(separator=" ").split())
        if value and len(value) <= 500 and pattern.search(value):
            return value
    return None
