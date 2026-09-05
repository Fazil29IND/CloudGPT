# Cloud Services Master Reference: AWS vs Google Cloud vs Azure

---

## 1. Cloud Architecture Lifecycle: End-to-End Phases

| Phase | Description | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- | :--- |
| **0. Account & Organization Setup** | Root account creation, multi-account topology, baseline guardrails | AWS Organizations, Control Tower, Account Factory for Terraform (AFT) | Resource Manager (Org/Folders/Projects), Cloud Identity, Infrastructure Manager | Management Groups, Subscriptions, Azure Landing Zones (Bicep/Terraform + Azure Verified Modules) |
| **1. Identity Foundations** | Authentication, directory services, access management, and RBAC | IAM, IAM Identity Center | Cloud IAM, Cloud Identity | Microsoft Entra ID, Azure RBAC |
| **2. Networking Foundation** | VPCs/VNets, subnets, DNS, hybrid, and cross-cloud connectivity | VPC, Route 53, Route 53 Global Resolver, Direct Connect, AWS Interconnect (preview) | VPC (Global), Cloud DNS, Cloud Interconnect | Virtual Network (VNet), Azure DNS, ExpressRoute |
| **3. Security Guardrails** | Threat detection, posture management, encryption keys, and secrets | GuardDuty, Security Hub, KMS, Secrets Manager | Security Command Center, Cloud KMS, Secret Manager | Microsoft Defender for Cloud, Key Vault, Azure Integrated HSM |
| **4. Compute Provisioning** | Virtual machines, serverless functions, and container instances | EC2, Lambda (incl. Lambda Managed Instances), ECS, EKS | Compute Engine, Cloud Functions (Gen 2), GKE | Virtual Machines, Azure Functions, AKS |
| **5. Storage** | Object, block, file, archival, and data lake storage | S3, EBS, EFS, FSx, S3 Glacier | Cloud Storage, Persistent Disk, Hyperdisk, Filestore, Cloud Storage Archive | Blob Storage, Managed Disks, Azure Files, Azure Archive Storage |
| **6. Databases** | Relational, NoSQL, in-memory cache, and globally distributed databases | RDS, Aurora, DynamoDB, ElastiCache, MemoryDB, Keyspaces, Neptune, Timestream | Cloud SQL, AlloyDB, Firestore, Bigtable, Memorystore, Spanner | Azure SQL Database, Azure SQL Managed Instance, Cosmos DB, Azure Cache for Redis, Azure HorizonDB |
| **7. Application Build & CI/CD** | Source control, continuous integration, build, test, and deployment automation | CodePipeline, CodeBuild, CodeDeploy, CodeCommit | Cloud Build, Cloud Deploy, Cloud Source Repositories | Azure Pipelines, Deployment Center, Azure Repos, GitHub Actions |
| **8. Containers, Serverless & Integration** | Managed orchestration, API gateways, message queues, and event routing | EKS, ECS, Fargate, App Runner, API Gateway, EventBridge, SQS, SNS | GKE, Cloud Run, Apigee, Eventarc, Pub/Sub | AKS, Container Apps, API Management, Event Grid, Service Bus |
| **9. Data, Analytics & AI** | Big data processing, data warehousing, business intelligence, ML, and agentic AI | Redshift, Glue, EMR, Athena, SageMaker AI, Amazon Bedrock, Bedrock AgentCore, Amazon Quick Suite | BigQuery, Dataflow, Dataproc, Looker, Gemini Enterprise Agent Platform, Agent Studio | Microsoft Fabric (Warehouse, Data Factory, Lakehouse), Microsoft Foundry, Copilot Studio |
| **10. Observability & Cost Governance** | Monitoring, centralized logging, distributed tracing, metrics, and spend optimization | CloudWatch, CloudWatch Logs, X-Ray, Cost Explorer, Budgets, Trusted Advisor | Cloud Monitoring, Cloud Logging, Cloud Trace, Cloud Billing Reports, Recommender | Azure Monitor, Application Insights, Log Analytics, Microsoft Cost Management, Azure Advisor |
| **11. Workload & Data Migration** | Server migration, database transfer, and high-capacity appliances | Application Migration Service (MGN), Database Migration Service (DMS), Snow Family, DataSync | Migrate to Virtual Machines, Database Migration Service, Transfer Appliance, Storage Transfer Service | Azure Migrate, Azure Database Migration Service, Data Box |
| **12. Scaling & Resource Optimization** | Auto-scaling groups, spot/preemptible instances, and commitment discounts | EC2 Auto Scaling, Spot Instances, Savings Plans, Compute Optimizer | Managed Instance Groups, Spot VMs, Committed Use Discounts (CUD) | Virtual Machine Scale Sets, Azure Spot VMs, Azure Reservations |
| **13. Disaster Recovery & Backup** | Centralized backups, cross-region replication, and continuous disaster recovery | AWS Backup, Elastic Disaster Recovery (EDR) | Backup and DR Service | Azure Backup, Azure Site Recovery (ASR) |
| **14. Decommissioning & Resource Teardown** | Infrastructure teardown, lifecycle storage purging, and tenant termination | CloudFormation stack deletion, S3 Lifecycle rules, AWS Organizations account closure | Infrastructure Manager teardown, Cloud Storage Lifecycle Management, Resource Manager project deletion | ARM/Bicep template deletion, Blob Storage lifecycle management, Subscription cancellation |

---

## 2. Categorized Cloud Service Catalog

### 1. Compute

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Virtual machines | EC2 | Compute Engine | Virtual Machines |
| Auto-scaling groups | EC2 Auto Scaling | Managed Instance Groups | Virtual Machine Scale Sets |
| Function-as-a-Service | Lambda (incl. Lambda Managed Instances) | Cloud Functions (Gen 2) | Azure Functions |
| Managed container runtime | App Runner / Fargate | Cloud Run | Container Apps |
| Batch computing | AWS Batch | Batch | Azure Batch |
| Bare metal servers | EC2 Bare Metal / Outposts | Bare Metal Solution | Azure Dedicated Host |
| VMware on cloud | VMware Cloud on AWS | Google Cloud VMware Engine | Azure VMware Solution |
| High-performance computing | ParallelCluster | Cloud HPC Toolkit | CycleCloud / Azure HPC Cache |
| Edge / telco compute | Wavelength, Local Zones, Outposts | Google Distributed Cloud Edge | Azure Stack Edge, Azure Stack HCI |
| PaaS app hosting | Elastic Beanstalk | App Engine | App Service |
| Dedicated single-tenant hosts | EC2 Dedicated Hosts | Sole-Tenant Nodes | Azure Dedicated Host |
| Spot / preemptible pricing | EC2 Spot Instances | Spot VMs | Azure Spot Virtual Machines |
| GPU / accelerated compute | EC2 P/G-series, Trainium3 UltraServers, Trainium4, Inferentia | Compute Engine GPUs / TPUs | NC/ND/NV-series VMs, Azure Maia AI accelerators |
| General-purpose custom CPU | Graviton5 | Custom Google Axion CPU | Azure Cobalt 200 |

### 2. Storage

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Object storage | S3 | Cloud Storage | Blob Storage |
| Block storage | EBS | Persistent Disk / Hyperdisk | Managed Disks |
| File storage (NFS/SMB) | EFS / FSx | Filestore | Azure Files |
| Cold / archival storage | S3 Glacier (Instant, Flexible, Deep Archive) | Cloud Storage (Coldline, Archive) | Azure Archive Storage |
| Hybrid storage gateway | Storage Gateway | Storage Transfer / Filestore Hybrid | Azure StorSimple / Azure File Sync |
| High-performance file systems | FSx for Lustre | Parallelstore | Azure Managed Lustre |
| Backup service | AWS Backup | Backup and DR Service | Azure Backup |
| Disaster recovery | AWS Elastic Disaster Recovery (EDR) | Google Cloud DR Service | Azure Site Recovery (ASR) |
| Data lake storage | S3 + AWS Lake Formation | Cloud Storage + BigLake | Azure Data Lake Storage (ADLS Gen2) / OneLake |

### 3. Databases

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Relational (managed) | RDS (PostgreSQL, MySQL, MariaDB, SQL Server, Oracle) | Cloud SQL (PostgreSQL, MySQL, SQL Server) | Azure SQL Database, Database for PostgreSQL / MySQL, Azure HorizonDB |
| Cloud-native relational | Aurora | AlloyDB | Azure SQL Managed Instance |
| NoSQL key-value / document | DynamoDB | Firestore / Datastore | Cosmos DB |
| Wide-column NoSQL | Keyspaces (Apache Cassandra-compatible) | Bigtable | Cosmos DB (Cassandra API) |
| In-memory cache | ElastiCache (Redis, Memcached), MemoryDB | Memorystore (Redis, Memcached) | Azure Cache for Redis |
| Data warehouse | Redshift | BigQuery | Microsoft Fabric Warehouse (Synapse in maintenance mode) |
| Graph database | Neptune | Spanner Graph | Cosmos DB (Gremlin API) |
| Time-series database | Timestream | Bigtable (Time-series engine) | Azure Data Explorer / Fabric Eventhouse |
| Globally distributed SQL | Aurora Global Database | Cloud Spanner | Cosmos DB / Azure SQL Hyperscale |
| Database migration | Database Migration Service (DMS) | Database Migration Service | Azure Database Migration Service |

### 4. Networking & Content Delivery

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Virtual private network fabric | VPC | VPC (Global by default) | Virtual Network (VNet) |
| Content delivery network | CloudFront | Cloud CDN | Azure Front Door / CDN |
| DNS management | Route 53, Route 53 Global Resolver | Cloud DNS | Azure DNS |
| Load balancing | Elastic Load Balancing (ALB, NLB, GLB) | Cloud Load Balancing | Azure Load Balancer, Application Gateway |
| Dedicated private connectivity | Direct Connect | Cloud Interconnect | ExpressRoute |
| Virtual private network (VPN) | Site-to-Site VPN, Client VPN | Cloud VPN | VPN Gateway |
| Cross-cloud private connectivity | AWS Interconnect | Cloud Interconnect Cross-Cloud | ExpressRoute Direct Cross-Cloud |
| Private service endpoints | AWS PrivateLink | Private Service Connect | Azure Private Link |
| DDoS protection | AWS Shield (Standard & Advanced) | Cloud Armor | Azure DDoS Protection |
| Web application firewall | AWS WAF | Cloud Armor | Azure WAF |
| Network transit hub | AWS Transit Gateway | Network Connectivity Center | Azure Virtual WAN |
| API gateway | Amazon API Gateway | Apigee / Cloud API Gateway | Azure API Management |
| Global network backbone | AWS Global Accelerator | Premium Tier Cloud Network | Azure Front Door |

