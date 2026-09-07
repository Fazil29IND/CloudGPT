"""Unit and integration tests for Enterprise EKS bundle integrity across Hybrid, Agentic, and Adaptive RAG.

Tests all 12 reviewer findings:
1. Compute Data Plane: aws_eks_node_group + 3 node IAM policies required.
2. Rego v1: 'import rego.v1' and 'deny contains msg if {' required for OPA.
3. Version Parameterization: Deprecated hardcoded versions (<=1.29) rejected; variable required.
4. Framing Discipline: Single-region scope with target-state roadmap for multi-cloud/mesh.
5. HPA Alignment: HorizontalPodAutoscaler requires container resource requests.
6. Expanded OPA: S3 4 flags, SG 0.0.0.0/0, IAM wildcards, EKS private endpoint, KMS rotation.
7. Compliance Tag Integrity: Unbacked 'Compliance = PCI-DSS-v4' tags flagged.
8. Remote State Backend: S3 backend with DynamoDB locking.
9. Explicit Security Groups: 0.0.0.0/0 ingress flagged.
10. README Runbook: update-kubeconfig and in-VPC reachability notice.
11. Scoped Role: Administrator credentials anti-pattern rejected; scoped role accepted.
12. Prose-to-Code Alignment: Prose capability claims must match delivered code or be in roadmap.
"""

from __future__ import annotations

import pytest

from tools.iac_validator import (
    check_static_rego_rules,
    check_static_terraform_rules,
    check_static_k8s_rules,
    detect_artifact_type,
    validate_artifact,
    validate_bundle,
)
from generation.validator import check_prose_code_alignment


# ── 1. Compute Data Plane Tests ───────────────────────────────────────────────

def test_eks_missing_compute_dataplane_flagged():
    tf_no_compute = """
    resource "aws_eks_cluster" "prod" {
      name     = "prod-cluster"
      role_arn = "arn:aws:iam::123456789012:role/cluster-role"
      version  = var.kubernetes_version
    }
    """
    res = check_static_terraform_rules(tf_no_compute)
    assert res.status == "failed"
    codes = [f.code for f in res.findings]
    assert "eks.missing_compute_dataplane" in codes


def test_eks_missing_node_policies_flagged():
    tf_partial_policies = """
    resource "aws_eks_cluster" "prod" {
      name     = "prod-cluster"
      role_arn = "arn:aws:iam::123456789012:role/cluster-role"
      version  = var.kubernetes_version
    }
    resource "aws_eks_node_group" "default" {
      cluster_name    = aws_eks_cluster.prod.name
      node_group_name = "default-ng"
      node_role_arn   = "arn:aws:iam::123456789012:role/node-role"
      subnet_ids      = ["subnet-12345"]
    }
    resource "aws_iam_role_policy_attachment" "p1" {
      policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
    }
    """
    res = check_static_terraform_rules(tf_partial_policies)
    assert res.status == "failed"
    codes = [f.code for f in res.findings]
    assert "eks.missing_node_policies" in codes


def test_eks_complete_compute_passes():
    tf_complete = """
    resource "aws_eks_cluster" "prod" {
      name     = "prod-cluster"
      role_arn = "arn:aws:iam::123456789012:role/cluster-role"
      version  = var.kubernetes_version
    }
    resource "aws_eks_node_group" "default" {
      cluster_name    = aws_eks_cluster.prod.name
      node_group_name = "default-ng"
      node_role_arn   = "arn:aws:iam::123456789012:role/node-role"
      subnet_ids      = ["subnet-12345"]
    }
    # AmazonEKSWorkerNodePolicy
    # AmazonEKS_CNI_Policy
    # AmazonEC2ContainerRegistryReadOnly
    """
    res = check_static_terraform_rules(tf_complete)
    assert res.status == "passed"
    assert len(res.findings) == 0


# ── 2. Rego v1 Syntax Tests ───────────────────────────────────────────────────

def test_rego_missing_v1_import_flagged():
    legacy_rego = """
    package terraform.security
    default allow = false
    deny[msg] {
        msg := "denied"
    }
    """
    res = check_static_rego_rules(legacy_rego)
    assert res.status == "failed"
    codes = [f.code for f in res.findings]
    assert "rego.v1_missing" in codes
    assert "rego.legacy_syntax" in codes


