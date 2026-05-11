# Cloud Setup Runbook

**Last verified:** 2026-05-10
**Estimated time:** ~60–90 minutes active. No external approval waits.
**Executor:** gaurav-gandhi-2411

---

## Pre-flight: Install WSL Tools

One-time setup in your WSL2 Ubuntu shell. Skip any tool you already have.

```bash
# Core packages
sudo apt-get update && sudo apt-get install -y \
  curl \
  jq \
  openssl \
  postgresql-client

# gcloud (if not installed)
which gcloud || (
  curl https://sdk.cloud.google.com | bash
  exec -l "$SHELL"
)

# gh — GitHub CLI (if not installed)
which gh || (
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) \
    signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] \
    https://cli.github.com/packages stable main" \
    | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null
  sudo apt-get update && sudo apt-get install -y gh
)

# Authenticate both CLIs (interactive — one-time per machine)
gcloud auth login
gcloud auth application-default login
gh auth login
```

---

## Shell Variables

**Source this block once before starting.** Every command in every section below
references these variables — no inline fill-ins anywhere.

```bash
# ── GCP project ────────────────────────────────────────────────────────────
export PROJECT_ID="agentic-travel-XXXXXX"     # globally unique; pick a short suffix
export REGION="asia-south1"                    # Mumbai; see tradeoff note in Section 1

# ── Service account ────────────────────────────────────────────────────────
export DEPLOYER_SA="travel-agent-deployer"
export SA_EMAIL="${DEPLOYER_SA}@${PROJECT_ID}.iam.gserviceaccount.com"

# ── Artifact Registry ──────────────────────────────────────────────────────
export AR_REPO="travel-agent"
export AR_HOST="${REGION}-docker.pkg.dev"      # derived from REGION — no manual fill-in

# ── Workload Identity Federation ───────────────────────────────────────────
export POOL_ID="github-pool"
export PROVIDER_ID="github-provider"

# ── GitHub ─────────────────────────────────────────────────────────────────
export GH_OWNER="gaurav-gandhi-2411"
export GH_REPO="agentic-travel-booking-system"
export GH_REPO_FULL="${GH_OWNER}/${GH_REPO}"

# ── Cloud Run service names ─────────────────────────────────────────────────
export STAGING_SERVICE="agentic-travel-booking-api-staging"
export PROD_SERVICE="agentic-travel-booking-api-prod"
```

> **Persistence tip:** Save this block to `.env.gcp` (gitignored) and `source .env.gcp`
> at the start of any future session that continues this runbook. `STAGING_URL`,
> `PROD_URL`, `WIF_PROVIDER`, and `PROJECT_NUMBER` are computed during execution;
> re-run the capture commands in their respective sections if you close the terminal.

---

## Order of Operations

Sections 1–7 (GCP) can run sequentially in one focused block. Sections 8–10 (Neon, Upstash, Vercel) are independent browser tasks. Section 12 (Travelpayouts) values are already captured before starting.

---

## What This Costs

**API spend: $0.** No paid LLM, GPU, or third-party API spend during development.
OpenRouter free tier, Groq, and Ollama handle all LLM inference; the Claude.ai
web interface covers manual QA (existing Pro/Max subscription). See ADR-0008 and ADR-0011.

**Infrastructure floor: ~$0.80/month.** Secret Manager charges $0.06/secret/month
for active secrets beyond the 6-version free tier. At ~13 secrets that's ~$0.80/month.
Every other service (Cloud Run, Artifact Registry, Cloud Scheduler, Cloud Trace, Logging,
Neon, Upstash, Vercel, GitHub Actions) runs within its free tier at v1 volume.

**Total: ~$0.80/month pre-launch.**

---

## Resource Summary

