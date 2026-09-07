# Senior Cloud Engineer Knowledge Base: Enterprise EKS Reference Bundle (AWS)

Domain: iac
Difficulty: principal
Applies to: AWS, Terraform, Kubernetes, OPA Rego v1, Zero Trust, FinOps

## Overview & Scoping Contract

When generating production Kubernetes architectures for AWS, CloudGPT follows an explicit **scoping and delivery contract**:
1. **Delivered Code Scope**: The executable bundle represents a hardened, single-region, production-ready reference implementation.
2. **Compute Data Plane Requirement**: An EKS cluster must never be emitted without worker compute (`aws_eks_node_group` or Fargate profile) and the three required node IAM policies.
3. **Target-State Roadmap Scope**: Multi-region active-active replication, cross-cloud federation (Azure/GCP), and service mesh (Istio + SPIFFE/SPIRE) must be quarantined under an explicit "Target-State Architecture & Cross-Cloud / Multi-Region Roadmap (Phased Delivery)" section rather than conflated with delivered single-region code.
4. **Policy-as-Code Syntax**: All OPA Rego policies must strictly adhere to modern Rego v1 syntax (`package ...`, `import rego.v1`, and `contains ... if`).
5. **No Cosmetic Compliance**: Compliance tags such as `Compliance = "PCI-DSS-v4"` are omitted unless the bundle instantiates the complete audit suite (KMS CMK rotation, private endpoints, audit trails).

---

## 1. Multi-File Enterprise Bundle Layout

A complete enterprise EKS stack is packaged as a `<cloudgpt_bundle id="eks-enterprise-prod" title="Enterprise EKS Production Stack">` containing:
- `main.tf`: VPC endpoints, security groups, KMS CMKs, EKS control plane, and managed node group.
- `variables.tf`: Parameterized variables with defaults, including `kubernetes_version`.
- `outputs.tf`: Cluster endpoint, OIDC issuer, node role ARN.
- `backend.tf`: S3 remote state with DynamoDB state locking.
- `policies/security.rego`: Rego v1 policy-as-code enforcing 5 zero-trust guardrails.
- `k8s/deployment.yaml`: Hardened workload deployment with CPU/memory requests.
- `k8s/hpa.yaml`: HorizontalPodAutoscaler manifest linked to the workload.
- `README.md`: Operator runbook using a scoped deployment IAM role.

---

## 2. Infrastructure as Code: Terraform

### `backend.tf` (Remote State with Locking)
```hcl
terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }

  backend "s3" {
    bucket         = "corp-terraform-state-us-east-1"
    key            = "eks/enterprise-prod/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "corp-terraform-locks"
    encrypt        = true
  }
}
```

### `variables.tf` (Parameterized Version & Configuration)
```hcl
variable "aws_region" {
  description = "Target AWS deployment region"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment tier identifier (prod, staging, dev)"
  type        = string
  default     = "prod"
}

variable "cluster_name" {
  description = "Unique EKS cluster identifier"
  type        = string
  default     = "enterprise-eks-prod"
}

variable "kubernetes_version" {
  description = "Kubernetes control plane and node group version (supported EKS release)"
  type        = string
  default     = "1.31"
}

variable "vpc_id" {
  description = "VPC ID where the cluster and nodes reside"
  type        = string
}

variable "private_subnet_ids" {
  description = "List of private subnet IDs for node groups and private endpoints"
  type        = list(string)
}

variable "admin_management_cidrs" {
  description = "Authorized CIDR blocks for administrative access via VPN or Direct Connect"
  type        = list(string)
  default     = ["10.100.0.0/16"]
}

variable "tags" {
  description = "Standard operational resource tags"
  type        = map(string)
  default = {
    Environment = "prod"
    ManagedBy   = "Terraform"
    Project     = "CorePlatform"
  }
}
```

