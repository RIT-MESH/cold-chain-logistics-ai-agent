# Architecture Diagrams — Cold-Chain Logistics AI Agent (2026-11-18)

All diagrams reflect the DEPLOYED configuration (Phases 0–6 complete).

## 1. System architecture

```mermaid
flowchart TB
  subgraph EDGE["Public edge"]
    CF["CloudFront d36yix30egjffn.cloudfront.net<br/>(HTTPS, fail-closed 403)"]
    COG["Cognito hosted login<br/>us-east-1_UwGkeVqC6"]
  end
  subgraph AWS["AWS us-east-1 (account 123456789012)"]
    ALB["ALB coldchain-dev-web :80<br/>(x-cf-origin-secret guard, 403 default)"]
    EC2["EC2 t3.large app host<br/>(9 containers, IMDSv2, SSM)"]
    RDS[("RDS SQL Server Express<br/>FDE_VIEWS read-only identities<br/>TLS verified")]
    S3[("S3 telemetry bucket<br/>SSE, versioned")]
    SQS[["SQS + DLQ<br/>redrive maxReceiveCount=5"]]
    SNS[["SNS coldchain-dev-alerts<br/>(no subscriptions — operator gate)"]]
    CW["CloudWatch: 4 log groups (90d)<br/>8 alarms, 7 custom metrics"]
    SM["Secrets Manager (12 containers<br/>→ tmpfs app.env)"]
  end
  PC[("Pinecone coldchain-sop-index-nim<br/>2048d cosine, namespace sops<br/>44 vectors / 7 SOP docs")]
  NIM["NVIDIA NIM<br/>GLM-5.3-Flash (ReAct) ·<br/>Nemotron-3.5-Lightning (synthesis) ·<br/>Nemotron-3.5-Embed (2048d)"]
  CF -->|"oauth2-proxy gate"| ALB --> EC2
  COG -.->|"OIDC login/logout"| CF
  EC2 --> RDS
  EC2 --> NIM
  EC2 --> PC
  S3 --> SQS --> EC2
  EC2 --> SNS
  EC2 --> CW
  SM -.->|"instance-role reads"| EC2
```

## 2. Data flow (synthetic dataset → consumption)

```mermaid
flowchart LR
  CSV["data/unified CSVs<br/>(4,020 shipments, 64,120 readings,<br/>seed 53026, 2021–Oct 2026)"]
  CSV -->|"FK-gated import"| RDS[("unified.* base tables")]
  RDS --> FDE["FDE_VIEWS (7+4 read-only views)"]
  FDE --> ST["Streamlit (9 sections)"]
  FDE --> GR["Grafana (6 dashboards)"]
  FDE --> AG["LangGraph SQL tool<br/>(guard: single SELECT, TOP 1-100)"]
```

## 3. Incident processing (Phase 4 event plane)

```mermaid
flowchart LR
  PUB["Synthetic event JSON<br/>(synthetic/ prefix)"] --> S3[("S3")]
  S3 -->|"ObjectCreated → SQS"| SQS[["SQS"]]
  SQS -->|"{bad ref / invalid payload → DLQ}"| DLQ[["DLQ + depth alarm"]]
  SQS --> WK["worker (compose phase4)"]
  WK -->|"C4 linkage check + EventId dedup"| RDS[("TelemetryEvents →<br/>TelemetryIncidents")]
  WK -->|"NEW incident only"| SNS[["SNS topic-ack"]]
  WK -->|"attempt row per publish"| LEDGER[("append-only<br/>IncidentNotificationAttempts")]
  REC["reconcile loop (15 min,<br/>sp_getapplock, bounded retries)"] -->|"re-publish lost Class-B"| SNS
  REC -->|"CW metrics"| CWA["reconcile-pending / exhausted alarms"]
```

## 4. AI retrieval workflow (Phase 6 final form)

```mermaid
flowchart TB
  Q["User question<br/>(LibreChat → gateway)"] --> PF{"UC-xxxxxx + SOP intent?<br/>(deterministic regex/keywords)"}
  PF -->|"no"| REACT["GLM ReAct loop<br/>(tools, guard, budget)"]
  PF -->|"yes"| E1["SQL incident evidence<br/>(fixed guard-valid queries, 230–370 ms)"]
  E1 --> E2["SQL notification state<br/>(120–225 ms)"]
  E2 --> E3["Pinecone SOP retrieval<br/>(embed_query → 3 cited chunks, ~0.8–1.3 s)"]
  E3 --> SYN["Nemotron-3.5-Lightning synthesis<br/>(~68 s, evidence-only, no tools)"]
  SYN --> VAL{"16-check deterministic validator<br/>(facts, citations, labels, semantics)"}
  VAL -->|"PASS"| ANS["Answer + audit events<br/>(69.9 s total E2E)"]
  VAL -->|"REJECT"| REJ["502 + exact failed checks<br/>(never returned as success)"]
  REACT --> ANS
```

## 5. Security architecture (layers)

```mermaid
flowchart LR
  U["Browser"] -->|"HTTPS"| CF["CloudFront (no public caching)"]
  CF --> COG["Cognito + oauth2-proxy<br/>(login, logout, session cookies)"]
  COG --> ALB["ALB guard header"]
  ALB --> SG["sg-app (ALB-only inbound)"]
  SG --> APP["Containers (mssql TLS verified,<br/>secrets from tmpfs env)"]
  APP -->|"FDE_VIEWS only"| DB[("RDS")]
  APP -->|"config-fixed index/namespace/filter"| PIN[("Pinecone")]
  APP --> AUD["CloudWatch audit JSON<br/>(request_id correlation, redaction)"]
```