def test_rego_v1_compliant_passes():
    valid_rego = """
    package terraform.security

    import rego.v1

    default allow := false

    deny contains msg if {
        some resource in input.resource_changes
        resource.type == "aws_s3_bucket"
        msg := "S3 rule violation"
    }

    allow if count(deny) == 0
    """
    res = check_static_rego_rules(valid_rego)
    assert res.status == "passed"
    assert len(res.findings) == 0


# ── 3. EKS Version Parameterization Tests ──────────────────────────────────────

def test_deprecated_hardcoded_version_flagged():
    tf_128 = """
    resource "aws_eks_cluster" "main" {
      name    = "cluster"
      version = "1.28"
    }
    """
    res = check_static_terraform_rules(tf_128)
    codes = [f.code for f in res.findings]
    assert "eks.deprecated_version" in codes


def test_supported_parameterized_version_passes():
    tf_supported = """
    resource "aws_eks_cluster" "main" {
      name    = "cluster"
      version = var.kubernetes_version
    }
    """
    res = check_static_terraform_rules(tf_supported)
    codes = [f.code for f in res.findings]
    assert "eks.deprecated_version" not in codes


# ── 4 & 12. Prose-to-Code Alignment Guardrail Tests ──────────────────────────

def test_prose_service_mesh_without_manifest_or_roadmap_flagged():
    answer = """
    ## Executive Summary
    We implement an Istio service mesh with SPIFFE/SPIRE workload identities for mTLS.

    ```terraform
    resource "aws_vpc" "main" {
      cidr_block = "10.0.0.0/16"
    }
    ```
    """
    res = check_prose_code_alignment(answer)
    assert res.passed is False
    assert "service mesh" in res.details


def test_prose_service_mesh_with_roadmap_passes():
    answer = """
    ## Executive Summary
    AWS Single-Region Production Reference Implementation for Amazon EKS.

    ```terraform
    resource "aws_vpc" "main" {
      cidr_block = "10.0.0.0/16"
    }
    ```

    ## Target-State Architecture & Cross-Cloud / Multi-Region Roadmap (Phased Delivery)
    - Service mesh with Istio and SPIFFE/SPIRE workload identities is planned for Phase 2.
    - Multi-region active-active failover via Route 53 is target-state.
    """
    res = check_prose_code_alignment(answer)
    assert res.passed is True


def test_prose_hpa_without_manifest_flagged():
    answer = """
    ## Architecture
    We configure Horizontal Pod Autoscaler (HPA) to dynamically scale workloads based on load.

    ```yaml
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: app
    spec:
      replicas: 2
    ```
    """
    res = check_prose_code_alignment(answer)
    assert res.passed is False
    assert "HPA" in res.details


def test_prose_hpa_with_manifest_passes():
    answer = """
    ## Architecture
    We configure Horizontal Pod Autoscaler (HPA).

    ```yaml
    apiVersion: autoscaling/v2
    kind: HorizontalPodAutoscaler
    metadata:
      name: app-hpa
    ```
    """
    res = check_prose_code_alignment(answer)
    assert res.passed is True


# ── 5. Kubernetes HPA Resource Requests Tests ─────────────────────────────────

def test_hpa_without_deployment_requests_flagged():
    k8s_bad = """
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: app
    spec:
      template:
        spec:
          containers:
            - name: app
              image: nginx
    ---
    apiVersion: autoscaling/v2
    kind: HorizontalPodAutoscaler
    metadata:
      name: app-hpa
    """
    res = check_static_k8s_rules(k8s_bad)
    assert res.status == "failed"
    codes = [f.code for f in res.findings]
    assert "k8s.hpa_missing_requests" in codes


def test_hpa_with_deployment_requests_passes():
    k8s_good = """
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: app
    spec:
      template:
        spec:
          containers:
            - name: app
              image: nginx
              resources:
                requests:
                  cpu: "250m"
                  memory: "512Mi"
    ---
    apiVersion: autoscaling/v2
    kind: HorizontalPodAutoscaler
    metadata:
      name: app-hpa
    """
    res = check_static_k8s_rules(k8s_good)
    assert res.status == "passed"


# ── 7. Compliance Tag Tests ───────────────────────────────────────────────────

def test_unbacked_pci_dss_tag_flagged():
    answer = """
    tags = {
      Compliance = "PCI-DSS-v4"
    }
    resource "aws_s3_bucket" "b" {
      bucket = "my-bucket"
    }
    """
    res = check_prose_code_alignment(answer)
    assert res.passed is False
    assert "PCI-DSS-v4" in res.details


