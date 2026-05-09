# Cloud Setup Runbook

**Last verified:** 2026-05-10
**Estimated time:** 3–4 hours (excluding Amadeus/Duffel account approval, which can take 24–48h)
**Executor:** gaurav-gandhi-2411

---

## Prerequisites

Before starting:

- [ ] GCP account with billing enabled (pay-as-you-go or active free trial)
- [ ] `gcloud` CLI installed and authenticated (`gcloud auth login`, `gcloud auth application-default login`)
- [ ] GitHub admin access on `gaurav-gandhi-2411/agentic-travel-booking-system`
- [ ] `gh` CLI installed and authenticated (`gh auth login`)
- [ ] Node 20+ and `psql` available locally for verification steps
- [ ] Neon account created at neon.tech (free, GitHub SSO)
- [ ] Upstash account created at upstash.com (free, GitHub SSO)
- [ ] Vercel account (free Hobby plan, GitHub SSO)
- [ ] Anthropic account at console.anthropic.com
- [ ] Amadeus developer account at developers.amadeus.com (can create during runbook)
- [ ] Duffel account at duffel.com (can create during runbook)

---

## Variables — set these before running any commands

```bash
export PROJECT_ID="agentic-travel-XXXXXX"   # Replace XXXXXX with a random suffix
                                              # Must be globally unique in GCP
export REGION="asia-south1"                  # Mumbai — closest to India
                                              # Alternative: us-central1 (cheapest free tier)
export GITHUB_REPO="gaurav-gandhi-2411/agentic-travel-booking-system"
export SA_EMAIL="travel-agent-deployer@${PROJECT_ID}.iam.gserviceaccount.com"
export AR_REPO="travel-agent"
export POOL_ID="github-pool"
export PROVIDER_ID="github-provider"
```

---

## Resource Summary

All resources created by this runbook and their cost tier at v1 volume:

| Resource | Service | Cost tier |
|---|---|---|
| GCP project | Cloud | Free (billing enabled, pay for actual usage) |
| Cloud Run (staging + prod) | GCP | **Free** — always-free: 2M req/month, 360K vCPU-sec, 180K GiB-sec |
| Artifact Registry | GCP | **~Free** — 0.5 GB free; image ~50 MB so effectively $0 at v1 |
| Secret Manager | GCP | **Near-free** — ~$0.06/secret/month; ~13 secrets = ~$0.80/month |
| Cloud Scheduler | GCP | **Free** — free tier: 3 jobs/month; we create 1 |
| Cloud Trace / Logging / Monitoring | GCP | **Free** — generous free tier covers v1 volume |
| Neon Postgres | Neon | **Free** — 1 project, 0.5 GB storage, auto-suspend after 5 min idle |
| Upstash Redis | Upstash | **Free** — 10K commands/day, 256 MB max |
| Vercel | Vercel | **Free** — Hobby plan, unlimited deploys, 100 GB bandwidth |
| Anthropic API | Anthropic | **Pay-per-token** — add $20 credit for dev through Phase 6; ~$0.03–$0.10/request |
| Amadeus Self-Service | Amadeus | **Free dev tier** — 2,000 API transactions/month |
| Duffel | Duffel | **Free in test mode** — production requires revenue-sharing agreement |
| GitHub Actions | GitHub | **Free** — public/private repos: 2,000 min/month free |

**Total estimated monthly cost at v1 (pre-launch):** ~$2–$5/month

---

## Section 1 — GCP Project Setup

**~15 minutes**

```bash
# 1.1 Create the project
gcloud projects create $PROJECT_ID \
  --name="Agentic Travel Booking System"

# 1.2 Set as default for this session
gcloud config set project $PROJECT_ID

# 1.3 Link billing (required before enabling APIs)
# Get your billing account ID:
gcloud billing accounts list
# Then link:
gcloud billing projects link $PROJECT_ID \
  --billing-account=BILLING_ACCOUNT_ID

# 1.4 Enable required APIs (single call)
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
  --project=$PROJECT_ID

# 1.5 Set default region for Cloud Run
gcloud config set run/region $REGION
```

**Region tradeoff:** `asia-south1` (Mumbai) gives lowest latency for India-based users and is
in the same region as most Indian travel API endpoints. `us-central1` has the cheapest Cloud Run
pricing and the widest always-free quota. For v1 with near-zero traffic, either works; choose
based on where your first demo tenant is located.

