# Senior Cloud Engineer Knowledge Base: Verified Module Catalog & Usage Recipes

Domain: iac
Difficulty: senior
Applies to: Terraform registry modules, Azure Verified Modules (AVM), Google Cloud modules

Rule of thumb: **prefer a verified module over a hand-rolled resource** when one
exists. Modules encode years of production fixes (subnet tagging, IAM boundaries,
feature flags) that a single-resource implementation always misses. Pin versions
with `~>` to the minor line, never `latest`.

## AWS — terraform-aws-modules (community-standard, registry.terraform.io)

### VPC (terraform-aws-modules/vpc/aws ~5.x)
```hcl
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.8"

  name = "prod-core"
  cidr = "10.40.0.0/16"

  azs             = ["us-east-1a", "us-east-1b", "us-east-1c"]
  private_subnets = ["10.40.1.0/24", "10.40.2.0/24", "10.40.3.0/24"]
  public_subnets  = ["10.40.101.0/24", "10.40.102.0/24", "10.40.103.0/24"]

  enable_nat_gateway   = true
  single_nat_gateway   = false          # one per AZ in prod; true only for dev
  enable_dns_hostnames = true           # required for private ALB / RDS endpoints

  public_subnet_tags  = { "kubernetes.io/role/elb" = "1" }
  private_subnet_tags = { "kubernetes.io/role/internal-elb" = "1" }
}
```
Gotchas: (1) one NAT gateway per AZ roughly triples NAT cost — it is still the
correct prod default for AZ independence; (2) tag subnets for EKS *at creation*
— moving the `kubernetes.io/role/*` tags later requires cluster restarts for
load-balancer discovery; (3) `enable_dns_hostnames` must be true or private
RDS/ALB endpoints fail to resolve.

### EKS (terraform-aws-modules/eks/aws ~20.x)
```hcl
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.8"

  cluster_name    = "prod-core"
  cluster_version = "1.31"

  vpc_id                   = module.vpc.vpc_id
  subnet_ids               = module.vpc.private_subnets
  control_plane_subnet_ids = module.vpc.intra_subnets

  cluster_endpoint_public_access       = true
  cluster_endpoint_public_access_cidrs = ["203.0.113.0/24"]  # office VPN only
  cluster_endpoint_private_access      = true

  cluster_encryption_config = { resources = ["secrets"] }

  enable_cluster_creator_admin_permissions = true
  eks_managed_node_groups = {
    apps = { min_size = 2, max_size = 10, desired_size = 3,
             instance_types = ["m6i.large"], capacity_type = "ON_DEMAND" }
    spot = { min_size = 0, max_size = 10, desired_size = 2,
             instance_types = ["m6a.large","m5a.large"], capacity_type = "SPOT" }
  }
}
```
Gotchas: IRSA is wired by passing `enable_irsa = true` (default) — the module
creates the OIDC provider; Karpenter/Cluster Autoscaler need the
`kubernetes.io/cluster/{name}` tags the module applies automatically; upgrading
control plane version never upgrades node groups — plan AMI rollouts separately.

### RDS / Aurora (terraform-aws-modules/rds-aurora/aws ~9.x)
```hcl
module "aurora_pg" {
  source  = "terraform-aws-modules/rds-aurora/aws"
  version = "~> 9.7"

  name           = "orders-pg"
  engine         = "aurora-postgresql"
  engine_version = "16.4"
  instance_class = "db.r7g.large"
  instances      = { writer = {}, reader1 = {} }

  vpc_id                 = module.vpc.vpc_id
  subnets                = module.vpc.database_subnets
  db_subnet_group_name   = module.vpc.database_subnet_group_name
  create_security_group  = true
  allowed_cidr_blocks    = module.vpc.private_subnets_cidr_blocks

  storage_encrypted   = true
  master_user_password = random_password.db.result   # or manage_passwords
  backup_retention_period = 14
  deletion_protection     = true
  apply_immediately       = false   # prod: changes go through a change window
}
```
Gotchas: `apply_immediately = true` during business hours is how outages
happen; `deletion_protection` and `final_snapshot_identifier` are mandatory in
prod modules; put the master password in Secrets Manager (the module supports
`manage_master_user_password = true`), never tfvars.

