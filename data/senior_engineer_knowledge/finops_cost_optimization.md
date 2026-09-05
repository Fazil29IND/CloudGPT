# Senior Cloud Engineer Knowledge Base: FinOps & Cost Optimization

Domain: finops
Difficulty: senior
Applies to: AWS, GCP, Azure

## Commitment Discount Decision Algorithm

### Spot vs On-Demand vs Savings Plans vs RI
Apply in order for each steady-state workload:
1. **Interruptible stateless work** (CI runners, batch, render, ML
   pre-training, fault-tolerant workers): Spot / Preemptible / Spot VMs.
   Expect 60–90% savings. Design: checkpointing, idempotent retries, fleet
   diversification across ≥3 instance types and all AZs, on-demand fallback
   floor of 10–20% of capacity.
2. **Steady always-on baseline** (measured as the p10–p25 of hourly usage
   over 30 days): commit with Savings Plans (AWS, 1yr no-upfront ~27% on
   compute SP) or CUDs (GCP, resource-based or spend-based) or Azure Savings
   Plan / Reserved Instances. Commit at the p25 level, never the peak.
3. **Everything above baseline**: on-demand with autoscaling + scale-to-zero
   where possible (Cloud Run min-instances=0, Fargate scheduled scaling,
   AKS KEDA).

### Commitment Math (worked example)
Baseline compute spend $10,000/month, stable for 6 months:
- p25 hourly usage = 70% of peak → commit 70%.
- Compute Savings Plan 1yr no-upfront ≈ 27% discount on 70% of spend.
- Savings = 0.70 × 0.27 × $10,000 ≈ $1,890/month.
- Risk: usage drop below commitment = paying for unused commitment. Cap
  commitment value where contract renewal uncertainty exists (quarterly
  re-evaluation for startups, annual for stable enterprise).

### RI Specifics (AWS/Azure)
- Size-flexible RIs (AWS EC2 Linux normalized units, Azure RI instance size
  flexibility within the same family) reduce commitment risk.
- Convertible RIs: trade instance family when architectures change; lower
  discount than standard RIs (~x% less) but they prevent stranded commits.
- RDS RIs are engine+instance specific (Aurora: Aurora Storage is NOT covered
  — compute and I/O only per reservation terms).

## Egress Cost Mitigation

### Where egress bites
1. Cross-AZ chatter: chatty microservices across AZs — collocate via
   topology-aware routing (EKS topology hints, GKE topology-aware
   autoscaling), cache locally, batch requests.
2. Cross-region replication of raw data: filter/aggregate before replicating
   (S3 Batch Operations + replication with filter prefixes; GCS turbo
   replication only for latency-critical objects).
3. Internet egress: CDN offload (CloudFront/Front Door/Media CDN) moves
   egress from data-processing origin to CDN rates; CloudFront egress is
   free from AWS origins (standard fee applies to the distribution).
4. VPC endpoints: gateway endpoints (S3, DynamoDB) are FREE; interface
   endpoints cost per hour + GB — justified for compliance or NAT savings.

### NAT Gateway Cost Trap
A single NAT Gateway processes $0.045/GB (us-east-1) — 10TB/month ≈ $450.
Fixes: VPC endpoints for S3/DynamoDB/ECR/APIs, private ECR mirrors, apt/pip
mirrors inside the VPC, and pin inter-AZ chatter before scaling NAT.

## Storage Lifecycle Algorithms

### Tier Transition Rules (object storage)
1. Age-based: transition incomplete multipart uploads (7 days), noncurrent
   versions (30 days), general objects (S3 Standard → IA at 30–45 days when
   access < once/quarter, → Glacier IR at 90 days, → Deep Archive at 365).
2. Access-based (S3 Intelligent-Tiering): use when access pattern unknown;
   monitoring fee is tiny relative to IA→Archive deltas; it does NOT cover
   <128KB objects — set lifecycle for small objects explicitly.
3. Deletion: expire noncurrent versions at 180 days; abort incomplete
   multipart uploads at 7 days (top silent cost for ML workloads).
4. GCP: Autoclass automates; Azure: Management Policy rules (last-modified
   or last-accessed based).

### Database Storage Costs
- Aurora storage auto-grows and never auto-shrinks; reclaim with
  `OPTIMIZE`/clustering or export-and-rebuild. Aurora I/O-Optimized wins
  when I/O > 25–30% of total Aurora cost — measure from Cost Explorer.
- DynamoDB: on-demand vs provisioned break-even at ~30% utilization
  sustained; consider reserved capacity for predictable provisioned tables.
- PITR backup retention is per-second storage: 7-day retention on a 1TB
  table costs roughly the daily change rate × 7 — trim retention.

## Idle Resource Sweeping

### Weekly automated sweep (pseudo-policy)
```
FOR each resource:
  IF type in {EBS unattached, old AMIs, unassociated EIPs, idle LBs,
              stopped instances >30d, empty node groups, unused log groups}:
    TAG candidate: finops/review=pending, date=now
  IF still candidate after 14 days AND no ownership claim:
    SNAPSHOT (if stateful) THEN delete
```
AWS: Config rules + Systems Manager Automation runbooks; Cost Explorer
resource-level tags. GCP: Active Assist (idle VM recommendations, unattached
PDs), Recommender API. Azure: Advisor, Cost Management Power BI app,
scheduled `az graph query` sweeps via Automation.

### Kubernetes-specific FinOps
1. Requests ≠ usage: VPA recommendations → requests right-sizing; target
   weighted utilization >65%.
2. Scale-to-zero: KEDA/Argo Events for batch namespaces; namespaces with no
   workloads overnight get scheduled down (CronJob scaling replicas to 0).
3. Spot for non-critical node pools with taints/tolerations; separate
   billing labels via pod labels → Kubecost/OpenCost allocation.
4. GPU sharing: time-slicing (dev), MIG partitioning (isolation), and
   priority preemption queues for training vs inference.

## Governance & Culture

1. Tag policy first: `team`, `service`, `env`, `cost-center` enforced via
   tag policies/SCP (AWS), Organization Policy + labels (GCP), Azure Policy
   deny-untagged. Untagged spend is unallocatable spend.
2. Unit economics: track cost per transaction/user/customer monthly;
   alert on >20% unit-cost regression after releases.
3. Anomaly alerts: AWS Cost Anomaly Detection, GCP budget + anomaly alerts,
   Azure Cost Management alerts — page on anomalies >$1k/day deltas.
4. Showback → chargeback: publish monthly per-team reports before
   enforcing chargeback; annotate anomalies with release/deploy markers.
5. Architect reviews: FinOps review gate for new VPC designs, data
   replication topologies, and managed-data-service selection — the most
   expensive decisions are architectural, not operational.