### 5. AI & Machine Learning

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Unified agentic AI platform | Amazon Bedrock + Bedrock AgentCore + Amazon Quick Suite | Gemini Enterprise Agent Platform (GEAP) | Microsoft Foundry + Copilot Studio + Agent 365 |
| MLOps & custom model training | Amazon SageMaker AI | Gemini Enterprise Agent Platform (Model Garden & Training) | Azure Machine Learning |
| Foundation model catalog | Amazon Bedrock (Anthropic Claude, Meta Llama, Mistral, Amazon Nova) | Model Garden (Gemini, Gemma, open-weight models) | Foundry Models (OpenAI GPT, Anthropic, Meta, Mistral, Cohere) |
| Agent runtime & execution | Amazon Bedrock AgentCore | Deployments (formerly Agent Engine) | Foundry Agent Service |
| Low-code agent builder | Amazon Quick Suite | Agent Studio / Workspace Studio | Microsoft Copilot Studio |
| Enterprise search & RAG | Amazon Bedrock Knowledge Bases (Amazon Kendra in maintenance mode) | Search (formerly Vertex AI Search) | Azure AI Search / Foundry IQ |
| AI coding assistant | Amazon Kiro (Amazon Q Developer sunset EOL April 2027) | Gemini Code Assist | GitHub Copilot |
| Computer vision | Amazon Rekognition | Vision AI | Azure AI Vision |
| Speech & voice processing | Amazon Transcribe / Polly | Speech-to-Text / Text-to-Speech | Azure AI Speech |
| Natural language processing | Amazon Comprehend | Natural Language AI | Azure AI Language |
| Translation services | Amazon Translate | Translation AI | Azure AI Translator |
| Document processing & OCR | Amazon Textract | Document AI | Azure AI Document Intelligence |
| Custom silicon training / inference | Trainium3 UltraServers, Trainium4, Inferentia2 | Cloud TPU v5e / v5p / v6e | Azure Maia AI Accelerators |
| Recommendation engine | Amazon Personalize | Recommendations AI | Azure AI Personalizer |
| Content moderation & safety | Amazon Rekognition Content Moderation | Vision AI SafeSearch | Azure AI Content Safety |
| Agent governance control plane | Bedrock AgentCore Identity & Observability | Gemini Enterprise Governance | Microsoft Agent 365 |
| Agent interoperability | Model Context Protocol (MCP) | Agent-to-Agent (A2A) Protocol, MCP | Model Context Protocol (MCP), A2A Protocol |

### 6. Analytics & Big Data

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Data warehouse & SQL analytics | Amazon Redshift | BigQuery | Microsoft Fabric Warehouse |
| Managed Spark & Hadoop | Amazon EMR | Dataproc | HDInsight / Fabric Data Engineering |
| Serverless ETL & data integration | AWS Glue | Cloud Dataflow | Azure Data Factory / Fabric Data Factory |
| Real-time streaming data | Amazon Kinesis, Amazon MSK (Kafka) | Pub/Sub + Cloud Dataflow | Azure Event Hubs, Stream Analytics, Fabric Eventstream |
| Business intelligence & visualization | Amazon QuickSight / Amazon Quick Suite | Looker | Power BI (Direct Lake mode) |
| Data catalog & governance | AWS Glue Data Catalog, Amazon DataZone | Knowledge Catalog (Dataplex) | Microsoft Purview |
| Workflow orchestration | Managed Workflows for Apache Airflow (MWAA) | Cloud Composer | Fabric Pipelines / Azure Data Factory |
| Search & log analytics | Amazon OpenSearch Service | BigQuery / Google SecOps | Azure Data Explorer / Fabric Eventhouse |
| Serverless interactive SQL query | Amazon Athena | BigQuery Serverless SQL | Microsoft Fabric SQL / Synapse Serverless SQL |
| Unified SaaS lakehouse platform | AWS Lake Formation + S3 + Athena | BigLake + BigQuery | Microsoft Fabric (OneLake) |

### 7. Containers & Orchestration

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Managed Kubernetes | Elastic Kubernetes Service (EKS) | Google Kubernetes Engine (GKE) | Azure Kubernetes Service (AKS) |
| Container orchestration (native) | Elastic Container Service (ECS) | Cloud Run (Multi-container) | Azure Container Instances (ACI) |
| Container registry | Elastic Container Registry (ECR) | Artifact Registry | Azure Container Registry (ACR) |
| Serverless containers | AWS Fargate | Cloud Run | Azure Container Apps (ACA) |
| Service mesh | Cloud Service Mesh (App Mesh deprecated) | Cloud Service Mesh (Istio-managed) | AKS Open Service Mesh / Istio add-on |

### 8. Serverless & Application Integration

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Event-driven compute | AWS Lambda | Cloud Functions (Gen 2) | Azure Functions |
| Workflow orchestration | AWS Step Functions | Cloud Workflows | Azure Logic Apps |
| Message queues | Amazon SQS | Cloud Tasks / Pub/Sub | Azure Service Bus (Queues) / Queue Storage |
| Publish/subscribe messaging | Amazon SNS | Cloud Pub/Sub | Azure Event Grid / Service Bus (Topics) |
| Event routing bus | Amazon EventBridge | Eventarc | Azure Event Grid |
| API management & routing | Amazon API Gateway | Apigee | Azure API Management |
| Enterprise integration (iPaaS) | Amazon AppFlow | Application Integration | Azure Logic Apps |

### 9. Security, Identity & Compliance

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Identity & access management | AWS IAM | Cloud IAM | Microsoft Entra ID |
| Single sign-on & directory federation | AWS IAM Identity Center | Cloud Identity | Microsoft Entra ID SSO |
| Secrets management | AWS Secrets Manager | Secret Manager | Azure Key Vault |
| Key management & encryption | AWS KMS, AWS CloudHSM | Cloud KMS, Cloud HSM | Azure Key Vault, Managed HSM, Integrated HSM |
| Threat detection & monitoring | Amazon GuardDuty | Security Command Center | Microsoft Defender for Cloud |
| Cloud security posture management | AWS Security Hub | Security Command Center Premium | Microsoft Defender for Cloud (CSPM) |
| Compliance auditing & reports | AWS Audit Manager, AWS Artifact | Compliance Reports Manager | Microsoft Purview Compliance Manager |
| SSL/TLS certificate management | AWS Certificate Manager (ACM) | Certificate Authority Service | Azure App Service Certificates / Key Vault |
| Network firewall & protection | AWS Network Firewall, AWS Shield | Cloud Armor, Cloud Firewall | Azure Firewall, Azure DDoS Protection |
| Confidential computing | AWS Nitro Enclaves | Confidential VMs | Azure Confidential Computing |

### 10. Management, Monitoring & Governance

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Metric collection & monitoring | Amazon CloudWatch | Cloud Monitoring | Azure Monitor |
| Centralized log analytics | Amazon CloudWatch Logs | Cloud Logging | Azure Monitor Logs (Log Analytics) |
| Distributed tracing | AWS X-Ray | Cloud Trace | Application Insights |
| Infrastructure-as-Code (IaC) | AWS CloudFormation, AWS CDK | Infrastructure Manager (Infra Manager) | Azure Resource Manager (ARM), Bicep |
| Cost management & budgets | AWS Cost Explorer, AWS Budgets | Cloud Billing Reports | Microsoft Cost Management + Billing |
| Multi-account governance & policies | AWS Organizations, AWS Control Tower, SCPs | Organization Policy Service, Resource Manager | Azure Management Groups, Azure Policy |
| Resource inventory & compliance | AWS Config | Cloud Asset Inventory | Azure Resource Graph |
| Architecture advisory & best practices | AWS Well-Architected Tool, Trusted Advisor | Architecture Framework, Cloud Recommender | Azure Advisor |

### 11. Developer Tools & DevOps

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Source code repository | AWS CodeCommit | Cloud Source Repositories | Azure Repos / GitHub |
| Continuous integration & build | AWS CodeBuild | Cloud Build | Azure Pipelines / GitHub Actions |
| Continuous deployment | AWS CodeDeploy | Cloud Deploy | Azure Pipelines / Deployment Center |
| Cloud-based IDE | Cloud9 (maintenance mode for existing users) | Cloud Shell Editor, Cloud Workstations | Visual Studio Code for Web / GitHub Codespaces |
| Agentic / spec-driven IDE | Amazon Kiro | Project Antigravity / Gemini Code Assist | GitHub Copilot Workspace / Agent Mode |
| Command-line & SDK interfaces | AWS CLI, AWS SDKs | gcloud CLI, Cloud Client Libraries | Azure CLI, Azure SDKs, Azure PowerShell |
| Infrastructure preview & planning | CloudFormation Change Sets | Infra Manager Preview | Bicep What-If |

### 12. Migration & Transfer

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Physical data transfer appliance | AWS Snowball, AWS Snowcone | Transfer Appliance | Azure Data Box |
| Online data transfer & sync | AWS DataSync | Storage Transfer Service | Azure Storage Mover / AzCopy |
| Server & virtual machine migration | AWS Application Migration Service (MGN) | Migrate to Virtual Machines | Azure Migrate |
| Mainframe modernization | AWS Mainframe Modernization | Dual Run / Mainframe Modernization | Azure Mainframe Migration |

### 13. Internet of Things (IoT)

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| IoT device connectivity & gateway | AWS IoT Core | Partner-based integration (Cloud IoT Core retired) | Azure IoT Hub |
| Edge compute runtime | AWS IoT Greengrass | Google Distributed Cloud Edge | Azure IoT Edge |
| IoT analytics & digital twins | AWS IoT SiteWise, AWS IoT TwinMaker | BigQuery IoT Streaming pipelines | Azure Digital Twins, Azure Data Explorer |

### 14. Media & Content Services

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Video transcoding & encoding | AWS Elemental MediaConvert | Transcoder API | Partner solutions (Azure Media Services retired) |
| Live streaming | AWS Elemental MediaLive, Amazon IVS | Live Stream API | Partner solutions / Microsoft Teams |
| Video-on-demand packaging | AWS Elemental MediaPackage | Video Stitcher API | Partner solutions / CDN streaming |

### 15. Blockchain & Quantum Computing

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Managed ledger / blockchain nodes | Amazon Managed Blockchain | Blockchain Node Engine | Azure Confidential Ledger |
| Quantum computing access | Amazon Braket | Quantum AI (Cirq ecosystem) | Azure Quantum |

### 16. Cost Management, Support & Marketplace

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Free support tier | Basic Support | Basic Support | Basic Support |
| Standard / developer paid support | Business Support+ (AI-assisted) | Standard Support | Developer Support |
| Production / enhanced paid support | Business Support+ | Enhanced Support | Standard Support / Professional Direct |
| Enterprise support | Enterprise Support, Unified Operations | Premium Support | Unified Support |
| Commercial software marketplace | AWS Marketplace | Google Cloud Marketplace | Azure Marketplace |
| Cost anomaly detection | AWS Cost Anomaly Detection | Cloud Billing Cost Anomaly Alerts | Microsoft Cost Management Anomaly Detection |
| Discount commitment models | Savings Plans, Reserved Instances (RI) | Committed Use Discounts (CUD) | Azure Savings Plans, Azure Reservations |