**Verification:**
```bash
gcloud projects describe $PROJECT_ID
gcloud services list --enabled --project=$PROJECT_ID | grep -E "run|artifactregistry|secretmanager|scheduler"
```

*Related: plan.md §9, §16*

---

## Section 2 — Service Account for Cloud Run

**~10 minutes**

```bash
# 2.1 Create the service account (used by GitHub Actions for deploy + by Cloud Run at runtime)
gcloud iam service-accounts create travel-agent-deployer \
  --display-name="Travel Agent CI/CD deployer" \
  --project=$PROJECT_ID

# 2.2 Grant minimum required roles
# Cloud Run: deploy revisions
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/run.admin"

# Artifact Registry: push images
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/artifactregistry.writer"

# Impersonate itself (required by Cloud Run deploy action)
gcloud iam service-accounts add-iam-policy-binding $SA_EMAIL \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/iam.serviceAccountUser" \
  --project=$PROJECT_ID

# Secret Manager: read secrets at runtime (scoped to individual secrets in Section 4)
# Project-level read access for Secret Manager metadata (list secrets)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/secretmanager.viewer"

```

> **No service account key is created.** Authentication flows through Workload Identity
> Federation (Section 3). This matches the triage-iq pattern.

**Verification:**
```bash
gcloud iam service-accounts describe $SA_EMAIL --project=$PROJECT_ID
gcloud projects get-iam-policy $PROJECT_ID \
  --flatten="bindings[].members" \
  --filter="bindings.members:${SA_EMAIL}" \
  --format="table(bindings.role)"
```

*Related: ADR-0001, plan.md §14, §16*

---

## Section 3 — Workload Identity Federation

**~20 minutes** (reuses triage-iq pool/provider pattern)

```bash
# 3.1 Get project number (needed for WIF resource names)
export PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')

# 3.2 Create the Workload Identity Pool
gcloud iam workload-identity-pools create $POOL_ID \
  --location=global \
  --display-name="GitHub Actions pool" \
  --description="WIF pool for GitHub Actions CI/CD" \
  --project=$PROJECT_ID

# 3.3 Create the OIDC provider (GitHub token issuer)
gcloud iam workload-identity-pools providers create-oidc $PROVIDER_ID \
  --location=global \
  --workload-identity-pool=$POOL_ID \
  --display-name="GitHub OIDC provider" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="\
google.subject=assertion.sub,\
attribute.actor=assertion.actor,\
attribute.repository=assertion.repository,\
attribute.ref=assertion.ref" \
  --attribute-condition="assertion.repository=='${GITHUB_REPO}'" \
  --project=$PROJECT_ID

# 3.4 Get the pool resource name for SA binding
export POOL_RESOURCE="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}"

# 3.5 Bind the SA to the pool — scoped to this specific repo
# Covers both push-to-main (deploy-staging) and tag pushes (deploy-prod)
gcloud iam service-accounts add-iam-policy-binding $SA_EMAIL \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/${POOL_RESOURCE}/attribute.repository/${GITHUB_REPO}" \
  --project=$PROJECT_ID

# 3.6 Capture the WIF provider resource name for GitHub secrets (Section 13)
export WIF_PROVIDER="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/providers/${PROVIDER_ID}"
echo "WIF_PROVIDER = $WIF_PROVIDER"
echo "WIF_SERVICE_ACCOUNT = $SA_EMAIL"
# Save these values — you will paste them into GitHub secrets in Section 13.
```

**Verification:**
```bash
gcloud iam workload-identity-pools providers describe $PROVIDER_ID \
  --workload-identity-pool=$POOL_ID \
  --location=global \
  --project=$PROJECT_ID
```

Expected output includes `issuerUri: https://token.actions.githubusercontent.com` and your
`attributeCondition`.

*Related: plan.md §16*

---

## Section 4 — Secret Manager: Seed All Secrets

**~15 minutes**

Creates all secrets with placeholder values. Real values are added in Sections 8–12.

