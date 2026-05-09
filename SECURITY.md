# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| main    | Yes       |

## Reporting a Vulnerability

Do **not** open a public GitHub issue for security vulnerabilities.

Email **security@[domain-tbd]** with:
- Description of the vulnerability
- Steps to reproduce
- Potential impact

You will receive a response within 48 hours. We follow coordinated disclosure:
vulnerabilities are patched before public disclosure.

## Security Posture

- API keys hashed in DB; never logged or returned after creation.
- Tenant provider credentials encrypted at rest via GCP KMS.
- Postgres Row-Level Security on all tables (defense-in-depth with app-layer checks).
- TLS 1.3 in transit. HSTS on web frontend. Cloud Run enforces HTTPS.
- PII scrubbed before log emission. No raw provider responses with traveler data
  persisted beyond the audit table.
- Static analysis: bandit (Python), ESLint (TS), gitleaks (secret scanning in CI).
- Dependency hygiene: pip-audit + npm audit + Dependabot weekly.
- See `docs/architecture/threat-model.md` (written in Phase 7).
