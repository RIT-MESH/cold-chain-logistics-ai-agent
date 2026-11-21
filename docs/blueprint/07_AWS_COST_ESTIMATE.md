# 07 ~ AWS Cost Estimate (requirement 8)

Revision 4 (2026-11-03: pricing-calculator estimate is a hard precondition
before provisioning; t3.large confirmed as a load-test candidate ~ R4-4).
**Assumed region: us-east-1 (N. Virginia) ~ region selection remains an
unresolved decision (U-2); prices scale differently elsewhere.**

> **Hard precondition (R4-4):** every figure in this document is a planning
> estimate. **A current AWS Pricing Calculator export for the selected
> region is required before any resource is provisioned**, and the estimate
> must be re-approved at Phase 1 acceptance and reconciled against the first
> actual bill at Phase 7. The capacity line (EC2 t3.large) is likewise a
> **test candidate pending the measured load test** at Phase 3 acceptance
> (`01` ~9), not a conclusion.

~ All figures are **planning estimates at on-demand list prices**, must be
re-validated with the AWS Pricing Calculator for the chosen region before
approval. **NVIDIA NIM token charges are a separate bill, never part of AWS
(R6-1 ~ replaces the former Ollama Max subscription line).**

> **Revision 6 amendment (2026-11-15, R6-1/R6-2 ~ LLM bill re-based to
> NVIDIA NIM):** the separate (non-AWS) bill is now **NVIDIA NIM per-token
> spend** for `z-ai/glm-5.3-flash`. Measured volumes: ~8.3k tokens for one
> live multi-tool conversation (C13, 2026-11-15); ~220k tokens for a full
> qualification battery of 15 conversations (2026-11-09) ~ **~15k tokens per
> tool-using conversation** as the planning figure. NIM publishes **no
> credit-balance API ~ usage objects only** (F-4) and no in-account spending
> dashboard; the CloudWatch `ColdChain/NimTokens` metric (Phase 3, C12) is
> the application-side consumption signal. Current per-token prices must be
> read from build.nvidia.com pricing at the Phase 1 pricing-calculator
> precondition and recorded in the approval export. AWS-side figures in ~3/~4
> are unchanged by R6-1/R6-2 (LLM inference never ran in AWS ~ ADR-2).

## 1. The two bills (explicit separation)

| Bill | Items | Est. monthly |
|---|---|---|
| **NVIDIA NIM (not AWS)** ~ R6-1 | per-token spend on `z-ai/glm-5.3-flash`; planning figure ~15k tokens per tool-using conversation (~220k tokens per 15-conversation battery, qualification-measured 2026-11-09; single live C13 conversation 8,263 tokens 2026-11-15) | variable ~ priced at build.nvidia.com per-token rates; **no subscription line, no credit system** (usage object only, F-4) |
| AWS (below) | all infrastructure | see tiers |

## 2. RDS for SQL Server ~ editions re-verified (correction C3)

Claims corrected against the current AWS RDS for SQL Server documentation
and AWS SQL Server licensing page (URLs in `12_AUDIT_CORRECTIONS.md`):

| Edition | License | Internal-app use | Constraints | Cost impact |
|---|---|---|---|---|
| **Express** | free | **Permitted** (previous blueprint wrongly called it "test-only" ~ Express IS licensed for internal applications; Microsoft only caps capability, not workload type) | 10 GB per database (dataset ~ 25 MB ~ far under); no SQL Agent; no Multi-AZ support; only small burstable classes (db.t3.micro / db.t3.small) | cheapest viable tier |
| **Web** | license-included ($) | **NOT permitted** ~ Microsoft licensing restricts Web edition to public/internet-facing web workloads; our internal operator tool does not qualify (previous blueprint wrongly recommended Web for the production-shaped tier) | single-AZ only | excluded from our tiers |
| **Standard** | license-included ($$$) | Permitted | Multi-AZ optional (2~ instance cost); broader classes (m5/r5 families) | production-shaped tier |
| Enterprise | license-included ($$$$) | Permitted | overkill for a synthetic dataset | not proposed |

Also factored (per current RDS docs): gp3 storage is supported for SQL
Server. (Databases-per-instance claim corrected below, R3-3.)

**Selected for this environment: Express (dev/test tier) with Standard as the
documented upgrade path.** SQL Agent absence is acceptable: no SQL Agent jobs
exist in this design (scheduling runs via SSM/CloudWatch Events instead).

**Correction R3-3 ~ databases per instance:** the previous revision claimed
RDS for SQL Server supports only one database per instance. That statement
was wrong: **AWS supports multiple user databases per RDS SQL Server
instance**, subject to edition limits (e.g., Express caps each *database* at
10 GB), instance-class storage limits, and deployment mode. Our design
nonetheless uses a single `ColdChainLogistics` database ~ a simplicity
choice, not a platform constraint.

## 3. Dev/test tier (recommended; us-east-1, on-demand)

