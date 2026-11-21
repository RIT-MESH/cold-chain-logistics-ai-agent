# Publication Policy — what this repository contains, and what it deliberately omits

This repository was assembled with an explicit **allowlist** from the original
working project — the entire project directory was never bulk-copied. Every
included file passed a two-method secret audit (Gitleaks + a
project-specific pattern sweep) before staging, and the staged tree was
re-scanned before the initial commit.

## Substitution policy (applied to text files)

These values are **not secrets**, but they were replaced with placeholders to
avoid publishing unnecessary live-infrastructure context:

| Original category | Published as | Why |
|---|---|---|
| AWS account id (also where embedded in resource names) | `123456789012` | account ids are not passwords but reveal infrastructure context |
| EC2 instance id | `i-0123456789abcdef0` | operational identifier, unnecessary in examples |
| Internal/private + historical public IPs | RFC5737 documentation addresses (`203.0.113.x`, `10.0.0.10`) | live network context |
| Internal RDS endpoint FQDN | `…example.rds.amazonaws.com` | internal endpoint |
| A hardcoded API key found in one probe harness | `os.environ["NVIDIA_API_KEY"]` | **actual secret — removed**; the key is treated as compromised-publication-prevented and was already scheduled for out-of-band rotation |

**Kept intentionally:** the CloudFront distribution URL and the Cognito
hosted-login domain are public URLs (published in the Hugging Face portfolio
for years), not credentials. Cognito pool/client ids are public identifiers
by design.

## Excluded by policy (never staged)

- **Real secrets**: the runtime `.env` (live NVIDIA/Pinecone API keys,
  database identity credentials) — never staged.
- **Terraform state and plans**: `errored.tfstate`, all `tfplan-*` files and
  `plan_*.txt` (saved plans embed generated secret values). Terraform state
  is never published.
- **Private operations material**: operations runbooks, session checkpoints
  and resume records, internal approval/cost-approval packages, the internal
  audit log, handover indexes, and project working notes.
- **Archives and third-party materials**: pre-packaged zips, the original
  course/business PDFs, and personal notes.
- **Runtime caches**: provider plugin caches, `__pycache__`, `.pytest_cache`.

## Sanitized examples

`examples/env*.example` and `infra/environments/*/dev.tfvars.example` use
descriptive placeholders only (`YOUR_NVIDIA_API_KEY`, `YOUR_DATABASE_PASSWORD`).
No real credential has ever been used as an example value.

## Known limitations of this process

- The synthetic CSV dataset files are published byte-true (no substitution) —
  they contain only synthetic shipment data by design.
- Documentation written during live operations (evidence reports) had the
  substitution pass applied; a residual mention of an operational identifier
  that does not match the substitution table is possible, though none was
  found by the scanners.

## Third-party exclusion (2026-11-22 final gate)

- `data/policy/Cold_Chain_Incident_SOP_v2.md` (demo-baseline SOP derived from
  third-party course materials, marked 'Internal Operations Only', no
  redistribution license) is EXCLUDED from the publication. The parser test
  covering it skips gracefully in its absence. All other policy documents
  (SYNTHETIC_SOP_A-F) are project-authored. The public AWS RDS global CA
  bundle is redistributed per AWS's published purpose for it.
