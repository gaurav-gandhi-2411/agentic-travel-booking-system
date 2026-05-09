# ADR-0008: Multi-Provider LLM Abstraction with Per-Agent Routing

**Status:** Accepted — 2026-05-09

---

## Context

The original plan specified the Anthropic Python SDK used directly throughout the
codebase — each agent imports `anthropic`, constructs `Messages`, and calls Claude.
This design was appropriate when the project had a single LLM vendor and a small API
budget.

The revised project scope adds two requirements that make direct SDK usage untenable:

1. **Open-source-first execution.** The system must run on local hardware (RTX 3070) and
   free-tier APIs with $0 spend. Anthropic SDK calls always incur cost. There is no
   free tier for the Anthropic API.

2. **Benchmark comparisons.** The research track requires running the same agent with
   multiple models (Qwen 2.5 7B, Qwen 2.5 72B, Llama 3.3 70B, Claude Sonnet 4.6) and
   comparing outputs. Direct SDK usage means each comparison requires forking agent code
   or adding conditional branches — not a maintainable evaluation framework.

The system needs to support:

- **Ollama** for local development (Qwen 2.5 7B/14B, no API cost, requires local GPU).
- **OpenRouter free tier** for cloud execution without local GPU (rate-limited but $0).
- **Groq free tier** as a fallback when OpenRouter is rate-limited.
- **Anthropic** for evaluation baselines only — off by default, requires `ANTHROPIC_API_KEY`.
- **vLLM** as a future path for production serving of fine-tuned models.

Additionally, different agents have different capability requirements. The narrow agents
(FlightHunter, HotelHunter, Booking) perform structured extraction and state-machine
transitions — tasks a 7B model handles well. The hard agents (Optimizer, Conversation)
require multi-step reasoning and nuanced language generation — tasks that may need a 14B
or 70B model to meet the acceptance bar from ADR-0009.

---

## Decision

Define an `LLMClient` **Protocol** in `apps/api/src/travel_agent/llm/base.py`. Each LLM
provider is an adapter that implements this Protocol. Agent code calls the Protocol;
the concrete adapter is injected at startup via a factory function.

