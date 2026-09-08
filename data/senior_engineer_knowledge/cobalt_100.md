# Senior Cloud Engineer Knowledge Base: Microsoft Azure Cobalt 100 Processors

Domain: compute
Difficulty: senior
Applies to: Azure

## Overview
The Microsoft Azure Cobalt 100 processor is a 64-bit ARM-based CPU custom-designed by Microsoft to power high-density, general-purpose cloud services and containerized workloads across Microsoft Azure. Built on the Arm Neoverse N2 infrastructure core on a 5-nanometer process technology, Cobalt 100 features 128 physical cores per socket, 12 DDR5 memory channels, and PCIe Gen 5 connectivity. Cobalt 100 powers Azure Virtual Machine families (Dpsv6, Dplsv6, and Epsv6), delivering up to 40% improved performance over current-generation commercial Arm instances in Azure.

## Key Features
- **128 Neoverse N2 Cores**: Native Armv9 architecture with dedicated L2 cache per core and symmetric multiprocessing.
- **12 DDR5 Channels**: High-bandwidth, low-latency memory subsystem supporting up to 4,800 MT/s transfers.
- **PCIe Gen 5 Interconnect**: High-speed peripheral connection to Azure Boost acceleration hardware for storage and networking offload.
- **Hardware Telemetry & Power Management**: Per-core frequency scaling and thermal telemetry optimized for dense data center rack environments.
- **Azure Boost Offload Engine**: Dedicated system-on-a-chip offloads network packet processing and remote disk I/O, freeing all 128 CPU cores for application execution.

## Pricing
- **Significant Cost Advantage**: Offers 20%–35% better price-performance compared to standard x86-64 Intel/AMD VM sizes in Azure.
- **Standard_D4ps_v6**: ~$0.158/hour on-demand (East US), with 4 vCPUs and 16 GiB RAM.
- **Azure Reservations**: 1-year and 3-year Reserved VM Instances provide up to 65% cost savings.

## Use Cases
- High-scale web applications, microservices, API gateways, and enterprise Java Spring services.
- Data streaming engines and message brokers (Apache Kafka, RabbitMQ, Event Hubs).
- Open-source databases (PostgreSQL, MySQL, Redis, MariaDB) compiled for AArch64.

## Limitations
- **Binary Architecture Compatibility**: x86-only proprietary binaries, closed-source Windows legacy COM+ components, or unported drivers cannot run natively.
- **Azure Disk Bursting Constraints**: Entry-level VM sizes have standard Azure Boost burst limits; heavy disk I/O requires Standard_Eps_v6 or Ultra Disk attachments.
- **Nested Virtualization**: Hyper-V nested virtualization on Cobalt 100 ARM is subject to platform preview availability.

## CLI Examples
```bash
# Deploy Azure VM powered by Cobalt 100 (Ubuntu 24.04 ARM64)
az vm create \
    --resource-group "rg-production" \
    --name "cobalt-api-worker" \
    --image "Canonical:ubuntu-24_04-lts:server-arm64:latest" \
    --size "Standard_D8ps_v6" \
    --admin-username "azureuser" \
    --ssh-key-value ~/.ssh/id_rsa.pub

# List available Cobalt 100 VM sizes in region
az vm list-sizes \
    --location "eastus2" \
    --query "[?starts_with(name, 'Standard_D') && contains(name, 'ps_v6')].name"
```

## Terraform / IaC
```hcl
resource "azurerm_linux_virtual_machine" "cobalt_node" {
  name                = "cobalt-prod-vm"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  size                = "Standard_D8ps_v6"
  admin_username      = "azureuser"

  network_interface_ids = [
    azurerm_network_interface.nic.id,
  ]

  admin_ssh_key {
    username   = "azureuser"
    public_key = file("~/.ssh/id_rsa.pub")
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Premium_LRS"
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server-arm64"
    version   = "latest"
  }

  tags = {
    Processor = "Cobalt-100"
    Arch      = "arm64"
  }
}
```

## References
1. Microsoft Blog: Introducing Azure Cobalt 100 (https://azure.microsoft.com/en-us/blog/introducing-azure-cobalt-100/)
2. Azure Virtual Machines Dpsv6 Series Documentation (https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/general-purpose/dpsv6-series)