| Resource | Service | Cost tier |
|---|---|---|
| GCP project | Cloud | Free |
| Cloud Run (staging + prod) | GCP | **Free** — 2M req/month, 360K vCPU-sec, 180K GiB-sec |
| Artifact Registry | GCP | **~Free** — 0.5 GB free; image ~50 MB |
| Secret Manager | GCP | **~$0.80/mo** — ~13 secrets × $0.06/secret/month |
| Cloud Scheduler | GCP | **Free** — 3 jobs/month; we use 1 |
| Cloud Trace / Logging / Monitoring | GCP | **Free** |
| Neon Postgres | Neon | **Free** — 0.5 GB, auto-suspend after 5 min idle |
| Upstash Redis | Upstash | **Free** — 10K commands/day, 256 MB |
| Vercel | Vercel | **Free** — Hobby plan |
| Anthropic API | Anthropic | **$0** — not used in this project (ADR-0011) |
| Travelpayouts API + Aviasales | Travelpayouts | **$0** — revenue-share affiliate; Aviasales Data API for flight pricing (ADR-0013) |
| GitHub Actions | GitHub | **Free** — 2,000 min/month |

---

## Section 1 — GCP Project Setup

**~10 minutes**

Project creation and billing attachment require the GCP Console (billing requires
browser verification). API enablement is fully CLI.

**1.1 Browser:** Create GCP project
- Go to **console.cloud.google.com** → New Project
- Project name: `Agentic Travel Booking System`
- Project ID: set this exactly to the value of `$PROJECT_ID`
- Click **Create**

**1.2 Browser:** Attach billing
- Billing → **Link a billing account** → select your pay-as-you-go account
- If no billing account exists: Billing → **Create billing account** first

**1.3 CLI:** Set default project and enable APIs

```bash
gcloud config set project "$PROJECT_ID"

# Auto-detect billing account (works if you have exactly one)
export BILLING_ACCOUNT=$(gcloud billing accounts list \
  --filter="open=true" --format="value(name)" | head -1)
echo "Billing account: $BILLING_ACCOUNT"
# If empty or wrong, review and set manually:
#   gcloud billing accounts list
#   export BILLING_ACCOUNT="0X0X0X-0X0X0X-0X0X0X"

gcloud billing projects link "$PROJECT_ID" \
  --billing-account="$BILLING_ACCOUNT"

# Enable all 10 required APIs in a single call
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  cloudscheduler.googleapis.com \
  cloudtrace.googleapis.com \
  logging.googleapis.com \
  monitoring.googleapis.com \
  --project="$PROJECT_ID"

gcloud config set run/region "$REGION"
```

**Region tradeoff:** `asia-south1` (Mumbai) — lowest latency for India-based users.
`us-central1` — cheapest Cloud Run pricing and widest free-tier quota. For v1
near-zero traffic either works; choose based on your first demo tenant's location.

**Verification:**
```bash
gcloud projects describe "$PROJECT_ID" --format="value(projectId,lifecycleState)"
# Expected: agentic-travel-XXXXXX   ACTIVE

gcloud services list --enabled --project="$PROJECT_ID" \
  --filter="NAME:(run.googleapis.com OR artifactregistry.googleapis.com OR \
secretmanager.googleapis.com OR cloudscheduler.googleapis.com OR \
iamcredentials.googleapis.com)" \
  --format="table(NAME)" | wc -l
# Expected: 6  (1 header row + 5 APIs)
```

*Related: plan.md §9, §16*

---

## Section 2 — Service Account

**~5 minutes** (fully CLI)

```bash
# 2.1 Create the service account
gcloud iam service-accounts create "$DEPLOYER_SA" \
  --display-name="Travel Agent CI/CD deployer" \
  --project="$PROJECT_ID"

# 2.2 Grant project-level roles in a single auditable loop
# roles/run.developer (not run.admin): CI needs to deploy revisions and shift traffic;
# it does not need to delete services or modify their IAM. Least-privilege per ADR posture.
ROLES=(
  roles/run.developer
  roles/artifactregistry.writer
  roles/secretmanager.viewer
)

for role in "${ROLES[@]}"; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="$role"
done

# 2.3 Allow the SA to act as itself (required by Cloud Run deploy action)
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/iam.serviceAccountUser" \
  --project="$PROJECT_ID"
```

> **No service account key is created.** Authentication flows through Workload Identity
> Federation (Section 3). This matches the triage-iq pattern.

**Verification:**
```bash
gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" \
  --format="table(email,disabled)"
# Expected: travel-agent-deployer@PROJECT_ID.iam.gserviceaccount.com   False

gcloud projects get-iam-policy "$PROJECT_ID" \
  --flatten="bindings[].members" \
  --filter="bindings.members:${SA_EMAIL}" \
  --format="table(bindings.role)"
# Expected: 3 rows — run.admin, artifactregistry.writer, secretmanager.viewer
```