```bash
# Helper function: create secret + add placeholder version
create_secret() {
  local name=$1
  gcloud secrets create "$name" \
    --replication-policy=automatic \
    --project=$PROJECT_ID 2>/dev/null || echo "Secret $name already exists"
  echo -n "PLACEHOLDER" | gcloud secrets versions add "$name" \
    --data-file=- \
    --project=$PROJECT_ID
}

# LLM
create_secret "anthropic-api-key"

# Travel providers
create_secret "amadeus-client-id"
create_secret "amadeus-client-secret"
create_secret "duffel-api-key"

# Database (Neon)
create_secret "neon-database-url-staging"
create_secret "neon-database-url-prod"

# Cache (Upstash)
create_secret "upstash-redis-url"
create_secret "upstash-redis-token"

# Auth (Clerk)
create_secret "clerk-secret-key"
create_secret "clerk-publishable-key"

# App signing
openssl rand -base64 64 | tr -d '\n' | gcloud secrets versions add jwt-signing-key \
  --data-file=- \
  --project=$PROJECT_ID 2>/dev/null || \
  openssl rand -base64 64 | tr -d '\n' | \
  (gcloud secrets create jwt-signing-key --replication-policy=automatic --project=$PROJECT_ID && \
   gcloud secrets versions add jwt-signing-key --data-file=- --project=$PROJECT_ID)

# Sentry (optional — can remain placeholder until Phase 9)
create_secret "sentry-dsn"
```

Grant the deployer SA access to read secrets at runtime:
```bash
for secret in \
  anthropic-api-key \
  amadeus-client-id amadeus-client-secret \
  duffel-api-key \
  neon-database-url-staging neon-database-url-prod \
  upstash-redis-url upstash-redis-token \
  clerk-secret-key clerk-publishable-key \
  jwt-signing-key sentry-dsn; do
  gcloud secrets add-iam-policy-binding "$secret" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/secretmanager.secretAccessor" \
    --project=$PROJECT_ID
done
```

**Verification:**
```bash
gcloud secrets list --project=$PROJECT_ID --format="table(name,createTime)"
# Expected: 12 secrets listed (tenant-credential-master-key is created in Section 5)
```

*Related: plan.md §8.4, §14*

---

## Section 5 — Application-Layer Encryption Key Seeding (AES-GCM)

**~5 minutes**

Tenant Amadeus/Duffel credentials are encrypted at rest using AES-256-GCM at the
application layer. A single 32-byte master key is stored in Secret Manager. This
supersedes the KMS approach originally specified in plan.md §8.4 — see ADR-0007 for
the rationale and the migration path to per-tenant key separation at commercial scale.

```bash
# 5.1 Generate a 32-byte master key locally (base64-encoded, never transmitted unencrypted)
MASTER_KEY=$(openssl rand -base64 32)

# 5.2 Create the secret and store the key
echo -n "$MASTER_KEY" | gcloud secrets create tenant-credential-master-key \
  --replication-policy=automatic \
  --data-file=- \
  --project=$PROJECT_ID

# 5.3 Grant deployer SA read access (used at runtime to load the key for encrypt/decrypt)
gcloud secrets add-iam-policy-binding tenant-credential-master-key \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/secretmanager.secretAccessor" \
  --project=$PROJECT_ID

# 5.4 Clear the key from shell environment (do not let it persist in history)
unset MASTER_KEY
history -d $(history 1 | awk '{print $1}') 2>/dev/null || true
```

> **Security posture (ADR-0007):** A single master key means a Secret Manager compromise
> exposes all tenant credentials. This is acceptable at v1 with a small tenant count and
> $0 budget. The migration to per-tenant key separation (or KMS) is documented in
> `docs/runbooks/master-key-rotation.md`, which will be written in Phase 7 when the
> multi-tenancy layer lands. For now, that path is a forward reference.

> **Quarterly rotation:** Generate a new key, re-encrypt all `tenant_credentials` rows in
> Postgres, and add a new Secret Manager version. The old version can be disabled after
> re-encryption is confirmed. Full protocol in `docs/runbooks/master-key-rotation.md`.

**Verification:**
```bash
gcloud secrets versions access latest --secret=tenant-credential-master-key \
  --project=$PROJECT_ID | wc -c
# Expected: ~44 (32 bytes base64-encoded = 43 characters + newline = 44)
```

*Related: ADR-0007, plan.md §8.4*

---

## Section 6 — Cloud Run Service Stubs

**~15 minutes**

Creates empty services so the deploy workflows have an existing target to update.
Using the official hello-world image as the initial revision.

