# Senior Cloud Engineer Knowledge Base: Failure-Class Postmortems

Domain: troubleshooting
Difficulty: senior
Applies to: production incidents across AWS, GCP, Azure — the lessons that only come from having implemented and broken systems

Each entry: timeline pattern → detection → fix → prevention. These are the
recurring classes; the specifics vary, the class rarely does.

## PM-1: Quota exhaustion during scale-out
**Timeline:** Black Friday load → autoscaler requests 40 new instances →
`vCPU limit exceeded` → new instances fail → alarms fire on traffic, not on
quota → incident discovered by customers.
**Detection:** alarm on *remaining* quota headroom (CloudWatch + Service
Quotas, GCP `compute.googleapis.com/quota`, Azure `Quota Usage` metrics), not
on errors after the fact.
**Fix:** request limit increase (minutes for regional CPU), temporarily
redistribute load to less-loaded regions/instances.
**Prevention:** quota headroom checks as a CI step before scale-out events
(campaigns, launches); IaC modules that compute worst-case footprint; service
quotas owned by a named engineer per account.

## PM-2: Terraform state corruption / concurrent apply
**Timeline:** Two engineers (or a pipeline and an engineer) apply to the same
stack → concurrent writes → state file divergence → resources orphaned,
drift "fixed" by deleting live infrastructure.
**Detection:** `terraform plan` shows surprise destroys; DynamoDB lock table
shows stale locks (auto-unlock after lockfile TTL is NOT safe by default).
**Fix:** `terraform state pull` from backup (S3 versioning — this is why state
bucket versioning is mandatory); `state push` after diffing; manually re-import
orphaned resources (`terraform import`) instead of destroy/recreate.
**Prevention:** remote state + locking (S3+DynamoDB / GCS+no-lock workaround→
use `terraform plan` in CI with `-lock-timeout`), one state per component per
environment, all applies through CI, humans get read-only on prod state.

## PM-3: IAM/credential lockout (locked out of your own cloud)
**Timeline:** SCP/policy "hardening" removes the last admin path → no
principal can act → root/org-admin recovery only.
**Detection:** simulate BEFORE deploy: AWS IAM Access Analyzer policy
simulation, `gcloud policy-troubleshoot`, Azure `az role assignment
list --include-inherited` + PIM activation test.
**Fix:** AWS Organizations management account emergency path (root with MFA),
Azure GDAP/global admin, GCP org recovery via support.
**Prevention:** break-glass accounts (2 per org, excluded from all policies,
credentials sealed + alarmed); every IAM change ships with a policy simulation
in CI; changes reviewed by a second engineer — IAM is a two-key change.

## PM-4: DNS split-horizon / private endpoint resolution failure
**Timeline:** Privatelink/Private Service Connect deployed → app cannot
resolve endpoint → fallback to public IP → SGs deny → outage that "no one
changed networking" for.
**Detection:** resolve from INSIDE the VPC, not your laptop:
`nslookup <endpoint> <vpc-dns>`; VPC flow logs showing REJECT to public IP.
**Fix:** correct private hosted zone / private DNS association
(`aws servicecatalog`-style association, Azure `privatelink.*` zone linked to
VNet, GCP DNS peering zones) + SG allow to the endpoint's ENI.
**Prevention:** endpoint DNS checks in the smoke-test stage of the runbook;
standardized private-DNS module per provider; never allow `.public` fallback
in app config for internal services.

## PM-5: KMS dependency loop (chicken-and-egg outage)
**Timeline:** Bucket/DB encrypted with CMK → key policy grants decrypt only to
a role whose trust depends on a resource inside the encrypted service →
rotation/recreation of the key makes data unreadable → restore impossible
until key restored.
**Detection:** `aws kms describe-key` key-state + CloudTrail Decrypt denials
after any key change.
**Fix:** recover key from deletion window (7–30 days) — this is why key
deletion window is set to the max (30) in prod; rebuild key policy granting
the service principal directly.
**Prevention:** never delete CMKs protecting stores; key policies grant BOTH
the workload role AND the service principal; key admins ≠ key users separation;
documented key inventory with blast-radius mapping.

## PM-6: Stateful app destroyed by stateless pipeline
**Timeline:** `terraform destroy` on "staging" pipeline runs against prod
workspace (tfvars mix-up / CI matrix bug) → RDS/S3 deleted → `force_destroy`
on bucket made it instant and unrecoverable.
**Detection:** pipeline guard: `terraform plan` destroy-detection gate (fail on
`X to destroy` beyond threshold).
**Fix:** point-in-time restore (PITR) for DBs; versioned objects + replication
for buckets; declare data-loss incident, restore order: secrets → datastores →
compute → DNS.
**Prevention:** `prevent_destroy` lifecycle on stateful resources; workspace/
environment name echo + manual approval gate for any apply containing
destroys; `force_destroy=false` in prod modules; separate CI identities per
environment (prod pipeline token cannot target staging and vice versa).

## PM-7: Egress cost surprise (FinOps postmortem)
**Timeline:** Month-end bill 8× normal → new analytics job cross-AZ reads a
replica 100TB/day; nobody attributed the transfer.
**Detection:** VPC flow logs + CUR/Azure Cost Management/GCP billing export
grouped by transfer type; alert on week-over-week data-processing-egress delta.
**Fix:** same-AZ reads, cache tier, or private endpoints (still cross-AZ
charged — same-AZ is the only free path); commitment for egress where offered.
**Prevention:** architecture reviews include a data-path map (which AZ, which
region, NAT or not); NAT gateway traffic is always tagged per-environment.

## PM-8: Silent regional dependency failure
**Timeline:** us-east-1 S3 control-plane hiccup → clusters in eu-west-1 fail
(image pulls, IAM instance profile lookups) — "regional" architectures that
still depend on a single-region control plane.
**Detection:** dependency map (images in eu-west ECR mirrors, IAM is global on
AWS but profile resolution hits regional endpoints); synthetic canaries per
dependency, not just per user path.
**Fix:** fail static (serve stale) where possible; pre-pulled images on nodes;
graceful degradation flags.
**Prevention:** every architecture review answers "what if region X control
plane is down for 2h" explicitly; dependency-free bootstrap (local registry
mirror, cached AMIs) for critical fleets.