*Related: ADR-0001, plan.md §14, §16*

---

## Section 3 — Workload Identity Federation

**~10 minutes** (fully CLI — reuses triage-iq pool/provider pattern)

```bash
# 3.1 Capture project number (required for WIF resource names)
export PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" \
  --format="value(projectNumber)")

# 3.2 Create the Workload Identity Pool
gcloud iam workload-identity-pools create "$POOL_ID" \
  --location=global \
  --display-name="GitHub Actions pool" \
  --description="WIF pool for GitHub Actions CI/CD" \
  --project="$PROJECT_ID"

# 3.3 Create the OIDC provider (GitHub as token issuer)
gcloud iam workload-identity-pools providers create-oidc "$PROVIDER_ID" \
  --location=global \
  --workload-identity-pool="$POOL_ID" \
  --display-name="GitHub OIDC provider" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="\
google.subject=assertion.sub,\
attribute.actor=assertion.actor,\
attribute.repository=assertion.repository,\
attribute.ref=assertion.ref" \
  --attribute-condition="assertion.repository=='${GH_REPO_FULL}'" \
  --project="$PROJECT_ID"

# 3.4 Build the pool resource name
export POOL_RESOURCE="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}"

# 3.5 Bind the SA — scoped to this exact repo only (covers push-to-main and tag pushes)
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/${POOL_RESOURCE}/attribute.repository/${GH_REPO_FULL}" \
  --project="$PROJECT_ID"

# 3.6 Build the full provider path needed for GitHub secrets (Section 13)
export WIF_PROVIDER="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/providers/${PROVIDER_ID}"

echo "=== Values to carry forward to Section 13 ==="
echo "WIF_PROVIDER        = $WIF_PROVIDER"
echo "WIF_SERVICE_ACCOUNT = $SA_EMAIL"
```

**Verification:**
```bash
gcloud iam workload-identity-pools providers describe "$PROVIDER_ID" \
  --workload-identity-pool="$POOL_ID" \
  --location=global \
  --project="$PROJECT_ID" \
  --format="table(name,state,oidc.issuerUri)"
# Expected: state=ACTIVE, issuerUri=https://token.actions.githubusercontent.com
```

*Related: plan.md §16*

---

## Section 4 — Secret Manager: Seed All Secrets

**~5 minutes** (fully CLI)

Creates 12 secrets with named placeholder values. Real values overwrite placeholders
in Sections 8–12.

```bash
# 4.1 Create all placeholder secrets in a single loop.
# Placeholder names are PLACEHOLDER_<SECRET_NAME_UPPER> for easy grep later.
SECRETS=(
  anthropic-api-key
  travelpayouts-api-token
  travelpayouts-partner-id
  travelpayouts-aviasales-marker
  neon-database-url-staging
  neon-database-url-prod
  upstash-redis-url
  upstash-redis-token
  clerk-secret-key
  clerk-publishable-key
  sentry-dsn
)

for name in "${SECRETS[@]}"; do
  placeholder="PLACEHOLDER_$(echo "$name" | tr '[:lower:]-' '[:upper:]_')"
  printf '%s' "$placeholder" | gcloud secrets create "$name" \
    --replication-policy=automatic \
    --data-file=- \
    --project="$PROJECT_ID"
  echo "Created: $name  (placeholder: $placeholder)"
done

# 4.2 jwt-signing-key gets a real random value immediately (not a placeholder)
openssl rand -base64 64 | tr -d '\n' | gcloud secrets create jwt-signing-key \
  --replication-policy=automatic \
  --data-file=- \
  --project="$PROJECT_ID"
echo "Created: jwt-signing-key  (random value)"

# 4.3 Grant the deployer SA read access to all secrets
for name in \
  anthropic-api-key \
  travelpayouts-api-token travelpayouts-partner-id travelpayouts-aviasales-marker \
  neon-database-url-staging neon-database-url-prod \
  upstash-redis-url upstash-redis-token \
  clerk-secret-key clerk-publishable-key \
  jwt-signing-key sentry-dsn; do
  gcloud secrets add-iam-policy-binding "$name" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/secretmanager.secretAccessor" \
    --project="$PROJECT_ID"
done
echo "SA access granted to all 12 secrets."
```