```bash
# 6.1 Create Artifact Registry repository (required before pushing app images)
gcloud artifacts repositories create $AR_REPO \
  --repository-format=docker \
  --location=$REGION \
  --description="Agentic Travel Booking System images" \
  --project=$PROJECT_ID

# 6.2 Deploy staging stub
gcloud run deploy agentic-travel-booking-api-staging \
  --image=gcr.io/cloudrun/hello \
  --region=$REGION \
  --platform=managed \
  --allow-unauthenticated \
  --service-account=$SA_EMAIL \
  --min-instances=0 \
  --max-instances=5 \
  --memory=512Mi \
  --cpu=1 \
  --timeout=60 \
  --project=$PROJECT_ID

# 6.3 Deploy prod stub
gcloud run deploy agentic-travel-booking-api-prod \
  --image=gcr.io/cloudrun/hello \
  --region=$REGION \
  --platform=managed \
  --allow-unauthenticated \
  --service-account=$SA_EMAIL \
  --min-instances=1 \
  --max-instances=20 \
  --memory=512Mi \
  --cpu=1 \
  --timeout=300 \
  --concurrency=80 \
  --project=$PROJECT_ID

# 6.4 Capture URLs
export STAGING_URL=$(gcloud run services describe agentic-travel-booking-api-staging \
  --region=$REGION --project=$PROJECT_ID --format='value(status.url)')
export PROD_URL=$(gcloud run services describe agentic-travel-booking-api-prod \
  --region=$REGION --project=$PROJECT_ID --format='value(status.url)')
echo "Staging: $STAGING_URL"
echo "Prod:    $PROD_URL"
```

**Verification:**
```bash
gcloud run services list --region=$REGION --project=$PROJECT_ID
curl --fail "$STAGING_URL"   # Returns hello-world HTML
curl --fail "$PROD_URL"
```

*Related: plan.md §9, §16*

---

## Section 7 — Cloud Scheduler Keep-Alive Cron

**~5 minutes**

Prevents Neon Postgres from auto-suspending between requests (Risk 1 from Phase 0
planning). The `/health` endpoint must issue `SELECT 1` against the DB (Phase 1
implementation requirement). Until Phase 1 ships, the cron hits a 404 — expected.

Cloud Scheduler free tier: 3 jobs/month. This is job 1 of 3.

```bash
# 7.1 Create Cloud Scheduler service account (if not already present)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-cloudscheduler.iam.gserviceaccount.com" \
  --role="roles/cloudscheduler.serviceAgent" 2>/dev/null || true

# 7.2 Create the keep-alive job
gcloud scheduler jobs create http neon-keepalive \
  --location=$REGION \
  --schedule="*/4 * * * *" \
  --uri="${STAGING_URL}/health" \
  --http-method=GET \
  --description="Keep Neon Postgres warm — prevents 1-3s cold start on first request after idle" \
  --project=$PROJECT_ID

# Note: Cloud Scheduler does not support all regions.
# If $REGION is not supported, use the closest available:
#   gcloud scheduler locations list
```

**Verification:**
```bash
gcloud scheduler jobs list --location=$REGION --project=$PROJECT_ID
# Manually trigger to confirm it fires (will 404 until Phase 1):
gcloud scheduler jobs run neon-keepalive --location=$REGION --project=$PROJECT_ID
```

*Related: plan.md §15 (Risk 1 from Phase 0 review), docs/backlog.md*

---

## Section 8 — Neon Postgres

**~15 minutes**

1. Go to **neon.tech** → New project → Name: `agentic-travel-booking-system`
2. Choose region: closest to `$REGION` (for `asia-south1` → Singapore; for `us-central1` → US East)
3. Postgres version: 16

**Create staging branch:**
- Project → Branches → New branch
- Name: `staging`, branch from: `main`

**Capture connection strings:**
- For each branch, go to Connection Details → Connection string (pooled, asyncpg driver)
- Format: `postgresql+asyncpg://user:password@ep-xxx.region.aws.neon.tech/neondb?sslmode=require`

```bash
# Store in Secret Manager (replace <...> with actual values)
echo -n "postgresql+asyncpg://<staging-connection-string>" | \
  gcloud secrets versions add neon-database-url-staging --data-file=- --project=$PROJECT_ID

echo -n "postgresql+asyncpg://<prod-connection-string>" | \
  gcloud secrets versions add neon-database-url-prod --data-file=- --project=$PROJECT_ID
```

**Verification:**
```bash
# Test staging connection (requires psql with sslmode support)
NEON_STAGING=$(gcloud secrets versions access latest \
  --secret=neon-database-url-staging --project=$PROJECT_ID | \
  sed 's|postgresql+asyncpg|postgresql|')
psql "$NEON_STAGING" -c "SELECT version(), current_database();"
```

