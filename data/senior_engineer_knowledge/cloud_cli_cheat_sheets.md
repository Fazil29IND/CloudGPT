# Senior Cloud Engineer Knowledge Base: Cloud CLI Cheat Sheets

Domain: cli
Difficulty: senior
Applies to: AWS CLI, gcloud CLI, Azure CLI

## AWS CLI Power Commands

### Identity & Access
```bash
# Who am I (account, ARN, region resolution order)
aws sts get-caller-identity

# Simulate whether a principal can perform actions (policy debugging)
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::123456789012:role/AppRole \
  --action-names s3:GetObject sts:AssumeRole \
  --resource-arns arn:aws:s3:::my-bucket/key arn:aws:iam::123456789012:role/Target

# Assume role with MFA and export session
creds=$(aws sts assume-role --role-arn arn:aws:iam::222233334444:role/Ops \
  --role-session-name cli --serial-number arn:aws:iam::111122223333:mfa/user \
  --token-code 123456 --query Credentials --output json)
export AWS_ACCESS_KEY_ID=$(echo $creds | jq -r .AccessKeyId)
export AWS_SECRET_ACCESS_KEY=$(echo $creds | jq -r .SecretAccessKey)
export AWS_SESSION_TOKEN=$(echo $creds | jq -r .SessionToken)
```

### EC2 / Compute
```bash
# Find spot interruption-prone capacity and price across types
aws ec2 describe-spot-price-history --instance-types m7g.xlarge m6i.xlarge \
  --product-descriptions "Linux/UNIX" --start-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --query 'SpotPriceHistory[].{type:InstanceType,az:AvailabilityZone,price:SpotPrice}'

# Instances with public IP + name in one view
aws ec2 describe-instances \
  --filters "Name=instance-state-name,Values=running" \
  --query 'Reservations[].Instances[].{id:InstanceId,name:Tags[?Key==`Name`]|[0].Value,ip:PublicIpAddress,type:InstanceType}' \
  --output table
```

### S3
```bash
# Big-key / size analysis without downloading
aws s3 ls s3://bucket --recursive --summarize | tail -3

# Server-side copy across accounts with owner override
aws s3 cp s3://src/ s3://dst/ --recursive \
  --metadata-directive COPY \
  --expected-bucket-owner 111122223333

# Select with SQL instead of full download
aws s3api select-object-content --bucket my-bucket --key big.csv \
  --expression "SELECT * FROM s3object s WHERE s.amount > 1000" \
  --expression-type SQL --input-serialization '{"CSV":{}}' \
  --output-serialization '{"CSV":{}}' result.csv
```

### Diagnostics
```bash
aws cloudtrail lookup-events --lookup-attributes AttributeKey=ResourceName,AttributeValue=my-bucket \
  --max-results 20 --output table

aws logs tail /aws/lambda/func --since 1h --follow --filter-pattern ERROR

aws support-trusted-advisor describe-check-result --check-id <id> --language en
```

## gcloud CLI Power Commands

### Projects & Identity
```bash
gcloud config list; gcloud auth list
gcloud config set project my-project
gcloud auth application-default login     # ADC for local dev

# Effective IAM for a resource (who can do what)
gcloud projects get-iam-policy my-project --flatten="bindings[].members" \
  --format="table(bindings.role, bindings.members)" | grep -i roles/editor

# Policy trouble-shooting for service account impersonation
gcloud iam service-accounts get-iam-policy SA@PROJECT.iam.gserviceaccount.com
```

### Compute / GKE
```bash
gcloud compute instances list --format="table(name,zone,machineType.basename(),networkInterfaces[0].accessConfigs[0].natIP)"

# Spot (preemptible) with maintenance action TERMINATE
gcloud compute instances create spot-box --zone=us-central1-a \
  --machine-type=e2-standard-4 --provisioning-model=SPOT \
  --instance-termination-action=DELETE

gcloud container clusters describe prod --region us-central1 \
  --format="value(status,endpoint,currentMasterVersion)"

gcloud container clusters upgrade prod --master --region us-central1 --cluster-version 1.31
```

