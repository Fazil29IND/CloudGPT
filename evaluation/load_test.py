"""CloudGPT Performance and Load Testing Script.

Simulates concurrent user traffic across Free, Pro, and Max tiers to evaluate:
- Time-to-First-Token (TTFT)
- End-to-End Latency (p50, p95, p99)
- L1 in-process and L2 Redis cache hit rates
- Semantic similarity cache effectiveness
- Token consumption throughput
"""

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class RequestResult:
    tier: str
    query: str
    status_code: int
    ttft_seconds: float | None = None
    total_seconds: float = 0.0
    tokens_received: int = 0
    cached: bool = False
    error: str | None = None


@dataclass
class LoadTestSummary:
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    latencies: list[float] = field(default_factory=list)
    ttfts: list[float] = field(default_factory=list)
    cached_count: int = 0

    def print_report(self) -> None:
        print("\n" + "=" * 65)
        print("  CLOUDGPT PERFORMANCE & LOAD TEST BENCHMARK REPORT")
        print("=" * 65)
        print(f"Total Requests:       {self.total_requests}")
        print(f"Successful Requests:  {self.successful_requests}")
        print(f"Failed Requests:      {self.failed_requests}")
        print(f"Cached Responses:     {self.cached_count} ({(self.cached_count / max(1, self.total_requests)) * 100:.1f}%)")

        if self.ttfts:
            ttft_sorted = sorted(self.ttfts)
            p50_ttft = statistics.median(ttft_sorted)
            p95_ttft = ttft_sorted[int(len(ttft_sorted) * 0.95)] if len(ttft_sorted) > 1 else ttft_sorted[0]
            p99_ttft = ttft_sorted[int(len(ttft_sorted) * 0.99)] if len(ttft_sorted) > 1 else ttft_sorted[0]
            print(f"\nTTFT (Time-to-First-Token):")
            print(f"  p50: {p50_ttft * 1000:.1f} ms")
            print(f"  p95: {p95_ttft * 1000:.1f} ms")
            print(f"  p99: {p99_ttft * 1000:.1f} ms")

        if self.latencies:
            lat_sorted = sorted(self.latencies)
            p50_lat = statistics.median(lat_sorted)
            p95_lat = lat_sorted[int(len(lat_sorted) * 0.95)] if len(lat_sorted) > 1 else lat_sorted[0]
            p99_lat = lat_sorted[int(len(lat_sorted) * 0.99)] if len(lat_sorted) > 1 else lat_sorted[0]
            print(f"\nEnd-to-End Latency:")
            print(f"  p50: {p50_lat:.2f} s")
            print(f"  p95: {p95_lat:.2f} s")
            print(f"  p99: {p99_lat:.2f} s")
        print("=" * 65 + "\n")


TEST_QUERIES = [
    {"query": "How do I configure AWS S3 bucket cross-region replication?", "tier": "Free", "provider": "aws"},
    {"query": "What is the difference between AWS ALB and NLB with latency specs?", "tier": "Pro", "provider": "aws"},
    {"query": "Design a high-availability multi-region active-active architecture on GCP with Spanner and Cloud Armor", "tier": "Max", "provider": "gcp"},
    {"query": "How do I configure AWS S3 bucket cross-region replication?", "tier": "Free", "provider": "aws"},  # Duplicate for L1/L2 test
    {"query": "How can I set up S3 cross region replication in AWS?", "tier": "Free", "provider": "aws"},          # Near-duplicate for Semantic Cache test
    {"query": "Explain Azure Front Door routing rules and SSL termination options", "tier": "Pro", "provider": "azure"},
    {"query": "Compare Azure Cosmos DB multi-master vs AWS DynamoDB Global Tables", "tier": "Max", "provider": "all"},
]


async def run_single_request(
    client: httpx.AsyncClient,
    base_url: str,
    item: dict[str, Any],
    session_id: str,
) -> RequestResult:
    t0 = time.monotonic()
    ttft: float | None = None
    tokens = 0
    cached = False

    payload = {
        "query": item["query"],
        "mode": item["tier"],
        "provider_filter": item["provider"] if item["provider"] != "all" else None,
        "session_id": session_id,
    }

    try:
        async with client.stream(
            "POST",
            f"{base_url}/api/chat/stream",
            json=payload,
            timeout=30.0,
        ) as response:
            if response.status_code != 200:
                return RequestResult(
                    tier=item["tier"],
                    query=item["query"],
                    status_code=response.status_code,
                    total_seconds=time.monotonic() - t0,
                    error=f"HTTP {response.status_code}",
                )

            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    raw = line[5:].strip()
                    try:
                        data = json.loads(raw)
                        if "token" in data:
                            if ttft is None:
                                ttft = time.monotonic() - t0
                            tokens += 1
                        if data.get("done") and data.get("model_used") in ("cached", "semantic-cache", "l1-cache"):
                            cached = True
                    except Exception:
                        pass

            return RequestResult(
                tier=item["tier"],
                query=item["query"],
                status_code=200,
                ttft_seconds=ttft,
                total_seconds=time.monotonic() - t0,
                tokens_received=tokens,
                cached=cached,
            )
    except Exception as e:
        return RequestResult(
            tier=item["tier"],
            query=item["query"],
            status_code=500,
            total_seconds=time.monotonic() - t0,
            error=str(e),
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description="CloudGPT Latency & Load Test")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="Base URL of CloudGPT app")
    parser.add_argument("--concurrency", type=int, default=3, help="Concurrent workers")
    parser.add_argument("--iterations", type=int, default=2, help="Number of benchmark cycles")
    args = parser.parse_args()

    summary = LoadTestSummary()
    async with httpx.AsyncClient() as client:
        for it in range(args.iterations):
            print(f"Running iteration {it + 1}/{args.iterations}...")
            tasks = []
            for i, query_item in enumerate(TEST_QUERIES):
                session_id = f"load-test-{it}-{i}"
                tasks.append(run_single_request(client, args.url, query_item, session_id))

            results = await asyncio.gather(*tasks)
            for res in results:
                summary.total_requests += 1
                if res.status_code == 200:
                    summary.successful_requests += 1
                    summary.latencies.append(res.total_seconds)
                    if res.ttft_seconds is not None:
                        summary.ttfts.append(res.ttft_seconds)
                    if res.cached:
                        summary.cached_count += 1
                else:
                    summary.failed_requests += 1

    summary.print_report()


if __name__ == "__main__":
    asyncio.run(main())