> **Neon free tier note:** The free tier auto-suspends compute after 5 minutes of
> inactivity (1–3s cold start on wake). The Cloud Scheduler cron (Section 7) mitigates
> this for staging. Production has min-instances=1 on Cloud Run which ensures at least
> one warm instance, but Neon still suspends — consider Neon Launch ($19/month) for
> production SLO compliance before launch.

*Related: plan.md §9, ADR-0004, §13.2 (p95 SLO)*

---

## Section 9 — Upstash Redis

**~10 minutes**

1. Go to **upstash.com** → Create database
2. Name: `agentic-travel-cache`
3. Region: closest to `$REGION` (for `asia-south1` → Singapore; for `us-central1` → US East)
4. Type: Regional (not Global — Global is paid)
5. TLS: enabled

**Capture credentials:**
- Dashboard → REST API → Endpoint (URL) and Token

```bash
echo -n "https://xxx.upstash.io" | \
  gcloud secrets versions add upstash-redis-url --data-file=- --project=$PROJECT_ID

echo -n "AXxxxxxxxxxxxxxxxx" | \
  gcloud secrets versions add upstash-redis-token --data-file=- --project=$PROJECT_ID
```

**Free tier ceiling:** 10,000 commands/day, 256 MB max. At v1 volume this is ample.
If a demo day spikes over 10K commands, the next day resets. Upstash Pro starts at $0.20
per 100K commands beyond the free tier if you need burst capacity.

**Verification:**
```bash
UPSTASH_URL=$(gcloud secrets versions access latest --secret=upstash-redis-url --project=$PROJECT_ID)
UPSTASH_TOKEN=$(gcloud secrets versions access latest --secret=upstash-redis-token --project=$PROJECT_ID)
curl -s -H "Authorization: Bearer $UPSTASH_TOKEN" "$UPSTASH_URL/ping"
# Expected: {"result":"PONG"}
```

*Related: plan.md §5.2, §8.3, §9*

---

## Section 10 — Vercel Project

**~15 minutes**

1. Go to **vercel.com** → Add New Project → Import from GitHub
2. Select repo `gaurav-gandhi-2411/agentic-travel-booking-system`
3. Root directory: `apps/web`
4. Framework preset: Next.js
5. Node.js version: 20.x
6. Leave build settings as default (Next.js auto-detected)

**Configure environment variables** (Vercel dashboard → Project Settings → Environment Variables):

| Variable | Environment | Value |
|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | Preview + Production | Staging URL for Preview; Prod URL for Production |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | Preview + Production | From Clerk (Section — not yet created; add placeholder) |
| `CLERK_SECRET_KEY` | Preview + Production | From Clerk (add placeholder) |

```bash
# Get Cloud Run URLs to configure NEXT_PUBLIC_API_BASE_URL
echo "Preview env NEXT_PUBLIC_API_BASE_URL = $STAGING_URL"
echo "Production env NEXT_PUBLIC_API_BASE_URL = $PROD_URL"
```

**Two deployment environments:**
- **Preview:** auto-deploys on every PR branch push. URL: `https://<branch>-<project>.vercel.app`
- **Production:** auto-deploys on push to `main`. URL: configured custom domain (or `<project>.vercel.app`)

**Trigger a preview deploy:**
Create a trivial branch (`git checkout -b test-vercel && git push origin test-vercel`),
open a PR, confirm Vercel posts a preview URL in the PR comments. Then close the PR.

**Verification:** Vercel dashboard shows deployment status green. Preview URL returns 404
(expected — no Next.js pages yet until Phase 8) not 500.

*Related: plan.md §9, §16*

---

## Section 11 — Anthropic API Key

**~10 minutes**

1. Go to **console.anthropic.com** → API Keys → Create Key
2. Name: `agentic-travel-dev`
3. Copy the key (shown once)

```bash
echo -n "sk-ant-xxx" | \
  gcloud secrets versions add anthropic-api-key --data-file=- --project=$PROJECT_ID
```

**Add credit:** Billing → Add credits → $20 recommended for development through Phase 6.
At $0.03–$0.10/request with prompt caching, $20 covers 200–650 full end-to-end
test requests.

**Prompt caching:** enabled by default for system prompts longer than 1,024 tokens. The
agent system prompts (Phase 3) will exceed this threshold. No configuration needed.

