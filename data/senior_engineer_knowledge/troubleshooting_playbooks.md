# Senior Cloud Engineer Knowledge Base: Production Troubleshooting Playbooks

Domain: troubleshooting
Difficulty: senior
Applies to: AWS, GCP, Azure, Kubernetes

## Kubernetes Crash Triage

### OOMKilled
Symptoms: container exit code 137, `LAST STATE: Terminated (Reason: OOMKilled)`.
Diagnosis:
1. `kubectl describe pod <pod>` — confirm OOMKilled and exit code 137.
2. Compare memory usage vs limits: `kubectl top pod <pod> --containers`.
3. JVM apps: check heap vs container limit (MaxRAMPercentage), not -Xmx alone.
4. Node-level pressure: `kubectl describe node` — MemoryPressure condition.
Fixes:
- Raise memory limit with headroom (p95 usage × 1.3 minimum).
- Fix leaks: heap profiles (pprof), off-heap/native leaks (glibc arenas — set
  `MALLOC_ARENA_MAX=2`), unbounded caches.
- For bursty workloads use VPA in recommendation mode before raising limits.

### CrashLoopBackOff
Diagnosis:
1. `kubectl logs <pod> --previous` — the previous container's final output.
2. `kubectl describe pod` — probe failures, command exit codes, config mounts.
3. Common causes: bad env/config/secret mount, failed DB migration at boot,
   liveness probe too aggressive (initialDelaySeconds too low), port mismatch.
Fixes:
- Liveness probe: add startupProbe so slow-boot apps are not killed early.
- Separate readiness from liveness; never use a probe that hits dependencies.
- Add exponential backoff config to boot-time migrations; make retries idempotent.

### ImagePullBackOff / ErrImagePull
1. Verify image exists: `kubectl get events`, try pull from a node.
2. Registry auth: check `imagePullSecrets`, ECR token refresh (ECR tokens
   expire after 12h — use ECR credential provider or IRSA), GCR workload
   identity, ACR managed identity.
3. Rate limiting: Docker Hub anonymous pulls — use a registry mirror or
   authenticated pulls with sidecar credential refresh.

### Evicted Pods
Node memory/disk pressure evicts pods. Check `kubectl describe node` events,
kubelet eviction thresholds (`nodefs.available<10%`), and clean up
logs/emptyDir/unused images (`crictl rmi --prune`). Set PriorityClasses so
critical workloads evict last.

## AWS IAM Cross-Account Role Trust

### Symptom: "User is not authorized to perform: sts:AssumeRole" or silent deny
1. Trust policy on the target role: principal must be the exact account/user/
   role ARN; conditions must match (`sts:ExternalId`, `aws:PrincipalOrgID`,
   `aws:SourceAccount` for service roles).
2. Caller identity permissions: `sts:AssumeRole` allowed on the role ARN.
3. SCPs and permission boundaries in BOTH accounts can deny the action.
4. Same-account role confusion: if caller and target are the same account,
   both trust policy AND identity policy must allow (explicit trust principal).
5. Check with the policy simulator:
   `aws iam simulate-principal-policy --policy-source-arn <caller> --action-names sts:AssumeRole --resource-arns <role>`
6. Service-linked assumption (Lambda → role): role trust principal must be
   `lambda.amazonaws.com` with `aws:SourceAccount` condition to prevent
   confused-deputy.

### S3 Access Denied Decision Tree
1. Identity policy allows s3:GetObject? 2. Bucket policy allows (or doesn't
   deny)? 3. SCP allows? 4. Block Public Accounting settings irrelevant for
   signed requests but check `aws:SecureTransport` conditions. 5. Object
   ownership mismatch (ACLs disabled vs enabled) — prefer Bucket owner
   enforced. 6. KMS key policy must grant the caller if the object is
   SSE-KMS encrypted — this is the most common hidden cause.

## GCP VPC Service Controls Perimeter Violations

### Symptom: "Request is prohibited by organization's policy. vpcServiceControlsUniqueIdentifier: ..."
1. Identify the violating client: paste the unique identifier into the VPC SC
   Troubleshooter (Cloud Console) or `gcloud access-context-manager perimeters`.
