# Senior Cloud Engineer Knowledge Base: Architecture & Resilience Patterns

Domain: architecture
Difficulty: senior
Applies to: AWS, GCP, Azure

## Multi-Region Active-Active Architecture

### When to Choose Active-Active
Choose active-active when RTO must approach zero and read scalability across
geographies matters. Choose active-passive (pilot light or warm standby) when
write conflicts, licensing, or cost make simultaneous writes unacceptable.

### Reference Topology (AWS)
1. Route 53 latency-based routing with health checks per regional endpoint.
2. Global Accelerator for static anycast IPs with failover in under 30 seconds.
3. Regional ALB → Auto Scaling groups spanning 3 AZs per region.
4. Aurora Global Database: primary cluster writes, secondary clusters serve
   local reads with typical replication lag under 1 second.
5. DynamoDB Global Tables for multi-region active-active NoSQL with
   last-writer-wins conflict resolution.
6. S3 Cross-Region Replication for objects; DynamoDB streams for cache invalidation.
7. Region-isolated deployment pipelines (CodePipeline per region, phased rollout).

### Reference Topology (GCP)
1. Global Cloud Load Balancing (anycast) with backend services in multiple regions.
2. Cloud Spanner multi-region configuration for strongly consistent writes.
3. Bigtable replication for high-throughput time-series across regions.
4. Cloud Storage dual-region or multi-region buckets.
5. Global VPC with subnet-per-region; no inter-region peering required.

### Reference Topology (Azure)
1. Azure Front Door with origin groups and priority/weighted routing.
2. Cosmos DB multi-region writes with Conflict Resolution Policy
   (Last-Writer-Wins using a monotonic timestamp path).
3. Azure SQL with auto-failover groups (readable secondary in paired region).
4. Traffic Manager as a fallback DNS-level control plane.

### Write Conflict Handling
- Single-writer-per-entity: partition writes by shard key region ownership.
- CRDTs or last-writer-wins with version vectors for mergeable state.
- Idempotent, order-tolerant event processing when replicating via queues.
- Accept eventual consistency in secondary regions; surface staleness in UI.

## RPO / RTO Engineering

### Definitions and Budget Decomposition
RPO (Recovery Point Objective): maximum tolerable data loss measured in time.
RTO (Recovery Time Objective): maximum tolerable recovery duration.
Decompose both across layers; the system RTO equals the slowest layer:

| Layer | RPO contribution | RTO contribution |
|---|---|---|
| DNS / traffic | 0 (anycast/health checks) | 30s–5min (TTL-bound) |
| Compute tier | 0 (stateless) | 2–10 min (autoscale warm-up) |
| Database | 0 (sync replication) to minutes (async) | 1–30 min (promotion, connection rerouting) |
| Caches | N/A (rebuildable) | 5–20 min (thundering-herd warmup) |
| Async pipelines | queue depth at failure time | minutes–hours (backlog drain) |

### Testing Regime
1. Quarterly game days: kill a region in staging that mirrors prod topology.
2. Automated RPO probes: write a canary every 30s with a monotonic timestamp;
   alert when the recovered region's most recent canary lags the budget.
3. RTO drills timed end-to-end from detection to first successful checkout.
4. Chaos experiments: terminate AZ, saturate replication links, corrupt a
   queue consumer to validate dead-letter handling.

## Zero-Trust Segmentation

### Principles
1. Identity is the perimeter: mTLS (SPIFFE/SPIRE on Kubernetes, service
   accounts in the cloud) for service-to-service authentication.
2. Least-privilege authorization at every hop: OPA/Envoy policies, IAM
   conditions, and VPC-level service networks (VPC Lattice, PSC, Private Link).
3. Explicit deny defaults: security groups with no 0.0.0.0/0 ingress;
   Private Endpoints instead of public endpoints; no public ingress without WAF.
4. Short-lived credentials: STS/OIDC federation for CI/CD, no long-lived keys.

### Implementation Patterns by Cloud
- AWS: PrivateLink + VPC endpoints (Gateway and Interface), Security Group
  referencing, Network Firewall with Suricata rules, Verified Access for user
  apps, IAM role trust policies with external IDs and condition keys.
- GCP: Private Service Connect, VPC Service Controls perimeters (data
  exfiltration protection), hierarchical firewall policies, BeyondCorp
  Enterprise context-aware access.
- Azure: Private Endpoints, NSGs with Application Security Groups, Azure
  Firewall Premium (IDPS), Entra Private Access, Private Link service.

### Common Failure Modes
- VPC SC perimeter blocks a service agent: enumerate `gcloud services vpc-peerings`
  and add Google-API service accounts to the perimeter's access list.
- Private endpoint DNS split-horizon mistakes: always create the private DNS
  zone in the hub and attach it to every spoke VPC/VNet.
- Cross-account role assumption fails silently: verify trust policy condition
  keys (`sts:ExternalId`, `aws:PrincipalOrgID`) and SCPs on both accounts.

## Disaster Recovery Patterns

### Pattern Selection Matrix
| Pattern | RPO | RTO | Relative cost | Complexity |
|---|---|---|---|---|
| Backup & restore | hours | 8–24h | $ | Low |
| Pilot light | minutes | 1–4h | $$ | Medium |
| Warm standby | seconds–minutes | 15–60min | $$$ | Medium-High |
| Multi-site active-active | ~0 | ~0 | $$$$ | High |

### Runbook Skeleton (Warm Standby Promotion)
1. Declare incident; page DR owner; freeze non-critical deployments.
2. Verify replica lag < budget (Aurora Global Database lag, Spanner failover
   readiness, Cosmos DB replication health).
3. Promote database (regional switchover or failover API).
4. Scale standby compute from pilot-light minimum to production capacity
   (pre-baked AMIs/images, IaC stack deploy, autoscaling override).
5. Flip traffic (Route 53 health-check failover, Front Door origin switch,
   global LB backend migration).
6. Validate: canary transactions, RPO probe, integration smoke tests.
7. Communicate status; log timeline; schedule retro and failback plan.

### Failback Discipline
Failback is a second migration: re-seed in the original region, replay the
delta, schedule a low-traffic window, and keep rollback snaps until verified.
Never fail back during the same business day as the original incident unless
the primary region is restored and canary-tested.

## Capacity & Consistency Notes for Seniors

- Reserve capacity or use provisioned concurrency (Lambda), min-instances
  (Cloud Run), pre-warmed instance pools (ACA) to avoid cold-start RTO spikes.
- Consistency budget: pick per data type — strong (payments, inventory),
  read-your-writes (user sessions), eventual (feeds, analytics).
- Quorum math: N=3, R=W=2 tolerates one node loss without availability loss.
- Beware cross-region message ordering: SQS FIFO is per-region; global
  ordering requires sequencing tokens and idempotent consumers.
