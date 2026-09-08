# Senior Cloud Engineer Knowledge Base: Amazon Q Developer (IDE, CLI & Console)

Domain: ai
Difficulty: senior
Applies to: AWS

## Overview
Amazon Q Developer is a generative AI assistant for software development and cloud operations integrated into IDEs (VS Code, JetBrains), the command-line interface (macOS/Linux/Windows shell), and the AWS Management Console. Built with foundational software engineering knowledge and deep AWS architecture expertise, Amazon Q Developer assists with code generation, multi-file code refactoring, legacy Java/framework version migrations, CloudWatch log diagnostic postmortems, and infrastructure-as-code generation.

## Key Features
- **Amazon Q CLI (Natural Language Terminal)**: Context-aware shell completion that translates natural language intent into complex AWS CLI, git, docker, and bash pipelines with dry-run safety explanations.
- **Code Transformation Agent**: Automated multi-repo Java language upgrades (Java 8/11 to Java 17/21), updating deprecated APIs, build files, and generating unit test suites.
- **Console & Diagnostic Troubleshooting**: Direct integration with AWS CloudWatch and CloudTrail to analyze stack traces, explain root causes, and propose remediation steps.
- **Security Vulnerability Scanning**: In-line code analysis that detects hardcoded secrets, SQL injection, CWE vulnerabilities, and proposes autofixes.
- **AWS Account Diagnostics**: Natural language querying of live AWS account resources, VPC topologies, and IAM policy permissions via Console chat.

## Pricing
- **Free Tier**: 50 chat queries per user/month, 5 code transformation tasks per month, basic CLI integration.
- **Pro Tier**: $19 per user/month for enterprise SSO management, unlimited chat, custom code repository indexing, and enhanced security scanning limits.

## Use Cases
- Accelerated infrastructure development: generating modular Terraform and AWS CDK stacks from architecture prompts.
- Rapid shell debugging: translating troubleshooting intentions into complex `aws ec2` or `kubectl` filtering queries without manual man-page lookups.
- Automated legacy code modernization: bulk upgrading enterprise Java Spring applications to modern runtimes.

## Limitations
- **Private Network VPC Isolation**: Q Developer Cloud interactions require egress to public AWS endpoints unless AWS PrivateLink endpoints are configured.
- **Custom Policy Gating**: Large enterprises must configure IAM identity center policies to restrict IP/code retention and prevent telemetry sharing.
- **Model Output Verification**: Generated IaC templates must undergo static syntax validation and security scanning before production commit.

## CLI Examples
```bash
# Ask Amazon Q CLI to generate complex AWS command
q "Find all untagged S3 buckets and output their creation dates as JSON"

# Translate diagnostic intent
q "List all pods in kube-system restart count greater than 5"

# Trigger workspace security scan via CLI
q scan
```

## Terraform / IaC
```hcl
resource "aws_iam_policy" "q_developer_access" {
  name        = "AmazonQDeveloperUserAccess"
  description = "Permissions for developers using Amazon Q in IDE and CLI"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "q:SendMessage",
          "q:StartConversation",
          "q:GenerateCode",
          "codewhisperer:GenerateRecommendations"
        ]
        Resource = "*"
      }
    ]
  })
}
```

## References
1. Amazon Q Developer User Guide (https://docs.aws.amazon.com/amazonq/latest/aws-builder-use-ug/what-is.html)
2. Amazon Q CLI Documentation (https://docs.aws.amazon.com/amazonq/latest/qcli-ug/what-is.html)
