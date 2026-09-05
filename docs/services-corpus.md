# CloudGPT — Services Corpus & Knowledge Base

## Overview
The CloudGPT knowledge base comprises 848 cloud service entries (`sources/sources.json`) across AWS, Google Cloud, and Microsoft Azure, spanning 27 catalog categories plus 15 end-to-end cloud architecture lifecycle phases:
- Compute (EC2, Lambda, ECS, GKE, Cloud Functions, Azure VMs, AKS, Container Apps)
- Storage (S3, EBS, EFS, Cloud Storage, Azure Blob, Azure Files)
- Databases (RDS, Aurora, DynamoDB, Cloud SQL, Spanner, Firestore, Cosmos DB)
- Networking & Content Delivery (VPC, CloudFront, Route 53, Cloud Load Balancing, Azure VNet, Front Door)
- Security, Identity & Compliance (IAM, KMS, Cognito, Cloud IAM, Azure AD / Entra ID)
- Machine Learning & Analytics (SageMaker, BigQuery, Vertex AI, Azure OpenAI)

## Ingestion Architecture
1. **Source Documents**: Maintained in `Services.md` and `sources/`.
2. **Chunking**: Semantic chunking (`chunking/semantic_chunker.py`) with provider and service metadata preservation.
3. **Indexing**: Dual indexing into Pinecone dense vector index and BM25S sparse index (`ingest_services.py`), written into versioned namespaces (`v1` / `v2` — see `embeddings/pinecone_manager.py`).
4. **Playbooks**: `data/senior_engineer_knowledge/` holds senior-engineer decision playbooks indexed alongside the service catalog.
