# Senior Cloud Engineer Knowledge Base: Amazon EKS Auto Mode

Domain: kubernetes
Difficulty: senior
Applies to: AWS

## Overview
Amazon EKS Auto Mode extends AWS management from the Kubernetes control plane down to the worker node infrastructure, storage provisioning, and network fabric. Built upon open-source Karpenter technology, EKS Auto Mode removes the burden of sizing, patching, securing, and maintaining EC2 node groups. The EKS control plane automatically provisions rightsized EC2 instances on-demand, manages OS security updates seamlessly with zero-downtime drain, mounts EBS volumes dynamically, and integrates VPC CNI networking out of the box.

## Key Features
- **Automated Node Lifecycle**: Eliminates static Managed Node Groups and ASGs; instances are provisioned and consolidated dynamically based on pending pod specs.
- **Integrated Storage Automation**: Automated CSI provisioning without manual daemonset installation; maps PersistentVolumeClaims directly to optimized EBS volumes.
- **Built-in Security & Patching**: Nodes run minimal AWS-curated AL2023 images with automatic CVE mitigation and managed node rotation.
- **Optimized VPC CNI**: Seamless IP address allocation per pod with native support for Security Groups for Pods without custom CNI configuration.
- **Native Consolidation**: Continuously packs workloads to minimize node count and reduce cloud waste.

## Pricing
- **Control Plane**: Standard $0.10/hour per EKS cluster.
- **Compute Surcharge**: EKS Auto Mode instances incur standard EC2 on-demand or Spot rates plus a minor managed compute fee per vCPU-hour (~$0.01/vCPU-hour).
- **Storage & Networking**: Standard EBS and VPC transfer pricing.

## Use Cases
- High-velocity development clusters where engineering teams want pure Kubernetes API semantics without managing infrastructure.
- Variable, bursty batch and ML inference workloads requiring sub-minute instance provisioning and rapid scale-to-zero.
- Enterprise platforms requiring automated CVE patching compliance for worker nodes without operational maintenance windows.

## Limitations
- **Custom Daemonset Constraints**: Deep kernel modifications and specialized host-level monitoring agents that require custom kernel modules may not be supported on managed AMI pools.
- **Custom VPC CNI Configuration**: Deeply custom eBPF CNIs (e.g. standalone Cilium in chaining mode) require opting out of Auto Mode networking.
- **Regional Rollout**: Certain instance families or bare-metal types may not be immediately available in Auto Mode pools in all regions.

## CLI Examples
```bash
# Describe EKS cluster compute capabilities
aws eks describe-cluster \
    --name "production-auto" \
    --query "cluster.computeConfig"

# Update existing cluster to enable Auto Mode compute
aws eks update-cluster-config \
    --name "staging-cluster" \
    --compute-config '{"enabled": true, "nodePools": ["general-purpose", "system"]}'

# Inspect auto-provisioned NodePool status via kubectl
kubectl get nodepools.karpenter.sh
```

## Terraform / IaC
```hcl
resource "aws_eks_cluster" "auto" {
  name     = "production-auto-eks"
  role_arn = aws_iam_role.cluster.arn
  version  = "1.31"

  vpc_config {
    subnet_ids = [aws_subnet.private_a.id, aws_subnet.private_b.id]
  }

  compute_config {
    enabled    = true
    node_pools = ["general-purpose", "system"]
  }

  storage_config {
    block_storage {
      enabled = true
    }
  }
}
```

## References
1. AWS EKS Auto Mode Documentation (https://docs.aws.amazon.com/eks/latest/userguide/auto-mode.html)
2. AWS Containers Blog: Introducing Amazon EKS Auto Mode.