### 17. End-User Computing & Virtual Desktops

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Virtual desktop infrastructure (VDI) | Amazon WorkSpaces, WorkSpaces Core | Chrome Enterprise / Partner solutions | Azure Virtual Desktop (AVD), Windows 365 Cloud PC |
| Application streaming | Amazon AppStream 2.0 | Chrome Enterprise App Streaming | Azure Virtual Desktop RemoteApp |

### 18. Communication & Notification Services

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Transactional email delivery | Amazon Simple Email Service (SES) | Partner integrations (SendGrid / Mailgun) | Azure Communication Services (Email) |
| SMS, voice & push notifications | Amazon SNS, Amazon Pinpoint | Firebase Cloud Messaging (FCM) | Azure Communication Services (SMS/Voice), Notification Hubs |
| Real-time voice, video & chat SDK | Amazon Chime SDK | WebRTC / Partner integrations | Azure Communication Services |

### 19. Domain Registration & DNS Management

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Domain name registration | Route 53 Domains | External registrar integration (Cloud Domains retired) | App Service Domains (partner registrar integration) |
| Authoritative public DNS | Route 53, Route 53 Global Resolver | Cloud DNS | Azure DNS |
| Private DNS for VPC / VNet | Route 53 Private Hosted Zones | Cloud DNS Private Zones | Azure Private DNS Zones |

### 20. Landing Zones & Governance-as-Code

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Automated multi-account baseline | AWS Control Tower + Account Factory for Terraform (AFT) | Cloud Foundation Toolkit + Infrastructure Manager | Azure Landing Zones (ALZ) + Azure Verified Modules |
| Policy-as-code guardrails | Service Control Policies (SCPs), Tag Policies | Organization Policy Service | Azure Policy, Initiative Definitions |
| AI agent governance control plane | Amazon Bedrock AgentCore Identity & Observability | Gemini Enterprise Governance | Microsoft Agent 365 |

### 21. Decommissioning & Resource Lifecycle Tools

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Stack & template teardown | CloudFormation stack deletion | Infrastructure Manager teardown | ARM / Bicep deployment deletion (`az deployment group delete`) |
| Storage lifecycle purging | S3 Lifecycle expiration rules | Cloud Storage Object Lifecycle Management | Blob Storage lifecycle management rules |
| Tenant / subscription closure | Account closure via AWS Organizations | Project deletion via Google Cloud Resource Manager | Subscription cancellation via Cost Management |
| Orphaned resource discovery | AWS Config (compliance rules) | Cloud Asset Inventory | Azure Resource Graph queries |

---

## 3. Platform Architecture & Strategic Comparison

### Ecosystem Breadth vs. Cohesion
- **AWS**: Delivers the broadest and deepest raw service catalog with granular configurability and mature operational primitives. AWS aggressively prunes early-generation tooling (e.g., Q Developer, Kendra, Q Business) in favor of modern consolidated architectures (Amazon Bedrock, AgentCore, Amazon Quick Suite).
- **Google Cloud**: Emphasizes developer simplicity, Kubernetes-native tooling, unified global networking, and data-centric ergonomics. GCP consolidates AI and agent services under the unified **Gemini Enterprise Agent Platform**.
- **Azure**: Offers deep integration with enterprise infrastructure, Microsoft 365, Microsoft Entra ID, and Windows ecosystems. Azure centers its data strategy around **Microsoft Fabric** (SaaS lakehouse) and its AI strategy around **Microsoft Foundry** and **Agent 365**.

### AI & Agentic Architecture
- **AWS**: Built on a modular three-anchor model: **Amazon Bedrock** (multi-model marketplace & knowledge bases), **Amazon Bedrock AgentCore** (runtime execution, identity, and observability), and **Amazon Quick Suite** (enterprise business user assistant layer). Custom hardware acceleration is powered by **Trainium3/4** and **Inferentia2**.
- **Google Cloud**: Unified under the **Gemini Enterprise Agent Platform (GEAP)**, combining Model Garden (200+ foundation models), Agent Studio, and runtime Deployments. Promotes cross-framework interoperability via the **Agent-to-Agent (A2A) protocol** alongside Model Context Protocol (MCP).
- **Azure**: Anchored by **Microsoft Foundry** (formerly Azure AI Foundry) with access to over 11,000 cataloged models (OpenAI, Anthropic, Meta, Mistral), paired with **Foundry Agent Service** for runtime execution and **Microsoft Agent 365** as a centralized governance control plane across enterprise agents.

### Data Lakehouse & Analytics Models
- **AWS**: Employs a decoupled composable architecture with **Amazon S3**, **AWS Lake Formation**, **AWS Glue**, **Amazon Athena**, and **Amazon Redshift Serverless**.
- **Google Cloud**: Centers around **BigQuery** and **BigLake**, providing zero-copy federation, built-in ML inference, and native integration with Looker and Cloud Dataflow.
- **Azure**: Unified under **Microsoft Fabric**, delivering an all-in-one SaaS lakehouse powered by **OneLake** (open Delta Lake/Parquet format), **Fabric Data Factory**, **Fabric Warehouse**, and **Power BI Direct Lake mode**.

### Networking Topologies
- **AWS & Azure**: VPCs and VNets are regional by default; multi-region and hybrid connectivity require peering, transit gateways (AWS Transit Gateway / Azure Virtual WAN), or dedicated interconnects. AWS introduces native cross-cloud peering with **AWS Interconnect**.
- **Google Cloud**: VPCs are globally scoped by default, enabling subnets in any region within a single network without explicit inter-region VPC peering.

### Governance, Landing Zones & Policy Enforcement
- **AWS**: Uses **AWS Control Tower**, **Account Factory for Terraform (AFT)**, and **Service Control Policies (SCPs)** to enforce guardrails across multi-account structures.
- **Google Cloud**: Leverages hierarchical Resource Manager structures (Organizations, Folders, Projects) governed by **Organization Policy Service** and automated via **Infrastructure Manager**.
- **Azure**: Utilizes **Management Groups**, **Subscriptions**, and **Azure Policy** applied through **Azure Landing Zones (ALZ)** and **Azure Verified Modules**.

### Enterprise Multi-Account & Resource Hierarchy
- **AWS**: Multi-account topology managed via **AWS Organizations** with Management (Root) account, Core/Security/Infrastructure Organizational Units (OUs), and Sandbox OUs. Guardrails enforced via **Service Control Policies (SCPs)** which act as filter barriers (cannot grant permissions, only restrict max allowable permissions) and **Resource Control Policies (RCPs)** applied directly to data resources (S3 buckets, KMS keys, SQS). Account vending and baseline guardrail provisioning automated via **AWS Control Tower** and **Account Factory for Terraform (AFT)**.
- **Google Cloud**: Hierarchical **Resource Manager** tree: Organization (tied 1:1 with Cloud Identity / Google Workspace domain) -> Folders (nested up to 10 levels deep for business units/environments) -> Projects (fundamental resource container, billing boundary, API enablement boundary) -> Resources. Governance enforced via **Organization Policy Service** constraints (e.g. `constraints/compute.vmExternalIpAccess`, `constraints/gcp.restrictCmekCryptoKeyProjects`) inherited down the hierarchy with boolean/list constraints and hierarchical inheritance rules.
- **Azure**: Tenant-root management group -> **Management Groups** (hierarchical container up to 6 levels deep, 10,000 management groups max per tenant) -> **Subscriptions** (billing and quota boundaries) -> **Resource Groups** (lifecycle and deployment boundary for related resources) -> Resources. Policy inheritance enforced via **Azure Policy** initiatives and definitions evaluated via Azure Resource Manager (ARM), utilizing `Deny`, `Audit`, `DeployIfNotExists`, and `Modify` effects, structured via **Azure Landing Zones (ALZ)** management group archetypes (Corp, Online, Decommissioned, Sandboxes).

### Identity, Zero Trust & IAM Policy Evaluation Logic
- **AWS**: Policy evaluation order: Explicit Deny > Service Control Policy (SCP) > Resource Control Policy (RCP) > Permissions Boundary > Session Policy > Identity-Based / Resource-Based Policy Allow. Cross-account access requires dual authorization (trust policy on resource + identity policy on principal). **AWS STS** generates temporary credentials (`AssumeRole`, `AssumeRoleWithWebIdentity` for keyless OIDC federation with GitHub Actions/Kubernetes). Attribute-Based Access Control (ABAC) dynamically controls access using tags (`aws:PrincipalTag` vs `aws:ResourceTag`).
- **Google Cloud**: Principal -> Role (collection of atomic permissions, e.g. `compute.instances.start`) -> Policy Binding attached to resource/project/folder/org. Evaluated as: Explicit Deny Policy -> Allow Policy. Fine-grained ABAC powered by Common Expression Language (CEL) **IAM Conditions** (evaluating request time, resource type, destination IP, or principal attributes). Keyless authentication enforced via **Workload Identity Federation (WIF)** for CI/CD and external clouds, eliminating long-lived service account JSON keys. **Workforce Identity Federation** allows enterprise IdPs (Okta, Azure AD) to authenticate human users directly without syncing accounts to Cloud Identity.
- **Azure**: **Microsoft Entra ID** (central cloud identity directory). Azure RBAC controls control plane actions (`Actions`, `NotActions`) and data plane operations (`DataActions`, `NotDataActions`). Role assignments made at Management Group, Subscription, Resource Group, or Resource scope. Fine-grained security enforced through Entra **Privileged Identity Management (PIM)** for Just-In-Time (JIT) time-bound role activations with approval workflows. **Managed Identities** (System-Assigned tied to resource lifecycle, User-Assigned reusable across resources) provide automatic token management against Entra ID, completely eliminating static connection strings and secrets in application code. **Conditional Access** policies enforce Zero Trust signals (user risk, sign-in risk, device compliance, IP range) before granting token issuance.