**Verification:**
```bash
gcloud secrets list --project="$PROJECT_ID" --format="table(name)" | wc -l
# Expected: 13  (1 header + 12 secrets; tenant-credential-master-key added in Section 5)

gcloud secrets list --project="$PROJECT_ID" --format="value(name)" | sort
# Spot-check: all 12 names appear in the output
```

*Related: plan.md §8.4, §14*

---

## Section 5 — Application-Layer Encryption Key (AES-256-GCM)

**~2 minutes** (fully CLI)

```bash
# Generate and store the 32-byte master key in a single pipeline
openssl rand -base64 32 | gcloud secrets create tenant-credential-master-key \
  --replication-policy=automatic \
  --data-file=- \
  --project="$PROJECT_ID"

# Grant deployer SA read access (used at runtime for encrypt/decrypt)
gcloud secrets add-iam-policy-binding tenant-credential-master-key \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/secretmanager.secretAccessor" \
  --project="$PROJECT_ID"
```

> **Security posture (ADR-0007):** A single master key means a Secret Manager compromise
> exposes all tenant credentials. Acceptable at v1 with a small tenant count and $0 budget.
> The migration path to per-tenant key separation (or Cloud KMS when commercial) is in
> docs/backlog.md §Phase 8. Full rotation protocol in `docs/runbooks/master-key-rotation.md`
> (written Phase 7).

**Verification:**
```bash
gcloud secrets versions access latest \
  --secret=tenant-credential-master-key \
  --project="$PROJECT_ID" | wc -c
# Expected: 44  (32 bytes base64-encoded = 43 chars + newline = 44)
```

*Related: ADR-0007, plan.md §8.4*

---

## Section 6 — Cloud Run Service Stubs

**~10 minutes** (fully CLI)

```bash
# 6.1 Create Artifact Registry repository
gcloud artifacts repositories create "$AR_REPO" \
  --repository-format=docker \
  --location="$REGION" \
  --description="Agentic Travel Booking System container images" \
  --project="$PROJECT_ID"

# 6.2 Deploy staging stub (hello-world image; replaced on first real deploy)
gcloud run deploy "$STAGING_SERVICE" \
  --image=gcr.io/cloudrun/hello \
  --region="$REGION" \
  --platform=managed \
  --allow-unauthenticated \
  --service-account="$SA_EMAIL" \
  --min-instances=0 \
  --max-instances=5 \
  --memory=512Mi \
  --cpu=1 \
  --timeout=60 \
  --project="$PROJECT_ID"

# 6.3 Deploy prod stub
gcloud run deploy "$PROD_SERVICE" \
  --image=gcr.io/cloudrun/hello \
  --region="$REGION" \
  --platform=managed \
  --allow-unauthenticated \
  --service-account="$SA_EMAIL" \
  --min-instances=1 \
  --max-instances=20 \
  --memory=512Mi \
  --cpu=1 \
  --timeout=300 \
  --concurrency=80 \
  --project="$PROJECT_ID"

# 6.4 Capture URLs (used in Sections 7, 10, 13, 15)
export STAGING_URL=$(gcloud run services describe "$STAGING_SERVICE" \
  --region="$REGION" --project="$PROJECT_ID" --format="value(status.url)")
export PROD_URL=$(gcloud run services describe "$PROD_SERVICE" \
  --region="$REGION" --project="$PROJECT_ID" --format="value(status.url)")
echo "Staging: $STAGING_URL"
echo "Prod:    $PROD_URL"
```

**Verification:**
```bash
gcloud run services list --region="$REGION" --project="$PROJECT_ID" \
  --format="table(metadata.name,status.url,status.conditions[0].type)"
# Expected: both services listed with Ready condition

curl -s "$STAGING_URL" | grep -o "It's running\|Congratulations"
curl -s "$PROD_URL"    | grep -o "It's running\|Congratulations"
# Expected: "It's running" from the hello-world image on each URL
```

*Related: plan.md §9, §16*

---

## Section 7 — Cloud Scheduler Keep-Alive Cron

**~3 minutes** (fully CLI)

Prevents Neon Postgres from auto-suspending between requests (Risk 1 from Phase 0
planning). The `/health` endpoint must issue `SELECT 1` — this is a Phase 1 requirement.