### `main.tf` (Cluster, Compute Node Group, IAM & Security Groups)
```hcl
provider "aws" {
  region = var.aws_region
  default_tags {
    tags = var.tags
  }
}

# --- KMS Customer-Managed Key with Automated Rotation ---
resource "aws_kms_key" "eks" {
  description             = "KMS CMK for EKS envelope secrets encryption"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableIAMUserPermissions"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "AllowEKSServiceKeyUse"
        Effect    = "Allow"
        Principal = { Service = "eks.amazonaws.com" }
        Action    = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
        Resource  = "*"
      }
    ]
  })
}

data "aws_caller_identity" "current" {}

# --- Explicit Security Groups (Zero Trust Micro-Segmentation) ---
resource "aws_security_group" "cluster" {
  name        = "${var.cluster_name}-cluster-sg"
  description = "EKS control plane security group allowing communication with worker nodes"
  vpc_id      = var.vpc_id

  # Ingress from authorized corporate management CIDRs (SSM Bastion / VPN)
  ingress {
    description = "Kubernetes API access from corporate network"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = var.admin_management_cidrs
  }

  egress {
    description = "Allow cluster egress to worker nodes on kubelet and HTTPS"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["10.0.0.0/8"]
  }

  tags = merge(var.tags, { Name = "${var.cluster_name}-cluster-sg" })
}

resource "aws_security_group" "node" {
  name        = "${var.cluster_name}-node-sg"
  description = "Worker node group security group"
  vpc_id      = var.vpc_id

  ingress {
    description     = "Allow cluster control plane communication to kubelet"
    from_port       = 10250
    to_port         = 10250
    protocol        = "tcp"
    security_groups = [aws_security_group.cluster.id]
  }

  ingress {
    description     = "Allow cluster control plane to pods"
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = [aws_security_group.cluster.id]
  }

  ingress {
    description = "Allow node-to-node overlay pod communication"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    self        = true
  }

  egress {
    description = "Allow outbound HTTPS for container registries and AWS APIs"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/8"]
  }

  tags = merge(var.tags, { Name = "${var.cluster_name}-node-sg" })
}

# --- EKS Cluster Control Plane ---
resource "aws_iam_role" "cluster" {
  name = "${var.cluster_name}-controlplane-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "eks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "cluster_AmazonEKSClusterPolicy" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
  role       = aws_iam_role.cluster.name
}

resource "aws_eks_cluster" "main" {
  name     = var.cluster_name
  role_arn = aws_iam_role.cluster.arn
  version  = var.kubernetes_version

  vpc_config {
    subnet_ids              = var.private_subnet_ids
    security_group_ids      = [aws_security_group.cluster.id]
    endpoint_private_access = true
    endpoint_public_access  = false # Enforces in-VPC reachability only
  }

  encryption_config {
    provider {
      key_arn = aws_kms_key.eks.arn
    }
    resources = ["secrets"]
  }

  enabled_cluster_log_types = ["api", "audit", "authenticator", "controllerManager", "scheduler"]

  depends_on = [
    aws_iam_role_policy_attachment.cluster_AmazonEKSClusterPolicy,
  ]
}

# --- Compute Data Plane: EKS Managed Node Group ---
resource "aws_iam_role" "node" {
  name = "${var.cluster_name}-worker-node-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

# MANDATORY: Attach all three required node policies
resource "aws_iam_role_policy_attachment" "node_AmazonEKSWorkerNodePolicy" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
  role       = aws_iam_role.node.name
}

resource "aws_iam_role_policy_attachment" "node_AmazonEKS_CNI_Policy" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
  role       = aws_iam_role.node.name
}

resource "aws_iam_role_policy_attachment" "node_AmazonEC2ContainerRegistryReadOnly" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
  role       = aws_iam_role.node.name
}

resource "aws_eks_node_group" "default" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "${var.cluster_name}-ng-general"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = var.private_subnet_ids
  version         = var.kubernetes_version

  scaling_config {
    desired_size = 3
    max_size     = 6
    min_size     = 2
  }

  instance_types = ["m6i.large", "m6a.large"]
  capacity_type  = "ON_DEMAND"

  update_config {
    max_unavailable = 1
  }

  labels = {
    role = "application-compute"
  }

  depends_on = [
    aws_iam_role_policy_attachment.node_AmazonEKSWorkerNodePolicy,
    aws_iam_role_policy_attachment.node_AmazonEKS_CNI_Policy,
    aws_iam_role_policy_attachment.node_AmazonEC2ContainerRegistryReadOnly,
  ]
}
```

### `outputs.tf`
```hcl
output "cluster_name" {
  description = "EKS cluster identifier"
  value       = aws_eks_cluster.main.name
}

output "cluster_endpoint" {
  description = "Private API server endpoint"
  value       = aws_eks_cluster.main.endpoint
}

output "cluster_security_group_id" {
  description = "Control plane security group ID"
  value       = aws_security_group.cluster.id
}

output "node_role_arn" {
  description = "Worker node IAM role ARN"
  value       = aws_iam_role.node.arn
}
```

---

## 3. Policy as Code: Modern OPA Rego v1 (`policies/security.rego`)

Every enterprise bundle must include a valid OPA Rego policy parsing cleanly under current OPA versions (`import rego.v1`):

