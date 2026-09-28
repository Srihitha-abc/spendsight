"""Groq chat client with primary/fallback models. Returns raw text; parsing/validation lives in verdict.py."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class GroqLLM:
    def __init__(self, api_key: str, model: str, fallback_model: str):
        from groq import Groq

        self.client = Groq(api_key=api_key, timeout=60.0, max_retries=1)
        self.models = [model, fallback_model]
        self.name = f"groq:{model}"

    def complete(self, system: str, user: str, json_mode: bool = True) -> tuple[str, str]:
        """Returns (text, model_used). Tries the primary model, then the fallback. Raises if both fail."""
        last: Exception | None = None
        for model in self.models:
            kwargs = dict(model=model, temperature=0.2, max_completion_tokens=1500,
                          messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            try:
                resp = self.client.chat.completions.create(**kwargs)
                return resp.choices[0].message.content or "", model
            except Exception as e:  # rate limit, bad request, json_validate_failed, ...
                log.warning("groq %s failed: %s", model, e)
                last = e
        raise RuntimeError(f"all Groq models failed: {last}")


def build_llm(api_key: str, model: str, fallback_model: str) -> GroqLLM | None:
    if not api_key:
        log.warning("GROQ_API_KEY not set: verdicts use the offline heuristic")
        return None
    return GroqLLM(api_key, model, fallback_model)
