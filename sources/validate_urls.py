"""Validate the "Official Documentation" URLs baked into the service corpus.

The manifest generator builds documentation URLs by slug pattern; any slug
that does not match the provider's real docs path produces a dead citation
link served to users. This script HEAD/GET-checks every URL in the manifest
and reports (optionally rewrites) the dead ones.

Usage:
    python sources/validate_urls.py                       # report only
    python sources/validate_urls.py --json out.json       # machine-readable
    python sources/validate_urls.py --concurrency 20      # parallel checks

Exit code 1 when dead links are found (CI-usable).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

BASE_DIR = Path(__file__).resolve().parent
MANIFEST_CANDIDATES = [BASE_DIR / "sources.json", BASE_DIR / "sources.csv"]


def _load_urls(path: Path) -> list[str]:
    urls: list[str] = []
    if path.suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else data.get("services", [])
        for svc in items:
            if isinstance(svc, dict):
                for key in ("official_docs", "doc_url", "url", "documentation_url"):
                    val = svc.get(key)
                    if isinstance(val, str) and val.startswith("http"):
                        urls.append(val)
    else:
        import csv

        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                for key in ("official_docs", "doc_url", "url", "documentation_url"):
                    val = (row.get(key) or "").strip()
                    if val.startswith("http"):
                        urls.append(val)
    return urls


async def _check(client: httpx.AsyncClient, url: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        started = time.monotonic()
        try:
            resp = await client.head(url, follow_redirects=True, timeout=10.0)
            # Some docs hosts reject HEAD; retry once with GET.
            if resp.status_code >= 400:
                resp = await client.get(url, follow_redirects=True, timeout=10.0)
            ok = resp.status_code < 400
            return {
                "url": url,
                "ok": ok,
                "status": resp.status_code,
                "final_url": str(resp.url),
                "ms": round((time.monotonic() - started) * 1000, 1),
            }
        except Exception as exc:
            return {"url": url, "ok": False, "status": None, "error": str(exc)}


async def main_async(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate corpus documentation URLs")
    parser.add_argument("--json", dest="json_out", help="Write results to a JSON file")
    parser.add_argument("--concurrency", type=int, default=10)
    args = parser.parse_args(argv)

    manifest = next((p for p in MANIFEST_CANDIDATES if p.exists()), None)
    if manifest is None:
        print("No manifest found (sources/sources.json or sources/sources.csv)")
        return 2

    urls = sorted(set(_load_urls(manifest)))
    print(f"Checking {len(urls)} unique documentation URLs from {manifest.name} ...")

    sem = asyncio.Semaphore(args.concurrency)
    headers = {"User-Agent": "CloudGPT-LinkValidator/1.0 (docs citation hygiene)"}
    async with httpx.AsyncClient(headers=headers) as client:
        results = await asyncio.gather(*[_check(client, u, sem) for u in urls])

    dead = [r for r in results if not r["ok"]]
    for r in sorted(dead, key=lambda x: x["url"]):
        status = r.get("status") if r.get("status") is not None else (r.get("error") or "unknown error")
        print(f"  DEAD [{status}] {r['url']}")

    print(f"\n{len(urls) - len(dead)} ok, {len(dead)} dead of {len(urls)}")

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"checked": len(urls), "dead": dead, "results": results}, indent=2),
            encoding="utf-8",
        )
        print(f"Results written to {args.json_out}")

    return 1 if dead else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