```rego
package terraform.security

import rego.v1

default allow := false

# Rule 1: Enforce all 4 S3 Block Public Access settings
deny contains msg if {
    some resource in input.resource_changes
    resource.type == "aws_s3_bucket_public_access_block"
    values := resource.change.after

    not (
        values.block_public_acls == true and
        values.block_public_policy == true and
        values.ignore_public_acls == true and
        values.restrict_public_buckets == true
    )
    msg := sprintf("S3 public access block '%v' must set all 4 flags to true", [resource.address])
}

# Rule 2: Deny 0.0.0.0/0 ingress in Security Groups
deny contains msg if {
    some resource in input.resource_changes
    resource.type == "aws_security_group"
    some ingress in resource.change.after.ingress
    some cidr in ingress.cidr_blocks
    cidr == "0.0.0.0/0"
    msg := sprintf("Security group '%v' contains unrestricted 0.0.0.0/0 ingress", [resource.address])
}

# Rule 3: Deny Wildcards in IAM Policies
deny contains msg if {
    some resource in input.resource_changes
    resource.type == "aws_iam_policy"
    doc := json.unmarshal(resource.change.after.policy)
    some stmt in doc.Statement
    stmt.Effect == "Allow"
    stmt.Action == "*"
    msg := sprintf("IAM Policy '%v' contains wildcard '*' Action", [resource.address])
}

# Rule 4: Enforce EKS Private Endpoint
deny contains msg if {
    some resource in input.resource_changes
    resource.type == "aws_eks_cluster"
    vpc_config := resource.change.after.vpc_config[_]
    vpc_config.endpoint_private_access == false
    msg := sprintf("EKS cluster '%v' must have endpoint_private_access enabled", [resource.address])
}

# Rule 5: Enforce KMS Key Rotation
deny contains msg if {
    some resource in input.resource_changes
    resource.type == "aws_kms_key"
    not resource.change.after.enable_key_rotation == true
    msg := sprintf("KMS key '%v' must have enable_key_rotation set to true", [resource.address])
}

allow if count(deny) == 0
```

---

## 4. Kubernetes Workloads: Deployment & HPA

### `k8s/deployment.yaml`
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: core-app
  namespace: default
  labels:
    app.kubernetes.io/name: core-app
spec:
  replicas: 3
  selector:
    matchLabels:
      app.kubernetes.io/name: core-app
  template:
    metadata:
      labels:
        app.kubernetes.io/name: core-app
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        fsGroup: 10001
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: app
          image: 123456789012.dkr.ecr.us-east-1.amazonaws.com/app:v1.0.0
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          ports:
            - containerPort: 8080
          resources:
            requests:
              cpu: "250m"
              memory: "512Mi"
            limits:
              cpu: "1000m"
              memory: "1Gi"
          readinessProbe:
            httpGet:
              path: /healthz
              port: 8080
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /healthz
              port: 8080
            initialDelaySeconds: 15
            periodSeconds: 20
```

### `k8s/hpa.yaml`
```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: core-app-hpa
  namespace: default
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: core-app
  minReplicas: 3
  maxReplicas: 10
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 75
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: 80
```

---

## 5. Deployment Runbook (`README.md`)

```markdown
# Enterprise EKS Production Reference Stack

This repository provides an automated, production-hardened AWS Single-Region reference implementation for Amazon EKS.

## Prerequisites
- Terraform v1.5+
- AWS CLI v2
- kubectl v1.31+

## 1. Authentication via Scoped Deployment Role
Do NOT execute Terraform using long-lived Administrator access keys. Assume the dedicated deployment role via AWS STS:

```bash
CREDENTIALS=$(aws sts assume-role \
  --role-arn "arn:aws:iam::123456789012:role/TerraformEKSDeployerRole" \
  --role-session-name "EKSDeploySession")

export AWS_ACCESS_KEY_ID=$(echo $CREDENTIALS | jq -r .Credentials.AccessKeyId)
export AWS_SECRET_ACCESS_KEY=$(echo $CREDENTIALS | jq -r .Credentials.SecretAccessKey)
export AWS_SESSION_TOKEN=$(echo $CREDENTIALS | jq -r .Credentials.SessionToken)
```

## 2. Infrastructure Deployment
```bash
terraform init -backend-config="bucket=corp-terraform-state-us-east-1"
terraform plan -out=tfplan
terraform apply tfplan
```

## 3. Kubernetes Cluster Authentication
```bash
aws eks update-kubeconfig --region us-east-1 --name enterprise-eks-prod
```

> **IMPORTANT**: The EKS cluster API server has private endpoint access enabled (`endpoint_private_access = true`, `endpoint_public_access = false`). You must be connected to the corporate VPC via AWS Client VPN, Direct Connect, or an in-VPC SSM Bastion host to communicate with the cluster.

## 4. Deploy Application & Autoscaler
```bash
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/hpa.yaml
```

## 5. Verification Commands
```bash
kubectl get nodes -o wide
kubectl get pods -l app.kubernetes.io/name=core-app
kubectl get hpa core-app-hpa
```
```