### Global Transit Networking & Cross-Cloud Interconnects
- **AWS**: VPCs are strictly regional entities. Multi-VPC and hybrid interconnectivity centers on **AWS Transit Gateway (TGW)** (50 Gbps per VPC attachment, route table isolation, ECMP across multiple VPN/Direct Connect attachments) and **AWS Cloud WAN** for dynamic global BGP WAN meshes across multiple AWS Regions and on-premises sites. Dedicated private connectivity via **AWS Direct Connect** (1G/10G/100G dedicated ports) and **AWS Interconnect** for direct low-latency peering with Azure and GCP. Private egress/ingress secured via **AWS PrivateLink** (interface endpoints backed by Network Load Balancers), **Gateway Load Balancers (GWLB)** for bump-in-the-wire inspection with third-party appliances, and **AWS Network Firewall**.
- **Google Cloud**: VPCs are globally scoped by default—a single VPC network spans all global regions with regional subnets communicating privately across Google's private fiber backbone without VPNs or peering. Complex hybrid routing managed by **Network Connectivity Center (NCC)** using Router Appliances for dynamic BGP route exchange. Dedicated private connections via **Cloud Interconnect** (Dedicated or Partner) and **Cross-Cloud Interconnect** (direct high-bandwidth links provisioned directly into AWS and Azure data centers). Private access enabled via **Private Service Connect (PSC)** using consumer forwarding rules and producer service attachments, avoiding overlapping IP space. Egress secured via **Cloud NAT** (with min ports per VM tuning to prevent port exhaustion) and **Secure Web Proxy**.
- **Azure**: VNets are regional. Multi-VNet connectivity utilizes VNet Peering (regional and global) or **Azure Virtual WAN** (hub-and-spoke architecture supporting up to 50 Gbps routing throughput per Virtual Hub, Any-to-Any routing, and Secured Virtual Hub integrating Azure Firewall). Hybrid connectivity anchored by **ExpressRoute** (private peering, Microsoft peering) and **ExpressRoute Direct Cross-Cloud**. Private workload exposure handled via **Azure Private Link** (Private Endpoints with private IP NICs in consumer VNet connecting to Private Link Services or Azure PaaS). Outbound connectivity managed via **Azure NAT Gateway** (providing 64,000 SNAT ports per public IP to completely prevent SNAT exhaustion) and **Azure Firewall Premium** (stateful inspection, TLS termination, IDPS, URL filtering).

### Disaster Recovery, RPO/RTO & Multi-Region Resiliency Patterns
- **AWS**: DR tiers: Backup & Restore (RPO hours, RTO hours) using AWS Backup cross-region copy; Pilot Light (RPO minutes, RTO tens of minutes) using Aurora Global Database / DynamoDB Global Tables with minimal compute standby; Warm Standby (RPO seconds, RTO minutes) using scaled-down active compute in secondary region; Multi-Region Active-Active (RPO ~0, RTO ~0) using DynamoDB Global Tables, Route 53 Application Recovery Controller (ARC) routing controls, and Global Accelerator. Automated failover validation via **AWS Fault Injection Service (FIS)**.
- **Google Cloud**: Resiliency tiers: Backup and DR Service with cross-region Cloud Storage dual-region/multi-region buckets (turbo replication guarantees RPO < 15 minutes); Cloud Spanner multi-region instances providing 99.999% availability with synchronous multi-region Paxos consensus (zero RPO, instant RTO failover); Global Cloud Load Balancing with multi-region backend services automatically rerouting traffic to healthy regions without DNS TTL delays.
- **Azure**: Resiliency tiers: Azure Site Recovery (ASR) for continuous VM replication; Azure Backup with Geo-Redundant Storage (GRS) and Read-Access Geo-Zone-Redundant Storage (RA-GZRS); Cosmos DB multi-region writes (99.999% SLA, sub-10ms latency globally); Azure SQL Database Auto-Failover Groups (1-hour grace period or automatic failover); Azure Front Door global anycast routing with health probes providing sub-minute traffic steering. Resilience testing orchestrated through **Azure Chaos Studio**.

### Serverless Concurrency, Execution Limits & Cold-Start Dynamics
- **AWS Lambda**: Concurrency model allocates 1 execution environment per concurrent request. Regional account soft limit 1,000 concurrency (burst limit 500-3,000 depending on region). 15-minute maximum execution timeout; 10,240 MB RAM; up to 10 GB ephemeral `/tmp` storage. Cold start mitigation: Provisioned Concurrency (pre-warmed sandboxes), SnapStart (microVM snapshot caching for Java 11/17/21 and Python/Node.js). Compute pricing scales in 1ms increments based on configured memory and CPU architecture (x86_64 vs arm64 Graviton).
- **Google Cloud Run & Cloud Functions (Gen 2)**: Cloud Functions Gen 2 is built directly on Cloud Run infrastructure. Concurrency model supports container concurrency (up to 1,000 concurrent requests per container instance), dramatically reducing total cold starts for web workloads. Execution timeout up to 60 minutes for HTTP requests and 10 minutes for event-driven triggers. Scales up to 32 GB RAM and 8 vCPUs. Cold start mitigation: Minimum instances (`--min-instances`) keeping containers running warm.
- **Azure Functions (Flex Consumption)**: Modern consumption tier featuring per-instance memory configuration (from 512 MB to 4 GB), fast elastic scaling, virtual network integration without dedicated App Service Plans, and instance concurrency control. Premium plan provides unbounded execution timeout; standard consumption caps at 10 minutes. Cold start mitigation: Always Ready instances maintaining pre-warmed execution capacity.

---

## 4. Next-Generation & Specialized Service Catalog (2026 Expansion)

### Next-Gen Compute & Specialized Silicon

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Serverless distributed SQL engine | Amazon Aurora DSQL | Cloud Spanner | Azure SQL Hyperscale / Cosmos DB for PostgreSQL |
| Clean-room data collaboration | AWS Clean Rooms | BigQuery Data Sharing / Analytics Hub | Microsoft Fabric / Azure Data Share |
| Agent runtime & tooling control plane | Amazon Bedrock AgentCore (Runtime, Gateway, Code Interpreter, Browser, Memory) | Gemini Enterprise Agent Platform — Deployments (Agent Engine) | Microsoft Foundry Agent Service |
| AI guardrails & responsible AI | Amazon Bedrock Guardrails | Gemini responsible-AI filters / Model Garden safety configs | Azure AI Content Safety (Foundry) |
| Large-scale simulation & digital twin | AWS SimSpace Weaver | Google Cloud Digital Twin / BigQuery digital twin pipelines | Azure Digital Twins |
| Zero-trust user & app access | AWS Verified Access | BeyondCorp Enterprise — Zero Trust | Microsoft Entra Private Access / Entra Internet Access |
| Cloud WAN fabric | AWS Cloud WAN | Network Connectivity Center | Azure Virtual WAN |
| Next-gen TPU training & inference | (Trainium3 UltraServers / Trainium4) | Cloud TPU v6e (Trillium), TPU v5p | (Azure Maia 100) |
| Confidential VMs & enclaves | AWS Nitro Enclaves | Confidential VMs / Confidential Space | Azure Confidential VMs (Intel TDX / AMD SEV-SNP) |
| Managed cloud workstations | (EC2 / WorkSpaces) | Cloud Workstations | Microsoft Dev Box |
| Cloud game streaming & rendering | (partner / G4/G5 instances) | Immersive Stream for Games | (partner / NV-series) |
| Simple bundled compute + storage | Amazon Lightsail | (Cloud Run / Compute Engine presets) | (App Service / VM presets) |
| Golden-image build pipeline | EC2 Image Builder | (VM Manager / custom pipelines) | Azure Image Builder (VM Image Builder) |

### Next-Gen Storage & Data Services

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| High-performance file cache | AWS File Cache | Filestore (High Scale SSD) | Azure Managed Lustre (AMLFS) cache |
| Single-digit-millisecond object store | S3 Express One Zone | (Cloud Storage Turbo tier / dual-region) | (Blob Storage Premium Block) |
| Enterprise NAS (ONTAP) | FSx for NetApp ONTAP | Google Cloud NetApp Volumes | Azure NetApp Files |
| NFS / OpenZFS managed file systems | FSx for OpenZFS | Filestore Zonal / Enterprise | Azure Files (NFS 4.1) / Azure Managed Lustre |
| ML-optimized block storage | (io2 Block Express / S3 Express) | Hyperdisk ML, Hyperdisk Balanced High Performance | Azure Elastic SAN (iSCSI) |
| Elastic SAN fabric | (EBS gp3 multi-attach) | Hyperdisk (pool-based) | Azure Elastic SAN |
| Storage migration orchestration | AWS DataSync / Storage Gateway | Storage Transfer Service / Database Migration Service | Azure Storage Mover |
| Vector-optimized database indexes | Aurora PostgreSQL pgvector, MemoryDB vectors, S3 Vectors | AlloyDB ScaNN indexes, Bigtable vector search | Cosmos DB DiskANN vector indexing |
| Distributed SQL without ops | Aurora DSQL (serverless multi-region strong consistency) | Cloud Spanner (global) | Azure SQL Database (Hyperscale) / Azure HorizonDB (postgreSQL hyperscale) |
| Self-hosted Postgres distribution | (RDS for PostgreSQL custom) | AlloyDB Omni (self-managed software) | (Azure Database for PostgreSQL flexible server) |
| Cassandra-compatible managed NoSQL | Amazon Keyspaces (with serverless) | Bigtable | Azure Managed Instance for Apache Cassandra |
| Time-series with InfluxDB engine | Amazon Timestream for LiveAnalytics / Timestream for InfluxDB | Bigtable time-series | Azure Data Explorer / Fabric Eventhouse |
| Purpose-built document DB | Amazon DocumentDB (MongoDB-compatible) | Firestore | Cosmos DB for MongoDB (vCore) |

### Next-Gen AI, Agents & Model Platforms

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Flagship frontier models | Amazon Nova (Premier/Pro/Lite) via Bedrock | Gemini 3 family (Pro / Flash / Flash-Lite) via Model Garden | OpenAI GPT-5 family + Anthropic Claude via Foundry Models |
| Healthcare-specialized multimodal model | AWS HealthImaging / HealthLake AI integrations | Med-Gemini (Healthcare AI) | Azure AI Health Insights (Radiology/Text Analytics for Health) |
| Custom AI accelerator (in-house silicon) | Trainium3 UltraServers, Trainium4, Inferentia2 | Cloud TPU v6e Trillium | Azure Maia 100 / Cobalt 200 |
| Vertex-level agent IDE & orchestration | Amazon Bedrock Flows / AgentCore Gateway | Agent Studio (GEAP) | Copilot Studio (low-code agents) |
| Enterprise agent governance & audit | Bedrock AgentCore Observability + Identity | Gemini Enterprise Governance | Microsoft Agent 365 (control plane) |
| Retrieval-augmented generation platform | Amazon Bedrock Knowledge Bases | Vertex AI Search (RAG) | Azure AI Search (Foundry IQ) |
| Conversational search & assistants | Amazon Q Business | Vertex AI Conversation / Agentspace | Microsoft 365 Copilot |
| Speech-to-speech foundation models | (Nova Sonic via Bedrock) | Gemini Live API | Azure AI Speech (Azure OpenAI Realtime) |
| Video generation & media AI | (Nova Reel via Bedrock) | Veo (Vertex AI Media Studio) | Azure OpenAI Sora (Foundry) |

