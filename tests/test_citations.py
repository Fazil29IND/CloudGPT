from citations.citation_manager import CitationManager


def test_citations_register_format_validate_and_reset():
    citations = CitationManager()
    source_number = citations.register_source("rag", "https://docs.aws.amazon.com/ec2/", "aws", "EC2", "EC2 docs", "Overview")
    assert source_number == 1
    formatted = citations.format_citations()
    assert "## References" in formatted
    assert "1. [EC2 docs](https://docs.aws.amazon.com/ec2/)" in formatted
    assert citations.validate_answer_citations("See AWS documentation.").is_valid is True
    assert citations.validate_answer_citations("See [SOURCE 1].").is_valid is True
    invalid = citations.validate_answer_citations("See [SOURCE 2].")
    assert invalid.is_valid is False
    assert invalid.invalid_references == [2]
    citations.reset()
    assert citations.get_sources() == []
