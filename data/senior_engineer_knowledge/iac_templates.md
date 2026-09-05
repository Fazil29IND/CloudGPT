# Senior Cloud Engineer Knowledge Base: Production IaC Templates

Domain: iac
Difficulty: senior
Applies to: Terraform, Bicep, CloudFormation, Helm

## Terraform: Multi-Tier Resilient VPC (AWS)

### Module Layout (production convention)
```
modules/
  network/        # VPC, subnets, NAT, endpoints
  compute/        # ALB + ASG or EKS
  data/           # RDS/Aurora, ElastiCache
  security/       # SGs, KMS, WAF
environments/
  prod/ main.tf terraform.tfvars
  staging/
```
State: S3 backend with DynamoDB lock table, bucket versioning ON, state
encryption with KMS CMK. One state per environment per component (blast-radius
isolation). Never share state between prod and non-prod.

### Network Module Skeleton
```hcl
resource "aws_vpc" "this" { cidr_block = var.cidr; enable_dns_support = true; enable_dns_hostnames = true }

resource "aws_subnet" "private" {
  for_each          = var.azs
  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(var.cidr, 8, index(var.azs, each.value) + 10)
  availability_zone = each.value
  tags = { Tier = "private", Name = "private-${each.value}" }
}

resource "aws_route_table_association" "private" {
  for_each       = aws_subnet.private
  subnet_id      = each.value.id
  route_table_id = aws_route_table.private[each.key].id
}

# VPC endpoints avoid NAT costs and keep traffic private
resource "aws_vpc_endpoint" "s3" {
  vpc_id          = aws_vpc.this.id
  service_name    = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids = [for rt in aws_route_table.private : rt.id]
}
```

### RDS with Resilience Defaults
```hcl
resource "aws_db_instance" "app" {
  identifier                   = "${var.env}-app"
  engine                       = "postgres"
  engine_version               = "16"
  instance_class               = var.instance_class
  storage_type                 = "gp3"
  allocated_storage            = 100
  multi_az                     = true
  backup_retention_period      = 14
  backup_window                = "03:00-04:00"
  deletion_protection          = true
  storage_encrypted            = true
  kms_key_id                   = var.kms_key_arn
  performance_insights_enabled = true
  skip_final_snapshot          = false
  final_snapshot_identifier    = "${var.env}-app-final"
}
```

## Terraform: EKS with Karpenter

### Cluster Essentials
```hcl
module "eks" {
  source          = "terraform-aws-modules/eks/aws"
  cluster_name    = "${var.env}-cluster"
  cluster_version = "1.31"
  vpc_id          = module.network.vpc_id
  subnet_ids      = module.network.private_subnet_ids

  cluster_endpoint_public_access       = true
  cluster_endpoint_public_access_cidrs = var.admin_cidrs
  cluster_encryption_config            = { resources = ["secrets"] }

  enable_irsa = true   # IAM Roles for Service Accounts — required by Karpenter
}
```

### Karpenter NodePool (cost + resilience)
```yaml
apiVersion: karpenter.sh/v1
kind: NodePool
metadata: { name: default }
spec:
  template:
    spec:
      requirements:
        - key: karpenter.sh/capacity-type
          operator: In
          values: ["spot", "on-demand"]   # on-demand fallback included
        - key: node.kubernetes.io/instance-type
          operator: In
          values: ["m7g.large","m7g.xlarge","m6i.large","m6i.xlarge"]
      disruption:
        consolidationPolicy: WhenEmptyOrUnderutilized
        consolidateAfter: 1m
  limits: { cpu: 200, memory: 400Gi }
```
Rules: diversify across 2+ instance families and all AZs for spot capacity;
always include an on-demand fallback; PodDisruptionBudgets required before
consolidation is safe; add `karpenter.sh/do-not-disrupt: "true"` on stateful
pods during backups.

## Bicep: Secure Hub-Spoke VNet