### Next-Gen Security & Governance

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Security data lake & SIEM | Amazon Security Lake + OpenSearch | Google SecOps (Chronicle SIEM/SOAR) | Microsoft Sentinel |
| Data security posture & DSPM | Amazon Macie (S3 data discovery) | Sensitive Data Protection (Cloud DLP) | Microsoft Purview Data Map / Information Protection |
| IAM access analysis | AWS IAM Access Analyzer | IAM Recommender / Policy Analyzer | Entra ID Governance / Access Reviews |
| Cross-account & multi-org security analytics | Security Hub cross-Region aggregation | Security Command Center Enterprise | Defender for Cloud multi-subscription / cloud connectors |
| Binary supply-chain attestation | AWS Signer | Binary Authorization | Azure Verified Containers / Image Integrity |
| Just-in-time key access | (KMS grants / conditional keys) | Key Access Justifications (KAJ) | Key Vault (Managed HSM, just-in-time via PIM) |
| Chaos & resilience engineering | AWS Fault Injection Service (FIS) | (custom / Chaos Toolkit on GKE) | Azure Chaos Studio |
| Ephemeral environment provisioning | (CloudFormation stacks / Proton) | (Infrastructure Manager previews) | Azure Deployment Environments (ADE) |

### Next-Gen Networking & Connectivity

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Application-network micro-segmentation | AWS VPC Lattice | (Service Mesh / Cloud Service Mesh) | Azure App Gateway for Containers (ALB Controller) |
| Multi-region DNS latency routing | Route 53 Latency/Geoprobing + Global Resolver | Cloud DNS Routing Policies (Geo/WRR) | Azure DNS + Traffic Manager / Front Door |
| Managed media/enterprise CDN | CloudFront (with CloudFront Functions) | Media CDN | Azure Front Door (Standard/Premium) |
| Hybrid DNS + routing hub | Route 53 Resolver endpoints / Cloud WAN | Cloud DNS + Network Connectivity Center (NCC) | Azure Route Server + Azure DNS |
| Event pipes & schedulers | EventBridge Pipes, EventBridge Scheduler | Eventarc (advanced filters, workflows) | Azure Logic Apps Standard, Event Grid Namespaces |
| Private service mesh egress control | VPC Lattice Service Networks | Secure Web Proxy | Azure Private Link + Firewall (DNAT) |

### Next-Gen Observability & Operations

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| AI-assisted operations & root cause | CloudWatch Investigations / DevOps Agent | Gemini Cloud Assist | Azure Monitor + Copilot in Azure |
| Application performance monitoring | CloudWatch Application Signals | Cloud Monitoring (APM / GKE Enterprise) | Application Insights (auto-instrumentation) |
| Real-time infra anomaly detection | CloudWatch Anomaly Detection | Cloud Monitoring anomaly detection | Azure Monitor Metric Alerts (dynamic thresholds) |
| Fleet patch & config compliance | AWS Systems Manager Patch Manager | OS Config Management (VM Manager) | Azure Update Manager / Guest Configuration |

### Next-Gen Hybrid, Edge & Sovereign

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Distributed cloud edge platform | AWS Outposts, Local Zones, Wavelength | Google Distributed Cloud (Edge / Hosted) | Azure Stack (HCI / Edge / Hub), Azure Local |
| Sovereign-cloud data boundaries | AWS Regions (opt-in) / Partition support | Sovereign Clouds / Assured Workloads | Azure Sovereign Landing Zone / National clouds |
| Telecom 5G core on cloud | (Wavelength) | Global Mobile Edge Cloud (GMEC) | Azure Operator Nexus / Private 5G Core |
| Spring Boot managed runtime | (ECS/App Runner with Java runtimes) | (Cloud Run Java / App Engine) | Azure Spring Apps |

### Next-Gen FinOps & Licensing

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Compute savings analytics | AWS Compute Optimizer + Savings Plans recommendations | Active Assist / CUD recommendations | Azure Advisor savings guidance |
| Container cost allocation | CloudWatch Container Insights + CUR | GKE cost allocation | AKS cost analysis views |
| License-included databases | RDS License-Included (SQL Server/Oracle) | (bring-your-own license SQL) | Azure SQL (AHB license benefit) |
| Multi-cloud billing normalization | CUR + Athena | BigQuery billing export | Azure Cost Management exports |

---

## 5. Categorized Cloud Service Catalog — 2026 Expansion (September 2026)

This section is the September 2026 verified sweep of service launches across **AWS re:Invent 2025**
(Nov 30 – Dec 4, 2025), **Google Cloud Next '26** (April 2026), and **Microsoft Build 2026**
(June 2, 2026). It uses the same `| Purpose | AWS | Google Cloud | Azure |` table format as the
categorized catalog above so the CloudGPT ingestion pipeline (`ingest_services.py` +
`sources/generate_manifest.py`) parses every row into category chunks and per-service knowledge
cards. Availability status is carried in the cell text (`(preview)`, `(GA)`, `(retired)`).

### 22. AI, Agents & Foundation Models — 2026 Expansion

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Frontier multimodal model family (2026) | Amazon Nova 2 Lite (1M context reasoning), Nova 2 Omni (preview, all-in-one multimodal + image generation), Nova 2 Sonic (preview, speech-to-speech) | Gemini 3 family (Gemini Enterprise) | OpenAI GPT-5 family (Foundry Models) |
| Custom frontier model building | Amazon Nova Forge | Gemini Enterprise Agent Platform (Model Garden + Training) | Foundry Models (fine-tuning + distillation) |
| Browser & UI workflow agents | Amazon Nova Act (GA) | Gemini Enterprise Agent Platform (Agent Studio) | Microsoft Copilot Studio (computer use) |
| Managed open-weight model marketplace | Amazon Bedrock managed open-weight models (Mistral Large 3, Qwen, Kimi, MiniMax, NVIDIA, OpenAI open-weight) | Model Garden open-weight catalog | Foundry open-weight models |
| Serverless experiment tracking | SageMaker AI Serverless MLflow | Vertex AI Experiments | Azure Machine Learning (managed MLflow) |
| Agent interoperability expansion | Strands Agents SDK + AgentCore Gateway (MCP) | Gemini Enterprise MCP Connector | Microsoft Agent 365 (MCP + A2A) |

### 23. Storage, Data & Analytics — 2026 Expansion

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Native vector storage in object storage | Amazon S3 Vectors (GA, up to 2B vectors per index) | BigQuery AI (vector search in warehouse) | Azure AI Search (Foundry IQ) |
| Autonomous table cost tiering | S3 Intelligent-Tiering for S3 Tables | BigQuery storage tiering | Fabric OneLake lifecycle tiering |
| Cross-region & cross-account table replication | S3 Tables replication | BigLake managed tables (cross-region) | Fabric OneLake mirroring |
| Prefix-level storage performance analytics | S3 Storage Lens (performance metrics + direct export to S3 Tables) | Cloud Storage insights | Azure Storage analytics |
| Object & file storage integration | S3 Access Points for FSx for ONTAP | Filestore + Cloud Storage hybrid | Azure Files + Blob integration |
| Unified observability data lake | CloudWatch unified data management (OCSF, Iceberg) | BigQuery log analytics | Azure Monitor + Fabric Eventhouse |
| Cross-cloud lakehouse federation | S3 + Athena (multi-account) | Cross-Cloud Lakehouse (BigQuery across AWS + Azure) | Fabric mirroring (multi-cloud) |

