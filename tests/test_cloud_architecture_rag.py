"""Tests verifying comprehensive enterprise cloud architecture data in Services.md and RAG chunks.

Covers:
- Well-Architected Framework pillars across AWS, GCP, and Azure
- Production quotas, limits, and throttling defense strategies
- FinOps, commitment models (Savings Plans, CUDs, Reservations), and hybrid licensing (AHB)
- Cloud migration 7 Rs frameworks (MAP, RaMP, CAF)
- Sovereign cloud partitions (GovCloud, Assured Workloads, Azure Government)
- RAG chunk coverage and ingestion parsing accuracy
"""

import json
from pathlib import Path
from ingest_services import parse_services_md

BASE_DIR = Path(__file__).resolve().parent.parent


def test_services_md_well_architected_framework():
    services_path = BASE_DIR / "Services.md"
    assert services_path.exists()
    content = services_path.read_text(encoding="utf-8")

    assert "## 6. Enterprise Well-Architected Framework Catalog & Comparison" in content
    assert "### Operational Excellence Pillar" in content
    assert "### Security Pillar" in content
    assert "### Reliability Pillar" in content
    assert "### Performance Efficiency Pillar" in content
    assert "### Cost Optimization Pillar" in content
    assert "### Sustainability Pillar" in content

    # Cross-cloud parity checks
    assert "AWS CloudFormation" in content and "Azure Bicep" in content and "Infrastructure Manager" in content
    assert "AWS Nitro System" in content and "Google Titanium" in content and "Azure Boost" in content


def test_services_md_quotas_and_throttling_limits():
    content = (BASE_DIR / "Services.md").read_text(encoding="utf-8")

    assert "## 7. Enterprise Quotas, Hard Limits & Throttling Catalog Matrix" in content
    # S3 throughput limits
    assert "3,500 PUT" in content and "5,500 GET" in content
    # DynamoDB partition limits
    assert "1,000 WCU" in content and "3,000 RCU" in content and "ProvisionedThroughputExceededException" in content
    # BigQuery & Cosmos DB limits
    assert "2,000 baseline slots" in content
    assert "10,000 RU/s" in content and "429" in content
    # Lambda execution timeout & concurrency
    assert "900 seconds (15 minutes)" in content
    assert "1,000 concurrent executions" in content


def test_services_md_finops_and_licensing():
    content = (BASE_DIR / "Services.md").read_text(encoding="utf-8")

    assert "## 8. Cloud FinOps, Commitment Economics & Hybrid Licensing Catalog Matrix" in content
    # Commitment models
    assert "Compute Savings Plans" in content
    assert "Committed Use Discounts" in content
    assert "Azure Savings Plans for Compute" in content
    # Inter-AZ data transfer
    assert "$0.01 per GB" in content
    # Enterprise licensing
    assert "Azure Hybrid Benefit (AHB)" in content
    assert "AWS License Manager" in content


def test_services_md_migration_7_rs():
    content = (BASE_DIR / "Services.md").read_text(encoding="utf-8")

    assert "## 9. Cloud Migration, Modernization & The 7 Rs Catalog Matrix" in content
    assert "1. Rehost" in content
    assert "2. Replatform" in content
    assert "3. Refactor" in content
    assert "4. Repurchase" in content
    assert "5. Retain" in content
    assert "6. Retire" in content
    assert "7. Relocate" in content
    # Migration programs
    assert "Migration Acceleration Program (MAP)" in content
    assert "Rapid Migration Program (RaMP)" in content
    assert "Cloud Adoption Framework (CAF)" in content


def test_services_md_sovereign_clouds():
    content = (BASE_DIR / "Services.md").read_text(encoding="utf-8")

    assert "## 10. Sovereign Cloud, Compliance & Government Partitions Catalog Matrix" in content
    assert "AWS GovCloud" in content
    assert "Google Cloud Assured Workloads" in content
    assert "Azure Government" in content
    assert "AWS Secret Region" in content
    assert "AWS European Sovereign Cloud" in content
    assert "FedRAMP High" in content
    assert "DoD Cloud Computing SRG IL4 / IL5" in content


def test_ingest_parser_captures_all_sections():
    parsed = parse_services_md(BASE_DIR / "Services.md")

    # Verify lifecycle phases intact
    assert len(parsed["lifecycle_phases"]) == 15

    # Verify strategy sections expanded to 10 deep dives
    assert len(parsed["strategy_sections"]) >= 10
    strategy_titles = {s["title"] for s in parsed["strategy_sections"]}
    assert "Enterprise Multi-Account & Resource Hierarchy" in strategy_titles
    assert "Identity, Zero Trust & IAM Policy Evaluation Logic" in strategy_titles
    assert "Global Transit Networking & Cross-Cloud Interconnects" in strategy_titles
    assert "Disaster Recovery, RPO/RTO & Multi-Region Resiliency Patterns" in strategy_titles
    assert "Serverless Concurrency, Execution Limits & Cold-Start Dynamics" in strategy_titles

    # Verify catalog categories expanded
    assert len(parsed["catalog_categories"]) >= 50


def test_generated_chunks_json_volume():
    chunks_path = BASE_DIR / "data" / "chunks" / "services_chunks.json"
    assert chunks_path.exists()

    with open(chunks_path, "r", encoding="utf-8") as f:
        chunks = json.load(f)

    # Ensure corpus expanded to over 1,000 comprehensive chunks
    assert len(chunks) >= 1000, f"Expected >= 1000 chunks, got {len(chunks)}"