### Storage / BigQuery
```bash
gsutil du -sh gs://bucket/**        # sizes (gsutil); prefer gcloud storage now:
gcloud storage du --summarize --recursive gs://bucket

gcloud storage objects update "gs://bucket/**" --cache-control="public,max-age=3600"

bq query --use_legacy_sql=false 'SELECT _TABLE_SUFFIX, COUNT(*) FROM `p.dataset.t*` WHERE _TABLE_SUFFIX BETWEEN "20260101" AND "20260201" GROUP BY 1'

bq cp p:dataset.src p:dataset.bak_$(date +%F)   # snapshot table
```

### VPC Service Controls & Diagnostics
```bash
gcloud access-context-manager perimeters list --policy=POLICY_ID
gcloud access-context-manager levels describe LEVEL_NAME --policy=POLICY_ID

gcloud logging read 'severity>=ERROR AND timestamp>="2026-08-30T00:00:00Z"' \
  --limit 50 --format=json | jq '.[].textPayload'
```

## Azure CLI (az) Power Commands

### Identity & Auth
```bash
az login --use-device-code
az account set --subscription "Prod-Sub"
az account show --query "{name:name, id:id, tenant:tenantId}"

# Who has access (role assignments scoped to a resource group)
az role assignment list --resource-group rg-prod --include-inherited \
  --query "[].{principal:principalName, role:roleDefinitionName, scope:scope}" -o table

# Impersonate with a managed identity token (from a VM/App Service)
token=$(curl -s -H Metadata:true "http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://management.azure.com/" | jq -r .access_token)
az account get-access-token  # interactive path
```

### Compute / AKS
```bash
az vm list -g rg-prod --query "[].{name:name, size:hardwareProfile.vmSize, ip:publicIps}" -o table

az aks show -g rg-prod -n aks-prod --query "{k8s:kubernetesVersion, fqdn:fqdn, sku:sku.name}"
az aks nodepool list -g rg-prod --cluster-name aks-prod \
  --query "[].{name:name, mode:mode, vm:vmSize, count:count, scale:enableAutoScaling}"

az aks command invoke -g rg-prod -n aks-prod \
  --command "kubectl get pods -A --field-selector=status.phase!=Running"
```

### Storage & Private Endpoints
```bash
az storage account list -g rg-prod --query "[].{name:name, sku:sku.name, https:enableHttpsTrafficOnly}"

# Verify private endpoint DNS resolution from a VM extension context
az network private-endpoint list -g rg-prod \
  --query "[].{name:name, subnet:subnet.id, groupIds:groupIds}"
az network private-dns zone list -g rg-hub -o table
az network private-dns link vnet list -g rg-hub --zone-name privatelink.blob.core.windows.net -o table
```

### Cost & Diagnostics
```bash
az costmanagement query --type ActualCost --timeframe MonthToDate \
  --scope "/subscriptions/SUB_ID" \
  --query "[].{cost:Cost, resource:ResourceId}" -o tsv | sort -k1 -rn | head

az monitor activity-log list --resource-id "/subscriptions/SUB/resourceGroups/rg-prod" \
  --start-time 2026-08-30T00:00:00Z --max-events 30 -o table

az network watcher flow-log show -g rg-prod --nsg nsg-app   # NSG flow logs for L4 triage
```

## Cross-Cloud Quick Reference

| Task | AWS | GCP | Azure |
|---|---|---|---|
| Current identity | `aws sts get-caller-identity` | `gcloud auth list` / `gcloud config list` | `az account show` |
| Tail logs | `aws logs tail LOGGROUP --follow` | `gcloud logging read FILTER` | `az monitor activity-log list` |
| List compute | `aws ec2 describe-instances` | `gcloud compute instances list` | `az vm list` |
| Object storage copy | `aws s3 cp --recursive` | `gcloud storage cp -r` | `az storage blob copy start-batch` |
| IAM debug | `simulate-principal-policy` | `get-iam-policy --flatten` | `az role assignment list` |
| Kubectl from CLI | EKS: `aws eks update-kubeconfig` | GKE: `gcloud container clusters get-credentials` | AKS: `az aks get-credentials` |

Shell ergonomics: enable `aws cli v2` pager disable (`AWS_PAGER=""`) for
scripts, `gcloud --quiet` for automation, `az config set core.output=jsonc`
for readable scripts. Always pin `--region`/`--location` in CI to avoid
implicit resolution drift.
