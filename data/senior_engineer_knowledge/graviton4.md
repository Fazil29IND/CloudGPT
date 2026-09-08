# Senior Cloud Engineer Knowledge Base: AWS Graviton4 Processors (R8g / C8g)

Domain: compute
Difficulty: senior
Applies to: AWS

## Overview
AWS Graviton4 represents Amazon's fourth-generation custom ARM64 silicon based on the Arm Neoverse V2 architecture. Graviton4 delivers up to 30% better compute performance, 50% more cores (up to 96 vCPUs), and 75% more memory bandwidth compared to Graviton3. Powering the R8g (memory-optimized) and C8g (compute-optimized) EC2 instances, Graviton4 features fully encrypted high-speed DDR5 memory and hardware-level branch target identification to thwart speculative execution side-channel vulnerabilities.

## Key Features
- **Neoverse V2 Architecture**: 2 MB L2 cache per core with wide vector processing pipelines (SVE2).
- **Physical Specifications**: Up to 96 cores per socket, 1.5 TB DDR5 memory, and up to 50 Gbps enhanced networking with ENA.
- **Hardware Security**: All physical DDR5 memory interfaces feature full-line inline encryption; native pointer authentication (PAC) and Branch Target Identification (BTI).
- **Energy Efficiency**: Up to 60% lower carbon footprint per compute unit compared to equivalent x86 5th-gen processors.
- **Nitro System Integration**: Offloads hypervisor, storage, and networking tasks to dedicated Nitro cards, making nearly 100% of physical cores available to guest workloads.

## Pricing
- **Cost Efficiency**: Typically priced 5%–10% lower per hourly instance rate than equivalent x86-64 (m7i/c7i) while providing 15%–25% higher throughput.
- **R8g.large**: ~$0.1336/hour (us-east-1 on-demand) with 2 vCPUs and 16 GiB RAM.
- **Savings Plans**: Eligible for standard Compute and EC2 Instance Savings Plans with up to 72% discount.

## Use Cases
- Memory-intensive in-memory caches (Redis, Dragonfly, KeyDB, Memcached) benefiting from enhanced DDR5 memory bandwidth.
- High-concurrency microservices, Spring Boot, Node.js, and Golang backend services compiled for `linux/arm64`.
- Distributed data analytics and large-scale relational databases (PostgreSQL, MySQL, Amazon Aurora).

## Limitations
- **ISA Compatibility**: Requires software to be compiled for ARM64 (AArch64); proprietary x86-only vendor binaries or legacy assembly cannot run natively.
- **Container Multi-Arch Builds**: CI/CD pipelines must produce multi-architecture container manifests (`docker buildx` with `linux/arm64` and `linux/amd64`).
- **SIMD Porting**: Workloads relying on Intel AVX-512 must be adapted to ARM SVE2/NEON instructions.

## CLI Examples
```bash
# Launch R8g instance running Amazon Linux 2023 ARM64
aws ec2 run-instances \
    --image-id resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64 \
    --instance-type r8g.2xlarge \
    --key-name prod-ssh-key \
    --subnet-id subnet-0123456789abcdef0 \
    --security-group-ids sg-0123456789abcdef0

# Query available Graviton4 offerings in region
aws ec2 describe-instance-types \
    --filters "Name=instance-type,Values=r8g.*,c8g.*" \
    --query "InstanceTypes[*].[InstanceType,VCpuInfo.DefaultVCpus,MemoryInfo.SizeInMiB]" \
    --output table
```

## Terraform / IaC
```hcl
data "aws_ssm_parameter" "al2023_arm" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

resource "aws_instance" "cache_node" {
  ami           = data.aws_ssm_parameter.al2023_arm.value
  instance_type = "r8g.xlarge"
  subnet_id     = aws_subnet.private_app.id

  vpc_security_group_ids = [aws_security_group.internal.id]

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 100
    encrypted             = true
    delete_on_termination = true
  }

  tags = {
    Name        = "graviton4-cache"
    Environment = "production"
    Arch        = "arm64"
  }
}
```

## References
1. AWS EC2 R8g Instances (https://aws.amazon.com/ec2/instance-types/r8g/)
2. AWS Graviton Technical Guide (https://github.com/aws/aws-graviton-getting-started)
