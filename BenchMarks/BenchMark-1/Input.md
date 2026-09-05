# The Multi-Cloud Gauntlet
### A hard benchmark prompt for testing an AI coding/cloud-engineering agent (AWS + GCP + Azure)

---

## How to use this

Paste everything inside the **"TASK PROMPT — paste to agent"** section verbatim into the AI agent you're testing (Claude Code, Antigravity, Devin-style agents, etc.), pointed at empty AWS/GCP/Azure sandbox accounts with billing caps set. Grade the output with the **Scoring Rubric** at the end. Nothing here is destructive to run — but do not point it at real production accounts.

Why this is hard, by design:
- Spans **three different IAM models, three networking models, three Kubernetes flavors** — an agent that only knows AWS (the common case) will visibly stumble.
- Every phase has an **objectively checkable Definition of Done** (a command, not a vibe) — no room for the agent to claim success without evidence.
- **Teardown is graded as hard as build** — most agents happily provision and never verify they can cleanly de-provision, which is where real cloud bills come from.
- Forces **cross-cutting consistency** (naming, tagging, secrets, cost) rather than isolated single-cloud tasks — this is where agents lose points even when each phase "works" in isolation.

---

## TASK PROMPT — paste to agent

### Role

You are an autonomous senior cloud engineer. You have shell access, and credentials for one AWS account, one GCP project, and one Azure subscription (all empty sandboxes). You must complete every phase below, in order, committing working increments as you go. You may not ask the user clarifying questions — if something is ambiguous, state your assumption in a commit message and proceed.

### Scenario

"Northwind Analytics" is launching a small public API product. Leadership wants it multi-cloud from day one: primary workloads on AWS, a GCP presence for their data/BigQuery-adjacent tooling, and an Azure footprint because a client mandates it contractually. You are building the platform from an empty git repo to a fully running, observable, secured, cost-governed, and cleanly destroyable system.

### Hard constraints (rules of engagement)

1. **IaC only.** Terraform ≥ 1.7 for all infrastructure in all three clouds. Any manual console/CLI click-ops change that isn't captured in code is an instant-fail.
2. **No static long-lived cloud credentials anywhere** — not in CI, not in code, not in a `.env`. CI/CD must authenticate via OIDC federation (GitHub Actions OIDC → AWS IAM role, GCP Workload Identity Federation, Azure federated credential on an App Registration). Compute workloads use their cloud's native workload identity (IAM roles for EKS pods / Workload Identity for GKE / Managed Identity for AKS) — never mounted key files.
3. **Single monorepo**, reproducible from a clean `terraform apply` in fresh empty accounts.
4. **Must fully tear down** to zero billable resources via a single script/command, independently verified by a resource-scan (see Phase 12).
5. **Combined budget cap: $75/day** across all three clouds, with automated alerts at 50% / 80% / 100%. Exceeding this without alerting is an instant-fail.
6. **No plaintext secrets in git — including history**, not just the latest commit.
7. **No inbound `0.0.0.0/0` on anything except port 443** on public load balancers. No public SSH/RDP anywhere.
8. **Every resource tagged/labeled per the schema below.** Untagged resources fail the audit script.
9. **No single giant final commit.** Each phase must land as its own reviewable commit/PR with a working, verifiable state.

### Repository layout (required, exactly)

```
northwind-platform/
├── README.md
├── ARCHITECTURE.md              # diagram-as-code (Mermaid) + narrative
├── RUNBOOK.md
├── Makefile                      # make plan / make apply / make destroy / make verify
├── .github/workflows/
│   ├── ci.yml                    # lint, test, scan
│   └── deploy.yml                # OIDC auth + gitops trigger
├── policy/                       # OPA/Conftest rego policies
├── infra/
│   ├── aws/{identity,network,eks,data,observability}/
│   ├── gcp/{identity,network,gke,data,observability}/
│   └── azure/{identity,network,aks,data,observability}/
├── gitops/                       # Argo CD Application manifests per cluster
├── services/api/                 # the actual sample API workload + Dockerfile
└── scripts/{bootstrap,teardown,verify,cost-audit}.sh
```

### Naming & tagging standard (apply everywhere, no exceptions)

