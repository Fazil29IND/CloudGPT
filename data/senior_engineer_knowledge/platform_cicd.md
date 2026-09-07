# Senior Cloud Engineer Knowledge Base: Platform & CI/CD for Infrastructure

Domain: architecture
Difficulty: senior
Applies to: GitHub Actions, Azure DevOps, Cloud Build — running IaC like production software

## The pipeline shape (identical in spirit across all three clouds)

```
PR opened:
  fmt check → tflint → terraform validate → checkov (HIGH blocks)
  → plan (read-only identity, plan artifact attached to PR) → two-key review
PR merged (main):
  → plan again (state may have moved) → apply to DEV (auto)
  → apply to STAGING (auto after dev smoke test)
  → apply to PROD (manual approval, deploy window, destroy-gate)
Nightly:
  → drift detection plan (no apply) → alarm on any diff
```

## Identity: OIDC federation, zero long-lived keys

**GitHub Actions → AWS:**
```yaml
permissions:
  id-token: write
  contents: read
steps:
  - uses: aws-actions/configure-aws-credentials@v4
    with:
      role-to-assume: arn:aws:iam::123456789012:role/ci-deploy-prod
      aws-region: us-east-1
```
Role trust: `token.actions.githubusercontent.com` with `sub` pinned to
`repo:org/repo:ref:refs/heads/main` and environment `prod`. One role per
environment per repo — the dev token must be physically unable to touch prod
state (see PM-6).

**GitHub Actions → GCP:** `google-github-actions/auth@v2` with
`workload_identity_provider` (projects/<n>/locations/global/workloadIdentityPools/ci/providers/github).
**Azure DevOps:** service connection with workload identity federation
(Entra app registration, federated credential) — no client secrets.

## Plan/apply separation (the discipline that prevents incidents)

1. Plan runs under a **read-only** identity (`terraform plan` needs only read).
2. The plan artifact is the thing that gets applied (`terraform show -json`
   attached to the release) — apply after re-plan + diff check, not blind.
3. Destroy gate: any plan with destroys beyond a threshold (e.g. >3 resources
   or any `aws_db_instance`) requires a second approver + typed confirmation.
4. State lockdown: prod state bucket policy denies writes to non-CI roles;
   humans get `state pull` (read) for debugging only.

## Drift detection (nightly)

```bash
terraform plan -detailed-exitcode -lock-timeout=60s
# exit 0 = no drift, 1 = error, 2 = drift → file an issue with the diff
```
Alarm on exit 2 for prod stacks. Console changes are treated as defects to be
either reverted or codified within a week — the pipeline is the source of
truth, not a suggestion.

## Module publishing (internal platform)

- Registry layout: `github.com/org/terraform-<provider>-<name>` with examples/
  per use-case, semver tags, CHANGELOG enforced (release-please style).
- Consumers pin `~>`; dependabot/renovate PRs module bumps; module owners run
  the consumer test matrix (terraform tests, `.tftest.hcl`) before tagging.
- Golden pipelines per stack type (web app, data plane, event bus) so teams
  inherit guardrails by default: hardened module defaults + policy gate +
  smoke test + alarm set, all pre-wired.

## Test pyramid for infrastructure

| Level | Tool | What it proves |
|---|---|---|
| Static | tflint, validate, kubeconform | Syntax + provider-valid attributes |
| Policy | checkov/conftest on plan JSON | Security & org policy compliance |
| Unit | terraform test / Terratest (small) | Module logic (subnet math, IAM shapes) |
| Integration | ephemeral env apply + smoke | The data path actually works |
| Restore | monthly restore drill | Backups are real (an untested backup is a rumor) |

## Cloud-specific notes
- **AWS:** `terraform apply` parallelism 10 default — raise deliberately for
  large flat resources; `create_before_destroy` lifecycle on every resource
  replaced in place (SGs, IAM roles); S3 backend with `use_lockfile` (S3
  native locking) or DynamoDB legacy — one mechanism, not both.
- **Azure:** deployment stacking via `terraform_data`/state mgmt groups;
  avoid `azurerm` provider's 30-min defaults being silently swallowed — set
  explicit timeouts on long resources (AKS ~90min).
- **GCP:** APIs activation race on fresh projects (`activate_apis` in project
  factory handles propagation); org policy checks via `gcloud org-policies
  dry-run` before enforcement.