2. Common causes and fixes:
   - On-prem/VPN traffic: add the source IP range to an ingress rule of the
     access policy level (access level with `ipSubnetworks` or device policy).
   - Service agent not in perimeter: add `serviceAccount` (Google-managed
     service agents like `<project-number>@cloudservices.gserviceaccount.com`
     and `@gcp-sa-*.iam.gserviceaccount.com`) to the perimeter access list.
   - Cross-perimeter data move: define a bridge perimeter or restructure so
     both projects sit in one perimeter stage.
   - Private Google Access off: enable on the subnet, and ensure `private.googleapis.com`
     (199.36.153.8/30) or `restricted.googleapis.com` (199.36.153.4/30) DNS
     records exist for restricted perimeters.
3. Dry-run mode: apply the perimeter with dry-run spec first and review logs
   for a week before enforcement.

### GCP SA Impersonation Errors
"Permission 'iam.serviceAccounts.actAs' denied" — grant the caller
`roles/iam.serviceAccountUser` on the SERVICE ACCOUNT (project-level grants
are over-broad), or `serviceAccountTokenCreator` for token minting.

## Azure Private DNS & Private Endpoint Resolution

### Symptom: Private endpoint resolves to public IP, or intermittent timeouts
1. The Private DNS Zone (`privatelink.blob.core.windows.net` etc.) must be
   linked to EVERY VNet that needs resolution — hub-linked zones do NOT
   propagate through peering automatically for the spoke's VMs unless linked.
2. Register `privatelink.<region>.backup.windowsazure.com` style zones per
   service; each service has its own zone names — missing zone = public path.
3. `nslookup <resource>.blob.core.windows.net` from the failing VM must
   return a 10.x private IP. If it returns public IP: check the VNet DNS
   servers (custom DNS must forward Azure zones to 168.63.129.16).
4. Cross-tenant/private endpoint in hub: associate the private endpoint with
   a private DNS zone group, or manage the zone manually with the endpoint's
   NIC IP as an A record.
5. On-prem resolvers: forward `<region>.privatelink` zones to the hub DNS
   resolver (Azure DNS Private Resolver inbound endpoint) — never split-horizon
   only inside Azure.

### Azure NSG / UDR Gotchas
- Asymmetric routing: if a UDR forces egress via firewall but the flow was
  initiated inbound through a public LB, health probes fail — add UDR
  exceptions for load balancer data paths or use Standard LB with HA ports.
- Private endpoint NSGs: use application security groups + service tags
  (`AzureCloud.<region>`) rather than broad internet rules.

## Redis / Memory Store Saturation

### Symptoms: rising latency, evictions, connection storms
1. Memory: `INFO memory` — check `used_memory` vs `maxmemory`, fragmentation
   ratio (>1.5 signals fragmentation; consider activedefrag). Evictions on a
   cache are normal; evictions on a session store are an outage.
2. Connections: `INFO clients` — client libraries leak connections when
   instances are recycled without closing; set timeouts and pool max size,
   and use lazy reconnect with jitter to avoid thundering reconnects.
3. Big keys: `redis-cli --bigkeys`; split sets/hashes larger than ~1MB;
   paginate with SCAN, never KEYS in production.
4. Hot keys: enable key-space notifications or proxy metrics; replicate hot
   reads into a local LRU cache.
5. AWS ElastiCache cluster mode: hash-tag keys (`{user123}:profile`) to keep
   multi-key operations on one slot; cross-slot commands fail with CROSSSLOT.
6. Persistence stalls: RDB fork spikes on large datasets — use AOF
   everysec or scale up memory to reduce fork pressure.

## Escalation Checklist (Any Incident)

1. Stabilize first (rollback, scale out, fail over) — root cause later.
2. Capture evidence before it rotates: logs snapshot, flame graphs, metrics.
3. Communicate on a cadence (every 15–30 min) even without new information.
4. Write the timeline during the incident, not after.
5. Postmortem: blameless, with at least one systemic action item (alerting,
   runbook, architecture) — never just "be more careful."
