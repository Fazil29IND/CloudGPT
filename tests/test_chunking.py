from chunking.metadata_extractor import MetadataExtractor
from chunking.semantic_chunker import SemanticChunker


SAMPLE = """# EC2 Guide

## Overview

EC2 supplies virtual machines for workloads. Choose a family based on workload needs.

## Example

```bash
aws ec2 describe-instances
```

## Limits

| Limit | Value |
| --- | --- |
| Instances | 20 |
"""


def test_chunker_keeps_heading_context_and_metadata():
    chunks = SemanticChunker(max_chunk_chars=1000).chunk_document(SAMPLE, provider="aws", category="compute", service="ec2", url="https://docs.aws.amazon.com/ec2/")
    assert chunks
    assert all(chunk.provider == "aws" and chunk.service == "ec2" for chunk in chunks)
    assert any("EC2 Guide" in chunk.heading_breadcrumb for chunk in chunks)
    assert all(chunk.content_hash and chunk.char_count == len(chunk.text) for chunk in chunks)


def test_chunker_preserves_code_and_table_in_context():
    chunks = SemanticChunker(max_chunk_chars=1000).chunk_document(SAMPLE, provider="aws", service="ec2")
    combined = "\n".join(chunk.text for chunk in chunks)
    assert "aws ec2 describe-instances" in combined
    assert "| Instances | 20 |" in combined


def test_metadata_extractor_identifies_provider_region_and_deprecation():
    metadata = MetadataExtractor().extract(
        "This service is deprecated. We recommend using New Service instead. Deploy in us-east-1.",
        "https://docs.aws.amazon.com/example/userguide/",
    )
    assert metadata.provider == "aws"
    assert metadata.document_type == "user_guide"
    assert metadata.is_deprecated is True
    assert metadata.regions_mentioned == ["us-east-1"]