**Verification:**
```bash
ANTHROPIC_KEY=$(gcloud secrets versions access latest --secret=anthropic-api-key --project=$PROJECT_ID)
curl -s https://api.anthropic.com/v1/messages \
  -H "x-api-key: $ANTHROPIC_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{"model":"claude-haiku-4-5-20251001","max_tokens":16,"messages":[{"role":"user","content":"hi"}]}' | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print('OK:', d['content'][0]['text'])"
```

*Related: plan.md §9, §15*

---

## Section 12 — Amadeus + Duffel Developer Accounts

**~30 minutes (+ up to 24–48h for Duffel access approval)**

### 12.1 Amadeus Self-Service

1. Go to **developers.amadeus.com** → Create account → Verify email
2. My Apps → New App → Name: `agentic-travel-dev`
3. APIs to add: Flight Offers Search, Hotel List, Hotel Offers Search, Flight Offers Price, Flight Create Orders
4. Copy **Client ID** and **Client Secret** (shown in app dashboard)

Free dev tier: 2,000 API transactions/month, sandbox only.

```bash
echo -n "YOUR_AMADEUS_CLIENT_ID" | \
  gcloud secrets versions add amadeus-client-id --data-file=- --project=$PROJECT_ID

echo -n "YOUR_AMADEUS_CLIENT_SECRET" | \
  gcloud secrets versions add amadeus-client-secret --data-file=- --project=$PROJECT_ID
```

**Verification:**
```bash
# Get OAuth2 token
AMADEUS_ID=$(gcloud secrets versions access latest --secret=amadeus-client-id --project=$PROJECT_ID)
AMADEUS_SECRET=$(gcloud secrets versions access latest --secret=amadeus-client-secret --project=$PROJECT_ID)

TOKEN=$(curl -s -X POST "https://test.api.amadeus.com/v1/security/oauth2/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials&client_id=${AMADEUS_ID}&client_secret=${AMADEUS_SECRET}" | \
  python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Search flights: LHR → CDG, tomorrow
DATE=$(date -d '+1 day' '+%Y-%m-%d' 2>/dev/null || date -v+1d '+%Y-%m-%d')
curl -s "https://test.api.amadeus.com/v2/shopping/flight-offers?originLocationCode=LHR&destinationLocationCode=CDG&departureDate=${DATE}&adults=1&max=1" \
  -H "Authorization: Bearer $TOKEN" | python3 -c "import sys,json; d=json.load(sys.stdin); print('OK: found', len(d.get('data',[])), 'flight(s)')"
```

> **Note for tenant onboarding:** each tenant must create their own Amadeus developer
> account. We never share a single credential pool (ADR-0002, plan.md §8.4). The
> tenant onboarding runbook (Phase 11) will document this step-by-step.

### 12.2 Duffel

1. Go to **duffel.com** → Create account → Verify email
2. Dashboard → Access Keys → Create Key → Name: `agentic-travel-dev`
3. Select **Test** environment
4. Copy the API key (prefix `duffel_test_xxx`)

> Duffel may require a short approval step for developer access. If access is not
> immediate, proceed with Sections 13–15 and return here once approved.

```bash
echo -n "duffel_test_xxx" | \
  gcloud secrets versions add duffel-api-key --data-file=- --project=$PROJECT_ID
```

**Verification:**
```bash
DUFFEL_KEY=$(gcloud secrets versions access latest --secret=duffel-api-key --project=$PROJECT_ID)
curl -s "https://api.duffel.com/air/airports?iata_country_code=GB" \
  -H "Authorization: Bearer $DUFFEL_KEY" \
  -H "Duffel-Version: v2" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print('OK:', d['meta']['total'], 'airports')"
```

*Related: ADR-0002, ADR-0003, plan.md §8.4, §9, docs/backlog.md (tenant Amadeus onboarding)*

---

## Section 13 — GitHub Actions Secrets and Variables

**~10 minutes**

