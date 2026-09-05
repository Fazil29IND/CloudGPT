"""
Production-Quality Async HTTP Document Fetcher for CloudGPT Corpus.

Supports rate limiting, ETag conditional requests (If-None-Match),
robots.txt verification, and exponential backoff retry.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

import httpx

# Ensure root is on path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from corpus.manifest import ManifestEntry

logger = logging.getLogger("corpus.fetcher")


@dataclass
class FetchResult:
    entry_id: str
    status: str       # "fetched" | "not_modified" | "blocked" | "error"
    content: Optional[str]
    http_status: int
    etag: Optional[str]
    last_modified: Optional[str]
    fetch_duration_ms: float


class DocFetcher:
    """Async documentation fetcher with rate limits and caching policies."""

    def __init__(
        self,
        rate_limit_rps: float = 1.0,
        max_concurrent: int = 3,
        timeout: int = 30,
        max_retries: int = 3,
        user_agent: str = "CloudGPT-Crawler/1.0 (+https://cloudgpt.local; educational)",
    ) -> None:
        self.rate_limit_rps = rate_limit_rps
        self.max_concurrent = max_concurrent
        self.timeout = timeout
        self.max_retries = max_retries
        self.user_agent = user_agent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._domain_last_request: dict[str, float] = {}
        self._robots_cache: dict[str, bool] = {}

    async def _check_robots_txt(self, client: httpx.AsyncClient, domain: str, path: str) -> bool:
        """Check whether crawling is allowed by robots.txt (cached per domain)."""
        if domain in self._robots_cache:
            return self._robots_cache[domain]

        robots_url = f"https://{domain}/robots.txt"
        try:
            resp = await client.get(robots_url, timeout=5.0)
            if resp.status_code == 200:
                # Basic check for Disallow: /
                text = resp.text.lower()
                is_disallowed = "user-agent: *\ndisallow: /\n" in text or "user-agent: *\r\ndisallow: /\r\n" in text
                self._robots_cache[domain] = not is_disallowed
                return not is_disallowed
        except Exception:
            pass

        self._robots_cache[domain] = True
        return True

    async def _rate_limit_domain(self, domain: str) -> None:
        """Enforce domain token bucket / delay."""
        interval = 1.0 / max(0.1, self.rate_limit_rps)
        last = self._domain_last_request.get(domain, 0.0)
        elapsed = time.monotonic() - last
        if elapsed < interval:
            await asyncio.sleep(interval - elapsed)
        self._domain_last_request[domain] = time.monotonic()

    async def fetch(self, entry: ManifestEntry) -> FetchResult:
        """Fetch a single manifest entry with conditional headers and backoff."""
        parsed = urlparse(entry.canonical_url)
        domain = parsed.netloc

        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
        }
        if entry.etag:
            headers["If-None-Match"] = entry.etag
        if entry.last_modified:
            headers["If-Modified-Since"] = entry.last_modified

        async with self._semaphore:
            await self._rate_limit_domain(domain)
            t0 = time.perf_counter()

            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                allowed = await self._check_robots_txt(client, domain, parsed.path)
                if not allowed:
                    return FetchResult(
                        entry_id=entry.entry_id,
                        status="blocked",
                        content=None,
                        http_status=403,
                        etag=None,
                        last_modified=None,
                        fetch_duration_ms=(time.perf_counter() - t0) * 1000,
                    )

                for attempt in range(1, self.max_retries + 1):
                    try:
                        resp = await client.get(entry.canonical_url, headers=headers)
                        duration_ms = (time.perf_counter() - t0) * 1000

                        if resp.status_code == 304:
                            return FetchResult(
                                entry_id=entry.entry_id,
                                status="not_modified",
                                content=None,
                                http_status=304,
                                etag=entry.etag,
                                last_modified=entry.last_modified,
                                fetch_duration_ms=duration_ms,
                            )
                        elif resp.status_code == 200:
                            return FetchResult(
                                entry_id=entry.entry_id,
                                status="fetched",
                                content=resp.text,
                                http_status=200,
                                etag=resp.headers.get("ETag"),
                                last_modified=resp.headers.get("Last-Modified"),
                                fetch_duration_ms=duration_ms,
                            )
                        elif resp.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                            await asyncio.sleep(2 ** (attempt - 1))
                            continue
                        else:
                            return FetchResult(
                                entry_id=entry.entry_id,
                                status="error",
                                content=None,
                                http_status=resp.status_code,
                                etag=None,
                                last_modified=None,
                                fetch_duration_ms=duration_ms,
                            )
                    except (httpx.RequestError, OSError):
                        if attempt < self.max_retries:
                            await asyncio.sleep(2 ** (attempt - 1))
                            continue
                        return FetchResult(
                            entry_id=entry.entry_id,
                            status="error",
                            content=None,
                            http_status=0,
                            etag=None,
                            last_modified=None,
                            fetch_duration_ms=(time.perf_counter() - t0) * 1000,
                        )

            return FetchResult(
                entry_id=entry.entry_id,
                status="error",
                content=None,
                http_status=0,
                etag=None,
                last_modified=None,
                fetch_duration_ms=(time.perf_counter() - t0) * 1000,
            )

    async def fetch_batch(
        self,
        entries: list[ManifestEntry],
        progress_cb: Optional[Callable[[int, int, FetchResult], None]] = None,
    ) -> list[FetchResult]:
        """Fetch a list of manifest entries concurrently."""
        results: list[FetchResult] = []
        total = len(entries)

        for idx, entry in enumerate(entries):
            res = await self.fetch(entry)
            results.append(res)
            if progress_cb:
                progress_cb(idx + 1, total, res)

        return results


def main() -> None:
    parser = argparse.ArgumentParser(description="CloudGPT Document Fetcher CLI")
    parser.add_argument("--manifest", default="corpus/manifest.json", help="Path to manifest JSON")
    parser.add_argument("--dry-run", action="store_true", help="Print crawl plan without network requests")
    parser.add_argument("--limit", type=int, default=10, help="Limit number of entries to process")
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    if not manifest_path.is_absolute():
        manifest_path = BASE_DIR / manifest_path

    if not manifest_path.exists():
        print(f"Error: manifest file not found at {manifest_path}")
        sys.exit(1)

    with open(manifest_path, "r", encoding="utf-8") as f:
        raw_entries = json.load(f)

    entries = [ManifestEntry(**e) for e in raw_entries][:args.limit]

    print(f"\nLoaded {len(entries)} manifest entries from {manifest_path.name}")
    if args.dry_run:
        print("\n--- DRY RUN CRAWL PLAN ---")
        for i, e in enumerate(entries, 1):
            print(f"[{i:02d}] {e.provider.upper():<6} | {e.service:<18} | {e.document_type:<14} | {e.canonical_url}")
        print(f"\nDry run completed for {len(entries)} items.\n")
        return

    fetcher = DocFetcher()
    print("\nStarting fetch execution...")
    results = asyncio.run(fetcher.fetch_batch(entries, lambda cur, tot, r: print(f"[{cur}/{tot}] {r.entry_id}: {r.status} ({r.http_status}) in {r.fetch_duration_ms:.1f}ms")))
    print(f"\nFetch completed: {len(results)} items processed.")


if __name__ == "__main__":
    main()
