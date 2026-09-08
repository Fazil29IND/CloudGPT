# Senior Cloud Engineer Knowledge Base: Cilium & eBPF Networking & Security

Domain: networking
Difficulty: senior
Applies to: Kubernetes, CNCF, AWS, GCP, Azure

## Overview
Cilium is an open-source, cloud-native solution for providing, securing, and observing network connectivity between workloads, powered by the Linux kernel technology eBPF (extended Berkeley Packet Filter). By embedding logic directly into the Linux kernel at the socket, XDP, and tc (traffic control) layers, Cilium bypasses the iptables/IPVS connection tracking bottlenecks of traditional Kubernetes networking. Cilium provides high-performance CNI networking, multi-cluster mesh connectivity, L3-L7 identity-aware network policies, mutual TLS (mTLS), and deep real-time observability via Hubble.

## Key Features
- **eBPF-Powered Data Plane**: Replaces Linux iptables and conntrack tables with dynamic in-kernel eBPF programs, eliminating O(N) scaling degradation as pod/service counts scale into the tens of thousands.
- **Identity-Based Security Policies**: Enforces security policies based on cryptographic Kubernetes pod labels and identity metadata rather than volatile pod IP addresses.
- **Hubble Observability**: Real-time visualization of network flows, HTTP/gRPC metrics, DNS query latencies, and security policy drops without injecting sidecar proxies.
- **Sidecarless Service Mesh**: Implements mutual TLS (mTLS), L7 routing, traffic splitting, and rate limiting directly in the node kernel and per-node Envoy proxies, reducing memory overhead by up to 80% compared to Istio sidecars.
- **Multi-Cloud Cluster Mesh**: Connects disparate Kubernetes clusters across AWS (EKS), GCP (GKE), Azure (AKS), and on-premises into a unified flat network mesh with cross-cluster service discovery.

## Pricing
- **Open Source (Apache 2.0)**: Completely free to deploy and run across any self-managed or cloud-managed Kubernetes cluster.
- **Isovalent Enterprise for Cilium**: Commercial enterprise subscription providing 24/7 SLA, FIPS compliance, BGP advanced peering, and egress gateway high availability.

## Use Cases
- High-scale Kubernetes production clusters (>500 nodes, >10,000 services) experiencing iptables packet drop bottlenecks.
- Zero-Trust security compliance requiring granular L7 HTTP method/path authorization and DNS egress filtering.
- Multi-cloud Kubernetes networking unifying cross-cloud service communication with transparent encryption (WireGuard or IPsec).

## Limitations
- **Kernel Version Dependency**: Requires modern Linux kernels (v5.4+ recommended, v5.10+ for advanced features like WireGuard and XDP acceleration).
- **Cloud Provider Integration Complexity**: In cloud-managed environments (e.g. AWS EKS), choosing between CNI chaining mode (retaining AWS VPC CNI for ENI allocation) or Cilium native routing requires deliberate IPAM planning.
- **eBPF Memory Footprint**: Heavy eBPF map sizing requires tuning node system memory limits (`bpf.mapDynamicSizeRatio`).

## CLI Examples
```bash
# Install Cilium CLI and deploy Cilium into cluster
cilium install --version 1.16.0 \
    --set routingMode=native \
    --set kubeProxyReplacement=true

# Verify eBPF datapath status and kernel maps
cilium status --verbose

# Inspect real-time network flows with Hubble CLI
hubble observe --namespace default --follow --protocol http
```

## Terraform / IaC
```hcl
resource "helm_release" "cilium" {
  name       = "cilium"
  repository = "https://helm.cilium.io/"
  chart      = "cilium"
  version    = "1.16.0"
  namespace  = "kube-system"

  set {
    name  = "kubeProxyReplacement"
    value = "true"
  }

  set {
    name  = "k8sServiceHost"
    value = "api.eks.internal"
  }

  set {
    name  = "k8sServicePort"
    value = "443"
  }

  set {
    name  = "hubble.enabled"
    value = "true"
  }

  set {
    name  = "hubble.relay.enabled"
    value = "true"
  }

  set {
    name  = "hubble.ui.enabled"
    value = "true"
  }
}
```

## References
1. Cilium Documentation (https://docs.cilium.io/)
2. CNCF Cilium Project Page (https://www.cncf.io/projects/cilium/)
