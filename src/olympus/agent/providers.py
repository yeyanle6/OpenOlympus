"""LLM Provider abstraction layer — supports multiple AI backends.

Each provider implements the same interface, allowing agents to use
different models without changing protocol or room logic.

Usage:
    provider = get_provider("anthropic_sdk")  # or "claude_cli", "openai", "ollama"
    response = await provider.complete(prompt, model="sonnet", ...)
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    """Unified response from any LLM provider."""
    text: str
    model: str = ""
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    stop_reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class LLMProvider(ABC):
    """Abstract base for LLM providers."""

    name: str = "base"

    @abstractmethod
    async def complete(
        self,
        prompt: str,
        *,
        model: str = "",
        max_turns: int = 1,
        tools: str = "",
        timeout: int = 600,
        permission_mode: str = "",
    ) -> LLMResponse:
        ...


class ClaudeCLIProvider(LLMProvider):
    """Provider using Claude CLI subprocess (current default)."""

    name = "claude_cli"

    async def complete(
        self,
        prompt: str,
        *,
        model: str = "",
        max_turns: int = 1,
        tools: str = "",
        timeout: int = 600,
        permission_mode: str = "",
    ) -> LLMResponse:
        cmd = ["claude", "-p", prompt, "--output-format", "json"]
        cmd.extend(["--max-turns", str(max_turns)])
        if model:
            cmd.extend(["--model", model])
        if tools is not None:
            cmd.extend(["--tools", tools])
        if permission_mode:
            cmd.extend(["--permission-mode", permission_mode])

        t0 = time.monotonic()
        proc = await asyncio.to_thread(
            subprocess.run, cmd,
            capture_output=True, text=True, timeout=timeout,
        )
        duration_ms = int((time.monotonic() - t0) * 1000)

        if proc.returncode != 0:
            raise RuntimeError(f"Claude CLI error: {proc.stderr[:500]}")

        data = json.loads(proc.stdout)
        text = data.get("result", "")
        if not isinstance(text, str):
            text = ""
        usage = data.get("usage", {})

        return LLMResponse(
            text=text,
            model=model or "default",
            tokens_input=usage.get("input_tokens", 0),
            tokens_output=usage.get("output_tokens", 0),
            cost_usd=data.get("total_cost_usd", 0.0),
            duration_ms=duration_ms,
            stop_reason=data.get("stop_reason", ""),
            raw=data,
        )


class AnthropicSDKProvider(LLMProvider):
    """Provider using Anthropic Python SDK directly (faster, streaming capable)."""

    name = "anthropic_sdk"

    MODEL_MAP = {
        "sonnet": "claude-sonnet-4-20250514",
        "opus": "claude-opus-4-20250514",
        "haiku": "claude-haiku-4-5-20251001",
    }

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key

    async def complete(
        self,
        prompt: str,
        *,
        model: str = "sonnet",
        max_turns: int = 1,
        tools: str = "",
        timeout: int = 600,
        permission_mode: str = "",
    ) -> LLMResponse:
        try:
            import anthropic
        except ImportError:
            raise RuntimeError(
                "anthropic package not installed. Run: pip install anthropic"
            )

        model_id = self.MODEL_MAP.get(model, model)
        client = anthropic.AsyncAnthropic(api_key=self._api_key)

        t0 = time.monotonic()
        response = await client.messages.create(
            model=model_id,
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        )
        duration_ms = int((time.monotonic() - t0) * 1000)

        text = ""
        for block in response.content:
            if hasattr(block, "text"):
                text += block.text

        return LLMResponse(
            text=text,
            model=model_id,
            tokens_input=response.usage.input_tokens,
            tokens_output=response.usage.output_tokens,
            cost_usd=0.0,  # SDK doesn't return cost directly
            duration_ms=duration_ms,
            stop_reason=response.stop_reason or "",
            raw={"id": response.id, "model": response.model},
        )


class OpenAIProvider(LLMProvider):
    """Provider for OpenAI-compatible APIs (GPT, local servers, etc.)."""

    name = "openai"

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self._api_key = api_key
        self._base_url = base_url

    async def complete(
        self,
        prompt: str,
        *,
        model: str = "gpt-4o",
        max_turns: int = 1,
        tools: str = "",
        timeout: int = 600,
        permission_mode: str = "",
    ) -> LLMResponse:
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise RuntimeError(
                "openai package not installed. Run: pip install openai"
            )

        client = AsyncOpenAI(api_key=self._api_key, base_url=self._base_url)

        t0 = time.monotonic()
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=8192,
        )
        duration_ms = int((time.monotonic() - t0) * 1000)

        text = response.choices[0].message.content or ""
        usage = response.usage

        return LLMResponse(
            text=text,
            model=model,
            tokens_input=usage.prompt_tokens if usage else 0,
            tokens_output=usage.completion_tokens if usage else 0,
            cost_usd=0.0,
            duration_ms=duration_ms,
            stop_reason=response.choices[0].finish_reason or "",
            raw={"id": response.id},
        )


class OllamaProvider(LLMProvider):
    """Provider for local Ollama models."""

    name = "ollama"

    def __init__(self, base_url: str = "http://localhost:11434"):
        self._base_url = base_url

    async def complete(
        self,
        prompt: str,
        *,
        model: str = "llama3",
        max_turns: int = 1,
        tools: str = "",
        timeout: int = 600,
        permission_mode: str = "",
    ) -> LLMResponse:
        import aiohttp

        t0 = time.monotonic()
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self._base_url}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False},
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                data = await resp.json()

        duration_ms = int((time.monotonic() - t0) * 1000)

        return LLMResponse(
            text=data.get("response", ""),
            model=model,
            tokens_input=data.get("prompt_eval_count", 0),
            tokens_output=data.get("eval_count", 0),
            cost_usd=0.0,
            duration_ms=duration_ms,
            stop_reason="stop",
            raw=data,
        )


# ── Provider Registry ────────────────────────────────────────

_PROVIDERS: dict[str, LLMProvider] = {}


def register_provider(provider: LLMProvider) -> None:
    _PROVIDERS[provider.name] = provider


def get_provider(name: str = "claude_cli") -> LLMProvider:
    if name not in _PROVIDERS:
        # Auto-register defaults
        if name == "claude_cli":
            register_provider(ClaudeCLIProvider())
        elif name == "anthropic_sdk":
            register_provider(AnthropicSDKProvider())
        elif name == "openai":
            register_provider(OpenAIProvider())
        elif name == "ollama":
            register_provider(OllamaProvider())
        else:
            raise ValueError(f"Unknown provider: {name}")
    return _PROVIDERS[name]


def list_providers() -> list[str]:
    return ["claude_cli", "anthropic_sdk", "openai", "ollama"]
