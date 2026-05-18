"""Integration tests for all five LLM adapters using VCR.py cassettes.

Each test enters the cassette context before creating the adapter so that
vcrpy can patch the httpx transport prior to client initialisation.
Cassettes are stored at tests/fixtures/cassettes/{adapter}/chat.yaml.
No real network calls are made -- record_mode="none" enforces this.

To re-record a cassette: delete the .yaml file and run the test with
LLM_ROUTING_PROFILE set and the appropriate API key in the environment.
vcrpy will record the interaction on first run and replay on subsequent runs.

Also contains unit tests for extra_params threading (mock-based, no cassette).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import vcr

from travel_agent.llm.anthropic import AnthropicAdapter
from travel_agent.llm.base import Message, ToolDefinition
from travel_agent.llm.groq import GroqAdapter
from travel_agent.llm.nvidia import NIMAdapter
from travel_agent.llm.ollama import OllamaAdapter
from travel_agent.llm.openrouter import OpenRouterAdapter
from travel_agent.llm.vllm import VLLMAdapter

_CASSETTE_DIR = Path(__file__).parent.parent.parent / "fixtures" / "cassettes"

_VCR = vcr.VCR(
    cassette_library_dir=str(_CASSETTE_DIR),
    record_mode="none",
    filter_headers=["authorization", "x-api-key", "cookie", "set-cookie"],
    decode_compressed_response=True,
    match_on=["method", "scheme", "host", "port", "path"],
)

_MSG = [Message(role="user", content="Say hi in one word")]
_TOOL_MSG = [Message(role="user", content="Find flights")]
_FLIGHT_TOOL = ToolDefinition(
    name="search_flights",
    description="Search flights",
    input_schema={"type": "object", "properties": {"origin": {"type": "string"}}},
)


# ── Anthropic ─────────────────────────────────────────────────────────────────


async def test_anthropic_chat_replays_cassette(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    with _VCR.use_cassette("anthropic/chat.yaml"):
        adapter = AnthropicAdapter()
        response = await adapter.chat(_MSG, model="claude-sonnet-4-6")
    assert response.content == "Hello from Anthropic!"
    assert response.model == "claude-sonnet-4-6"
    assert response.input_tokens == 8
    assert response.output_tokens == 6
    assert response.tool_calls == []
    assert response.latency_ms >= 0


# ── Ollama ────────────────────────────────────────────────────────────────────


async def test_ollama_chat_replays_cassette() -> None:
    with _VCR.use_cassette("ollama/chat.yaml"):
        adapter = OllamaAdapter()
        response = await adapter.chat(_MSG, model="qwen2.5:7b")
    assert response.content == "Hello from Ollama!"
    assert response.model == "qwen2.5:7b"
    assert response.input_tokens == 10
    assert response.output_tokens == 6
    assert response.tool_calls == []


async def test_ollama_accepts_custom_base_url() -> None:
    with _VCR.use_cassette("ollama/chat.yaml"):
        adapter = OllamaAdapter(base_url="http://localhost:11434")
        response = await adapter.chat(_MSG, model="qwen2.5:7b")
    assert response.content == "Hello from Ollama!"


# ── OpenRouter ────────────────────────────────────────────────────────────────


async def test_openrouter_chat_replays_cassette(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
    with _VCR.use_cassette("openrouter/chat.yaml"):
        adapter = OpenRouterAdapter()
        response = await adapter.chat(_MSG, model="qwen/qwen-2.5-72b-instruct:free")
    assert response.content == "Hello from OpenRouter!"
    assert response.model == "qwen/qwen-2.5-72b-instruct:free"
    assert response.input_tokens == 12
    assert response.output_tokens == 5
    assert response.tool_calls == []


# ── Groq ──────────────────────────────────────────────────────────────────────


async def test_groq_chat_replays_cassette(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test-key")
    with _VCR.use_cassette("groq/chat.yaml"):
        adapter = GroqAdapter()
        response = await adapter.chat(_MSG, model="llama-3.3-70b-versatile")
    assert response.content == "Hello from Groq!"
    assert response.model == "llama-3.3-70b-versatile"
    assert response.input_tokens == 11
    assert response.output_tokens == 4
    assert response.tool_calls == []


# ── NVIDIA NIM ────────────────────────────────────────────────────────────────


async def test_nvidia_nim_chat_replays_cassette(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-key")
    with _VCR.use_cassette("nvidia/chat.yaml"):
        adapter = NIMAdapter()
        response = await adapter.chat(_MSG, model="deepseek-ai/deepseek-v4-flash")
    assert response.content == "Hello from NIM!"
    assert response.model == "deepseek-ai/deepseek-v4-flash"
    assert response.input_tokens == 10
    assert response.output_tokens == 4
    assert response.tool_calls == []


async def test_nvidia_nim_tool_call_via_cassette(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-key")
    with _VCR.use_cassette("nvidia/chat_tool_call.yaml"):
        adapter = NIMAdapter()
        response = await adapter.chat(
            _TOOL_MSG, model="deepseek-ai/deepseek-v4-flash", tools=[_FLIGHT_TOOL]
        )
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "search_flights"
    assert response.tool_calls[0].input == {"origin": "BOM", "destination": "CDG"}
    assert response.content == ""


async def test_nvidia_nim_raises_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="NVIDIA_API_KEY"):
        NIMAdapter()


# ── vLLM ──────────────────────────────────────────────────────────────────────


async def test_vllm_chat_replays_cassette() -> None:
    with _VCR.use_cassette("vllm/chat.yaml"):
        adapter = VLLMAdapter()
        response = await adapter.chat(_MSG, model="qwen2.5-7b-instruct")
    assert response.content == "Hello from vLLM!"
    assert response.model == "qwen2.5-7b-instruct"
    assert response.input_tokens == 10
    assert response.output_tokens == 5
    assert response.tool_calls == []


async def test_vllm_accepts_custom_base_url() -> None:
    with _VCR.use_cassette("vllm/chat.yaml"):
        adapter = VLLMAdapter(base_url="http://localhost:8000")
        response = await adapter.chat(_MSG, model="qwen2.5-7b-instruct")
    assert response.content == "Hello from vLLM!"


# ── Tool-call path (_openai_compat.py lines 41-42) ────────────────────────────


async def test_ollama_tool_call_via_cassette() -> None:
    with _VCR.use_cassette("ollama/chat_tool_call.yaml"):
        adapter = OllamaAdapter()
        response = await adapter.chat(_TOOL_MSG, model="qwen2.5:7b", tools=[_FLIGHT_TOOL])
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "search_flights"
    assert response.tool_calls[0].input == {"origin": "BOM", "destination": "CDG"}
    assert response.content == ""


async def test_openrouter_tool_call_via_cassette(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
    with _VCR.use_cassette("openrouter/chat_tool_call.yaml"):
        adapter = OpenRouterAdapter()
        response = await adapter.chat(
            _TOOL_MSG, model="qwen/qwen-2.5-72b-instruct:free", tools=[_FLIGHT_TOOL]
        )
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "search_flights"
    assert response.content == ""


async def test_groq_tool_call_via_cassette(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test-key")
    with _VCR.use_cassette("groq/chat_tool_call.yaml"):
        adapter = GroqAdapter()
        response = await adapter.chat(
            _TOOL_MSG, model="llama-3.3-70b-versatile", tools=[_FLIGHT_TOOL]
        )
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "search_flights"
    assert response.content == ""


async def test_vllm_tool_call_via_cassette() -> None:
    with _VCR.use_cassette("vllm/chat_tool_call.yaml"):
        adapter = VLLMAdapter()
        response = await adapter.chat(_TOOL_MSG, model="qwen2.5-7b-instruct", tools=[_FLIGHT_TOOL])
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "search_flights"
    assert response.content == ""


# ── extra_params threading (no cassette — mock-based) ────────────────────────


async def test_groq_extra_params_reach_api_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """extra_params dict is merged into the create() kwargs sent to the Groq API."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test-key")
    adapter = GroqAdapter()

    fake_choice = MagicMock()
    fake_choice.message.content = "ok"
    fake_choice.message.tool_calls = None
    fake_resp = MagicMock()
    fake_resp.choices = [fake_choice]
    fake_resp.model = "openai/gpt-oss-120b"
    fake_resp.usage.prompt_tokens = 10
    fake_resp.usage.completion_tokens = 5

    mock_create = AsyncMock(return_value=fake_resp)
    with patch.object(adapter._client.chat.completions, "create", mock_create):
        await adapter.chat(
            _MSG,
            model="openai/gpt-oss-120b",
            extra_params={"reasoning_effort": "low"},
        )

    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs.get("reasoning_effort") == "low"