| Item | Spec | Est. $/mo | Notes |
|---|---|---|---|
| EC2 app host | t3.large (2 vCPU/8 GB), 730 h ~ **candidate pending Phase 3 load test (R4-4)** | ~61 | Streamlit + gateway + LibreChat + MongoDB + Grafana + worker |
| EC2 public IPv4 | 1 address ~ $0.005/hr | ~3.6 | required for IGW egress without NAT (chosen design `01` ~3) |
| EBS gp3 | 50 GB | ~6 | images, Mongo volume |
| RDS SQL Server **Express** | db.t3.small (2 GB RAM) | ~26 | db.t3.micro (~13) viable but 1 GB RAM is tight for SQL Server; both valid Express classes |
| RDS storage gp3 | 20 GB | ~2.5 | |
| RDS automated backups | ~ DB-size free allocation | ~0 | 25 MB DB ~ negligible overage |
| ALB | 1 ALB + LCU + ALB public IPv4 | ~22 | incl. ~$3.6 public-IPv4; can be omitted in a locked-down demo (SSM tunnels only ~ save ~22) |
| ACM | 1 public cert | 0 | |
| S3 telemetry archive | ~1 GB | <1 | |
| SQS + DLQ | <1M requests | ~0 | free tier |
| SNS | email | <1 | |
| Secrets Manager | ~9 secrets ~ $0.40 + API | ~4 | values seeded out-of-band |
| CloudWatch | logs ~5 GB ingest + metrics + ~4 alarms | ~10 | ingest $0.50/GB, $0.10/alarm |
| SSM | Session/RunCommand on one instance | 0 | SSM on one instance is free tier |
| **Total** | | **~ $135~140/mo** | ~ $115 with db.t3.micro and/or no ALB |

## 4. Production-shaped tier (internal-app licensing: Standard)

| Item | Spec | Est. $/mo |
|---|---|---|
| EC2 + IPv4 + EBS | as above | ~71 |
| RDS **Standard** | db.m5.large, single-AZ | ~432 (~ 864 multi-AZ) |
| RDS storage + backups | 20 GB gp3, PITR window | ~3 |
| ALB + cert + LCU | | ~22 |
| S3/SQS/SNS/Secrets/CW | as above | ~15 |
| **Total** | | **~ $540/mo** (~ $975 multi-AZ) |

## 5. Optional / avoided items

| Item | Est. | Status |
|---|---|---|
| NAT gateway (if app host moved to private subnets ~ alternative posture `01` ~3) | +$33 + $0.045/GB processed | not chosen; documented alternative |
| VPC endpoints (SSM/Secrets/ECR interfaces, if private posture) | +~$7.3/mo per endpoint per AZ | not chosen |
| MongoDB Atlas M10 (instead of container) | +~$57 | not chosen ~ container on EC2 (ADR-1) |
| Multi-AZ RDS | ~2 instance | Express/Web ineligible; Standard optional |
| GPU | $0 | never ~ LLM is NVIDIA NIM (R6-1); no inference infra (ADR-2 as amended by R6-1) |
| Instance resize (R3-6 thresholds) | t3.large ~ m5.large ~ +$9/mo; ~ r5.large ~ +$55/mo | only if capacity-budget alarms fire or U-9 resolves as LOCAL embeddings |

## 6. Operating modes and what keeps billing (correction R3-7)

### Mode A ~ 24/7 operation (dev/test tier)

The ~3 table as quoted: **~ $135~140/mo** (all services run continuously;
~ $115 with db.t3.micro and/or no ALB).

### Mode B ~ development-hours operation (~10 h/day weekdays ~ 220 h/mo)

| Line | Mode A ($/mo) | Mode B ($/mo) | Notes |
|---|---|---|---|
| EC2 compute (t3.large $0.0832/h) | 61 | 220 h ~ 0.0832 ~ **18** | instance stopped outside hours |
| EC2 public IPv4 ($0.005/h) | 3.6 | 220 h ~ 0.005 ~ **1** | charged **only while attached to a running instance** |
| EBS gp3 50 GB | 6 | **6** | billed regardless of instance state |
| RDS Express db.t3.small (+storage) | 28.5 | **28.5** | RDS keeps running (see note below) |
| ALB | 22 | **22** | ALBs cannot be "stopped" ~ only deleted |
| Secrets Manager + CW + S3 + SQS/SNS | ~15 | ~15 | time-independent |
| **Total** | **~ 135~140** | **~ 90** | |

**What keeps billing when the EC2 host is stopped** (explicit):

| Service | Stopped-host behavior |
|---|---|
| EC2 compute + public IPv4 | stops billing (EBS volume persists) |
| EBS volumes | **continue** (provisioned capacity) |
| RDS compute + storage | **continues** unless the RDS instance itself is *stopped* ~ possible for up to **7 days continuously** (then it auto-starts); storage and backups bill even while stopped |
| ALB | **continues** (delete it between demo cycles to save ~$22, at re-provision cost) |
| Secrets Manager, CloudWatch (ingested logs storage), S3, SQS, SNS | **continue** (tiny) |

Mode B with RDS also stopped during idle weeks (within the 7-day rule)
reduces the RDS compute line to hours-run + $2.5 storage ~ **~ $65~75/mo**.

### Production-shaped tier

Quoted ~4 numbers assume 24/7; Standard-edition RDS cannot be scheduled
indefinitely (7-day stop rule), so Mode B mostly saves the EC2 lines only:
~ $540/mo 24/7 ~ ~ $495/mo dev-hours.

### Levers (preserved from earlier revisions)

1. Billing alarm at 120% of the mode's estimate ~ deployed in Phase 1
   (R3-8), threshold set per chosen mode.
2. NIM token-budget alarm on the `ColdChain/NimTokens` metric (renamed by
   R6-1; Phase 3, C12) ~ separate from the AWS bill; NIM exposes no credit
   API (F-4), so the emitted metric is the enforcement signal.
3. RDS final snapshots retained only while the environment exists.
4. **Re-validate every line in the AWS Pricing Calculator for the selected
   region before sign-off ~ hard precondition for provisioning (R4-4),
   re-checked at Phase 1 acceptance.**