**Protocol definition (simplified):**

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class LLMClient(Protocol):
    async def chat(
        self,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int = 2048,
        temperature: float = 0.0,
        system: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse: ...
```

`LLMResponse` is a dataclass with fields: `content: str`, `model: str`,
`input_tokens: int`, `output_tokens: int`, `latency_ms: float`.

**Adapters implemented (stubs in Phase 0.X, full implementations in Phase 2.5):**

| Adapter | Module | Default profile |
|---|---|---|
| `OllamaAdapter` | `llm/ollama.py` | `local` |
| `OpenRouterAdapter` | `llm/openrouter.py` | `free` |
| `GroqAdapter` | `llm/groq.py` | `free` (fallback) |
| `AnthropicAdapter` | `llm/anthropic.py` | `eval` only |
| `vLLMAdapter` | `llm/vllm.py` | future — stub only |

The `AnthropicAdapter` raises `RuntimeError` on instantiation if `ANTHROPIC_API_KEY` is
not set, preventing accidental spend in local/CI environments.

**Per-agent routing via `apps/api/config/llm_routing.yaml`:**

```yaml
profiles:
  local:
    planner:           qwen2.5:7b
    flight_hunter:     qwen2.5:7b
    hotel_hunter:      qwen2.5:7b
    optimizer:         qwen2.5:14b
    booking:           qwen2.5:7b
    conversation:      qwen2.5:14b
    provider:          ollama
    base_url:          http://localhost:11434

  free:
    planner:           qwen/qwen-2.5-72b-instruct:free
    flight_hunter:     meta-llama/llama-3.3-70b-instruct:free
    hotel_hunter:      meta-llama/llama-3.3-70b-instruct:free
    optimizer:         qwen/qwen-2.5-72b-instruct:free
    booking:           meta-llama/llama-3.3-70b-instruct:free
    conversation:      qwen/qwen-2.5-72b-instruct:free
    provider:          openrouter
    fallback_provider: groq
    fallback_models:
      flight_hunter:   llama-3.3-70b-versatile
      hotel_hunter:    llama-3.3-70b-versatile
      booking:         llama-3.3-70b-versatile
      planner:         llama-3.3-70b-versatile
      optimizer:       llama-3.3-70b-versatile
      conversation:    llama-3.3-70b-versatile

  eval:
    planner:           claude-sonnet-4-6
    flight_hunter:     claude-haiku-4-5-20251001
    hotel_hunter:      claude-haiku-4-5-20251001
    optimizer:         claude-sonnet-4-6
    booking:           claude-haiku-4-5-20251001
    conversation:      claude-sonnet-4-6
    provider:          anthropic
```

**Profile selection:** `LLM_ROUTING_PROFILE` environment variable. Defaults to `local`
if unset. Loaded once at application startup by `llm/routing.py`.

**Factory function:** `get_llm_client(agent: str) -> LLMClient` in `llm/__init__.py`.
Reads the active profile, resolves the model name and provider for the named agent, and
returns the appropriate adapter instance. Agents call this factory; they do not
instantiate adapters directly.

**~150 lines of glue code total** across `base.py`, `routing.py`, and `__init__.py`.
Each adapter is ~50–80 lines (HTTP client setup + `chat()` implementation).

---

## Consequences

**Positive:**
- Agents are fully vendor-agnostic. An agent that works under `OllamaAdapter` works under
  `AnthropicAdapter` without code changes — the Protocol contract enforces this.
- The eval harness (ADR-0010) can instantiate any adapter and run the same agent against
  multiple models by swapping the injected `LLMClient`.
- Switching the entire system from one provider to another in local dev is one environment
  variable change.
- Mock `LLMClient` for unit tests is a 10-line class — no HTTP stubs, no API keys.
- New providers require only a new adapter file; existing agent code is untouched.

**Negative:**
- The abstraction normalizes to a lowest-common-denominator interface. Provider-specific
  features — Anthropic's extended thinking, OpenRouter's provider routing headers, Groq's
  speculative decoding — are not exposed through the Protocol. Per-provider kwargs can
  be passed via `**kwargs` but this is not type-safe.
- Streaming responses are not in the initial Protocol. Agents that need streaming (the
  ConversationManagerAgent in Phase 8) will require a second protocol method or an
  adapter-specific code path. This is a known limitation accepted for v1.
- Every new provider needs an adapter. The abstraction adds ~50–80 lines per provider;
  this is acceptable at the current provider count but grows linearly.

**Neutral:**
- The `provider` field in `llm_routing.yaml` determines which adapter class is
  instantiated. The `model` field is passed as a string to the adapter's `chat()` method.
  The adapter is responsible for knowing how to send that model string to its backend.
- `runtime_checkable` Protocol means `isinstance(client, LLMClient)` works in tests even
  without inheriting from a base class.

---

## Alternatives Considered

### Alternative 1: LiteLLM

LiteLLM is a library that provides a unified interface over ~100 LLM providers.

**Rejected because:**
- Heavy dependency (~15 MB install, pulls in numerous provider SDKs even when unused).
- Opinionated retry and fallback semantics conflict with our `CallBudget` enforcement
  in the Coordinator (ADR-0001). LiteLLM's automatic retries could silently blow the
  20-LLM-call budget.
- LiteLLM's model string format (`anthropic/claude-sonnet-4-6`) is different from each
  provider's native format, adding a translation layer we'd need to maintain.
- Version stability has been inconsistent; breaking changes in minor versions have caused
  production incidents in other projects.

### Alternative 2: Direct provider SDKs at the agent level

Each agent imports the SDK it needs: `from anthropic import AsyncAnthropic` for eval,
`import httpx` for OpenRouter, etc. Agents contain conditional branches for provider
selection.

**Rejected because:**
- N agents × M providers = N×M coupling points. A change to the OpenRouter API requires
  touching every agent that uses it.
- Agents become untestable without standing up provider connections or writing provider-
  specific mocks. The Protocol approach reduces mocking to a single 10-line class.
- The eval harness cannot swap providers without modifying agent code.

### Alternative 3: Single provider with model aliasing

Keep the Anthropic SDK, but configure it to point at an OpenAI-compatible proxy
(e.g., Ollama's OpenAI-compat endpoint) for local runs.

**Rejected because:**
- Anthropic SDK is not designed for non-Anthropic backends. Model parameter handling,
  system prompt placement, and tool-use schemas differ between Anthropic and
  OpenAI-compatible APIs.
- This would require Ollama to expose an Anthropic-compatible API, which it does not.
  The OpenAI-compat shim works for OpenAI-format clients, not Anthropic-format clients.

---

*Referenced plan.md sections: §4.1, §9, §11 (Phase 2.5), §20*
*See also: ADR-0009 (model selection), ADR-0010 (eval harness), apps/api/config/llm_routing.yaml*
