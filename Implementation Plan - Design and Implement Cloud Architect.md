# Implementation Plan — From "Designing Architect" to "Designing AND Implementing" Architect

**Workstream:** Making Apex / Core / Lite design **and implement** cloud architecture at principal-engineer level
**Date:** 2026-09-07
**Constraints (non-negotiable, honored throughout):**
1. **RAG workflow structure untouched** — all new data flows through the *existing* `ingest_services.py` pipeline (chunking → embeddings → Pinecone/BM25 namespaces). Edits within it only where a config entry or knowledge-pack registration is *additive*.
2. **Redis workflow untouched** — no changes to semantic cache, rate limiter, session cache structures.
3. **Pretrained LLM untouched** — no model ID changes; quality comes from data, prompts, thinking (already Apex=Max / Core=High / Lite=Low), and a validation-feedback loop.
4. Everything must be **honestly classified** per `docs/IMPLEMENTATION_STATUS.md`.

---

## 1. Research Findings — The Current Best Solution (2025–2026 literature + practice)

| Finding | Source | What CloudGPT adopts |
|---|---|---|
| **Iterative feedback loop is the state of the art.** IaCGen: format check → syntax check → deploy validation, with errors fed back to the LLM. 54.6–91.6% of templates became deployable within **10 repair iterations**. | [arXiv:2506.05623 (IaCGen, FSE 2025)](https://arxiv.org/abs/2506.05623) | Generate → validate → repair loop with a capped iteration budget (config, default 3 in-request; 10 is the research ceiling) |
| **Feeding validator errors/warnings back to the agent** is the canonical self-correction mechanism. | [arXiv:2411.19043](https://arxiv.org/html/2411.19043v1) | Validator stderr/stdout becomes the retry message payload — reuses the existing `generation/` retry machinery |
| **Canonical validation gate = terraform validate + TFLint + Checkov/CFNLint** (used by the Multi-IaC-Eval benchmark). | [arXiv:2509.05303](https://arxiv.org/html/2509.05303v1) | Layered validator tool: `terraform fmt -check` → `terraform validate` → `tflint` → `checkov` → `cfn-lint` / `helm lint` + `kubeconform` for K8s paths |
| **Run linters automatically on every generated IaC file** (editor-level hooks), with CI policy gates. | [Gruntwork: AI coding assistants and IaC](https://www.gruntwork.io/blog/ai-coding-assistants-and-infrastructure-as-code-velocity-without-losing-control) | Validation runs on every `<cloudgpt_artifact type="iac">` block automatically — not opt-in |
| **Re-run security checks after every repair iteration** — fixes can regress previously-passing checks. | [arXiv:2608.13404](https://arxiv.org/html/2608.13404v2) | The loop re-runs the *full* validator stack each iteration, not just the failed layer |
| **Plan-in-sandbox + plan-JSON policy check** catches provider-logic errors static lint cannot. | [Practitioner pattern (r/Terraform), Spacelift scanner roundup](https://spacelift.io/blog/terraform-scanning-tools) | Phase 4 (optional/deployment-dependent): `terraform plan` against a sandbox account behind an explicit operator credential gate |
| Official architecture corpora to ingest: AWS Well-Architected + Architecture Center, Azure Architecture Center + Well-Architected + CAF, GCP Architecture Framework + Terraform blueprints. | [AWS WAF](https://aws.amazon.com/architecture/well-architected/), [Azure WAF](https://learn.microsoft.com/en-us/azure/well-architected/), [GCP Framework](https://docs.cloud.google.com/architecture/framework), [GCP blueprints](https://docs.cloud.google.com/docs/terraform/blueprints/terraform-blueprints) | Workstream A manifest expansion |
| Official module catalogs = "implemented, not just designed" knowledge: terraform-aws-modules (57 modules), Azure Verified Modules, terraform-google-modules / Cloud Foundation Toolkit (50 modules). | [terraform-aws-modules](https://github.com/terraform-aws-modules), [Azure Verified Modules](https://azure.github.io/Azure-Verified-Modules/), [CFT](https://github.com/GoogleCloudPlatform/cloud-foundation-toolkit) | Module-catalog knowledge packs in the `iac-templates` namespace |

**Conclusion:** the current best solution = **(1)** practitioner-grade + official-architecture data in RAG, **(2)** a generate→validate→repair loop reusing existing generation retry machinery, **(3)** tier-persona prompts, **(4)** measured acceptance. No model changes required.

---

## 2. Tier Personas & Behavior Matrix (target state)

| Dimension | **Apex** — Principal Cloud Architect & Distinguished Engineer | **Core** — Staff Cloud Engineer | **Lite** — Fast Field Engineer |
|---|---|---|---|
| Design output | Full multi-cloud blueprints, decision matrices, WAF/CAF/framework evaluation, DR/RTO/RPO, Mermaid topology | Single-workload architecture with trade-offs | Direct answer + minimal architecture note |
| Implementation output | **Complete multi-file IaC repos** (Terraform/Bicep/CFN/Helm) as `<cloudgpt_bundle>`, production-deployable, with variables/outputs/backend/locking, CI pipeline artifact, and verification runbook | **Complete single-purpose IaC** for the requested service, runnable as-is, with verification commands | Config snippets / CLI one-liners, correct but minimal |
| Validation loop | Full stack, max repair iterations (config, default 3) | terraform validate + checkov, 2 iterations | Deterministic checks only, 0 repair iterations |
| Evidence | Cites WAF pillars, module catalogs, framework docs | Cites module usage + provider docs | Cites provider docs |
| Thinking | Max (already default) | High (already default) | Low — speed preserved |

---

## 3. Workstream A — Knowledge Expansion ("research everywhere and add data")

All additions flow through **existing, untouched** ingestion:
`corpus/manifest.json → corpus/fetcher.py (robots.txt + ETags) → corpus/normalizer.py → ingest_services.py → versioned namespaces → promote/rollback/DLQ`
and `data/senior_engineer_knowledge/*.md → build_knowledge_chunks() → KNOWLEDGE_FILES namespaces`.

### A1. Activate the dormant official-docs fetcher (zero pipeline changes)
`corpus/manifest.json` already holds **90 curated official-doc entries** and the fetcher pipeline exists but was never run to completion. Plan:
1. Dry-run fetch over the existing 90 entries; verify normalizer output quality; promote into versioned namespaces.
2. **Expand the manifest (+~120 entries)** — additive JSON only:
   - **AWS:** Well-Architected Framework (5 pillars + Cost Optimization, Security, Reliability lenses), Architecture Center reference architectures (serverless, EKS, multi-region, hybrid, landing zone), Prescriptive Guidance top patterns, Terraform on AWS docs.
   - **Azure:** Well-Architected Framework, Cloud Adoption Framework (ready/adopt/govern), Architecture Center reference architectures (App Service, AKS, multitenancy, mission-critical), Azure Verified Modules docs.
   - **GCP:** Architecture Framework, Architecture Center how-tos, Cloud Foundation Toolkit docs, Terraform blueprints docs.
3. Run `sources/validate_urls.py` (built in the remediation) over new entries before promotion — dead links never enter the corpus.

### A2. Author practitioner knowledge packs (the "implemented it" experience)
New markdown packs in `data/senior_engineer_knowledge/` — each registered with one **additive** `KNOWLEDGE_FILES` entry:

| File (new or expanded) | Namespace | Content (authored, practitioner-grade — no templates) |
|---|---|---|
| `iac_templates.md` (expand) | `iac-templates` | Production Terraform per top service: remote state + locking, module layout, variable/outputs conventions, `for_each` patterns, drift/import handling, CI/CD plan-apply gates. Bicep + CFN equivalents for flagship services. |
| `module_catalog.md` (new) | `iac-templates` | Module **usage recipes** with real registry source addresses: `terraform-aws-modules/{vpc,eks,rds,alb,ecr,…}/aws`, `Azure/avm-res-*`, `terraform-google-modules/{network,project-factory,…}` — inputs/outputs/known gotchas, version pinning, AVM requirements. |
| `implementation_runbooks.md` (new) | `senior-engineer-knowledge` | Provision → verify → operate runbooks per service family: exact CLI/API sequences, smoke tests, rollback, deletion ordering, tag governance. |
| `postmortems.md` (new) | `troubleshooting-playbooks` | Real failure-class postmortems: quota exhaustion, IMDSv2/RBAC lockouts, DNS split-horizon, state-file corruption, KMS dependency loops — timeline, detection, fix, prevention. |
| `security_hardening.md` (new) | `senior-engineer-knowledge` | Checkov-rule-aligned hardening per service: encryption/KMS, IMDS, public-access blocks, network policies, logging/audit (CloudTrail, Audit Logs, Defender), policy-as-code samples. |
| `migration_and_cutover.md` (new) | `senior-engineer-knowledge` | Migration playbooks: DB migration/cutover windows, blue/green + canary, storage lifecycle migrations, landing-zone moves. |
| `platform_cicd.md` (new) | `senior-engineer-knowledge` | GitHub Actions/Azure DevOps/Cloud Build pipelines for IaC: plan/apply separation, environments, approvals, OIDC auth (no long-lived keys), drift detection schedules. |

### A3. Module catalogs ingestion (scripted, one-off + refreshable)
`sources/fetch_module_catalogs.py` (new, additive): pull README + example blocks + input/output tables for the three catalogs above via GitHub raw/registry metadata, emit knowledge-card chunks into the `iac-templates` namespace through the existing chunker/embedder. Respect robots.txt and rate limits (reuse `corpus/fetcher.py` helpers). Target: ~150 module pages across the three clouds.

### A4. Ingestion, indexing, and retrieval verification (existing commands only)
```bash
python ingest_services.py          # existing pipeline — chunk + embed + upsert
python evaluation/baseline_snapshot.py    # capture index stats artifact
python -m pytest tests/test_rag_services.py -v   # live roundtrip still green
```
Plus a **retrieval eval expansion**: add ~40 implementation-flavored questions ("give me a production VPC module layout with locked remote state", "AVM resource module for storage with CMK") to `evaluation/golden_set.json` and run `evaluation/retrieval_eval.py --with-retrieval-eval` for measured recall.

---

## 4. Workstream B — Implementation Capability (generation layer; no RAG/Redis structural changes)

### B1. New tool: layered IaC validator (`tools/iac_validator.py`)
- Validates artifact content (passed as string, not filesystem paths from the LLM): materialize to a temp dir → run the stack in order, **aggregate all findings**.
- Stack: `terraform fmt -check` → `terraform validate` (uses an init-cached plugin dir, config-provided) → `tflint` → `checkov -f <file> --framework terraform` → `cfn-lint` (CloudFormation artifacts) → `helm lint` + `kubeconform` (K8s artifacts).
- Every binary **optional and detected at runtime** (`shutil.which`); when absent, the layer reports `skipped` honestly — mirroring the cloud-tools pattern (never fabricate a pass).
- Result schema: `{valid: bool, layers: [{name, status: passed|failed|skipped, findings: [{code, message, line}]}], duration_ms}`.
- Wired into the **existing tools stage** exactly like `_safe_cloud_resources` (gated, fault-isolated, timeout-bounded) and invoked post-generation on artifacts.

### B2. Validate-and-repair loop (reuses existing `generation/` retry machinery)
- After generation, if artifacts exist and validation fails: feed the aggregated findings into the existing `build_retry_messages` bounded loop (already wired in adaptive/agentic paths; add the base-pipeline call site).
- New config fields (+ `.env.example`): `iac_validation_enabled: bool = True`, `iac_max_repair_iterations: int = 3` (Apex), tier caps enforced in `generation/policies_from_settings` — Apex 3, Core 2, Lite 0; `iac_validator_timeout_seconds: int = 120`.
- **Full stack re-runs each iteration** (regression guard from the research).
- Loop results land in `PipelineResult.validation` and are surfaced as an SSE-visible artifact metadata block (additive event fields only — existing event shapes untouched).

### B3. Artifact protocol upgrade (prompt-level; Apex prompt already mandates artifacts)
- Extend the artifact taxonomy: `type="iac" | "script" | "policy" | "k8s" | "pipeline" | "doc"`.
- Tier prompts gain an **Implementation Mandate** section (matching existing prompt style): complete deployable code, version-pinned module sources, state/locking, verification commands, rollback note; forbid `# TODO` (Apex already does; extend to Core).
- Lite prompt: "correct minimal snippet + one verification command" (speed contract).

### B4. Data-driven prompting (RAG → implementation quality)
- Core/Apex prompt guidance: *consult retrieved module-catalog and runbook chunks before emitting IaC; prefer registry modules over hand-rolled resources where a verified module exists* — leverages Workstream A data through the untouched RAG path.

---

## 5. Workstream C — Verification, Evaluation, and Honest Status

1. **IaC golden set** (`evaluation/implementation_golden_set.json`, new): ~40 tasks ("EKS cluster with private endpoint + IRSA", "AVM storage with CMK + lifecycle") with acceptance = validators pass + required properties present (CMK, encryption, no public access).
2. **Acceptance gates** (computed, added to `acceptance_check.py`): validator tool importable + layer detection works; repair-loop config respected per tier; golden set executes (validator-dependent tests skip honestly when binaries absent).
3. **Status honesty:** `docs/IMPLEMENTATION_STATUS.md` rows updated: knowledge packs → *Tested*; validator tool → *Tested* (mocked binaries + optional real-binary tests); repair loop → *Tested*; plan-in-sandbox → *Architecture Only* until an operator wires credentials.
4. **Benchmark:** extend `scratch/benchmark_tiers_post.py`-style harness into `evaluation/implementation_eval.py` — per-tier deployability rate measured with the real validator stack; report actual numbers only.

---

## 6. Phasing, File Touch List, Effort

| Phase | Deliverables | Files | Risk |
|---|---|---|---|
| **P1 — Knowledge (data-first)** | 90-entry fetch dry-run + promotion; +120 manifest entries; 6 knowledge packs authored; module-catalog fetcher; golden questions | `corpus/manifest.json`, `data/senior_engineer_knowledge/*.md`, `ingest_services.py` (additive KNOWLEDGE_FILES entries only), `sources/fetch_module_catalogs.py` | **Low** — additive data through existing pipeline |
| **P2 — Validator tool** | Layered validator + tests (mocked + real-binary optional) + tools-stage wiring | `tools/iac_validator.py`, `api/chat_routes.py` (additive `_safe_iac_validation`), `config.py`, `.env.example` | Medium — new tool, fault-isolated |
| **P3 — Repair loop + prompts** | Retry-machinery call site, tier policy caps, Implementation Mandate prompts | `generation/policy.py`, `generation/validator.py` (policy), `api/chat_routes.py`, `llm/system_prompts.py` | Medium — edits within generation layer |
| **P4 — Eval + status** | Golden set, acceptance gates, implementation eval, status doc | `evaluation/*`, `docs/IMPLEMENTATION_STATUS.md` | Low |

**Acceptance criteria (Definition of Done):**
- [ ] Official-architecture corpus promoted; `retrieval_eval` shows measured recall on new implementation questions (published numbers, not targets).
- [ ] Apex emits a complete multi-file IaC bundle that passes the validator stack (real binaries) for the top-10 golden tasks; Core passes validate+checkov for single-service tasks.
- [ ] Repair loop demonstrably improves validator status across iterations, with per-iteration metrics (per the regression-guard research, full stack re-run each time).
- [ ] Lite p95 latency within existing budget (thinking stays Low; no validator loop).
- [ ] RAG and Redis workflow structures **byte-identical in behavior** — only additive registrations; `docs/IMPLEMENTATION_STATUS.md` updated with five-level classification.
- [ ] No fabricated validation results: missing binaries → `skipped`, never `passed`.

---

## Sources
- [IaCGen: Deployability-Centric IaC Generation (arXiv:2506.05623)](https://arxiv.org/abs/2506.05623)
- [Using a Feedback Loop for LLM-based IaC Generation (arXiv:2411.19043)](https://arxiv.org/html/2411.19043v1)
- [Multi-IaC-Eval benchmark (arXiv:2509.05303)](https://arxiv.org/html/2509.05303v1)
- [Does Fixing Break Security? (arXiv:2608.13404)](https://arxiv.org/html/2608.13404v2)
- [Gruntwork — AI coding assistants and IaC](https://www.gruntwork.io/blog/ai-coding-assistants-and-infrastructure-as-code-velocity-without-losing-control)
- [Spacelift — Terraform scanning tools](https://spacelift.io/blog/terraform-scanning-tools)
- [AWS Well-Architected Framework](https://aws.amazon.com/architecture/well-architected/) · [AWS Architecture Center](https://aws.amazon.com/architecture/)
- [Azure Well-Architected Framework](https://learn.microsoft.com/en-us/azure/well-architected/) · [Azure Verified Modules](https://azure.github.io/Azure-Verified-Modules/)
- [GCP Architecture Framework](https://docs.cloud.google.com/architecture/framework) · [GCP Terraform blueprints](https://docs.cloud.google.com/docs/terraform/blueprints/terraform-blueprints) · [Cloud Foundation Toolkit](https://github.com/GoogleCloudPlatform/cloud-foundation-toolkit)
- [terraform-aws-modules](https://github.com/terraform-aws-modules) · [terraform-google-modules](https://registry.terraform.io/namespaces/terraform-google-modules)