- Resource name pattern: `mcg-<cloud>-<env>-<resource>-<region-short>` e.g. `mcg-aws-prod-vpc-use1`, `mcg-gcp-prod-gke-usc1`, `mcg-az-prod-aks-eus`.
- Mandatory tags/labels on every resource: `project=northwind-gauntlet`, `environment=prod`, `owner=<team-email>`, `cost_center=platform`, `managed_by=terraform`.
- Non-overlapping CIDR blocks (needed for Phase 2's cross-cloud VPN mesh):
  - AWS VPC: `10.10.0.0/16` in `us-east-1`
  - GCP VPC: `10.20.0.0/16` in `us-central1`
  - Azure VNet: `10.30.0.0/16` in `eastus`

---

## Phases

### Phase 0 — Bootstrap & repo hygiene *(weight: 2)*

1. Initialize the monorepo with the exact layout above.
2. Set up pre-commit hooks: `terraform fmt -check`, `tflint`, `gitleaks protect`.
3. Add a root `Makefile` with `plan`, `apply`, `destroy`, `verify` targets that fan out to all three cloud directories in dependency order.
4. Write the tagging schema into a single shared Terraform module (`infra/modules/tags`) so it can't drift between clouds.

**Done when:** `make plan` runs clean against all three empty accounts with zero manual steps beyond exporting the three OIDC-derived credentials.

### Phase 1 — Identity & access foundation *(weight: 8)*

1. **AWS:** create an IAM Identity Center (or plain IAM if sandbox lacks Identity Center) admin role, plus a scoped `northwind-ci` role assumable only via GitHub OIDC (`token.actions.githubusercontent.com`), trust-policy-restricted to your specific repo + branch.
2. **GCP:** create the project (or use given one), enable required APIs, create a Workload Identity Pool + Provider trusting GitHub OIDC, and a service account impersonable only from CI, scoped to least privilege (no `roles/owner`, no `roles/editor`).
3. **Azure:** create an App Registration with a federated credential trusting your GitHub repo/branch (no client secret), and a custom RBAC role definition scoped to the resource group only — not subscription-wide `Contributor`.
4. Write one IAM policy example per cloud that demonstrates least privilege explicitly (deny wildcard actions/resources) and one comment per policy explaining the business justification for each granted permission.
5. Verify no service account/role anywhere has `*:*`, `roles/owner`, `roles/editor`, or subscription-level `Owner`/`Contributor`.

**Done when:** `grep -r "Action.*\*" infra/*/identity/` and equivalent GCP/Azure checks return nothing for prod-scoped policies, and a `terraform plan` run from a GitHub Actions job succeeds using only OIDC — no secrets in the workflow file.

### Phase 2 — Networking backbone & cross-cloud connectivity *(weight: 12)*

1. Create the three VPC/VPC/VNet with the CIDRs specified above, each with public and private subnets across ≥2 AZs/zones.
2. Stand up a site-to-site VPN mesh (AWS VPN Gateway ↔ GCP Cloud VPN ↔ Azure VPN Gateway) so all three private networks can route to each other over private IPs — no cloud's private workloads should need to leave via the public internet to reach another cloud's private workloads.
3. Set up private DNS resolution across the mesh (e.g., Route 53 Resolver forwarding rules ↔ Cloud DNS peering ↔ Azure Private DNS Resolver) so a workload in GCP can resolve `db.aws.internal.northwind` and get the AWS-side private IP.
4. Lock security groups / firewall rules / NSGs down to only the specific ports needed between the meshed CIDRs — no cross-cloud "allow all internal."
5. Public ingress: only via a load balancer per cloud, TLS-terminated, port 443 only.

**Done when:** from a private instance in AWS, `curl` to a private-IP service in GCP and Azure succeeds over the mesh; `nmap` from outside shows nothing open except 443 on the load balancers.

### Phase 3 — Container platform & service mesh *(weight: 14)*

1. Provision **EKS** (AWS), **GKE** (GCP), **AKS** (Azure) — all three with **private API endpoints** (or IP-allowlisted if the sandbox forces public endpoints), autoscaling node groups (min 1 / max 3), and workload identity wired per Phase 1.
2. Install **Istio** (or Linkerd if you justify the tradeoff in `ARCHITECTURE.md`) on all three clusters and join them into a single multi-primary mesh with mTLS enforced mesh-wide (`STRICT` mode).
3. Deploy the `services/api` sample workload to all three clusters behind the mesh, with a `VirtualService`/routing rule that can shift traffic percentage between clusters (needed for Phase 10).
4. Enforce `NetworkPolicy` / mesh authorization policies so pods can only talk to the specific services they need — default-deny otherwise.

**Done when:** `istioctl proxy-status` across all three clusters shows synced sidecars; a request from a pod in GKE to the AKS-hosted service succeeds over mTLS and fails if mTLS is disabled on either side; `kubectl auth can-i --list` for the workload's service account shows only the permissions it actually uses.

### Phase 4 — CI/CD & GitOps *(weight: 10)*

1. GitHub Actions pipeline: on PR, run `terraform fmt/validate`, `tflint`, `tfsec`/`checkov`, `gitleaks`, container image build + `trivy` scan, and OPA/Conftest policy checks (Phase 8) — **all as required status checks that block merge.**
2. On merge to `main`, authenticate to all three clouds via OIDC only, run `terraform apply` per cloud in dependency order (identity → network → cluster → data → observability), then push the new image tag as a GitOps commit that Argo CD (installed on all three clusters) reconciles.
3. Pipeline must be idempotent — running it twice with no changes produces "no changes" plans, not drift or duplicate resources.

**Done when:** a PR with a deliberately broken IAM policy (wildcard action) is auto-blocked by the OPA check with a human-readable failure message, and a clean PR flows all the way to all three clusters showing the new image running, with zero manual `kubectl apply` or console steps.

### Phase 5 — Secrets management *(weight: 6)*

1. Stand up a central secret store reachable from all three clouds over the private mesh (HashiCorp Vault is the reference answer; a justified per-cloud-native alternative federated via the mesh is acceptable if documented).
2. Workloads fetch secrets at runtime via their workload identity (no secret ever lands in an env var baked into an image or a Kubernetes `Secret` object in plaintext at rest — use a CSI secrets driver or equivalent).
3. Rotate a test secret and prove propagation to a running pod in all three clusters without a redeploy.

**Done when:** `gitleaks detect --source . --log-opts="--all"` (full history, not just HEAD) returns zero findings, and `kubectl get secret -o yaml` on the workload's namespace shows no plaintext credential values.

### Phase 6 — Data layer & cross-cloud backup *(weight: 9)*

1. Primary datastore: managed Postgres in AWS (RDS), encrypted at rest with a customer-managed KMS key, private-subnet only.
2. Automated encrypted backups replicated to object storage in **both** other clouds (S3 → GCS and S3 → Azure Blob, or your own justified replication path), each encrypted with that cloud's own customer-managed key (not the source key copied over).
3. Backup retention: 7 daily, 4 weekly. Document RPO achieved.

**Done when:** deleting the primary RDS instance and restoring from the GCS-replicated backup (simulating an AWS-region-level loss) succeeds and the API serves correct data again within the RTO defined in Phase 10.

### Phase 7 — Observability & SLOs *(weight: 8)*

1. Deploy an OpenTelemetry Collector per cluster, exporting metrics/logs/traces to a single unified backend (self-hosted Prometheus+Grafana+Loki+Tempo, or a single managed backend that ingests all three clouds — do not run three disconnected Grafanas).
2. Define at least 3 SLOs for the API: availability ≥ 99.9%, p95 latency < 300ms, error rate < 1%. Wire burn-rate alerts for each.
3. One dashboard must show all three clouds' instances of the workload side by side.

**Done when:** killing a pod in one cluster shows the failure show up on the shared dashboard within 30 seconds, and a synthetic load test that pushes error rate above 1% fires the configured alert.

### Phase 8 — Security & policy-as-code *(weight: 12)*

1. OPA/Conftest policies (from Phase 4) must reject: wildcard IAM actions/resources in prod, security groups/firewalls open to `0.0.0.0/0` on anything but 443, unencrypted storage buckets/disks, untagged resources, and container images running as root.
2. Add IaC scanning (`tfsec`/`checkov`) and container scanning (`trivy`) as blocking CI gates, not advisory.
3. Put a WAF (or cloud-native equivalent) in front of every public load balancer with at minimum a rate-limit rule and an SQLi/XSS managed rule set.

**Done when:** a PR that opens port 22 to `0.0.0.0/0` is auto-rejected by CI with the specific policy name and line quoted in the failure output.

### Phase 9 — Cost governance *(weight: 4)*

1. Budgets + alerts (50/80/100%) in all three clouds, combined effective cap $75/day.
2. A scheduled job (`scripts/cost-audit.sh` + a CI cron) that flags any resource missing the mandatory tags, and any resource with zero traffic/utilization for 48h.
3. A single combined cost report (even a simple generated markdown/CSV pulling from all three billing APIs) checked into `ARCHITECTURE.md` or a `COSTS.md`.

**Done when:** the audit script run against the live environment reports $0 untagged spend and the projected daily cost is under $75, shown with real numbers, not estimates.

### Phase 10 — Resilience / failover drill *(weight: 10)*

1. Simulate an AWS `us-east-1` outage (e.g., scale the AWS cluster's workload to zero / block its LB via a chaos script — do not actually rely on the cloud provider having a real outage).
2. Using the Istio traffic-shifting from Phase 3, automatically (not manually) shift 100% of traffic to the GCP and/or Azure clusters.
3. Define and hit: **RTO < 10 minutes**, **RPO < 15 minutes** (using the Phase 6 backups). Document actual measured numbers, not targets.

**Done when:** a scripted drill (`scripts/chaos-drill.sh`) that kills the AWS path, waits, and then curls the public endpoint shows continuous < 1% error rate throughout, with a timestamped log proving RTO/RPO were met.

### Phase 11 — Documentation *(weight: 3)*

1. `ARCHITECTURE.md`: a Mermaid diagram of the full cross-cloud topology (networking, clusters, data flow, mesh) plus a short narrative and the key tradeoffs you made and why.
2. `RUNBOOK.md`: on-call steps for "the public API is down," "a cloud region is degraded," and "rotate a leaked secret."

**Done when:** a person unfamiliar with the repo can follow `RUNBOOK.md` to correctly diagnose a seeded failure within 10 minutes.

### Phase 12 — Full teardown *(weight: 3, plus hard gate — see below)*

1. A single command (`make destroy` / `scripts/teardown.sh`) tears down all resources in all three clouds in the correct dependency order (reverse of creation).
2. A verification scan (`scripts/verify-teardown.sh`) queries all three clouds' resource-listing APIs and confirms **zero** resources remain tagged `project=northwind-gauntlet` — including things agents commonly forget: DNS records, KMS keys (schedule deletion), load balancer IPs, VPN gateways, CloudWatch/Log Analytics log groups, container registries, and backup snapshots.

**Done when:** `verify-teardown.sh` exits 0 with an explicit "0 resources found" line for each of the three clouds.

---

## Instant-fail conditions (override the score below — cap total at 40/100 if any occur)

- Any manual console change not captured in Terraform.
- A static, long-lived cloud access key/secret anywhere in the repo, CI config, or a running container.
- A plaintext secret anywhere in git history.
- Any public ingress port other than 443 open to `0.0.0.0/0`.
- Teardown leaves any billable resource running, verified by `verify-teardown.sh`.
- Budget alerting missing or the environment exceeds $75/day without an alert firing.

## Scoring rubric (100 points total)

| Phase | Weight |
|---|---|
| 0 — Bootstrap & repo hygiene | 2 |
| 1 — Identity & access foundation | 8 |
| 2 — Networking backbone & cross-cloud connectivity | 12 |
| 3 — Container platform & service mesh | 14 |
| 4 — CI/CD & GitOps | 10 |
| 5 — Secrets management | 6 |
| 6 — Data layer & cross-cloud backup | 9 |
| 7 — Observability & SLOs | 8 |
| 8 — Security & policy-as-code | 12 |
| 9 — Cost governance | 4 |
| 10 — Resilience / failover drill | 10 |
| 11 — Documentation | 3 |
| 12 — Full teardown | 2 (+ hard gate above) |
| **Total** | **100** |

Each phase scores 0 (not done), partial (done but Definition-of-Done check fails or is unverifiable), or full (Definition-of-Done check passes with evidence — logs/output pasted, not claimed).

## Submission format

The agent should end its run with a single summary comment/commit containing:
1. A table of all 13 phases with status (done / partial / failed) and the exact command output used as evidence for each.
2. Actual measured RTO/RPO/cost numbers, not targets.
3. Any deliberate deviation from these instructions, with justification.