> **Until Phase 1 ships `/health`, this cron will receive a 404 from the Cloud Run stub.
> This is expected and harmless. Create the job now so it's ready the moment Phase 1 lands.**

Cloud Scheduler free tier: 3 jobs/month. This runbook uses 1.

```bash
# Ensure PROJECT_NUMBER is set (captured in Section 3; re-export if session was closed)
: "${PROJECT_NUMBER:=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')}"

gcloud scheduler jobs create http neon-keepalive \
  --location="$REGION" \
  --schedule="*/4 * * * *" \
  --uri="${STAGING_URL}/health" \
  --http-method=GET \
  --description="Keep Neon Postgres warm — prevents 1-3s cold start after idle" \
  --project="$PROJECT_ID"

# If $REGION is not supported for Scheduler, use the nearest available:
#   gcloud scheduler locations list
```

**Verification:**
```bash
gcloud scheduler jobs describe neon-keepalive \
  --location="$REGION" --project="$PROJECT_ID" \
  --format="table(name,schedule,httpTarget.uri,state)"
# Expected: state=ENABLED, schedule=*/4 * * * *, uri ends with /health

gcloud scheduler jobs run neon-keepalive \
  --location="$REGION" --project="$PROJECT_ID"
echo "Exit $?  — 0 means the scheduler accepted the trigger. 404 from /health is expected until Phase 1."
```

*Related: plan.md §15 Risk 1*

---

## Section 8 — Neon Postgres

**~15 minutes** (browser for project + branch creation; CLI for credentials)

**8.1 Browser:** Create Neon project
- Go to **neon.tech** → **New Project**
- Name: `agentic-travel-booking-system`
- Region: for `asia-south1` → **AWS ap-southeast-1 (Singapore)**; for `us-central1` → **AWS us-east-1**
- Postgres version: **16**
- Click **Create Project**

**8.2 Browser:** Create staging branch
- Project → **Branches** → **New Branch**
- Name: `staging`, branch from: `main`

**8.3 Browser:** Capture connection strings
- Select the **`main`** branch → **Connection Details** tab
- Driver: **asyncpg** (or psycopg; the `postgresql+asyncpg://` prefix is what matters)
- Copy the connection string for `main` → this is your prod URL
- Switch to **`staging`** branch → copy its connection string → this is your staging URL

```bash
# Paste your actual values here, then run the export block
export NEON_URL_STAGING="postgresql+asyncpg://user:password@ep-xxx.region.aws.neon.tech/neondb?sslmode=require"
export NEON_URL_PROD="postgresql+asyncpg://user:password@ep-xxx.region.aws.neon.tech/neondb?sslmode=require"
```

**8.4 CLI:** Store in Secret Manager

```bash
printf '%s' "$NEON_URL_STAGING" | \
  gcloud secrets versions add neon-database-url-staging \
  --data-file=- --project="$PROJECT_ID"

printf '%s' "$NEON_URL_PROD" | \
  gcloud secrets versions add neon-database-url-prod \
  --data-file=- --project="$PROJECT_ID"
```

**Verification:**
```bash
# Pull staging URL from Secret Manager and verify connectivity
NEON_TEST=$(gcloud secrets versions access latest \
  --secret=neon-database-url-staging --project="$PROJECT_ID" | \
  sed 's|postgresql+asyncpg|postgresql|')
psql "$NEON_TEST" -c "SELECT version(), current_database();"
# Expected: PostgreSQL 16.x ... | neondb
```

> **Neon free tier:** Auto-suspends after 5 min idle (1–3s cold start on wake). The Cloud
> Scheduler cron (Section 7) keeps staging warm. For production SLO compliance before launch,
> consider Neon Launch ($19/month).

*Related: plan.md §9, ADR-0004*

---

## Section 9 — Upstash Redis

**~5 minutes** (browser for database creation; CLI for credentials)

**9.1 Browser:** Create Upstash database
- Go to **upstash.com** → **Create Database**
- Name: `agentic-travel-cache`
- Type: **Regional** (not Global — Global is paid)
- Region: for `asia-south1` → **Singapore**; for `us-central1` → **US East**
- TLS: **enabled**
- Click **Create**

**9.2 Browser:** Capture REST credentials
- Database → **REST API** tab
- Copy **Endpoint** (URL starting `https://`) and **Token**