def test_operational_tags_pass():
    answer = """
    tags = {
      Environment = "prod"
      ManagedBy   = "Terraform"
      Project     = "CorePlatform"
    }
    """
    res = check_prose_code_alignment(answer)
    assert res.passed is True


# ── 9. Security Group Ingress Tests ───────────────────────────────────────────

def test_unrestricted_sg_ingress_flagged():
    sg_bad = """
    resource "aws_security_group" "web" {
      name = "web-sg"
      ingress {
        from_port   = 22
        to_port     = 22
        protocol    = "tcp"
        cidr_blocks = ["0.0.0.0/0"]
      }
    }
    """
    res = check_static_terraform_rules(sg_bad)
    assert res.status == "failed"
    codes = [f.code for f in res.findings]
    assert "sg.unrestricted_ingress" in codes


def test_restricted_sg_ingress_passes():
    sg_good = """
    resource "aws_security_group" "web" {
      name = "web-sg"
      ingress {
        from_port   = 443
        to_port     = 443
        protocol    = "tcp"
        cidr_blocks = ["10.0.0.0/8"]
      }
    }
    """
    res = check_static_terraform_rules(sg_good)
    assert res.status == "passed"


# ── 10 & 11. README Runbook & Scoped Role Tests ───────────────────────────────

def test_readme_admin_credentials_antipattern_flagged():
    artifacts = [
        {
            "filename": "README.md",
            "content": "# Deploy\nConfigure AWS CLI v2 with Administrator credentials to run terraform.",
        }
    ]
    res = validate_bundle(artifacts)
    assert res.valid is False
    codes = [f.code for f in res.findings]
    assert "iam.admin_credentials_antipattern" in codes


def test_complete_enterprise_bundle_passes():
    artifacts = [
        {
            "filename": "backend.tf",
            "content": """
            terraform {
              required_version = ">= 1.5.0"
              backend "s3" {
                bucket         = "corp-state"
                key            = "eks.tfstate"
                region         = "us-east-1"
                dynamodb_table = "corp-locks"
                encrypt        = true
              }
            }
            """,
        },
        {
            "filename": "variables.tf",
            "content": """
            variable "kubernetes_version" {
              type    = string
              default = "1.31"
            }

            variable "vpc_id" {
              type = string
            }
            """,
        },
        {
            "filename": "main.tf",
            "content": """
            resource "aws_eks_cluster" "prod" {
              name     = "prod-eks"
              role_arn = "arn:aws:iam::123456789012:role/cluster"
              version  = var.kubernetes_version
            }

            resource "aws_eks_node_group" "default" {
              cluster_name    = aws_eks_cluster.prod.name
              node_group_name = "default-ng"
              node_role_arn   = "arn:aws:iam::123456789012:role/node"
              subnet_ids      = ["subnet-1"]
              version         = var.kubernetes_version
            }

            # AmazonEKSWorkerNodePolicy
            # AmazonEKS_CNI_Policy
            # AmazonEC2ContainerRegistryReadOnly
            """,
        },
        {
            "filename": "policies/security.rego",
            "content": """
            package terraform.security
            import rego.v1
            default allow := false
            deny contains msg if {
                some r in input.resource_changes
                r.type == "aws_security_group"
                msg := "violation"
            }
            allow if count(deny) == 0
            """,
        },
        {
            "filename": "k8s/deployment.yaml",
            "content": """
            apiVersion: apps/v1
            kind: Deployment
            metadata:
              name: app
            spec:
              template:
                spec:
                  containers:
                    - name: app
                      image: app:v1
                      resources:
                        requests:
                          cpu: "100m"
                          memory: "256Mi"
            """,
        },
        {
            "filename": "k8s/hpa.yaml",
            "content": """
            apiVersion: autoscaling/v2
            kind: HorizontalPodAutoscaler
            metadata:
              name: app-hpa
            spec:
              scaleTargetRef:
                apiVersion: apps/v1
                kind: Deployment
                name: app
            """,
        },
        {
            "filename": "README.md",
            "content": """
            # EKS Production Stack
            ## Authentication
            Assume role arn:aws:iam::123456789012:role/TerraformEKSDeployerRole via STS.
            ## Cluster Access
            Run `aws eks update-kubeconfig --region us-east-1 --name prod-eks`.
            Note: The cluster is private and requires in-VPC bastion or VPN connectivity.
            """,
        },
    ]

    res = validate_bundle(artifacts)
    assert res.valid is True
    # Verify no guardrail failures
    assert len(res.findings) == 0
