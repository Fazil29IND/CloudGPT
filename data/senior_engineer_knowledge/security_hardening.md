# Senior Cloud Engineer Knowledge Base: Security Hardening Aligned to IaC Scanners

Domain: architecture
Difficulty: senior
Applies to: AWS, GCP, Azure — findings classes that Checkov/Trivy/KICS flag, with the correct Terraform fix

Every pattern below maps to a scanner rule class. Ship hardened defaults in
modules so 90% of scanner findings never occur.

## S3 / Cloud Storage (CKV_AWS_*, storage checks)
```hcl
resource "aws_s3_bucket_public_access_block" "this" {
  bucket                  = aws_s3_bucket.this.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id
  rule { apply_server_side_encryption_by_default {
    sse_algorithm = "aws:kms"; kms_master_key_id = aws_kms_key.this.arn } }
}
resource "aws_s3_bucket_versioning" "this" { bucket = aws_s3_bucket.this.id
  versioning_configuration { status = "Enabled" } }
resource "aws_s3_bucket_logging" "this" { bucket = aws_s3_bucket.this.id
  target_bucket = aws_s3_bucket.logs.id; target_prefix = "s3-access/" }
```
GCS equivalent: `public_access_prevention = "ENFORCED"`,
`uniform_bucket_level_access = true`, CMEK via `encryption { default_kms_key_name }`.

## Instance metadata / SSRF (CKV_AWS_8, IMDSv2)
```hcl
resource "aws_launch_template" "app" {
  metadata_options {
    http_endpoint = "enabled"    # required for IMDS, but:
    http_tokens   = "required"   # IMDSv2 ONLY — blocks SSRF token theft
    http_put_response_hop_limit = 1
  }
}
```
This is the classic SSRF→credentials chain ( Capital One class). Hop limit 1
unless containers need IMDS (then 2 + SG tightening).

## Encryption & KMS (CKV_AWS_19/65, CMK classes)
- EBS/RDS/S3/SQS/SNS: explicit `server_side_encryption_configuration`/`storage_encrypted = true` with a **CMK**, not AWS-managed keys, for regulated data.
- KMS key policy grants: workload role + service principal + break-glass admin; key rotation (`enable_key_rotation = true`) for AWS-managed rotations; document CMK blast radius.

## Network exposure (0.0.0.0/0 classes)
```hcl
# Wrong: ingress from anywhere on SSH/RDP
cidr_blocks = ["0.0.0.0/0"]
# Right: SSM replaces SSH entirely; no inbound management ports
resource "aws_security_group" "app" {
  ingress { from_port = 443; to_port = 443; protocol = "tcp"
            security_groups = [aws_security_group.alb.id] }  # source = the ALB SG, not CIDR
}
```
- Azure: `ip_security_restriction` with `action=Allow` from specific CIDRs; deny-all default; NSG flow logs on.
- GCP: no default-allow firewall rules; use network tags/targeted service accounts; Private Google Access on data subnets.

## Kubernetes (CKV_K8S_*)
- `runAsNonRoot: true`, `readOnlyRootFilesystem: true`, drop `ALL` capabilities, `allowPrivilegeEscalation: false` — the standard pod-hardening block:
```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 10001
  seccompProfile: { type: RuntimeDefault }
containers:
  - securityContext:
      allowPrivilegeEscalation: false
      readOnlyRootFilesystem: true
      capabilities: { drop: ["ALL"] }
    volumeMounts: [{ name: tmp, mountPath: /tmp }]
```
- NetworkPolicies default-deny both directions per namespace, then allow-list explicitly; kubeconform validates schema, Checkov flags the missing policy.

## Secrets & identities
- Never plaintext secrets in IaC: Secrets Manager/Key Vault/Secret Manager refs.
- CI/CD: OIDC federation (GitHub OIDC→AWS role, Workload Identity Federation→GCP, Entra workload identity) — zero long-lived keys (scanner classes CKV_AWS_* on provider blocks with `access_key` present).
- Read-scoped task roles per workload; no `*` actions/resources in prod policies (Checkov flags wildcards — treat as errors, not warnings).

## Logging & audit completeness (the "prove it" layer)
| Provider | Minimum audit set |
|---|---|
| AWS | CloudTrail org trail (all regions, log-file validation, KMS), S3 access logs, VPC flow logs (rejected + accepted), RDS Performance Insights, ELB access logs |
| Azure | Diagnostic settings to LA workspace for P0 services, activity log export, NSG flow logs, Defender plans for data services |
| GCP | Cloud Audit Logs incl. Data Access for storage/GCS, log sink to bucket + BigQuery, VPC flow logs on data subnets |

## Policy-as-code gate (CI)
```bash
checkov -d infra/ --framework terraform --hard-fail-on HIGH --skip-check CKV_AWS_145  # documented exceptions only
tflint --recursive
terraform validate
```
Exceptions live in a `checkov.yml` with a linked ticket and expiry — scanner
noise without an expiry is how real findings get ignored.

## OPA Rego v1 Guardrails (Zero-Trust Standard)
All Open Policy Agent rules must be written with `import rego.v1` and use modern `contains ... if` syntax:
```rego
package terraform.security

import rego.v1

default allow := false

# Enforce all 4 S3 block public access flags
deny contains msg if {
    some resource in input.resource_changes
    resource.type == "aws_s3_bucket_public_access_block"
    v := resource.change.after
    not (v.block_public_acls == true and v.block_public_policy == true and v.ignore_public_acls == true and v.restrict_public_buckets == true)
    msg := sprintf("S3 bucket '%v' must enable all 4 public access block flags", [resource.address])
}

# Deny 0.0.0.0/0 ingress
deny contains msg if {
    some resource in input.resource_changes
    resource.type == "aws_security_group"
    some ingress in resource.change.after.ingress
    some cidr in ingress.cidr_blocks
    cidr == "0.0.0.0/0"
    msg := sprintf("Security group '%v' must not permit 0.0.0.0/0 ingress", [resource.address])
}

# Deny IAM wildcard actions
deny contains msg if {
    some resource in input.resource_changes
    resource.type == "aws_iam_policy"
    doc := json.unmarshal(resource.change.after.policy)
    some stmt in doc.Statement
    stmt.Effect == "Allow"
    stmt.Action == "*"
    msg := sprintf("IAM policy '%v' must not allow wildcard '*' actions", [resource.address])
}

allow if count(deny) == 0
```

## Compliance Claim Governance (PCI-DSS v4)
A `Compliance = "PCI-DSS-v4"` tag alone is NOT compliance. Never emit compliance tags unless backed by executable controls:
1. Encryption: Customer-managed KMS key with automated rotation (`enable_key_rotation = true`).
2. Network Isolation: Private VPC subnets, disabled or restricted EKS public API endpoint (`endpoint_private_access = true`), no `0.0.0.0/0` security group rules.
3. Storage Security: Full S3 block public access on all buckets, server-side encryption via KMS CMK.
4. Audit Trails: Multi-region AWS CloudTrail with log-file validation enabled and CloudWatch Logs delivery.
5. Workload Hardening: Non-root execution (`runAsNonRoot: true`), read-only root filesystems, and dropped Linux capabilities.
If these controls are not delivered in the bundle, omit compliance claims and use operational tags (`Environment`, `ManagedBy`, `Project`).
