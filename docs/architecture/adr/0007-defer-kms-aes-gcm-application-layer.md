# ADR-0007: Defer KMS, Use AES-GCM in Application Layer

**Status:** Accepted — 2026-05-09

---

## Context

plan.md §8.4 originally specified GCP Cloud KMS for encrypting per-tenant travel provider
credentials (Amadeus client ID/secret, Duffel API key) stored in Postgres. KMS provides
HSM-backed key management with automatic rotation, per-key IAM, and audit logging — a
strong posture for multi-tenant SaaS.

The project operates on a strict $0 budget. KMS is priced at:

- **$0.06 per key version per month** — at one key version per tenant, this is $0.06 ×
  number of tenants per month, which compounds with growth.
- **$0.03 per 10,000 cryptographic operations** — encrypt/decrypt calls at runtime, billed
  per request.

"Near-free" is not the same as "free." Even at v1 with two tenants and modest traffic,
KMS adds ~$0.15–$0.50/month. This violates the budget constraint. More importantly,
taking on KMS operational complexity (key rotation, IAM per key, version management) is
disproportionate for a pre-revenue system.

The requirement to protect tenant credentials at rest remains. The question is mechanism,
not goal.

GCP Secret Manager, which the project already uses, allows up to **6 active versions per
secret at no additional cost** beyond the ~$0.06/secret/month base rate. A master key
stored in Secret Manager and rotated quarterly satisfies the protection requirement at $0
incremental cost.

---

## Decision

Use **AES-256-GCM** encryption at the application layer. A single 32-byte master key is:

1. Generated locally with `openssl rand -base64 32` (never transmitted unencrypted).
2. Stored in GCP Secret Manager as `tenant-credential-master-key`.
3. Loaded by the application at startup via the Secret Manager API.
4. Used by `apps/api/src/travel_agent/tenancy/crypto.py` (~30 lines using the
   `cryptography` library) to encrypt credentials before writing them to Postgres and
   decrypt them on read.

**Encryption scheme:**

```python
# Pseudocode — implementation in tenancy/crypto.py
key = base64.urlsafe_b64decode(master_key_from_secret_manager)
iv = os.urandom(12)                      # 96-bit nonce, fresh per encryption
ct, tag = aes_gcm_encrypt(key, iv, plaintext)
stored = base64.b64encode(iv + tag + ct) # IV + auth tag + ciphertext, stored in Postgres
```

Decryption is the inverse; the 12-byte IV is embedded in the stored value so rotation can
re-encrypt rows by decrypting with the old key and re-encrypting with the new one.

**Key rotation protocol:** Quarterly manual rotation, documented in
`docs/runbooks/master-key-rotation.md` (created in Phase 7 when multi-tenancy lands).
Rotation procedure: generate new key → add new Secret Manager version → re-encrypt all
`tenant_credentials` rows → disable old version → verify. The 6-version limit means up to
5 historical versions can be retained (sufficient for a recovery window).

**Deferral condition:** Migrate to Cloud KMS when the project reaches the commercial tier
and revenue justifies ~$0.10–$0.50/month in key-management costs and the operational
complexity of per-tenant key issuance. The application-layer crypto interface is designed
to be swappable — the `crypto.py` module exposes `encrypt(plaintext)` and
`decrypt(ciphertext)`, so the backing implementation can change without touching agent
code.

---

## Consequences

**Positive:**
- $0 incremental cost. Secret Manager is already in the stack; one additional secret is
  within the free-tier allocation.
- Implementation is straightforward: ~30 lines using `cryptography`, a well-audited,
  widely-deployed Python library.
- The `encrypt`/`decrypt` interface is swap-friendly — KMS can back the same interface in
  a future phase without touching call sites.
- No additional GCP API to enable, no additional IAM role class to learn.

**Negative:**
- **No HSM backing.** The master key is a software secret, not hardware-attested. An
  attacker who exfiltrates both the Secret Manager secret and the Postgres database gets
  plaintext credentials. KMS would require a separate HSM compromise.
- **No per-tenant key separation.** A single master key encrypts all tenants' credentials.
  Master key compromise means all tenants are affected. With KMS, each tenant could have
  an independent key with independent rotation.
- **Manual rotation.** KMS supports automatic rotation with zero-downtime key versions.
  Our protocol requires a scripted re-encryption run. This is a ~5-minute operation but
  requires human scheduling.
- **No per-operation audit log.** KMS logs every encrypt/decrypt call in Cloud Audit Logs.
  Secret Manager logs access to the master key at load time but not at each use.

**Neutral:**
- `cryptography` library is already a transitive dependency via several other packages.
  Adding it explicitly to `pyproject.toml` is documentation more than a new dependency.
- The stored format (IV + tag + ciphertext, base64-encoded) is self-describing enough that
  re-encryption scripts do not need to know which key version was used — the active Secret
  Manager version is always the encryption key; decryption tries the current version first.

---

## Alternatives Considered

### Alternative 1: GCP Cloud KMS

Per-tenant keys with automatic 90-day rotation. Full HSM backing and per-operation audit
logs. Tight IAM scoping per key.

**Rejected because:** cost ($0.06/key-version/month per tenant + per-op fees). At v1
pre-revenue with a $0 budget, this is not acceptable. KMS is noted as the migration target
in the decision above.

### Alternative 2: Per-tenant secrets in Secret Manager

Each tenant gets a dedicated secret in Secret Manager for their provider credentials (no
application-layer encryption; Secret Manager is the encryption boundary).

**Rejected because:** the Secret Manager free tier allows up to **6 active secret
versions** across the project (not per secret). At one secret per tenant, hitting 6
tenants means paying for additional secrets. The v1 target is a small number of tenants,
but we do not want the architecture to become more expensive at 5–6 tenants than at 2.
Additionally, per-tenant secrets require per-tenant IAM bindings, which adds operational
surface at tenant onboarding.

### Alternative 3: Application encryption with key in environment variable

Store the master key in an environment variable on Cloud Run rather than in Secret
Manager.

**Rejected because:** environment variables are accessible via process inspection
(`/proc/self/environ` on Linux, Cloud Run metadata endpoint, error reporting frameworks,
and structured logs that accidentally capture env). Secret Manager provides a narrower
access path: the application must actively call the API, which is auditable and
revocable. Env vars also complicate rotation (requires a new Cloud Run revision for each
rotation vs. adding a new Secret Manager version).

---

*Referenced plan.md sections: §8.4, §14, §15*
*See also: docs/runbooks/cloud-setup.md §5, docs/runbooks/master-key-rotation.md (Phase 7)*