### 24. Compute, Silicon & Databases — 2026 Expansion

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Memory-optimized EPYC instances | EC2 X8aedz instances (5th Gen AMD EPYC, up to 3 TiB) | Compute Engine (E2 + EPYC) | Azure Epsv5 / EbDsv5 series |
| In-house custom CPU VMs (GA) | (Graviton5) | (Axion) | Azure Cobalt 200 VMs (GA, ~50% perf uplift) |
| Next-gen AI training silicon | Trainium3 UltraServers | 8th-generation TPUs (Next '26) | Azure Maia 100 |
| Long-running multi-step serverless workflows | Lambda Durable Functions (preview) | Cloud Workflows (durable execution) | Azure Durable Functions |
| Kubernetes workload orchestration expansion | EKS Capabilities | GKE (fleet orchestration) | AKS (Build 2026 updates) |
| Developer-focused SQL Server database | RDS Developer Edition (SQL Server) | Cloud SQL (Enterprise editions) | Azure SQL Database free offer |
| Commitment discounts for databases | Database Savings Plans | Committed Use Discounts | Azure savings plan for databases |
| Globally distributed serverless Postgres | Aurora DSQL (GA) | AlloyDB | Azure HorizonDB (GA) |
| Distributed SQL (global consistency) | Aurora Global Database | Cloud Spanner | Cosmos DB |

### 25. Security, Operations & Developer Tooling — 2026 Expansion

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Autonomous operations agent | AWS DevOps Agent (preview) | Gemini Cloud Assist | Copilot in Azure |
| Agentic security code review | AWS Security Agent (preview) | Security Command Center (Gemini) | Defender for Cloud (Copilot) |
| Cross-service attack path correlation | GuardDuty Extended Threat Detection | Security Command Center Enterprise | Defender XDR correlation |
| Near-real-time findings analytics | Security Hub (GA with analytics + risk prioritization) | Security Command Center Premium | Defender Cloud Security (estates view) |
| AI-assisted policy authoring | IAM Policy Autopilot (MCP server) | IAM Recommender + Policy Analyzer | Entra ID Governance |
| AI-augmented support plans | Enhanced AWS Support (AI triage) | Google Cloud Premium Support (Gemini) | Azure Unified Support (Copilot) |
| Migration modernization suite | AWS Transform (custom + Windows + Mainframe Reimagine) | Dual Run | Azure Migrate (modernization) |
| Privacy-preserving synthetic data | AWS Clean Rooms (synthetic dataset generation) | BigQuery (differential privacy) | Microsoft Presidio + Fabric |
| Sovereign AI infrastructure | AWS AI Factories (on-premises turnkey) | Google Distributed Cloud (air-gapped) | Foundry Local on Azure Local |
| Agentic developer CLIs | Kiro CLI | Gemini CLI | GitHub Copilot CLI (Build 2026 refresh) |

### 26. Vector & RAG Infrastructure (2026)

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Object-storage-native vector index | Amazon S3 Vectors (GA, S3 Vector Buckets) | BigQuery AI vector search | (use Azure AI Search) |
| GPU-accelerated vector engine in search service | OpenSearch Service (GPU-accelerated vector search) | Vertex AI Vector Search | Azure AI Search (vector + semantic ranker) |
| Managed RAG orchestration | Amazon Bedrock Knowledge Bases | Vertex AI Search (RAG) | Azure AI Search / Foundry IQ |
| Managed semantic caching for LLM responses | (partner solutions) | Vertex AI inference caching | Azure AI Foundry (response caching) |

### 27. Naming & Lifecycle Changes (2026 Sweep)

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Speech-to-speech model refresh | Nova 2 Sonic (preview) supersedes Nova Sonic | Gemini Live API | Azure AI Speech (realtime API) |
| Legacy code repository | AWS CodeCommit (deprecated) | Cloud Source Repositories (deprecated) | Azure Repos |
| AI IDE transition | Amazon Kiro (Amazon Q Developer sunset EOL April 2027) | Gemini Code Assist + Project Antigravity | GitHub Copilot Workspace |
| Managed PostgreSQL branding | Aurora DSQL + Aurora PostgreSQL | AlloyDB | Azure HorizonDB (GA, managed PostgreSQL) |
| Enterprise search rebrand | Bedrock Knowledge Bases (Kendra in maintenance mode) | Search (formerly Vertex AI Search) | Foundry IQ (formerly Azure AI Search integration) |

### 28. Multi-Cloud Zero-Trust, SASE & Edge Network Fabrics (2026 Expansion)

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Zero-trust private application access | AWS Verified Access | BeyondCorp Enterprise | Microsoft Entra Private Access |
| Zero-trust secure web gateway & internet outbound | AWS Network Firewall / VPC egress proxy | Secure Web Proxy | Microsoft Entra Internet Access |
| Service-to-service application networking | AWS VPC Lattice | Cloud Service Mesh | Azure Application Gateway for Containers |
| Unified multi-cloud WAN mesh | AWS Cloud WAN | Network Connectivity Center | Azure Virtual WAN |
| Edge micro-datacenter appliance | AWS Outposts rack / 1U/2U servers | Google Distributed Cloud Edge | Azure Local (formerly Azure Stack HCI) |

### 29. Sustainable FinOps, Carbon Accounting & Capacity Planning (2026 Expansion)

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Cloud carbon footprint reporting | AWS Customer Carbon Footprint Tool | Carbon Footprint | Microsoft Emissions Impact Dashboard |
| Granular carbon optimization recommendations | AWS Compute Optimizer (carbon metrics) | Active Assist (carbon impact recommender) | Azure Carbon Optimization |
| Commitment & reservation management | AWS Cost Explorer (Savings Plans / RIs) | Cloud Billing Committed Use Discounts | Microsoft Cost Management (Reservations / Savings Plans) |
| Automated storage tiering governance | S3 Intelligent-Tiering | Cloud Storage Autoclass | Azure Blob Storage Lifecycle Management |
| Real-time cost anomaly alerting | AWS Cost Anomaly Detection | Cloud Billing Budget & Anomaly Alerts | Microsoft Cost Management Anomaly Alerts |

### 30. AI Agent Tooling, MCP Gateways & Protocol Interoperability (2026 Expansion)

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Native Model Context Protocol (MCP) server & client gateway | Amazon Bedrock AgentCore Gateway (MCP Native) | Gemini Enterprise MCP Connector | Microsoft Agent 365 MCP Hub |
| Agent-to-Agent (A2A) cross-enterprise coordination | Strands Agents SDK + Bedrock AgentCore | Agent-to-Agent (A2A) Protocol Bridge | Microsoft Semantic Kernel A2A Broker |
| Sandboxed code execution for AI agents | Amazon Bedrock AgentCore Code Interpreter | Cloud Run Sandboxed Worker | Azure Container Apps Dynamic Sessions |
| Autonomous browser automation runtime | Amazon Nova Act / Bedrock AgentCore Browser | Gemini Enterprise Browser Agent | Microsoft Copilot Studio Computer Use |
| Long-term episodic agent memory store | Bedrock AgentCore Memory | Gemini Enterprise Memory Service | Microsoft Foundry Agent Memory |

### 31. Next-Gen Enterprise Compute & Silicon Architecture (2026 Expansion)

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Custom ARM enterprise processors | AWS Graviton4 / Graviton5 | Google Axion ARM CPUs | Azure Cobalt 100 / Cobalt 200 |
| Next-gen AI training & accelerator clusters | AWS Trainium2 / Trainium3 UltraServers | Cloud TPU v6e (Trillium) / v5p | Azure Maia 100 AI Accelerators |
| Fully automated Kubernetes node management | Amazon EKS Auto Mode | GKE Autopilot (Next-Gen) | AKS Automatic |
| High-density serverless function instances | AWS Lambda Managed Instances | Cloud Functions Gen 2 (Worker Pools) | Azure Functions Flex Consumption |
| High-performance serverless container GPUs | AWS ECS on Fargate with GPU | Cloud Run with NVIDIA L4/H100 GPUs | Azure Container Apps Dedicated GPU Profiles |
| Ultra-low latency memory-tier databases | Amazon MemoryDB for Valkey | Memorystore for Valkey | Azure Managed Redis (Valkey Engine) |
| Cloud-native columnar PostgreSQL acceleration | Amazon Aurora PostgreSQL Optimized Reads | AlloyDB Columnar Engine with Omni | Azure Database for PostgreSQL Citus / Columnar |

---

## 6. Enterprise Well-Architected Framework Catalog & Comparison

The Well-Architected Framework provides architectural evaluation criteria across six core pillars. Organizations evaluate cloud workloads against these pillars to guarantee high operational reliability, hardened security posture, and cost efficiency.

### Operational Excellence Pillar

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Infrastructure as Code (IaC) | AWS CloudFormation, AWS CDK, Terraform / OpenTofu with AWS Provider | Google Cloud Infrastructure Manager, Terraform with Google Provider, Config Connector | Azure Bicep, Azure Resource Manager (ARM) templates, Terraform with Azurerm Provider |
| Configuration compliance & drift detection | AWS Config (conformance packs, proactive evaluation) | Cloud Asset Inventory (real-time monitoring, export to BigQuery) | Azure Policy (drift compliance, guest configuration) |
| Automated deployment pipelines | AWS CodePipeline + AWS CodeDeploy (Canary, Blue/Green with Linear and Canary bake periods) | Cloud Deploy (progressive delivery, automatic rollback, Canary verification) | Azure Pipelines / GitHub Actions (Deployment Environments, manual approval gates) |
| Fleet patching & operational runbooks | AWS Systems Manager (SSM Run Command, Patch Manager, Automation documents) | Compute Engine OS Config (VM Manager, OS patch policies) | Azure Update Manager (dynamic scoping, scheduled periodic assessments) |
| Chaos & resilience experimentation | AWS Fault Injection Service (FIS) for fault injection into EC2, EKS, RDS | Chaos Toolkit on GKE, Google Cloud resilience validation | Azure Chaos Studio (agent-based and service-direct fault injection) |

### Security Pillar

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Data encryption at rest & envelope encryption | AWS KMS (Customer Managed Keys CMK, multi-Region keys, HSM-backed) | Cloud KMS (Software, Cloud HSM, External Key Manager EKM with Key Access Justifications KAJ) | Azure Key Vault (Standard, Premium Managed HSM, Confidential VMs) |
| Centralized identity & single sign-on | AWS IAM Identity Center (SAML 2.0, SCIM directory sync) | Cloud Identity + Workforce Identity Federation | Microsoft Entra ID (Conditional Access, PIM, Workload Identities) |
| Automated threat detection & anomaly analysis | Amazon GuardDuty (EKS audit logs, VPC Flow Logs, S3 access, Malware runtime, RDS login events) | Security Command Center (SCC Enterprise, Event Threat Detection, Container Threat Detection) | Microsoft Defender for Cloud + Microsoft Sentinel (Cloud SIEM/SOAR) |
| Web Application Firewall & DDoS mitigation | AWS WAF (Managed Rule Groups, Rate-based rules) + AWS Shield Advanced | Cloud Armor (adaptive protection, WAF rules, DDoS mitigation up to 46M rps) | Azure WAF on Application Gateway / Front Door + Azure DDoS Protection |
| Secrets management & automatic rotation | AWS Secrets Manager (native Lambda rotation for RDS, Redshift, DocumentDB) | Secret Manager (automatic versioning, CMEK, Pub/Sub rotation triggers) | Azure Key Vault Secrets (Event Grid rotation triggers with Azure Functions) |

### Reliability Pillar

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Global DNS routing & health failover | Route 53 (Latency, Geolocation, Geoproximity, Failover routing policies) + Route 53 ARC | Cloud DNS (Routing policies: Geolocation, Weighted round-robin, failover health checks) | Azure Traffic Manager / Azure Front Door (Anycast routing, health probe failover) |
| Multi-region database replication | Amazon Aurora Global Database (storage-level physical replication <1s latency) | Cloud Spanner (Multi-region synchronous Paxos consensus, 99.999% SLA) | Azure Cosmos DB (Multi-region active-active writes, sub-10ms global latency) |
| Cross-region object replication | S3 Cross-Region Replication (CRR, S3 Batch Replication, Replication Time Control RTC 15 min) | Cloud Storage Dual-Region / Multi-Region with Turbo Replication (RPO < 15 minutes) | Azure Blob Storage GRS / GZRS / Object Replication |
| Automated disaster recovery orchestration | AWS Elastic Disaster Recovery (EDR, block-level continuous data replication) | Google Cloud Backup and DR Service | Azure Site Recovery (ASR, continuous VM replication, automated recovery plans) |
| Circuit breaking & traffic shedding | AWS App Mesh / Envoy on ECS/EKS, API Gateway throttling & usage plans | Cloud Service Mesh (Traffic Director, outlier detection, circuit breaking) | Azure Application Gateway for Containers, API Management rate limiting |

### Performance Efficiency Pillar

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Hardware offload hypervisors | AWS Nitro System (Nitro Cards for VPC, EBS, Local NVMe, Enclaves) | Google Titanium (Custom silicon Titanium adapter, Titanium security offload) | Azure Boost (Microsoft dedicated FPGA/ASIC offload for networking and storage) |
| High-performance distributed file systems | Amazon FSx for Lustre (hundreds of GB/s, millions of IOPS) | Cloud Parallelstore (NVMe-based scratch and persistent HPC storage) | Azure Managed Lustre (integrated with Azure Machine Learning and AKS) |
| Memory-tier caching & acceleration | Amazon MemoryDB for Valkey / Redis (microsecond read/write, multi-AZ durable transaction log) | Memorystore for Valkey / Redis (in-memory cluster, high throughput) | Azure Managed Redis (Valkey Engine, active-active geo-distribution) |
| Serverless container acceleration | AWS ECS on Fargate with Graviton and NVIDIA GPU support | Cloud Run with NVIDIA L4/H100 GPU acceleration | Azure Container Apps Dedicated GPU Profiles (A100, H100) |
| Edge content caching & compute | CloudFront + CloudFront Functions / Lambda@Edge | Cloud CDN + Edge Functions (Media CDN) | Azure Front Door Rules Engine + Azure Functions |

### Cost Optimization Pillar

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Compute commitment optimization | AWS Compute Savings Plans (1-yr / 3-yr, up to 66% discount, cross-EC2/Fargate/Lambda) | Committed Use Discounts (CUDs: flexible spend-based up to 57%, resource-based up to 70%) | Azure Savings Plans for Compute (up to 65% discount across VM, Container Apps, App Service) |
| Automated storage tiering | Amazon S3 Intelligent-Tiering (Frequent, Infrequent, Archive Instant, Archive Flexible, Deep) | Cloud Storage Autoclass (automatically transitions objects based on last-access timestamp) | Azure Blob Storage Lifecycle Management rules (Cool, Cold, Archive transitions) |
| Idle & oversized resource recommendations | AWS Compute Optimizer (ML-driven EC2, EBS, Lambda rightsizing recommendations) | Active Assist Recommender (VM rightsizing, idle VM detection, unattached disk detection) | Azure Advisor (cost recommendations, underutilized VM shutdown/resize) |
| Cost allocation & anomaly detection | AWS Cost Allocation Tags + AWS Cost Anomaly Detection (ML-driven spend spike alerts) | Cloud Billing Labels + Cloud Billing Budget & Anomaly Alerts | Azure Cost Management Tags + Cost Anomaly Detection alerts |
| Containerized workload cost attribution | CloudWatch Container Insights with cost allocation (EKS/ECS pod-level split) | GKE Cost Allocation (namespace and label-based cost export to BigQuery) | AKS Cost Analysis (Azure Cost Management integration via OpenCost) |

### Sustainability Pillar

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Cloud carbon emissions reporting | AWS Customer Carbon Footprint Tool (Scope 1, 2 emissions by region and service) | Google Cloud Carbon Footprint (gross carbon emissions, Scope 1, 2, 3 data) | Microsoft Emissions Impact Dashboard (Scope 1, 2, 3 carbon accounting) |
| Energy-efficient compute architectures | AWS Graviton4 / Graviton5 processors (up to 60% less energy consumption than comparable x86) | Google Axion ARM processors (up to 60% better energy efficiency than x86) | Azure Cobalt 100 / 200 ARM processors (up to 50% lower power footprint) |
| Green region workload scheduling | AWS Well-Architected sustainability guidance (region selection based on grid carbon intensity) | Google Cloud Carbon-Aware Computing (Active Assist carbon recommender, low-carbon region badge) | Azure Carbon Optimization (carbon-efficient region placement advice) |

---

## 7. Enterprise Quotas, Hard Limits & Throttling Catalog Matrix

Understanding hard platform quotas and throughput throttling dynamics is critical for building resilient enterprise architectures. The following matrix documents hard limits, throttling behaviors, and mitigation patterns.

### Storage & Database Throughput Limits

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Object storage per-prefix throughput | Amazon S3: 3,500 PUT/COPY/POST/DELETE and 5,500 GET/HEAD requests per second per prefix. Scale infinitely by partitioning keys across distinct prefixes. | Cloud Storage: 1,000 write operations per second per bucket initial; auto-scales by ramping up traffic (double every 20 minutes) without prefix bottlenecks. | Azure Blob Storage: Up to 20,000 requests per second per storage account; 500 Gbps egress max bandwidth. Large partition scale via multi-container partitioning. |
| NoSQL partition throughput limits | Amazon DynamoDB: 1,000 WCU (1 KB write/sec) and 3,000 RCU (4 KB read/sec) per physical partition. Maximum partition size 10 GB. Excess causes ProvisionedThroughputExceededException. Mitigation: High-cardinality partition keys, write sharding (suffixing `#0` to `#9`), DynamoDB Accelerator (DAX). | Cloud Bigtable: 10,000 QPS (reads) or 10,000 QPS (writes) per cluster node for 1 KB rows. Throttles on hotspotting. Mitigation: Lexicographical row-key design avoiding timestamps as primary key prefix. | Azure Cosmos DB: 10,000 RU/s and 50 GB storage per physical partition. Hot partition returns HTTP 429 (Request Rate Too Large). Mitigation: High-cardinality synthetic partition keys, Hierarchical Partition Keys (up to 3 keys). |
| Data warehouse concurrency limits | Amazon Redshift Serverless: 50 concurrent queries max per workgroup. Excess queries queue in Workload Management (WLM) queues. Mitigation: Query concurrency scaling clusters, Amazon Athena for ad-hoc serverless queries. | Google BigQuery: 2,000 baseline slots per project default (autoscaling up to dynamic limits). Maximum 4,000 partitions per table. Mitigation: BI Engine memory acceleration, table clustering instead of over-partitioning. | Microsoft Fabric / Synapse: Concurrency limits based on DWU / Capacity Units (CU). Maximum 128 concurrent queries. Mitigation: Fabric direct lake mode, read scale-out replicas. |
| Managed relational connection limits | Amazon RDS PostgreSQL: Max connections = `LEAST({DBInstanceClassMemory/9531392}, 5000)`. Max 5,000. Excess returns "FATAL: remaining connection slots are reserved". Mitigation: Amazon RDS Proxy. | Cloud SQL: Max connections depends on RAM (up to 4,000 for high-memory tiers). Excess rejected. Mitigation: Cloud SQL Auth Proxy with client-side connection pooling. | Azure Database for PostgreSQL: Max connections scaled by vCore (e.g. 50-5,000). Mitigation: Built-in PgBouncer connection pooler in Flexible Server. |

### Compute & Serverless Scaling Limits

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Function execution timeout | AWS Lambda: 900 seconds (15 minutes) hard limit. Workloads requiring longer execution must use AWS Step Functions or ECS/Fargate tasks. | Cloud Functions (Gen 2): 3,600 seconds (60 minutes) for HTTP-triggered functions; 600 seconds (10 minutes) for event-triggered. | Azure Functions: Consumption plan default 300s (max 600s); Premium and Dedicated plans default 30 min (can be configured to unbounded). |
| Regional concurrency & burst scaling | AWS Lambda: 1,000 concurrent executions per region default (soft limit, requestable). Burst concurrency 500-3,000 initial, +500/min. Returns HTTP 429 `TooManyRequestsException`. Mitigation: Provisioned Concurrency, SQS dead-letter queues. | Cloud Functions (Gen 2) / Cloud Run: 1,000 concurrent container instances per region default. Up to 1,000 concurrent requests per container instance (total 1,000,000 concurrent HTTP requests). Mitigation: `--min-instances`. | Azure Functions: Scale-out rate up to 1 instance every 10 seconds for consumption; Flex Consumption scales instances elastically with pre-warmed capacity. |
| Function payload & memory limits | AWS Lambda: 6 MB synchronous invocation payload; 256 KB asynchronous invocation payload; 10,240 MB max memory; 10 GB `/tmp` ephemeral storage. | Cloud Functions (Gen 2): 32 MB HTTP request/response payload; 32 GB max memory; 8 vCPUs. | Azure Functions: 100 MB default max request body size; Flex Consumption supports up to 4 GB RAM per instance. |
| Managed container cluster scaling | Amazon EKS: Up to 5,000 nodes and 737,000 pods per cluster. Amazon EKS Auto Mode eliminates node-group management overhead via automatic Karpenter-driven provisioning. | Google GKE: Up to 15,000 nodes and 300,000 pods per cluster in standard; GKE Autopilot manages node scaling and bin-packing automatically with pod-level SLAs. | Azure AKS: Up to 5,000 nodes per cluster with Azure CNI Overlay. AKS Automatic automates cluster management, security baseline enforcement, and node provisioning. |

### Networking & API Gateway Limits

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| API gateway throughput & payload | Amazon API Gateway: 10,000 requests per second (RPS) default per region with 5,000 burst. Maximum payload size 10 MB. Integration timeout 29 seconds hard limit. | Apigee / Cloud Endpoints: Apigee scales to tens of thousands RPS based on allocated environment capacity; 10 MB payload limit default. | Azure API Management: Scale measured in units; Consumption tier up to 500 RPS; Premium tier supports multi-region scaling to 10,000+ RPS. Payload limit 100 MB default. |
| NAT gateway egress connection limits | AWS NAT Gateway: 55,000 concurrent connections per destination IP. Exceeding causes packet drops and `ErrorPortAllocation` metrics. Mitigation: Allocate multiple secondary private IPs to NAT Gateway or use VPC Endpoints. | Cloud NAT: Default 64 min ports per VM instance; max 64,000 concurrent connections per NAT gateway IP. Mitigation: Turn on dynamic port allocation (`--enable-dynamic-port-allocation`). | Azure NAT Gateway: 64,000 outbound concurrent SNAT connections per public IP address; supports up to 16 public IPs per gateway (over 1 million concurrent flows). Zero SNAT exhaustion when properly provisioned. |
| VPC transit throughput | AWS Transit Gateway: 50 Gbps maximum bandwidth per VPC attachment with ECMP. Route table limit 10,000 routes. | Google Network Connectivity Center: Up to 50 Gbps per interconnect attachment; dynamic BGP routing updates without attachment bottlenecks. | Azure Virtual WAN: Up to 50 Gbps routing throughput per Virtual Hub (scales with hub routing units). 2,000 VM connections per vHub. |

---

## 8. Cloud FinOps, Commitment Economics & Hybrid Licensing Catalog Matrix

Managing unit economics, compute commitment discounts, network egress tariffs, and hybrid enterprise software licenses represents the financial backbone of multi-cloud management.

### Compute Commitments & Cost Governance

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Flexible compute commitment | AWS Compute Savings Plans (1-yr or 3-yr commitment, hourly spend commitment $/hr, up to 66% discount, applies across EC2, Fargate, Lambda across any region). | Flexible Spend-based Committed Use Discounts (1-yr or 3-yr $/hr commitment, up to 57% savings, automatically applies across Compute Engine, Cloud Run, GKE). | Azure Savings Plans for Compute (1-yr or 3-yr hourly spend commitment, up to 65% discount, applies across VMs, Azure Container Apps, Azure App Service). |
| Workload-specific commitment | AWS EC2 Instance Savings Plans (up to 72% discount, committed to specific instance family in specific region, allows instance size and OS changes). | Resource-based Committed Use Discounts (up to 70% savings, committed to specific vCPU/RAM quotas or GPU models in specific region). | Azure Reserved Virtual Machine Instances (up to 72% discount, committed to specific VM series and region; supports instance size flexibility within group). |
| Spot & preemptible capacity | EC2 Spot Instances (up to 90% discount on on-demand, 2-minute interruption notice delivered via EventBridge and instance metadata). | Spot VMs (up to 91% discount on on-demand, 30-second termination notice via metadata, dynamic preemption based on datacenter demand). | Azure Spot Virtual Machines (up to 90% discount on pay-as-you-go, 30-second termination notice via Azure Scheduled Events). |
| Multi-cloud billing export & normalization | AWS Cost and Usage Report (CUR / CUR 2.0) exported in Parquet format to Amazon S3, queryable via Amazon Athena and ingested into FinOps tools. | Cloud Billing export to BigQuery (standard usage, detailed usage with resource tags, pricing data exported in real time). | Azure Cost Management data exports (scheduled CSV/Parquet export to Azure Blob Storage, integrated with Microsoft Fabric). |

### Data Transfer & Interconnect Tariffs

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Inter-Availability Zone data transfer | $0.01 per GB in each direction ($0.02/GB round-trip) between EC2 instances in different AZs within the same AWS Region. | $0.01 per GB egress between VMs in different zones within the same Google Cloud region. | $0.01 per GB inbound and $0.01 per GB outbound ($0.02/GB round-trip) across Availability Zones in the same Azure region. |
| Inter-Region backbone transfer | Ranges from $0.01 to $0.02 per GB depending on source and destination AWS Regions across the AWS global private fiber network. | $0.01 per GB for intra-continent inter-region transfer (e.g., US to US regions); $0.05-$0.08/GB inter-continental. | $0.02 per GB for intra-continent inter-region transfer; $0.035-$0.08/GB inter-continental across Azure global backbone. |
| Public internet egress pricing | First 100 GB/month free; then $0.09/GB (up to 10 TB), dropping to $0.05/GB at scale. | First 100 GB/month free; Standard Tier uses public internet routing (~$0.085/GB); Premium Tier uses Google global backbone (~$0.12/GB). | First 100 GB/month free; Routing Preference: Internet egress (~$0.08/GB) vs Microsoft Global Network egress (~$0.087-$0.12/GB). |
| Dedicated private port fees | Direct Connect: $0.30/hr (1G port), $2.25/hr (10G port), $22.50/hr (100G port). Egress data transfer discounted to $0.02/GB. | Cloud Interconnect: $0.34/hr (10G Dedicated), $2.42/hr (100G Dedicated). Egress over interconnect discounted to $0.02-$0.04/GB. | ExpressRoute: Monthly port fee (Metered vs Unlimited data plans). ExpressRoute Direct: $6,000/month (100G dual-port direct pair). |

### Hybrid Enterprise Software Licensing

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Windows Server & SQL Server license mobility | AWS License Manager: Bring Your Own License (BYOL) on EC2 Dedicated Hosts or EC2 with Software Assurance; License-Included instances carry hourly license premium. | Google Cloud License Mobility: Bring your own Microsoft licenses with active Software Assurance to Sole-Tenant Nodes or Compute Engine instances. | Azure Hybrid Benefit (AHB): Use existing on-premises Windows Server and SQL Server licenses with Software Assurance to pay base Linux compute rate (up to 85% savings). |
| Red Hat Enterprise Linux (RHEL) & SUSE | AWS License Manager / AWS Marketplace pay-as-you-go or BYOL subscriptions via Red Hat Cloud Access. | Compute Engine RHEL/SLES pay-as-you-go licenses or Red Hat Cloud Access BYOL images. | Azure Hybrid Benefit for Linux: Bring existing RHEL and SUSE subscriptions to Azure VMs to pay base compute rate. |

---

## 9. Cloud Migration, Modernization & The 7 Rs Catalog Matrix

Migrating legacy on-premises datacenters to hyperscaler clouds follows structured migration frameworks (the 7 Rs of migration) paired with automated discovery and migration tooling.

### The 7 Rs Migration Strategies

| Strategy | Description | AWS Implementation | Google Cloud Implementation | Azure Implementation |
| :--- | :--- | :--- | :--- | :--- |
| 1. Rehost ("Lift and Shift") | Move workloads directly to cloud VMs without architectural code changes. | AWS Application Migration Service (MGN, block-level continuous replication) | Migrate to Virtual Machines (formerly Migrate for Compute Engine) | Azure Migrate (Server Migration tool with agentless VM replication) |
| 2. Replatform ("Lift, Tinker and Shift") | Replace underlying platform components (e.g. self-managed database to managed DB) without changing core app code. | Amazon RDS, Amazon Elastic Beanstalk, Amazon DocumentDB | Cloud SQL, App Engine, Memorystore | Azure App Service, Azure SQL Managed Instance, Azure Database for PostgreSQL |
| 3. Refactor ("Re-architect") | Rewrite application into cloud-native microservices, serverless functions, and event-driven patterns. | AWS Lambda, Amazon ECS / EKS, Amazon EventBridge, Amazon DynamoDB | Cloud Functions (Gen 2), Cloud Run, Google Kubernetes Engine (GKE), Pub/Sub | Azure Functions, Azure Container Apps, Azure Kubernetes Service (AKS), Event Grid |
| 4. Repurchase ("Drop and Shop") | Transition from legacy bespoke software to software-as-a-service (SaaS) products. | AWS Marketplace SaaS subscriptions | Google Cloud Marketplace SaaS solutions | Azure Marketplace SaaS applications, Microsoft 365, Dynamics 365 |
| 5. Retain ("Revisit") | Keep applications on-premises or on legacy infrastructure due to regulatory, latency, or depreciation reasons. | AWS Outposts, AWS Local Zones, AWS Storage Gateway | Google Distributed Cloud Edge, Anthos Bare Metal | Azure Local (Azure Stack HCI), Azure Arc |
| 6. Retire | Decommission obsolete applications identified during portfolio discovery. | Application discovery portfolio analysis, AWS Migration Hub | StratoZone portfolio analysis, Cloud Asset Inventory | Azure Migrate Discovery and Assessment, Azure Resource Graph |
| 7. Relocate ("Hypervisor Lift") | Migrate virtual machines at the hypervisor level without modifying VM images or IP schemes. | VMware Cloud on AWS | Google Cloud VMware Engine (GCVE) | Azure VMware Solution (AVS) |

### Enterprise Migration Frameworks & Programs

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| Structured enterprise migration program | AWS Migration Acceleration Program (MAP): 3-phase methodology (Assess, Mobilize, Migrate & Modernize) with financial incentives and credits. | Google Cloud Rapid Migration Program (RaMP): Comprehensive framework covering discovery, business case, migration tooling, and financial co-investments. | Azure Cloud Adoption Framework (CAF): Strategy, Plan, Ready, Adopt, Govern, and Manage phases paired with Azure Migrate and Modernize program credits. |
| Portfolio discovery & dependency mapping | AWS Migration Hub + AWS Application Discovery Service (agentless and agent-based network dependency mapping). | StratoZone (agentless datacenter scanning, TCO analysis, dependency discovery). | Azure Migrate: Discovery and Assessment (agentless VMware/Hyper-V/physical server discovery, web app assessment). |
| Database migration & schema conversion | AWS Database Migration Service (DMS) + AWS Schema Conversion Tool (SCT, converts Oracle/SQL Server to Aurora PostgreSQL/MySQL). | Google Cloud Database Migration Service (DMS, homogeneous and heterogeneous migrations to Cloud SQL and AlloyDB). | Azure Database Migration Service (DMS, offline and online database migrations to Azure SQL and Cosmos DB). |
| Offline physical data transfer appliances | AWS Snowcone (8 TB), AWS Snowball Edge (up to 210 TB storage/compute), AWS Snowmobile (exabyte-scale shipping container). | Google Cloud Transfer Appliance (40 TB or 300 TB ruggedized appliances for physical data transport). | Azure Data Box Disk (35 TB), Azure Data Box (80 TB), Azure Data Box Heavy (1 PB storage device). |

---

## 10. Sovereign Cloud, Compliance & Government Partitions Catalog Matrix

Enterprises operating in defense, public sector, healthcare, and highly regulated jurisdictions require physically isolated, sovereign, or air-gapped cloud environments with strict compliance accreditations.

### Sovereign Clouds & Government Cloud Partitions

| Purpose | AWS | Google Cloud | Azure |
| :--- | :--- | :--- | :--- |
| US Government & Defense partitions | AWS GovCloud (US-East, US-West): Physically isolated partitions operated by US citizens on US soil; DoD IL4, IL5 accredited; ITAR and FedRAMP High compliant. | Google Cloud Assured Workloads for Government: Cryptographic boundary enforcement, personnel access controls (US Persons), DoD IL4/IL5 and FedRAMP High. | Azure Government (US Gov Virginia, US Gov Texas, US Gov Arizona): Dedicated physical datacenters; DoD IL5 accredited; FedRAMP High, CJIS, ITAR compliance. |
| Classified & air-gapped defense cloud | AWS Secret Region (DoD IL6 / Secret) and AWS Top Secret Region (TS/SCI): Physically air-gapped from the public internet. | Google Distributed Cloud Hosted (air-gapped): Turnkey on-premises hardware with local control planes without public internet dependency. | Azure Government Secret and Azure Government Top Secret: Dedicated air-gapped regions built for US classified national security workloads. |
| European digital sovereignty | AWS European Sovereign Cloud: Independent cloud partition located within the EU, operated exclusively by EU resident personnel in Europe. | Google Cloud Sovereign Solutions: Partnerships with European national trusted partners (T-Systems in Germany, Thales/S3NS in France) with external key management. | Microsoft Cloud for Sovereignty / Azure Sovereign Landing Zone: Resident data residency, confidential computing, and operational transparency policies. |
| Confidential computing & secure enclaves | AWS Nitro Enclaves: Isolated compute environments with no persistent storage, no interactive access, and cryptographic attestation. | Google Cloud Confidential VMs & Confidential Space: Hardware-level memory encryption via AMD SEV-SNP and Intel TDX; multi-party collaborative computation. | Azure Confidential VMs / Azure Confidential Containers: AMD SEV-SNP and Intel SGX hardware memory encryption with Azure Attestation service. |

### Global Compliance Standards Matrix

| Regulation / Compliance Standard | AWS Coverage | Google Cloud Coverage | Azure Coverage |
| :--- | :--- | :--- | :--- |
| FedRAMP High | Certified across AWS Commercial and GovCloud regions. | Certified via Google Cloud Assured Workloads. | Certified across Azure Commercial and Azure Government. |
| DoD Cloud Computing SRG IL4 / IL5 | AWS GovCloud accredited for IL4 and IL5 workloads. | Google Assured Workloads accredited for DoD IL4 and IL5. | Azure Government accredited for DoD IL4 and IL5 workloads. |
| HIPAA / HITECH (Healthcare data) | Covered via AWS Business Associate Addendum (BAA). | Covered via Google Cloud Business Associate Agreement (BAA). | Covered via Microsoft HIPAA Business Associate Agreement (BAA). |
| PCI-DSS Level 1 (Payment Card Security) | Certified Level 1 Service Provider across all global regions. | Certified Level 1 Service Provider across all core services. | Certified Level 1 Service Provider globally. |
| SOC 1, SOC 2, SOC 3 & ISO 27001 | Annual SOC 1/2/3 reports and ISO 27001/27017/27018 certifications available via AWS Artifact. | SOC 1/2/3 and ISO 27001 certifications available via Google Cloud Compliance Reports Manager. | SOC 1/2/3 and ISO 27001 reports available via Microsoft Service Trust Portal. |