```bicep
param location string = resourceGroup().location
param hubCidr string = '10.0.0.0/20'
param spokeCidr string = '10.1.0.0/20'

var dnsServers = ['10.0.1.4']

resource hubVnet 'Microsoft.Network/virtualNetworks@2023-09-01' = {
  name: 'hub-vnet'
  location: location
  properties: {
    addressSpace: { addressPrefixes: [hubCidr] }
    dhcpOptions: { dnsServers: dnsServers }
    subnets: [
      { name: 'AzureFirewallSubnet', properties: { addressPrefix: '10.0.0.0/24' } }
      { name: 'dns-resolver-inbound', properties: { addressPrefix: '10.0.1.0/28', delegation: [] } }
    ]
  }
}

resource spokeVnet 'Microsoft.Network/virtualNetworks@2023-09-01' = {
  name: 'spoke-vnet'
  location: location
  properties: {
    addressSpace: { addressPrefixes: [spokeCidr] }
    subnets: [
      { name: 'apps', properties: { addressPrefix: '10.1.0.0/24', networkSecurityGroup: { id: appNsg.id }, privateEndpointNetworkPolicies: 'Enabled' } }
    ]
  }
}

resource peeringHubToSpoke 'Microsoft.Network/virtualNetworks/virtualNetworkPeerings@2023-09-01' = {
  parent: hubVnet
  name: 'hub-to-spoke'
  properties: { remoteVirtualNetwork: { id: spokeVnet.id }, allowForwardedTraffic: true }
}
```
Senior notes: use `allowGatewayTransit`/`useRemoteGateways` correctly in hub
peering; private endpoint subnets need `privateEndpointNetworkPolicies:
'Enabled'` for NSG support; deploy Private DNS zones in the hub and link every
spoke.

## CloudFormation: Multi-AZ ALB + ASG

```yaml
Resources:
  AppALB:
    Type: AWS::ElasticLoadBalancingV2::LoadBalancer
    Properties:
      Scheme: internet-facing
      Type: application
      Subnets: !Ref PublicSubnetIds
      SecurityGroups: [!Ref ALBSecurityGroup]

  AppTargetGroup:
    Type: AWS::ElasticLoadBalancingV2::TargetGroup
    Properties:
      VpcId: !Ref VpcId
      Protocol: HTTP
      Port: 8080
      HealthCheckPath: /healthz
      HealthCheckIntervalSeconds: 15
      HealthyThresholdCount: 2
      TargetType: instance

  Listener:
    Type: AWS::ElasticLoadBalancingV2::Listener
    Properties:
      DefaultActions: [{ Type: forward, TargetGroupArn: !Ref AppTargetGroup }]
      LoadBalancerArn: !Ref AppALB
      Port: 443
      Protocol: HTTPS
      Certificates: [{ CertificateArn: !Ref CertificateArn }]
      SslPolicy: ELBSecurityPolicy-TLS13-1-2-2021-06
```
Change sets are mandatory in prod: `aws cloudformation deploy` uses change
sets implicitly, but review `aws cloudformation describe-change-set` in the
pipeline gate for destructive Replacement actions.

## Helm: Production Chart Conventions

1. Never hardcode image tags in values.yaml — inject via pipeline (digest pin
   preferred: `image: repo/app@sha256:...`).
2. Probes: startupProbe (max 5 min budget), livenessProbe (cheap, local),
   readinessProbe (may check dependencies if cheap and cached).
3. Resources: requests = p50, limits = p95 × 1.3 for memory; CPU limit off or
   generous to avoid throttling GC-heavy workloads.
4. Use `helm diff upgrade` in CI; `--atomic` for automatic rollback; lock
   dependencies (`Chart.lock`) and vendor critical subcharts.
5. PodDisruptionBudget on every deployment; topologySpreadConstraints across
   zones (`topologyKey: topology.kubernetes.io/zone`).

## IaC Guardrails (All Tools)

- Policy-as-code: OPA/Conftest in CI (deny 0.0.0.0/0 ingress, unencrypted
  storage, public IPs on private tiers), AWS CloudFormation Guard for
  CloudFormation, Azure Policy Assignments for Bicep output.
- Drift detection: `terraform plan -detailed-exitcode` nightly;
  `aws cloudformation drift-detection-stack`; Azure `what-if`.
- Secrets: never in state or values; reference secret managers at runtime
  (External Secrets Operator, Vault CSI, Key Vault CSI driver).