```bash
# Ensure you have the values from earlier sections:
echo "WIF_PROVIDER = $WIF_PROVIDER"
echo "WIF_SERVICE_ACCOUNT = $SA_EMAIL"
echo "GCP_PROJECT_ID = $PROJECT_ID"
echo "CLOUD_RUN_REGION = $REGION"

# Add repository secrets (sensitive — not visible after creation)
gh secret set WIF_PROVIDER \
  --body="$WIF_PROVIDER" \
  --repo=$GITHUB_REPO

gh secret set WIF_SERVICE_ACCOUNT \
  --body="$SA_EMAIL" \
  --repo=$GITHUB_REPO

# Add repository variables (visible in Actions UI, not sensitive)
gh variable set GCP_PROJECT_ID \
  --body="$PROJECT_ID" \
  --repo=$GITHUB_REPO

gh variable set CLOUD_RUN_REGION \
  --body="$REGION" \
  --repo=$GITHUB_REPO

gh variable set ARTIFACT_REGISTRY_REPO \
  --body="$AR_REPO" \
  --repo=$GITHUB_REPO
```

**Verification:**
```bash
gh secret list --repo=$GITHUB_REPO
gh variable list --repo=$GITHUB_REPO
```

Expected secrets: `WIF_PROVIDER`, `WIF_SERVICE_ACCOUNT`
Expected variables: `GCP_PROJECT_ID`, `CLOUD_RUN_REGION`, `ARTIFACT_REGISTRY_REPO`

*Related: plan.md §16*

---

## Section 14 — Enable Deploy Workflows

**~5 minutes**

Both deploy workflows are gated with `if: ${{ false }}` at the job level (commit 22).
Remove this gate once all secrets and variables from Section 13 are in place.

```bash
# In your local repo:
git checkout -b chore/enable-deploy-workflows

# Edit .github/workflows/deploy-staging.yml:
# Remove the line: if: ${{ false }}  # Disabled until Stage 0.4 GCP provisioning is complete.

# Edit .github/workflows/deploy-prod.yml:
# Remove the same line.

# Commit and push
git add .github/workflows/deploy-staging.yml .github/workflows/deploy-prod.yml
git commit -m "chore: enable Cloud Run deploy workflows post-Stage-0.4 provisioning"
git push origin chore/enable-deploy-workflows
# Open PR, merge to main.
```

> This is the one commit in this runbook that goes through the normal PR process.
> Do not push directly to main.

---

## Section 15 — End-to-End Validation

**~15 minutes**

```bash
# 15.1 Trigger a staging deploy by pushing a trivial commit to main
echo "# runbook validation $(date)" >> docs/runbooks/cloud-setup.md
git add docs/runbooks/cloud-setup.md
git commit -m "chore: runbook validation trigger"
git push origin main

# 15.2 Watch the deploy-staging workflow
gh run watch --repo=$GITHUB_REPO

# 15.3 Confirm the Cloud Run revision updated
gcloud run revisions list \
  --service=agentic-travel-booking-api-staging \
  --region=$REGION \
  --project=$PROJECT_ID

# 15.4 Smoke test staging (will 404 until Phase 1 ships the API, but must return 404 not 5xx)
STAGING_URL=$(gcloud run services describe agentic-travel-booking-api-staging \
  --region=$REGION --project=$PROJECT_ID --format='value(status.url)')
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$STAGING_URL/health")
echo "Health endpoint status: $STATUS"
# 404 = expected (no app yet). 200 = Phase 1 is live. 5xx = investigate.

# 15.5 Confirm Neon is reachable from the scheduled cron
gcloud scheduler jobs run neon-keepalive --location=$REGION --project=$PROJECT_ID
# Check logs: Cloud Logging -> Logs Explorer -> resource type: Cloud Scheduler Job

# 15.6 Confirm Vercel preview deployed
gh pr list --repo=$GITHUB_REPO
# Any open PRs should have a Vercel preview URL in the PR checks.
```

**All green:** CI passing on main, deploy-staging fired and updated the Cloud Run revision,
staging URL returns non-5xx, Neon scheduler job ran without GCP error, Vercel preview
deploys on PRs. You are ready to authorize Phase 1.

---

## Appendix — Cleaning Up (if you need to start over)

```bash
# Delete all GCP resources (destructive — use only to reset)
gcloud projects delete $PROJECT_ID
# Neon: neon.tech dashboard -> Project Settings -> Delete project
# Upstash: upstash.com dashboard -> Database -> Delete
# Vercel: vercel.com -> Project Settings -> Delete Project
# GitHub secrets: gh secret remove WIF_PROVIDER --repo=$GITHUB_REPO (etc.)
```

---

*This runbook covers plan.md Phase 0 (§11) external provisioning requirements.
Once complete, authorize Phase 1 (Provider Adapters + Search) per plan.md §11.*
