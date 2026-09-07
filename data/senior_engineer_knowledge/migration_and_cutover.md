# Senior Cloud Engineer Knowledge Base: Migration & Cutover Playbooks

Domain: architecture
Difficulty: senior
Applies to: DB cutovers, blue/green, canary, storage lifecycle, landing-zone moves

## MP-1: Database cutover with minimal downtime (the expand-migrate-contract pattern)

**Expand (backwards-compatible schema + replica):**
1. Add columns/tables as nullable or with defaults — never rename in place.
2. Stand up target DB (RDS/Aurora/Cloud SQL/Flexible Server) in the target
   topology; seed with snapshot/restore, then continuous replication
   (DMS / Database Migration Service / logical replication).
3. Dual-write behind a feature flag (write both old+new, read old) — verify
   row counts + checksums per table continuously.

**Migrate (switch reads, then writes):**
4. Flip reads to new DB behind the same flag; compare latency + correctness
   dashboards for a business cycle.
5. Flip writes (short write-freeze window if no distributed transaction path);
   monitor replication lag drain to zero before unfreezing.
6. Run the app 48h with dual-read capability retained.

**Contract (cleanup, ≥1 week later):** remove dual-write flag, drop old
schema/instance only after backup + backup-restore test. Never contract
during the same release as migrate.

**Rollback:** re-point reads/writes to old DB (kept in sync until contract).
After contract, rollback = PITR restore (data loss accepted at contract time —
this is why the contract waits).

## MP-2: Blue/green for stateless fleets

1. Green stack deployed fully parallel (new ASG/ECS service/revision), same
   config-as-code, separate from blue (no shared ASG).
2. Smoke test green on its internal endpoint + isolated listener (:10080).
3. Shift at the traffic layer: weighted target groups (ALB), revision traffic
   split (Cloud Run/Container Apps), or route53 weighted records (cross-stack).
4. Canary ramp 5% → 25% → 50% → 100%, each step gated on error-rate +
   latency alarms scoped to the new target only; auto-rollback on breach.
5. Blue retained hot for one full traffic cycle (daily peak), then scaled to
   zero, then terminated (IaC keep=1 release).

**Gotchas that have bitten everyone:** sticky sessions across incompatible
auth versions; DB migrations shared by blue+green (expand-migrate-contract
applies here too); cache shape changes (warm the green cache via shadow reads
before shift); websocket/long-lived connections drain slowly — plan drain time.

## MP-3: Canary for high-risk changes

- Traffic-based: 1% slice on headers/geo (service mesh or gateway), metrics
  per-slice. Statistical guardrail: compare canary vs control with fixed
  thresholds AND a minimum sample size — premature rollback on noise trains
  on-call to ignore alarms.
- Shadow/mirror traffic first for request-shape validation (no user impact,
  side-effects suppressed via idempotency keys).

## MP-4: Storage lifecycle & cross-class migrations (S3/GCS/ADLS)

1. Class changes via lifecycle rules (S3 IA/Glacier, GCS Nearline/Coldline,
   ADLS hot→cool) — free, asynchronous, reversible per-object.
2. Region/account moves: `aws s3 sync`/Storage Transfer Service/GCS transfer
   — verify with object-count + ETag/checksum spot checks + manifest compare,
   not spot checks alone. Manifest compare: full object list (key, size,
   etag) diffed on both sides — the only proof of completeness.
3. Same-region same-class copies are not free at egress/requests level for
   cross-account — cost-model before moving; cross-region egress is the
   budget killer (see PM-7).
4. ACL/permission parity is the classic silent failure: bucket policies, IAM
   bindings, object ACLs, KMS keys must move in the same cutover.

## MP-5: Landing-zone / account moves (organizations)

1. Inventory first: every resource tagged with owning account/workload;
   unknown-ownership resources are the blocker (quarantine tag + TTL).
2. AWS: Resource Access Manager for shared VPCs; move accounts between OUs
   freely (SCP changes apply immediately — simulate first, see PM-3);
   cross-account resource policies need re-pointing (S3, KMS, ECR).
3. Azure: subscription-to-management-group moves are cheap; tenant moves
   (EA→MCA) are a project — plan billing, RBAC, and key vault access re-wiring.
4. GCP: project moves between folders re-evaluate org policies — use Policy
   Analyzer before, not after.
5. Freeze window: infrastructure changes frozen during org moves; app deploys
   continue.

## Universal cutover checklist
- [ ] Documented rollback point + owner + time-box (who executes, at what trigger)
- [ ] Data verified by manifest/count/checksum, not by "the sync job succeeded"
- [ ] Alarms migrated/mirrored BEFORE the shift
- [ ] DNS/TTL pre-lowered (3600 → 60) a day ahead when DNS-level cutovers exist
- [ ] Cutover runbook dry-run in staging with the same tooling (no snowflake)
- [ ] Change ticket with blast radius, comms plan, and customer-visible status page step
- [ ] Contract/cleanup scheduled as its own later change (never same-release)
