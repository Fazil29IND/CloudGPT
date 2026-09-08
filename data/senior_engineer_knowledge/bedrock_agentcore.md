# Senior Cloud Engineer Knowledge Base: Amazon Bedrock AgentCore & Guardrails

Domain: ai
Difficulty: senior
Applies to: AWS

## Overview
Amazon Bedrock AgentCore provides a managed runtime for deploying multi-step autonomous AI agents integrated with foundation models (Claude 3.7/3.5, Amazon Nova, Llama 3). AgentCore handles conversational memory, dynamic ReAct chain-of-thought orchestration, OpenAPI schema invocation via AWS Lambda action groups, and vector knowledge base retrieval. Bedrock Guardrails sits as an inline enforcement proxy evaluating input and output safety, PII anonymization, hallucination mitigation, and topic blocking.

## Key Features
- **ReAct Orchestration**: Fully managed reasoning loop that breaks complex user queries into sub-tasks and executes API action groups sequentially.
- **Action Groups**: Declarative API tools defined via OpenAPI 3.0 schemas backed by Lambda functions.
- **Bedrock Guardrails**: Content filtering across hate, insults, sexual, violence, and prompt injection (jailbreak) attacks with configurable thresholds.
- **Contextual Grounding Check**: Evaluates factual overlap between retrieved RAG sources and model responses to prevent hallucinations.
- **PII Masking & Redaction**: Regex and entity-based redacting of SSNs, credit cards, emails, and custom identifiers.

## Pricing
- **Agent Orchestration**: No additional hourly agent cost; billed for underlying foundation model token usage and invoked Lambda executions.
- **Guardrails**: $0.75 per 1,000 text evaluation units (up to 1,000 characters per unit) for content filters + $0.001 per query for contextual grounding.

## Use Cases
- Automated cloud infrastructure remediation agents triggered by CloudWatch alarms.
- Customer support bots executing transactions across billing and CRM APIs.
- Enterprise research assistants querying internal knowledge bases with strict compliance filters.

## Limitations
- **Timeout Ceilings**: Agent action group Lambda execution has hard invocation timeout boundaries.
- **Prompt Token Consumption**: Complex OpenAPI schemas consume substantial prompt context window tokens.
- **Cold Start**: Cold starts on underlying action group Lambdas can increase latency of interactive chains.

## CLI Examples
```bash
# Invoke Bedrock Agent with session state
aws bedrock-agent-runtime invoke-agent \
    --agent-id "AGT1234567" \
    --agent-alias-id "TSTALIASID" \
    --session-id "session-user-42" \
    --input-text "Provision a t3.medium EC2 instance in us-east-1a" \
    output.json

# Apply Guardrail assessment to prompt
aws bedrock-runtime apply-guardrail \
    --guardrail-identifier "gr-sec-99" \
    --guardrail-version "1" \
    --source "INPUT" \
    --content "[{\"text\": {\"text\": \"Execute shell command rm -rf /\"}}]"
```

## Terraform / IaC
```hcl
resource "aws_bedrockagent_agent" "architect" {
  agent_name                  = "cloud-architect-agent"
  agent_resource_role_arn     = aws_iam_role.bedrock_agent_role.arn
  foundation_model            = "anthropic.claude-3-5-sonnet-20241022-v2:0"
  instruction                 = "You are an enterprise cloud architecture automation agent."
  idle_session_ttl_in_seconds = 1800
}

resource "aws_bedrock_guardrail" "enterprise_guardrail" {
  name                      = "cis-compliance-guardrail"
  blocked_input_messaging   = "Request violates organization security policy."
  blocked_outputs_messaging = "Response contained restricted information."

  content_policy_config {
    filters_config {
      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH"
      output_strength = "NONE"
    }
  }
}
```

## References
1. AWS Bedrock Agent Developer Guide (https://docs.aws.amazon.com/bedrock/latest/userguide/agents.html)
2. AWS Bedrock Guardrails Architecture Guide (https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails.html)