### S3 (terraform-aws-modules/s3-bucket/aws ~4.x)
```hcl
module "artifacts" {
  source  = "terraform-aws-modules/s3-bucket/aws"
  version = "~> 4.2"

  bucket        = "prod-artifacts-eu"
  force_destroy = false

  block_public_policy     = true
  restrict_public_buckets = true
  ignore_public_acls      = true
  deny_public_acls        = true

  versioning = { status = true }
  server_side_encryption_configuration = {
    rule = { apply_server_side_encryption_by_default = {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.s3.arn
    } }
  }
  attach_public_policy = false
}
```
Checkov will flag any bucket without: public-access block (all four), SSE, and
versioning+lock. The module exposes all of them — set them explicitly.

## Azure — Azure Verified Modules (AVM, registry.terraform.io/namespaces/Azure)

AVM resource modules are named `Azure/avm-res-<product>-<service>/azurerm` and
enforce Microsoft's module standards: CMK-capable encryption params, diagnostic
settings input, role-assignment input, and `enable_telemetry` flag.

### Storage account (Azure/avm-res-storage-storageaccount)
```hcl
module "storage" {
  source  = "Azure/avm-res-storage-storageaccount/azurerm"
  version = "~> 0.2"   # AVM modules iterate fast; read the changelog

  name                = "proddatalake001"   # globally unique, lowercase+numeric
  resource_group_name = azurerm_rg.core.name
  location            = azurerm_rg.core.location

  account_tier             = "Standard"
  account_replication_type = "GRS"
  account_kind             = "StorageV2"

  public_network_access_enabled = false
  network_rules = {
    default_action = "Deny"
    bypass         = ["AzureServices"]
  }

  managed_identities = { system_managed_identity_enabled = true }
  diagnostic_settings = { to_la = {
    name                  = "to-la"
    workspace_resource_id = azurerm_log_analytics_workspace.core.id
  } }
}
```
Gotchas: AVM modules require `enable_telemetry` decisions explicitly; private
endpoints are sub-modules — pass `private_endpoints = {...}` with the subnet id
and DNS zone links; never set `public_network_access_enabled = true` for data
accounts in prod.

### Key Vault (Azure/avm-res-keyvault-vault)
Pass `network_acls` with `default_action = "Deny"`, enable RBAC authorization
(`enable_rbac_authorization = true`) instead of access policies, and wire
purge protection + soft delete for anything holding CMKs.

## GCP — terraform-google-modules / Cloud Foundation Toolkit

### Network (terraform-google-modules/network/google ~9.x)
```hcl
module "vpc" {
  source  = "terraform-google-modules/network/google"
  version = "~> 9.1"

  project_id   = var.project_id
  network_name = "prod-vpc"
  routing_mode = "REGIONAL"

  subnets = [
    { subnet_name = "apps", subnet_ip = "10.50.1.0/24", subnet_region = "us-central1",
      subnet_private_access = true },
    { subnet_name = "data", subnet_ip = "10.50.2.0/24", subnet_region = "us-central1",
      subnet_private_access = true },
  ]
  secondary_ranges = {
    apps = [{ range_name = "pods", ip_cidr_range = "10.51.0.0/16" },
            { range_name = "svcs", ip_cidr_range = "10.52.0.0/20" }]
  }
}
```
Gotchas: secondary ranges are sized at creation (VPC-native GKE cannot resize
pod CIDRs without recreation) — over-allocate (/16) even when /24 would do;
Private Google Access (`subnet_private_access`) is what makes artifact/CR
endpoints reachable without external IPs.

### Project factory (terraform-google-modules/project-factory)
Standard enterprise pattern: `svpc_host_project_id` for shared-VPC attach,
`billing_account`, `activate_apis` list, `disable_services_on_destroy=false`,
bucket for project-level Terraform state created via the
`terraform-google-bootstrap` module. Never hand-roll project creation — the
factory handles API propagation waits, lien handling, and shared-VPC service
attachment races.

## Version discipline & upgrade practice
- Pin `version = "~> major.minor"`; record upgrades as their own PRs with
  `terraform state` reconciliation notes.
- Read module CHANGELOG before bumping minor lines — terraform-aws-modules
  breaks on renamed variables between minor versions.
- Vendor critical modules (`terraform_provider` mirrors) only when network
  isolation requires it; otherwise stay on the registry.
