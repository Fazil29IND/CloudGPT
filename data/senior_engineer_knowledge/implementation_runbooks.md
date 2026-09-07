# Senior Cloud Engineer Knowledge Base: Implementation Runbooks (Provision → Verify → Operate)

Domain: architecture
Difficulty: senior
Applies to: AWS, GCP, Azure — the operational sequence after design, before "done"

A design is not implemented until: it is provisioned, a smoke test proves the
data path, alarms exist for its failure modes, and a rollback is documented.
These runbooks are the standard sequence per service family.

## Runbook 1: Public web app behind ALB (AWS)

**Provision:** VPC (2+ AZ, public+private subnets) → ALB in public subnets
(HTTP→HTTPS redirect, TLS ACM cert, access logs to S3) → ASG or ECS service in
private subnets behind target group with health checks on a real endpoint
(`/healthz`), NOT `/` → SGs: ALB SG allows 443 from 0.0.0.0/0, app SG allows
only ALB SG on app port, DB SG allows only app SG.

**Verify (smoke test, in order):**
```bash
# 1. DNS resolves
dig +short app.example.com
# 2. TLS is valid and redirect works
curl -sI http://app.example.com | head -3          # expect 301 → https
curl -sI https://app.example.com/healthz           # expect 200
# 3. Data path: app can reach DB (run from app host / ECS exec)
nc -zv orders-db.cluster-xyz.us-east-1.rds.amazonaws.com 5432
# 4. Scaling works: hammer the endpoint, watch ASG step out, then in
aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names app-asg \
  --query 'AutoScalingGroups[0].[DesiredCapacity,Instances[].HealthStatus]'
```

**Operate:** alarms on target 5xx rate, ALB unhealthy-host count, ASG scaling
activity; access to instances only via SSM Session Manager (no bastion, no
22/3389 inbound); patching via immutable AMI rotation, never in-place.

**Rollback:** deploy = launch template version pinning; rollback = redeploy
previous AMI/revision. DB changes are separate releases (expand-migrate-contract).

## Runbook 2: Private RDS/Aurora with secrets rotation

**Provision:** DB subnet group (private subnets only), KMS CMK encryption,
security group allowing only the app SG, deletion protection, 14-day backups,
Performance Insights on, credentials in Secrets Manager.

**Verify:**
```bash
# Endpoint resolves ONLY privately
dig +short orders-db.cluster-xyz.us-east-1.rds.amazonaws.com   # private IP, from VPC
aws rds describe-db-instances --db-instance-identifier orders-db \
  --query 'DBInstances[0].[StorageEncrypted,DeletionProtection,BackupRetentionPeriod]'
# Connect as the app (never with master)
psql "host=orders-db... user=app_user sslmode=require" -c "select 1"
# Prove rotation works
aws secretsmanager rotate-secret --secret-id prod/orders-db/app
```

**Operate:** CPU/conn alarms, deadlocks, replica lag (readers); change windows
for engine upgrades; never `apply_immediately`; test restores monthly
(`restore to point in time` into a scratch instance, run app smoke test
against it) — an untested backup is a rumor.

## Runbook 3: AKS cluster with workload identity

**Provision:** Baseline AKS: private API server or authorized IP ranges, user
node pool (system pool tainted), Azure CNI overlay or kubenet, AAD workload
identity (no pod-level SPN secrets), Azure Policy add-on, Azure Monitor
container insights, Key Vault CSI driver.

**Verify:**
```bash
az aks show -g rg-core -n prod-aks \
  --query '[apiServerAccessProfile.enablePrivateCluster, networkProfile.networkPolicy, securityProfile.workloadIdentity.enabled]'
# Prove workload identity: a test pod fetches a KV secret via its identity
kubectl run witest --rm -it --image=mcr.microsoft.com/azure-cli \
  --overrides='{"spec":{"serviceAccountName":"wit-sa"}}' -- \
  az keyvault secret show --vault-name prod-kv --name test-secret --query value
# Prove policy: deploy a privileged pod, expect rejection by Azure Policy
```

**Operate:** upgrade cadence = control plane first, node pools second, add-ons
third, one minor per window; use `kubectl drain`-safe PDBs before any node
pool rotation; alert on cluster autoscaler failures, not just pod restarts.

## Runbook 4: GKE private cluster with shared VPC

**Provision:** project-factory service project attached to host VPC, private
nodes with authorized networks, private Google Access subnets, Workload
Identity (GCP IAM federation), Binary Authorization on, Cloud Audit Logs
(Data Access for GCS/GCR at minimum) exported to log bucket.

**Verify:**
```bash
gcloud container clusters describe prod-gke --region us-central1 \
  --format='value(privateClusterConfig.enablePrivateNodes, masterAuth)' 
# Workload identity proof: pod authenticates as its KSA→GSA mapping
kubectl run gsatest --rm -it --image=google/cloud-sdk:slim \
  -- gcloud auth list   # expect the mapped GSA identity
# Private endpoints only
gcloud compute instances list --filter='name~gke' --format='value(networkInterfaces[0].accessConfig)'  # expect empty
```

**Operate:** release channels (REGULAR), surge upgrades with PDBs, IP range
capacity checks before upgrade (pods CIDR exhaustion blocks upgrades), quota
checks before scale-out events.

## Runbook 5: Serverless API (Lambda / Cloud Run / Functions)

**Provision (Lambda example):** function in private subnets (or no VPC when no
VPC resource is needed — VPC-attached Lambda pays cold-start ENI latency),
reserved concurrency for downstream protection, DLQ + on-failure destination,
CloudWatch alarms on `Errors` + `Throttles` + p95 `Duration`, API Gateway with
access logging + WAF.

**Verify:**
```bash
aws lambda get-function-configuration --function-name orders-api \
  --query '[Timeout,MemorySize,ReservedConcurrentExecutions,DeadLetterConfig]'
aws lambda invoke --function-name orders-api --payload '{"ping":1}' out.json && cat out.json
# Idempotency: same request id twice → no double side effect
```

**Operate:** concurrency math: downstream DB max connections ≤ reserved
concurrency; DLQ redrive runbook; version+alias traffic shifting for canary
(10% → 50% → 100%) with automatic rollback on alias-error alarm.

## Cross-cutting "definition of implemented"
1. Everything via IaC; console changes treated as drift, detected by a nightly plan.
2. Smoke test in CI after apply, not a manual check.
3. Alarms wired to the failure modes identified at design time (each SPOF has one).
4. Deletion/teardown path tested once in staging (orphaned RGs/subscriptions are a debt bomb).
5. Access: no standing admin; deployment identities are OIDC-federated, scoped per environment.
