# Bootstrap the Terraform state bucket + dedicated CMK — RUN OUT-OF-BAND by
# the operator, ONCE per account, BEFORE the first `terraform init`
# (blueprint 01 §2, correction C2 / ADR-14). Terraform cannot manage the
# bucket its own state lives in.
#
# Usage (from an administrator machine with aws CLI v2 configured):
#   ./scripts/bootstrap_tfstate.ps1 -AccountId 123456789012 [-Region us-east-1]
#
# Output: the bucket name + KMS key ARN to paste into infra/backend.hcl.example
# copies (backend.dev.hcl / backend.prod.hcl).
#
# This script creates ONLY the state bucket and its CMK — no application
# infrastructure (Phase 1 `terraform apply` is a separate, approved step).
param(
    [Parameter(Mandatory = $true)]
    [string]$AccountId,
    [string]$Region = "us-east-1"
)

$ErrorActionPreference = "Stop"
$Bucket = "cc-tfstate-$AccountId"

Write-Host "== Bootstrap Terraform state bucket (blueprint 01 SS2, C2/ADR-14) =="
Write-Host "Account: $AccountId  Region: $Region  Bucket: $Bucket"

# 1. Dedicated CMK for state encryption (SSE-KMS, 01 SS2). NOTE: KMS keys are
#    REGIONAL — create in the state bucket's region.
$kms = aws kms create-key --region $Region --description "Terraform state bucket SSE-KMS (cold-chain)" --output json | ConvertFrom-Json
$KeyArn = $kms.KeyMetadata.Arn
aws kms create-alias --region $Region --alias-name alias/coldchain-tfstate --target-key-id $kms.KeyMetadata.KeyId
Write-Host "KMS key: $KeyArn"

# 2. Bucket with versioning + SSE-KMS + public access blocked.
# us-east-1 quirk: LocationConstraint must NOT be passed for us-east-1.
if ($Region -eq "us-east-1") {
    aws s3api create-bucket --bucket $Bucket --region us-east-1
} else {
    aws s3api create-bucket --bucket $Bucket --region $Region --create-bucket-configuration LocationConstraint=$Region
}
aws s3api put-bucket-versioning --bucket $Bucket --versioning-configuration Status=Enabled
# JSON via --cli-input-json (PowerShell mangles inline double-quoted JSON):
$encJson = @{
    Rules = @(
        @{ ApplyServerSideEncryptionByDefault = @{ SSEAlgorithm = "aws:kms"; KMSMasterKeyID = $kms.KeyMetadata.KeyId }; BucketKeyEnabled = $true }
    )
} | ConvertTo-Json -Depth 5 -Compress
$encJson | Out-File -Encoding utf8 "$env:TEMP\cc-tfstate-enc.json"
aws s3api put-bucket-encryption --bucket $Bucket --cli-input-json "file://$env:TEMP\cc-tfstate-enc.json"
Remove-Item "$env:TEMP\cc-tfstate-enc.json" -Force
aws s3api put-public-access-block --bucket $Bucket --public-access-block-configuration `
    BlockPublicAcls=$true,IgnorePublicAcls=$true,BlockPublicPolicy=$true,RestrictPublicBuckets=$true

Write-Host ""
Write-Host "DONE. Copy into infra/backend.dev.hcl / backend.prod.hcl:"
Write-Host "  bucket       = ""$Bucket"""
Write-Host "  region       = ""$Region"""
Write-Host "  use_lockfile = true"
Write-Host "  encrypt      = true"
Write-Host "  kms_key_id   = ""$KeyArn"""
Write-Host "  key          = ""env/dev/terraform.tfstate""   # env/prod/... for prod"
