# Architecture Decision Records — Index

All ADRs for the agentic-travel-booking-system project.

> ADRs 0017 and 0018 were not created (numbering gap is intentional — those designs were absorbed into adjacent ADRs during Phase 2C).

| ADR | Title | Phase | One-line summary |
|---|---|---|---|
| [0001](0001-multi-agent-coordinator-pattern.md) | Multi-Agent Coordinator Pattern | 1 | Deterministic Python coordinator drives stateless agents; coordinator owns pipeline flow, not the agents |
| [0002](0002-provider-adapter-pattern.md) | Provider Adapter Pattern | 1 | Protocol-based adapter interface for LLM providers; no inheritance, structural duck-typing |
| [0003](0003-affiliate-vs-merchant-of-record.md) | Affiliate Redirect vs. Merchant-of-Record Booking | 1 | Use affiliate redirect model (lower liability, faster to ship); MoR deferred to Phase 3 |
| [0004](0004-postgres-rls-for-tenancy.md) | Postgres RLS for Multi-Tenant Isolation | 1 | Row-level security for multi-tenancy; deferred until Phase 3 when Postgres is provisioned |
| [0005](0005-hierarchical-window-search.md) | Hierarchical Window Search Algorithm | 1 | Multi-level date-window search to balance coverage vs. API cost |
| [0006](0006-pareto-frontier-archetypes.md) | Pareto Frontier Archetypes | 1 | OptimizerAgent selects best-value and best-experience flights via Pareto frontier |
| [0007](0007-defer-kms-aes-gcm-application-layer.md) | Defer KMS, Use AES-GCM in Application Layer | 1 | Deferred KMS for field encryption; AES-GCM in-app for Phase 1 |
| [0008](0008-multi-provider-llm-abstraction.md) | Multi-Provider LLM Abstraction with Per-Agent Routing | 2C | `llm_routing.yaml` profiles + per-agent routing; Groq primary, NIM and Anthropic secondary |
| [0009](0009-open-source-model-strategy.md) | Open-Source Model Strategy and Per-Agent Acceptance Bar | 2C | Tier each agent by accuracy requirement; use free-tier open-source where the bar is met |
| [0010](0010-eval-harness-design.md) | Eval Harness Design | 2C | Custom JSONL-based eval harness; no external eval framework; 24 deterministic scenarios |
| [0011](0011-dataset-generation-pipeline.md) | Dataset Generation via OpenRouter Teacher + Claude.ai QA | 2C | Synthetic dataset generation pipeline for initial training/baseline |
| [0012](0012-publishing-strategy.md) | Publishing Strategy for Models, Datasets, and Methodology | 2C | Open-source methodology + HuggingFace Space demo; no proprietary data release |
| [0013](0013-provider-stack-revision.md) | Provider Stack Revision — Travelpayouts + Synthetic | 2C | Replaced Amadeus with Travelpayouts (affiliate) + SyntheticProvider for eval stability |
| [0014](0014-synthetic-provider-design.md) | Synthetic Provider Design | 2C | Deterministic synthetic flight provider for reproducible evals; no live API dependency |
| [0015](0015-optimizer-eval-design.md) | Optimizer Eval Harness Design | 2C | 24-scenario Pareto eval with label-correctness + coherence gates; thresholds in thresholds.py |
| [0016](0016-llm-judge-design.md) | LLM-Judge Design for Coherence Scoring | 2C | Median-of-3 LLM judge; Qwen3-32B primary on Groq; file-based JSON cache keyed by (scenario, label, explanation) |
| [0019](0019-conversation-manager-agent.md) | ConversationManagerAgent — Level 2 Intent Classification | 2C.4 | Single-turn intent classifier (REFINE / REPLAN / NO_OP); Llama 3.3 default; Level 2 scope |
| [0020](0020-prompt-caching.md) | Prompt Caching Strategy | 2C.4.5 | Anthropic prompt caching wired but inactive (prompts below 1,024-token threshold); Phase 3 work |
| [0021](0021-cache-observability.md) | Cache Backend Observability | 2D.1 | Structured log events for Redis cache selection, hits, and misses; Upstash Redis confirmed active |
| [0022](0022-skip-ci-guardrail.md) | [skip ci] Guardrail via Required Status Check | 2D.2 | `check-no-skip-ci` required status check blocks any commit with [skip ci] from merging to main |
| [0023](0023-production-deploy-v0-6-0.md) | Production Deploy: v0.5.0 → v0.6.0 | 2D.3 | First production deploy in two weeks; canary gate; revealed backend was frozen since May 15 |
| [0024](0024-production-frontend-alignment.md) | Production Frontend Alignment: Vercel Freeze + Empty Env Vars | 2D.4 | Both prod surfaces were silently non-functional from day one; fixed via redeploy + env var correction |
| [0025](0025-staleness-guardrail.md) | Production Staleness Guardrail | 2D.5 | Daily GitHub Actions cron checks both Cloud Run and Vercel for drift vs main; alert-only, never deploys |
| [0026](0026-eval-rigor.md) | Eval Rigor: Judge Consistency Gate and Cache Poison Fix | 2D.6 | `judge_model`/`parse_failed` on JudgeScore; validate-on-read; cross-profile gate; Approach 3 rationale |
