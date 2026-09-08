# Senior Cloud Engineer Knowledge Base: Google Cloud Hyperdisk ML

Domain: storage
Difficulty: senior
Applies to: GCP

## Overview
Google Cloud Hyperdisk ML is a block storage volume purpose-engineered for high-throughput AI and Machine Learning model training and inference workloads. Hyperdisk ML delivers up to 1,200,000 IOPS and 300,000 MB/s of aggregated read throughput per volume, and uniquely features multi-reader support that allows up to 2,500 Compute Engine or GKE instances to concurrently attach to the same volume in read-only mode (`READ_ONLY`). This dramatically accelerates model weight loading times and eliminates duplicate storage costs across large GPU/TPU clusters.

## Key Features
- **Unprecedented Read Throughput**: Up to 300,000 MB/s throughput, delivering up to 100x the throughput of standard persistent disks.
- **Massive Multi-Reader Fanout**: Concurrently attach a single volume to up to 2,500 instances in read-only mode, enabling shared model checkpoint loading across distributed clusters.
- **Dynamic Throughput Provisioning**: Decouple capacity provisioning from throughput provisioning; provision exact MB/s required independently of disk size.
- **Titanium Architecture Integration**: Custom hardware offload engines manage data streaming directly into host memory without consuming host CPU cycles.
- **GKE Persistent Volume Integration**: Supported as native ReadOnlyMany (ROX) Kubernetes StorageClass for distributed model serving fleets (vLLM, TGI, Triton).

## Pricing
- **Capacity**: ~$0.08 per GB-month (us-central1).
- **Provisioned Read Throughput**: ~$0.006 per MB/s-month.
- **Multi-Reader Attachment**: Minor per-attachment fee per hour.

## Use Cases
- Large-scale distributed LLM inference: sharing 70B–405B model weights across hundreds of GPU nodes (H100/A100/L4) without copying weights to local disk.
- Distributed deep learning training: fast multi-node dataset epoch streaming.
- Genomic sequencing and large geospatial tile analytics.

## Limitations
- **Read-Heavy Focus**: High write performance is not the design goal; writes require detaching multi-readers and switching to ReadWriteOnce mode.
- **Machine Family Compatibility**: Available primarily on modern Google Cloud machine families (A3, G2, C3, C4A, N4).
- **Zonal Scope**: Volumes are provisioned within a single availability zone; cross-zone usage requires cross-zone disk snapshots or replication.

## CLI Examples
```bash
# Create 1 TB Hyperdisk ML volume with 5,000 MB/s provisioned throughput
gcloud compute disks create "llama3-weights-vol" \
    --zone="us-central1-a" \
    --type="hyperdisk-ml" \
    --size="1000GB" \
    --provisioned-throughput="5000"

# Attach Hyperdisk ML volume to instance in Read-Only mode
gcloud compute instances attach-disk "inference-worker-01" \
    --zone="us-central1-a" \
    --disk="llama3-weights-vol" \
    --mode="READ_ONLY" \
    --device-name="model-weights"
```

## Terraform / IaC
```hcl
resource "google_compute_disk" "model_storage" {
  name                   = "llama3-405b-weights"
  type                   = "hyperdisk-ml"
  zone                   = "us-central1-a"
  size                   = 2000
  provisioned_throughput = 10000

  labels = {
    workload = "llm-inference"
    model    = "llama3"
  }
}

resource "google_compute_attached_disk" "worker_attachment" {
  count       = 4
  disk        = google_compute_disk.model_storage.id
  instance    = google_compute_instance.gpu_nodes[count.index].id
  mode        = "READ_ONLY"
  device_name = "model-storage"
}
```

## References
1. Google Cloud Compute Engine Hyperdisk ML (https://cloud.google.com/compute/docs/disks/hyperdisk-ml)
2. Google Cloud Storage Blog: Scale AI workloads with Hyperdisk ML.
