"""Explicitly configured OpenAI-compatible transport, with no embedded credentials."""

import asyncio
from dataclasses import dataclass, field
import os
import re
import httpx


@dataclass
class LLMConfig:
    base_url: str
    model: str
    api_key: str = field(default="", repr=False)
    timeout: float = 600
    max_tokens: int = 12000
    retries: int = 1
    no_think: bool = False
    ollama_cpu: bool = False

    @classmethod
    def from_env(cls, base_url=None, model=None, no_think=False, ollama_cpu=False):
        base_url = base_url or os.environ.get("SKILL_RULE_LLM_BASE_URL")
        model = model or os.environ.get("SKILL_RULE_LLM_MODEL")
        if not base_url or not model:
            raise ValueError(
                "Configure SKILL_RULE_LLM_BASE_URL and SKILL_RULE_LLM_MODEL, or pass --base-url and --llm-model"
            )
        return cls(
            base_url=base_url,
            model=model,
            api_key=os.environ.get("SKILL_RULE_LLM_API_KEY", ""),
            no_think=no_think,
            ollama_cpu=ollama_cpu,
        )


class LLMClient:
    def __init__(self, config: LLMConfig):
        self.config = config
        self.calls = 0

    async def __call__(self, system, user):
        config = self.config
        if config.no_think:
            user += "\n\n/no_think"
        url = config.base_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        headers = (
            {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
        )
        payload = dict(
            model=config.model,
            messages=[
                dict(role="system", content=system),
                dict(role="user", content=user),
            ],
            temperature=0.0,
            max_tokens=config.max_tokens,
            response_format={"type": "json_object"},
        )
        if config.ollama_cpu:
            # The native Ollama endpoint exposes CPU allocation explicitly.
            from urllib.parse import urlsplit, urlunsplit

            parsed = urlsplit(config.base_url)
            url = urlunsplit((parsed.scheme, parsed.netloc, "/api/chat", "", ""))
            payload = dict(
                model=config.model,
                messages=payload["messages"],
                stream=False,
                format="json",
                options=dict(
                    num_gpu=0,
                    num_thread=8,
                    num_ctx=32768,
                    temperature=0.0,
                    num_predict=config.max_tokens,
                ),
            )
            if config.no_think:
                payload["think"] = False
        # CPU prompt processing can be slow with the full schema prompt.
        timeout = max(config.timeout, 1800) if config.ollama_cpu else config.timeout
        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(config.retries + 1):
                try:
                    self.calls += 1
                    response = await client.post(url, headers=headers, json=payload)
                except httpx.TransportError:
                    if attempt == config.retries:
                        raise RuntimeError(
                            "LLM transport failed; check endpoint and timeout"
                        ) from None
                    await asyncio.sleep(2)
                    continue
                if response.status_code >= 500 and attempt < config.retries:
                    await asyncio.sleep(2)
                    continue
                if response.status_code != 200:
                    raise RuntimeError(f"LLM returned HTTP {response.status_code}")
                data = response.json()
                content = (
                    data.get("message", {}).get("content")
                    if config.ollama_cpu
                    else data.get("choices", [{}])[0].get("message", {}).get("content")
                )
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("LLM response has no textual graph content")
                return re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