```bash
export UPSTASH_URL="https://xxx.upstash.io"
export UPSTASH_TOKEN="AXxxxxxxxxxxxxxxxxx"
```

**9.3 CLI:** Store in Secret Manager

```bash
printf '%s' "$UPSTASH_URL" | \
  gcloud secrets versions add upstash-redis-url \
  --data-file=- --project="$PROJECT_ID"

printf '%s' "$UPSTASH_TOKEN" | \
  gcloud secrets versions add upstash-redis-token \
  --data-file=- --project="$PROJECT_ID"
```

**Verification:**
```bash
URL_CHECK=$(gcloud secrets versions access latest \
  --secret=upstash-redis-url --project="$PROJECT_ID")
TOK_CHECK=$(gcloud secrets versions access latest \
  --secret=upstash-redis-token --project="$PROJECT_ID")
curl -s -H "Authorization: Bearer $TOK_CHECK" "${URL_CHECK}/ping" | jq -r '.result'
# Expected: PONG
```

**Free tier ceiling:** 10K commands/day, 256 MB max — ample at v1 volume.

*Related: plan.md §5.2, §8.3, §9*

---

## Section 10 — Vercel Project

**~15 minutes** (browser-only — Vercel CLI requires interactive OAuth that doesn't fit copy-paste flow)

**10.1** Go to **vercel.com/new** → **Import Git Repository**
- Select `gaurav-gandhi-2411/agentic-travel-booking-system`
- **Root Directory:** click Edit → type `apps/web` → confirm
- **Framework Preset:** Next.js (auto-detected)
- **Node.js Version:** 20.x
- Leave build settings as default

**10.2** Project Settings → **Environment Variables** — add these three variables:

| Variable | Environment | Value |
|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | Preview | Paste `$STAGING_URL` |
| `NEXT_PUBLIC_API_BASE_URL` | Production | Paste `$PROD_URL` |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | Preview + Production | `PLACEHOLDER_CLERK_PUBLISHABLE_KEY` |
| `CLERK_SECRET_KEY` | Preview + Production | `PLACEHOLDER_CLERK_SECRET_KEY` |

```bash
# Print values to copy-paste into the Vercel dashboard
echo "NEXT_PUBLIC_API_BASE_URL (Preview)    = $STAGING_URL"
echo "NEXT_PUBLIC_API_BASE_URL (Production) = $PROD_URL"
```

**10.3** Trigger a preview deploy to verify the Vercel integration:

```bash
git checkout -b chore/verify-vercel
git commit --allow-empty -m "chore: trigger Vercel preview deploy verification"
git push origin chore/verify-vercel
gh pr create \
  --title "chore: verify Vercel preview deploy" \
  --body "Verification PR — close once the Vercel preview URL appears in PR checks." \
  --repo="$GH_REPO_FULL"
```

**Verification:** PR checks show a Vercel preview URL within ~2 minutes. Open the URL —
expect a 404 (no Next.js pages until Phase 8), not a 5xx. Close the PR and delete the branch.

*Related: plan.md §9, §16*

---

## Section 11 — Anthropic API

**No action required.**

This project uses **no Anthropic API spend**. The `anthropic-api-key` placeholder created
in Section 4 can remain as-is throughout development.

LLM inference runs on OpenRouter free tier, Groq, and Ollama, selected by
`LLM_ROUTING_PROFILE` (see ADR-0008). The `eval` profile — which routes to
`claude-sonnet-4-6` via the Anthropic API — is reserved for **manual baseline
benchmarks only** and is explicitly **off in CI and normal development** (ADR-0010).

Dataset generation for fine-tuning uses the Claude.ai web interface (existing Pro/Max
subscription), not the API. No API key is needed (ADR-0011).

*Related: ADR-0008, ADR-0010, ADR-0011*

---

## Section 12 — Travelpayouts Credentials

**~5 minutes** (browser to retrieve credentials; CLI to store them)

Travelpayouts is the primary flight data provider (ADR-0013). The token is already
obtained; this section stores it in Secret Manager alongside the partner ID and
Aviasales marker used for affiliate link construction.

**12.1 Browser:** Retrieve credentials
- Go to **travelpayouts.com** → Profile → **API** → **Data API**
- Copy your **API Token**
- Go to **Programs** → **Aviasales** → Partner tools
- Note your **Partner ID** (numeric, shown in the partner tools header)
- Note your **Marker** (6-digit affiliate marker for Aviasales deep links)

**12.2 CLI:** Export and store in Secret Manager

```bash
export TP_TOKEN="YOUR_TRAVELPAYOUTS_API_TOKEN"
export TP_PARTNER_ID="YOUR_PARTNER_ID"
export TP_MARKER="YOUR_AVIASALES_MARKER"

printf '%s' "$TP_TOKEN" | \
  gcloud secrets versions add travelpayouts-api-token \
  --data-file=- --project="$PROJECT_ID"

printf '%s' "$TP_PARTNER_ID" | \
  gcloud secrets versions add travelpayouts-partner-id \
  --data-file=- --project="$PROJECT_ID"

printf '%s' "$TP_MARKER" | \
  gcloud secrets versions add travelpayouts-aviasales-marker \
  --data-file=- --project="$PROJECT_ID"
```

**Verification:**
```bash
TP_TOKEN_CHECK=$(gcloud secrets versions access latest \
  --secret=travelpayouts-api-token --project="$PROJECT_ID")

curl -s "https://api.travelpayouts.com/v1/prices/cheap?\
origin=DEL&destination=BOM&currency=INR&token=${TP_TOKEN_CHECK}" \
  | jq 'keys | length'
# Expected: a non-zero integer (destination keys in the response)
# If 0 or null: verify the token and confirm Aviasales Data API is enabled on your account
```

*Related: ADR-0013, ADR-0014, plan.md §9*

---

## Section 13 — GitHub Actions Secrets and Variables

**~5 minutes** (fully CLI)

```bash
# Confirm all required values are in scope
echo "WIF_PROVIDER        = $WIF_PROVIDER"
echo "WIF_SERVICE_ACCOUNT = $SA_EMAIL"
echo "GCP_PROJECT_ID      = $PROJECT_ID"
echo "CLOUD_RUN_REGION    = $REGION"
echo "AR_REPO             = $AR_REPO"
echo "AR_HOST             = $AR_HOST"

# 2 repository secrets (sensitive — value hidden after creation)
gh secret set WIF_PROVIDER \
  --body="$WIF_PROVIDER" \
  --repo="$GH_REPO_FULL"

gh secret set WIF_SERVICE_ACCOUNT \
  --body="$SA_EMAIL" \
  --repo="$GH_REPO_FULL"

# 4 repository variables (visible in Actions UI, not sensitive)
gh variable set GCP_PROJECT_ID \
  --body="$PROJECT_ID" \
  --repo="$GH_REPO_FULL"

gh variable set CLOUD_RUN_REGION \
  --body="$REGION" \
  --repo="$GH_REPO_FULL"

gh variable set ARTIFACT_REGISTRY_REPO \
  --body="$AR_REPO" \
  --repo="$GH_REPO_FULL"

gh variable set ARTIFACT_REGISTRY_HOST \
  --body="$AR_HOST" \
  --repo="$GH_REPO_FULL"
```

**Verification:**
```bash
echo "=== Secrets ===" && gh secret list --repo="$GH_REPO_FULL"
echo "=== Variables ===" && gh variable list --repo="$GH_REPO_FULL"
```

Expected secrets: `WIF_PROVIDER`, `WIF_SERVICE_ACCOUNT`
Expected variables: `GCP_PROJECT_ID`, `CLOUD_RUN_REGION`, `ARTIFACT_REGISTRY_REPO`, `ARTIFACT_REGISTRY_HOST`

*Related: plan.md §16*

---

## Section 14 — Enable Deploy Workflows

**~5 minutes**

Both deploy workflows are gated with `if: ${{ false }}` (commit 22). Remove the gate
now that all secrets and variables are in place.

```bash
# Remove the disabling gate from both workflows
sed -i '/if: \${{ false }}.*Stage 0\.4/d' \
  .github/workflows/deploy-staging.yml \
  .github/workflows/deploy-prod.yml

# Verify the lines are gone (should print nothing)
grep -n 'if: \${{ false }}' \
  .github/workflows/deploy-staging.yml \
  .github/workflows/deploy-prod.yml \
  && echo "ERROR: gate line still present — check file manually" \
  || echo "OK: gate removed from both files"

# Commit via PR — do not push directly to main
git checkout -b chore/enable-deploy-workflows
git add .github/workflows/deploy-staging.yml .github/workflows/deploy-prod.yml
git commit -m "chore: enable Cloud Run deploy workflows post-Stage-0.4 provisioning"
git push origin chore/enable-deploy-workflows

gh pr create \
  --title "chore: enable Cloud Run deploy workflows" \
  --body "Removes the if-false gate. All Stage 0.4 secrets and variables are in place." \
  --repo="$GH_REPO_FULL"
```

> **Merge the PR before running Section 15.** The merge to main triggers the first real
> staging deploy.

---

## Section 15 — End-to-End Smoke Test

**~15 minutes** (fully CLI — run after Section 14 PR is merged)

```bash
# 15.1 List recent workflow runs and watch the staging deploy
gh run list --repo="$GH_REPO_FULL" --limit=5
gh run watch --repo="$GH_REPO_FULL"
# Select the most recent run when prompted.
# Expected: all jobs green (build, push, deploy-staging).

# 15.2 Confirm the Cloud Run revision updated (should show app image, not hello-world)
gcloud run revisions list \
  --service="$STAGING_SERVICE" \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --format="table(metadata.name,status.conditions[0].status,spec.containers[0].image)" \
  --limit=3
# Expected: latest revision ACTIVE with your app image (not gcr.io/cloudrun/hello)

# 15.3 Smoke test the staging URL
STAGING_URL=$(gcloud run services describe "$STAGING_SERVICE" \
  --region="$REGION" --project="$PROJECT_ID" --format="value(status.url)")

STATUS=$(curl -s -o /dev/null -w "%{http_code}" "${STAGING_URL}/health")
echo "Health endpoint HTTP status: $STATUS"
# 404 = expected (Phase 1 /health not yet implemented)
# 200 = Phase 1 is live
# 5xx = investigate Cloud Run logs

# 15.4 Confirm the Cloud Scheduler hit the service
gcloud scheduler jobs run neon-keepalive \
  --location="$REGION" --project="$PROJECT_ID"
echo "Scheduler trigger fired — 404 from /health is expected until Phase 1"

# 15.5 Tail Cloud Run logs to confirm the request arrived
gcloud logging read \
  "resource.type=cloud_run_revision \
   resource.labels.service_name=${STAGING_SERVICE} \
   httpRequest.status=404" \
  --project="$PROJECT_ID" \
  --limit=5 \
  --format="table(timestamp,httpRequest.status,httpRequest.requestUrl)"
# Expected: one entry with status=404 and requestUrl ending in /health
```

**All green checklist:**
- [ ] `gh run watch` completed with all jobs green
- [ ] Latest Cloud Run revision shows the app image (not `gcr.io/cloudrun/hello`)
- [ ] `curl /health` returns 404 (or 200 if Phase 1 merged), not 5xx
- [ ] Scheduler job trigger exits 0; 404 log entry visible
- [ ] Vercel preview URL appears in open PR checks

**You are ready to authorize Phase 1.**

---

## Appendix — Clean Up (Start Over)

```bash
# Delete all GCP resources (destructive — resets the entire project)
gcloud projects delete "$PROJECT_ID"

# Remove GitHub secrets and variables
gh secret remove WIF_PROVIDER           --repo="$GH_REPO_FULL"
gh secret remove WIF_SERVICE_ACCOUNT    --repo="$GH_REPO_FULL"
gh variable remove GCP_PROJECT_ID       --repo="$GH_REPO_FULL"
gh variable remove CLOUD_RUN_REGION     --repo="$GH_REPO_FULL"
gh variable remove ARTIFACT_REGISTRY_REPO --repo="$GH_REPO_FULL"
gh variable remove ARTIFACT_REGISTRY_HOST --repo="$GH_REPO_FULL"

# Neon: neon.tech dashboard → Project Settings → Delete Project
# Upstash: upstash.com → Database → Delete
# Vercel: vercel.com → Project Settings → Delete Project
```

---

*This runbook covers plan.md Phase 0 (§11) external provisioning requirements.
Once Section 15 is green, authorize Phase 1 (Provider Adapters + Search) per plan.md §11.*
