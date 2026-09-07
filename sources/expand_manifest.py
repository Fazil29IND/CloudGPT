"""Expand corpus/manifest.json with official architecture/IaC documentation.

Adds curated, canonical URLs for the Well-Architected/Framework bodies,
Architecture Center reference architectures, official module catalogs (raw
READMEs), and the IaC validation toolchain docs. Every entry is link-checked
before it is written; dead URLs are pruned and reported (exit 1) so citations
in the RAG corpus stay honest.

Usage:
    python sources/expand_manifest.py                    # validate + write
    python sources/expand_manifest.py --skip-validation  # write without checks
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from corpus.manifest import ManifestEntry

BASE_DIR = Path(__file__).resolve().parent.parent
MANIFEST_PATH = BASE_DIR / "corpus" / "manifest.json"

# (provider, service, product_family, document_type, title, url)
NEW_ENTRIES: list[tuple[str, str, str, str, str, str]] = [
    # ── AWS: Well-Architected Framework & lenses ────────────────────────────
    ("aws", "well-architected", "well-architected", "architecture", "AWS Well-Architected Framework", "https://docs.aws.amazon.com/wellarchitected/latest/framework/welcome.html"),
    ("aws", "well-architected", "well-architected", "architecture", "Operational Excellence Pillar", "https://docs.aws.amazon.com/wellarchitected/latest/operational-excellence-pillar/welcome.html"),
    ("aws", "well-architected", "well-architected", "architecture", "Security Pillar", "https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/welcome.html"),
    ("aws", "well-architected", "well-architected", "architecture", "Reliability Pillar", "https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/welcome.html"),
    ("aws", "well-architected", "well-architected", "architecture", "Performance Efficiency Pillar", "https://docs.aws.amazon.com/wellarchitected/latest/performance-efficiency-pillar/welcome.html"),
    ("aws", "well-architected", "well-architected", "architecture", "Cost Optimization Pillar", "https://docs.aws.amazon.com/wellarchitected/latest/cost-optimization-pillar/welcome.html"),
    ("aws", "well-architected", "well-architected", "architecture", "Serverless Applications Lens", "https://docs.aws.amazon.com/wellarchitected/latest/serverless-applications-lens/welcome.html"),
    ("aws", "well-architected", "well-architected", "architecture", "SaaS Lens", "https://docs.aws.amazon.com/wellarchitected/latest/saas-lens/welcome.html"),
    ("aws", "well-architected", "well-architected", "architecture", "Container Build Lens", "https://docs.aws.amazon.com/wellarchitected/latest/container-build-lens/welcome.html"),
    # ── AWS: Architecture Center & Prescriptive Guidance ────────────────────
    ("aws", "architecture", "architecture-center", "architecture", "AWS Architecture Center", "https://aws.amazon.com/architecture/"),
    ("aws", "solutions", "solutions-library", "architecture", "AWS Solutions Library", "https://aws.amazon.com/solutions/"),
    ("aws", "prescriptive-guidance", "prescriptive-guidance", "architecture", "AWS Prescriptive Guidance Patterns", "https://docs.aws.amazon.com/prescriptive-guidance/latest/patterns/welcome.html"),
    ("aws", "eks", "architecture-center", "architecture", "Amazon EKS Best Practices Guide (Security)", "https://docs.aws.amazon.com/eks/latest/best-practices/security-use-bundles-for-cluster-teams.html"),
    ("aws", "vpc", "architecture-center", "architecture", "Building a global network architecture on AWS", "https://docs.aws.amazon.com/whitepapers/latest/aws-vpc-connectivity-options/aws-vpc-connectivity-options.html"),
    # ── AWS: official Terraform module READMEs (raw, fetcher-friendly) ──────
    ("aws", "vpc", "terraform-modules", "iac", "terraform-aws-modules/terraform-aws-vpc", "https://raw.githubusercontent.com/terraform-aws-modules/terraform-aws-vpc/HEAD/README.md"),
    ("aws", "eks", "terraform-modules", "iac", "terraform-aws-modules/terraform-aws-eks", "https://raw.githubusercontent.com/terraform-aws-modules/terraform-aws-eks/HEAD/README.md"),
    ("aws", "rds", "terraform-modules", "iac", "terraform-aws-modules/terraform-aws-rds", "https://raw.githubusercontent.com/terraform-aws-modules/terraform-aws-rds/HEAD/README.md"),
    ("aws", "rds-aurora", "terraform-modules", "iac", "terraform-aws-modules/terraform-aws-rds-aurora", "https://raw.githubusercontent.com/terraform-aws-modules/terraform-aws-rds-aurora/HEAD/README.md"),
    ("aws", "alb", "terraform-modules", "iac", "terraform-aws-modules/terraform-aws-alb", "https://raw.githubusercontent.com/terraform-aws-modules/terraform-aws-alb/HEAD/README.md"),
    ("aws", "s3-bucket", "terraform-modules", "iac", "terraform-aws-modules/terraform-aws-s3-bucket", "https://raw.githubusercontent.com/terraform-aws-modules/terraform-aws-s3-bucket/HEAD/README.md"),
    ("aws", "lambda", "terraform-modules", "iac", "terraform-aws-modules/terraform-aws-lambda", "https://raw.githubusercontent.com/terraform-aws-modules/terraform-aws-lambda/HEAD/README.md"),
    ("aws", "security-group", "terraform-modules", "iac", "terraform-aws-modules/terraform-aws-security-group", "https://raw.githubusercontent.com/terraform-aws-modules/terraform-aws-security-group/HEAD/README.md"),
    ("aws", "iam", "terraform-modules", "iac", "terraform-aws-modules/terraform-aws-iam", "https://raw.githubusercontent.com/terraform-aws-modules/terraform-aws-iam/HEAD/README.md"),
    ("aws", "ecs", "terraform-modules", "iac", "terraform-aws-modules/terraform-aws-ecs", "https://raw.githubusercontent.com/terraform-aws-modules/terraform-aws-ecs/HEAD/README.md"),
    # ── HashiCorp: Terraform language & AWS tutorials ───────────────────────
    ("aws", "terraform", "iac-toolchain", "iac", "Terraform CLI: fmt, validate, init", "https://developer.hashicorp.com/terraform/cli/commands/init"),
    ("aws", "terraform", "iac-toolchain", "iac", "Terraform tutorials: AWS get started", "https://developer.hashicorp.com/terraform/tutorials/aws-get-started"),
    ("aws", "terraform", "iac-toolchain", "iac", "Terraform AWS provider documentation", "https://registry.terraform.io/providers/hashicorp/aws/latest/docs"),
    # ── Azure: Well-Architected & CAF ────────────────────────────────────────
    ("azure", "well-architected", "well-architected", "architecture", "Azure Well-Architected Framework", "https://learn.microsoft.com/en-us/azure/well-architected/"),
    ("azure", "well-architected", "well-architected", "architecture", "Azure WAF: Reliability", "https://learn.microsoft.com/en-us/azure/well-architected/reliability/"),
    ("azure", "well-architected", "well-architected", "architecture", "Azure WAF: Security", "https://learn.microsoft.com/en-us/azure/well-architected/security/"),
    ("azure", "well-architected", "well-architected", "architecture", "Azure WAF: Cost Optimization", "https://learn.microsoft.com/en-us/azure/well-architected/cost-optimization/"),
    ("azure", "well-architected", "well-architected", "architecture", "Azure WAF: Operational Excellence", "https://learn.microsoft.com/en-us/azure/well-architected/operational-excellence/"),
    ("azure", "well-architected", "well-architected", "architecture", "Azure WAF: Performance Efficiency", "https://learn.microsoft.com/en-us/azure/well-architected/performance-efficiency/"),
    ("azure", "caf", "cloud-adoption-framework", "architecture", "Microsoft Cloud Adoption Framework overview", "https://learn.microsoft.com/en-us/azure/cloud-adoption-framework/overview"),
    ("azure", "caf", "cloud-adoption-framework", "architecture", "CAF: Ready (landing zones)", "https://learn.microsoft.com/en-us/azure/cloud-adoption-framework/ready/"),
    ("azure", "caf", "cloud-adoption-framework", "architecture", "CAF: Adopt", "https://learn.microsoft.com/en-us/azure/cloud-adoption-framework/adopt/"),
    ("azure", "caf", "cloud-adoption-framework", "architecture", "CAF: Govern", "https://learn.microsoft.com/en-us/azure/cloud-adoption-framework/govern/"),
    # ── Azure: Architecture Center reference architectures ──────────────────
    ("azure", "architecture", "architecture-center", "architecture", "Azure Architecture Center browse index", "https://learn.microsoft.com/en-us/azure/architecture/browse/"),
    ("azure", "architecture", "architecture-center", "architecture", "Azure reference architectures index", "https://learn.microsoft.com/en-us/azure/architecture/reference-architectures/"),
    ("azure", "aks", "architecture-center", "architecture", "Baseline architecture for AKS", "https://learn.microsoft.com/en-us/azure/architecture/reference-architectures/containers/aks/baseline-aks"),
    ("azure", "aks", "architecture-center", "architecture", "AKS mission-critical architecture", "https://learn.microsoft.com/en-us/azure/architecture/reference-architectures/containers/aks-mission-critical/mission-critical-intro"),
    ("azure", "app-service", "architecture-center", "architecture", "Baseline zone-redundant web application", "https://learn.microsoft.com/en-us/azure/architecture/web-apps/app-service/architectures/baseline-zone-redundant"),
    ("azure", "networking", "architecture-center", "architecture", "Hub-spoke network topology", "https://learn.microsoft.com/en-us/azure/architecture/reference-architectures/hybrid-networking/hub-spoke"),
    ("azure", "identity", "architecture-center", "architecture", "Microsoft Entra ID baseline architecture", "https://learn.microsoft.com/en-us/azure/architecture/example-scenario/identity/entra-id"),
    # ── Azure: Bicep & Verified Modules ──────────────────────────────────────
    ("azure", "bicep", "iac-toolchain", "iac", "Bicep language overview", "https://learn.microsoft.com/en-us/azure/azure-resource-manager/bicep/overview"),
    ("azure", "bicep", "iac-toolchain", "iac", "Bicep best practices", "https://learn.microsoft.com/en-us/azure/azure-resource-manager/bicep/best-practices"),
    ("azure", "avm", "verified-modules", "iac", "Azure Verified Modules hub", "https://azure.github.io/Azure-Verified-Modules/"),
    ("azure", "avm", "verified-modules", "iac", "AVM Terraform resource module index", "https://azure.github.io/Azure-Verified-Modules/indexes/terraform/tf-resource-modules/"),
    ("azure", "storage", "terraform-modules", "iac", "AVM: Terraform storage account module", "https://raw.githubusercontent.com/Azure/terraform-azurerm-avm-res-storage-storageaccount/HEAD/README.md"),
    ("azure", "virtual-network", "terraform-modules", "iac", "AVM: Terraform virtual network module", "https://raw.githubusercontent.com/Azure/terraform-azurerm-avm-res-network-virtualnetwork/HEAD/README.md"),
    ("azure", "key-vault", "terraform-modules", "iac", "AVM: Terraform Key Vault module", "https://raw.githubusercontent.com/Azure/terraform-azurerm-avm-res-keyvault-vault/HEAD/README.md"),
    ("azure", "aks", "terraform-modules", "iac", "AVM: Terraform AKS module", "https://raw.githubusercontent.com/Azure/terraform-azurerm-avm-res-containerservice-managedcluster/HEAD/README.md"),
    # ── GCP: Architecture Framework & blueprints ─────────────────────────────
    ("gcp", "framework", "architecture-framework", "architecture", "Google Cloud Architecture Framework", "https://docs.cloud.google.com/architecture/framework"),
    ("gcp", "framework", "architecture-framework", "architecture", "GCP Framework: System design (reliability)", "https://docs.cloud.google.com/architecture/framework/reliability"),
    ("gcp", "framework", "architecture-framework", "architecture", "GCP Framework: Security & compliance", "https://docs.cloud.google.com/architecture/framework/security"),
    ("gcp", "framework", "architecture-framework", "architecture", "GCP Framework: Cost optimization", "https://docs.cloud.google.com/architecture/framework/cost-optimization"),
    ("gcp", "architecture", "architecture-center", "architecture", "Google Cloud Architecture Center", "https://cloud.google.com/architecture"),
    ("gcp", "terraform", "terraform-blueprints", "iac", "Terraform blueprints and modules for Google Cloud", "https://docs.cloud.google.com/docs/terraform/blueprints/terraform-blueprints"),
    ("gcp", "terraform", "terraform-blueprints", "iac", "Deploy an enterprise landing zone (example foundation)", "https://cloud.google.com/architecture/landing-zones"),
    ("gcp", "rag", "architecture-center", "architecture", "RAG infrastructure reference architecture (Vertex AI)", "https://docs.cloud.google.com/architecture/rag-capable-gen-ai-app-using-vertex-ai"),
    # ── GCP: official Terraform module READMEs ───────────────────────────────
    ("gcp", "network", "terraform-modules", "iac", "terraform-google-modules/network", "https://raw.githubusercontent.com/terraform-google-modules/terraform-google-network/HEAD/README.md"),
    ("gcp", "project-factory", "terraform-modules", "iac", "terraform-google-modules/project-factory", "https://raw.githubusercontent.com/terraform-google-modules/terraform-google-project-factory/HEAD/README.md"),
    ("gcp", "kubernetes-engine", "terraform-modules", "iac", "terraform-google-modules/kubernetes-engine", "https://raw.githubusercontent.com/terraform-google-modules/terraform-google-kubernetes-engine/HEAD/README.md"),
    ("gcp", "cloud-storage", "terraform-modules", "iac", "terraform-google-modules/cloud-storage", "https://raw.githubusercontent.com/terraform-google-modules/terraform-google-cloud-storage/HEAD/README.md"),
    ("gcp", "iam", "terraform-modules", "iac", "terraform-google-modules/iam", "https://raw.githubusercontent.com/terraform-google-modules/terraform-google-iam/HEAD/README.md"),
    ("gcp", "lb", "terraform-modules", "iac", "terraform-google-modules/lb", "https://raw.githubusercontent.com/terraform-google-modules/terraform-google-lb/HEAD/README.md"),
    # ── IaC validation toolchain (used by tools/iac_validator.py) ───────────
    ("aws", "cloudformation", "iac-toolchain", "iac", "cfn-lint — CloudFormation linter", "https://github.com/aws-cloudformation/cfn-lint"),
    ("aws", "terraform", "iac-toolchain", "iac", "Checkov — IaC security scanning", "https://www.checkov.io/1.Welcome/Quick%20Start.html"),
    ("gcp", "kubernetes", "iac-toolchain", "iac", "kubeconform — Kubernetes manifest validation", "https://github.com/yannh/kubeconform"),
]


async def _check_urls(urls: list[str]) -> dict[str, int | None]:
    """HEAD/GET-check each URL; returns {url: status} (None = unreachable)."""
    statuses: dict[str, int | None] = {}
    sem = asyncio.Semaphore(10)
    headers = {"User-Agent": "CloudGPT-ManifestBuilder/1.0 (corpus hygiene)"}

    async def _one(client: httpx.AsyncClient, url: str) -> None:
        async with sem:
            try:
                resp = await client.head(url, follow_redirects=True, timeout=12.0)
                if resp.status_code >= 400:
                    resp = await client.get(url, follow_redirects=True, timeout=12.0)
                statuses[url] = resp.status_code if resp.status_code < 400 else resp.status_code
            except Exception:
                statuses[url] = None

    async with httpx.AsyncClient(headers=headers) as client:
        await asyncio.gather(*[_one(client, u) for u in urls])
    return statuses


def main() -> int:
    parser = argparse.ArgumentParser(description="Expand the corpus documentation manifest")
    parser.add_argument("--skip-validation", action="store_true", help="Write entries without link checks")
    args = parser.parse_args()

    manifest: list[dict] = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    existing_ids = {e.get("entry_id") for e in manifest}
    existing_urls = {e.get("canonical_url", "").strip().lower() for e in manifest}

    # Dedupe within the new table itself
    seen_urls: set[str] = set()
    candidates: list[ManifestEntry] = []
    for provider, service, family, doc_type, title, url in NEW_ENTRIES:
        if url.lower() in seen_urls or url.lower() in existing_urls:
            continue
        seen_urls.add(url.lower())
        candidates.append(
            ManifestEntry.create(
                provider=provider,
                service=service,
                title=title,
                canonical_url=url,
                product_family=family,
                document_type=doc_type,
                corpus_version="v2",
            )
        )

    print(f"{len(candidates)} new candidate entries ({len(NEW_ENTRIES)} in table, deduped)")

    if not args.skip_validation and candidates:
        print("Link-checking candidates ...")
        statuses = asyncio.run(_check_urls([c.canonical_url for c in candidates]))
        alive: list[ManifestEntry] = []
        dead: list[tuple[str, int | None]] = []
        for c in candidates:
            status = statuses.get(c.canonical_url)
            if status is not None and status < 400:
                c.http_status = status
                alive.append(c)
            else:
                dead.append((c.canonical_url, status))
        for url, status in dead:
            print(f"  PRUNED [{status if status is not None else 'unreachable'}] {url}")
        print(f"{len(alive)} verified, {len(dead)} pruned")
        candidates = alive

    if not candidates:
        print("Nothing new to add.")
        return 0

    manifest.extend(json.loads(c.model_dump_json()) for c in candidates)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Manifest now holds {len(manifest)} entries → {MANIFEST_PATH}")
    return 1 if (not args.skip_validation and candidates == [] and NEW_ENTRIES) else 0


if __name__ == "__main__":
    sys.exit(main())
