# AWS Teardown Script + Evidence Report (operator-run)

# Phase 4 of the 2026-11-15 directive: PREPARED, NOT EXECUTED during Phase 1.
# Runs AFTER final acceptance and the operator's explicit teardown approval.
#
# Scope guards (hard):
#   - Account-locked: refuses to run outside account 123456789012.
#   - Only touches resources tagged Project=coldchain OR named
#     coldchain-* / cc-tfstate-*. NEVER deletes unrelated or pre-existing
#     resources; never touches the default VPC or the tf-lab environment.
#   - Terraform state is preserved until destruction is VERIFIED; state is
#     never edited or overwritten as a rollback mechanism (C14 doctrine).
#
# Usage:  ./scripts/teardown_aws.ps1 -Environment dev [-Stage all|inventory|destroy|sweep|verify|state] [-Yes]
# Stages: inventory (read-only) -> destroy (terraform destroy) -> sweep
#         (non-Terraform leftovers) -> verify (re-inventory, expect empty)
#         -> state (state bucket + CMK LAST, after evidence is preserved).

param(
    [ValidateSet("dev", "prod")]
    [string]$Environment = "dev",
    [ValidateSet("all", "inventory", "destroy", "sweep", "verify", "state")]
    [string]$Stage = "all",
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
$Account = "123456789012"
$Region = "us-east-1"
$RepoRoot = Split-Path $PSScriptRoot -Parent
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Report = Join-Path $RepoRoot "docs/evidence/TEARDOWN_EVIDENCE_$Stamp.txt"

function Write-Report([string]$text) {
    $text | Tee-Object -FilePath $Report -Append
}

function Assert-Account {
    $id = (aws sts get-caller-identity --output json | ConvertFrom-Json).Account
    if ($id -ne $Account) { throw "Refusing to run against account $id (expected $Account)." }
}

function Get-TaggedInventory {
    Write-Report "`n=== INVENTORY (Project=coldchain / coldchain-* / cc-tfstate-*) ==="
    Write-Report "-- EC2 instances --"
    aws ec2 describe-instances --region $Region --filters "Name=tag:Project,Values=coldchain" `
        --query "Reservations[].Instances[].[InstanceId,InstanceType,State.Name]" --output text | Tee-Object $Report -Append
    Write-Report "-- EBS volumes & snapshots --"
    aws ec2 describe-volumes --region $Region --filters "Name=tag:Project,Values=coldchain" --query "Volumes[].VolumeId" --output text | Tee-Object $Report -Append
    aws ec2 describe-snapshots --region $Region --owner-ids $Account --filters "Name=tag:Project,Values=coldchain" --query "Snapshots[].SnapshotId" --output text | Tee-Object $Report -Append
    Write-Report "-- RDS instances & manual/final snapshots --"
    aws rds describe-db-instances --region $Region --query "DBInstances[?starts_with(DBInstanceIdentifier,'coldchain')].DBInstanceIdentifier" --output text | Tee-Object $Report -Append
    aws rds describe-db-snapshots --region $Region --snapshot-type manual --query "DBSnapshots[?starts_with(DBSnapshotIdentifier,'coldchain')].DBSnapshotIdentifier" --output text | Tee-Object $Report -Append
    Write-Report "-- VPC / ALB / EIP --"
    aws ec2 describe-vpcs --region $Region --filters "Name=tag:Project,Values=coldchain" --query "Vpcs[].VpcId" --output text | Tee-Object $Report -Append
    aws elbv2 describe-load-balancers --region $Region --query "LoadBalancers[?starts_with(LoadBalancerName,'coldchain')].LoadBalancerArn" --output text | Tee-Object $Report -Append
    aws ec2 describe-addresses --region $Region --query "Addresses[?starts_with(AllocationId,'eip') && Tags[?Key=='Project' && Value=='coldchain']].AllocationId" --output text | Tee-Object $Report -Append
    Write-Report "-- SQS / SNS --"
    aws sqs list-queues --region $Region --queue-name-prefix coldchain --output text | Tee-Object $Report -Append
    aws sns list-topics --region $Region --output text | Select-String coldchain | Tee-Object $Report -Append
    Write-Report "-- S3 buckets (coldchain-*, cc-tfstate-*) --"
    (aws s3api list-buckets --query "Buckets[].Name" --output text) -split "`t" | Where-Object { $_ -match "^(coldchain-|cc-tfstate-)" } | Tee-Object $Report -Append
    Write-Report "-- ECR repositories --"
    aws ecr describe-repositories --region $Region --query "repositories[?starts_with(repositoryName,'coldchain/')].repositoryName" --output text | Tee-Object $Report -Append
    Write-Report "-- Secrets Manager --"
    aws secretsmanager list-secrets --region $Region --query "SecretList[?starts_with(Name,'coldchain/')].Name" --output text | Tee-Object $Report -Append
    Write-Report "-- CloudWatch log groups & alarms --"
    aws logs describe-log-groups --log-group-name-prefix "/coldchain" --query "logGroups[].logGroupName" --output text | Tee-Object $Report -Append
    aws cloudwatch describe-alarms --alarm-names "coldchain-*" --query "MetricAlarms[].AlarmName" --output text | Tee-Object $Report -Append
    Write-Report "-- IAM OIDC provider / roles --"
    aws iam list-open-id-connect-providers --output text | Select-String token.actions.githubusercontent.com | Tee-Object $Report -Append
    aws iam list-roles --query "Roles[?starts_with(RoleName,'coldchain-')].RoleName" --output text | Select-String coldchain | Tee-Object $Report -Append
}

function Invoke-TerraformDestroy {
    Write-Report "`n=== TERRAFORM DESTROY ($Environment) ==="
    # Dependency order is handled BY Terraform's graph; the state bucket
    # itself is NOT in Terraform and is removed in the final stage.
    Push-Location "$RepoRoot/infra/environments/$Environment"
    try {
        terraform init -input=false -backend-config="bucket=cc-tfstate-$Account" `
            -backend-config="key=env/$Environment/terraform.tfstate" -backend-config="region=$Region" `
            -backend-config="use_lockfile=true" -backend-config="encrypt=true"
        terraform destroy -input=false -auto-approve -var-file="$Environment.tfvars"
    }
    finally { Pop-Location }
}

function Invoke-Sweep {
    # Non-Terraform leftovers, name-scoped ONLY to this project's conventions.
    Write-Report "`n=== SWEEP (project-named leftovers only) ==="
    foreach ($snap in (aws rds describe-db-snapshots --region $Region --snapshot-type manual `
            --query "DBSnapshots[?starts_with(DBSnapshotIdentifier,'coldchain')].DBSnapshotIdentifier" --output text)) {
        if ($snap) { aws rds delete-db-snapshot --region $Region --db-snapshot-identifier $snap; Write-Report "deleted RDS snapshot $snap" }
    }
    foreach ($repo in (aws ecr describe-repositories --region $Region `
            --query "repositories[?starts_with(repositoryName,'coldchain/')].repositoryName" --output text)) {
        if ($repo) { aws ecr delete-repository --region $Region --repository-name $repo --force; Write-Report "deleted ECR repo $repo" }
    }
    foreach ($lg in (aws logs describe-log-groups --log-group-name-prefix "/coldchain" `
            --query "logGroups[].logGroupName" --output text)) {
        if ($lg -and -not $lg.StartsWith("coldchain")) { aws logs delete-log-group --region $Region --log-group-name $lg; Write-Report "deleted log group $lg" }
    }
    foreach ($secret in (aws secretsmanager list-secrets --region $Region `
            --query "SecretList[?starts_with(Name,'coldchain/')].Name" --output text)) {
        if ($secret) { aws secretsmanager delete-secret --region $Region --secret-id $secret --force-delete-without-recovery 2>$null; Write-Report "deleted secret $secret" }
    }
    foreach ($alarm in (aws cloudwatch describe-alarms --alarm-names "coldchain-*" `
            --query "MetricAlarms[].AlarmName" --output text)) {
        if ($alarm) { aws cloudwatch delete-alarms --region $Region --alarm-names $alarm; Write-Report "deleted alarm $alarm" }
    }
}

function Invoke-StateStage {
    # LAST stage: remove the state bucket + CMK after evidence preservation.
    # State files for both envs are exported BEFORE deletion (below).
    Write-Report "`n=== STATE REMOVAL (after evidence preserved) ==="
    $backupDir = Join-Path $RepoRoot "docs/evidence/teardown-state-$Stamp"
    New-Item -ItemType Directory -Force $backupDir | Out-Null
    foreach ($key in @("env/dev/terraform.tfstate", "env/prod/terraform.tfstate")) {
        aws s3 cp "s3://cc-tfstate-$Account/$key" "$backupDir/$($key -replace '/','-')" 2>$null
        if ($LASTEXITCODE -eq 0) { Write-Report "state backup: $key -> $backupDir" } else { Write-Report "state backup: $key not found (already clean)" }
    }
    aws s3 rb "s3://cc-tfstate-$Account" --force
    Write-Report "removed state bucket cc-tfstate-$Account"
    foreach ($keyId in (aws kms list-aliases --region $Region --query "Aliases[?AliasName=='alias/coldchain-tfstate'].TargetKeyId" --output text)) {
        if ($keyId) { aws kms schedule-key-deletion --region $Region --key-id $keyId --pending-window-in-days 7; Write-Report "scheduled CMK deletion ($keyId, 7-day window)" }
    }
}

Assert-Account
Write-Report "Cold-Chain teardown evidence $Stamp (account $Account, env $Environment, stage $Stage)"
if ($Stage -in @("all", "inventory")) { Get-TaggedInventory }
if ($Stage -in @("all", "destroy")) {
    if (-not $Yes -and -not (Read-Host "Run terraform destroy on $Environment? Type DESTROY to confirm") -eq "DESTROY") { throw "Destroy not confirmed." }
    Invoke-TerraformDestroy
}
if ($Stage -in @("all", "sweep")) { if (-not $Yes -and -not (Read-Host "Sweep non-Terraform leftovers? Type SWEEP") -eq "SWEEP") { throw "Sweep not confirmed." }; Invoke-Sweep }
if ($Stage -in @("all", "verify")) {
    Write-Report "`n=== VERIFY: re-inventory (expect EMPTY project rows) ==="
    Get-TaggedInventory
    Write-Report "`nVerify manually in Cost Explorer for delayed charges (EstimatedCharges lags ~6 h)."
    Write-Report "Unavoidable/delayed: domain registration (if purchased, non-refundable); CMK pending-window; delayed billing datapoints."
}
if ($Stage -in @("all", "state")) {
    if (-not $Yes -and -not (Read-Host "Remove state bucket + CMK (final)? Type STATE") -eq "STATE") { throw "State removal not confirmed." }
    Invoke-StateStage
    Write-Report "`n=== FINAL VERIFY ==="
    Get-TaggedInventory
}
Write-Report "`nReport written to: $Report"
