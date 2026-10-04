"""HTTP-only adapters; LM Studio and free routers share the OpenAI protocol."""

from __future__ import annotations

import os
from typing import Any, Literal, Protocol

import httpx
from pydantic import Field, field_validator
from schema import StrictModel


class ProviderConfig(StrictModel):
    name: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    provider: Literal["gemini", "openai"]
    model: str = Field(min_length=1)
    base_url: str
    api_key_env: str | None = None
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_output_tokens: int = Field(default=4096, ge=1)
    timeout_seconds: float = Field(default=120.0, gt=0)
    pacing_seconds: float = Field(default=0.0, ge=0)
    structured_output: bool = True
    extra_body: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        url = httpx.URL(value)
        if url.scheme not in ("http", "https") or not url.host:
            raise ValueError("base_url must be an HTTP(S) URL")
        if url.username or url.password or url.query or url.fragment:
            raise ValueError("base_url must not contain credentials, query parameters or fragments")
        return value.rstrip("/")

    @field_validator("extra_body")
    @classmethod
    def protect_shared_settings(cls, value: dict[str, Any]) -> dict[str, Any]:
        reserved = {
            "model",
            "models",
            "route",
            "messages",
            "contents",
            "systemInstruction",
            "generationConfig",
            "temperature",
            "max_tokens",
            "max_completion_tokens",
            "stream",
            "response_format",
            "tools",
            "tool_choice",
            "functions",
            "function_call",
        }
        if reserved & value.keys():
            raise ValueError("extra_body cannot override shared settings or enable tools")
        return value


class Generation(StrictModel):
    text: str
    input_tokens: int | None = Field(ge=0)
    output_tokens: int | None = Field(ge=0)
    total_tokens: int | None = Field(ge=0)
    reasoning_tokens: int | None = Field(ge=0)
    finish_reason: str | None
    http_status: int
    resolved_model: str | None = None


class LLMProvider(Protocol):
    async def generate(
        self, messages: list[dict[str, str]], response_schema: dict
    ) -> Generation: ...


def api_key(config: ProviderConfig) -> str | None:
    if config.api_key_env is None:
        if config.provider == "gemini":
            raise ValueError("Gemini requires api_key_env")
        return None
    value = os.environ.get(config.api_key_env)
    if not value:
        raise ValueError(f"Missing API key environment variable: {config.api_key_env}")
    return value


class OpenAICompatibleProvider:
    def __init__(self, client: httpx.AsyncClient, config: ProviderConfig):
        self.client, self.config = client, config
        key = api_key(config)
        self.headers = {"Authorization": f"Bearer {key}"} if key else {}

    async def generate(self, messages: list[dict[str, str]], response_schema: dict) -> Generation:
        body: dict[str, Any] = {
            **self.config.extra_body,
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_output_tokens,
            "stream": False,
        }
        if self.config.structured_output:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "travel_constraints",
                    "strict": True,
                    "schema": response_schema,
                },
            }
        response = await self.client.post(
            f"{self.config.base_url}/chat/completions",
            headers=self.headers,
            json=body,
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        choice = payload["choices"][0]
        text = choice["message"].get("content")
        if text is not None and not isinstance(text, str):
            raise ValueError("Provider content must be text or null")
        usage = payload.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        return Generation(
            text=text or "",
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            reasoning_tokens=details.get("reasoning_tokens"),
            finish_reason=choice.get("finish_reason"),
            http_status=response.status_code,
            resolved_model=payload.get("model"),
        )


class GeminiProvider:
    def __init__(self, client: httpx.AsyncClient, config: ProviderConfig):
        self.client, self.config = client, config
        key = api_key(config)
        if key is None:
            raise ValueError("Gemini requires an API key")
        self.headers = {"x-goog-api-key": key}

    async def generate(self, messages: list[dict[str, str]], response_schema: dict) -> Generation:
        settings: dict[str, Any] = {
            "temperature": self.config.temperature,
            "maxOutputTokens": self.config.max_output_tokens,
        }
        if self.config.structured_output:
            settings.update(responseMimeType="application/json", responseJsonSchema=response_schema)
        body = {
            **self.config.extra_body,
            "systemInstruction": {"parts": [{"text": messages[0]["content"]}]},
            "contents": [{"role": "user", "parts": [{"text": messages[1]["content"]}]}],
            "generationConfig": settings,
        }
        # Model IDs are supplied without the 'models/' prefix.
        model = self.config.model.removeprefix("models/")
        response = await self.client.post(
            f"{self.config.base_url}/models/{model}:generateContent",
            headers=self.headers,
            json=body,
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        block_reason = (payload.get("promptFeedback") or {}).get("blockReason")
        candidate = payload["candidates"][0] if payload.get("candidates") else {}
        if not candidate and not block_reason:
            raise ValueError("Provider returned no candidates or block reason")
        parts = candidate.get("content", {}).get("parts", [])
        text = "".join(part["text"] for part in parts if "text" in part and not part.get("thought"))
        usage = payload.get("usageMetadata") or {}
        return Generation(
            text=text,
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
            total_tokens=usage.get("totalTokenCount"),
            reasoning_tokens=usage.get("thoughtsTokenCount"),
            finish_reason=candidate.get("finishReason") or block_reason,
            http_status=response.status_code,
            resolved_model=payload.get("modelVersion"),
        )
