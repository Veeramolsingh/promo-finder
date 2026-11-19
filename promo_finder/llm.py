"""The one place that talks to an AI model.

Swapping models is a config change: every call goes through `LLM.json_call`,
and a model is named as "<provider>:<model id>", for example

    groq:openai/gpt-oss-120b
    groq:qwen/qwen3.8-27b
    anthropic:claude-opus-5-5
    gemini:gemini-2.5-flash
    custom:<model>          (any OpenAI-compatible endpoint, e.g. one Kuku provides)

Providers are defined in config.yaml (base URL + which env var holds the key).
Responses are cached on disk, so re-running never pays twice for the same prompt.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path


class LLMError(RuntimeError):
    pass


class DailyLimitError(LLMError):
    """The provider's daily quota is used up: stop the run, keep what we have."""


@dataclass
class LLMResult:
    data: dict | None            # parsed JSON (None if the model never produced valid JSON)
    text: str                    # raw text the model returned
    model: str
    usage: dict = field(default_factory=dict)
    cached: bool = False
    seconds: float = 0.0
    error: str | None = None


def _clean_error(msg: str) -> str:
    """Provider errors can contain account/organisation ids - never copy those into reports."""
    msg = re.sub(r"in organization `[^`]*`", "", msg)
    msg = re.sub(r"\borg_[A-Za-z0-9]+", "org_***", msg)
    return msg[:200]


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


class LLM:
    def __init__(self, spec: str, providers: dict, params: dict | None = None,
                 cache_dir: str | Path = ".cache/llm"):
        if ":" not in spec:
            raise LLMError(f"Model must look like provider:model, got '{spec}'")
        self.provider_name, self.model = spec.split(":", 1)
        if self.provider_name not in providers:
            raise LLMError(f"Unknown provider '{self.provider_name}'. Known: {list(providers)}")
        self.spec = spec
        self.p = providers[self.provider_name]
        self.params = params or {}
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        key_env = self.p.get("api_key_env")
        self.api_key = os.environ.get(key_env, "") if key_env else ""
        if key_env and not self.api_key:
            raise LLMError(f"Set {key_env} in .env to use provider '{self.provider_name}'")
        self._client = None

    # ---- provider plumbing -------------------------------------------------
    def _openai_client(self):
        from openai import OpenAI
        if self._client is None:
            base_url = self.p.get("base_url") or os.environ.get(self.p.get("base_url_env", ""), "")
            if not base_url:
                raise LLMError(f"No base URL for provider '{self.provider_name}' "
                               f"(set {self.p.get('base_url_env')} in .env)")
            self._client = OpenAI(api_key=self.api_key or "none", base_url=base_url,
                                  max_retries=8, timeout=180)
        return self._client

    def _anthropic_client(self):
        import anthropic
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=self.api_key, max_retries=8)
        return self._client

    def _call_openai_compatible(self, system: str, user: str, max_tokens: int) -> tuple[str, dict]:
        client = self._openai_client()
        kwargs = dict(model=self.model, max_tokens=max_tokens,
                      messages=[{"role": "system", "content": system},
                                {"role": "user", "content": user}])
        extra = dict(self.params)
        if "temperature" in extra:
            kwargs["temperature"] = extra.pop("temperature")
        use_json_mode = extra.pop("json_mode", True)
        if use_json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if extra:
            kwargs["extra_body"] = extra
        from openai import RateLimitError, BadRequestError
        try:
            r = self._create_with_quota_wait(client, kwargs)
        except BadRequestError as e:
            # Some models reject JSON mode or produce output that fails Groq's JSON check.
            # Retry once in plain mode; we parse the JSON ourselves anyway.
            if use_json_mode:
                kwargs.pop("response_format", None)
                r = client.chat.completions.create(**kwargs)
            else:
                raise
        text = r.choices[0].message.content or ""
        u = r.usage
        usage = {"input_tokens": getattr(u, "prompt_tokens", None),
                 "output_tokens": getattr(u, "completion_tokens", None)} if u else {}
        return text, usage

    def _create_with_quota_wait(self, client, kwargs):
        """Free tiers have a rolling daily token allowance that refills over time.
        When the provider says "try again in 7m12s", wait that long (up to a cap) and
        continue, instead of failing the whole run."""
        from openai import RateLimitError
        max_wait = float(os.environ.get("MAX_QUOTA_WAIT_SECONDS", "1200"))
        for _ in range(12):
            try:
                return client.chat.completions.create(**kwargs)
            except RateLimitError as e:
                msg = str(e)
                daily = any(k in msg.lower() for k in ("per day", "tpd", "rpd"))
                m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", msg)
                wait = 0.0
                if m and any(m.groups()):
                    h, mi, s = m.groups()
                    wait = int(h or 0) * 3600 + int(mi or 0) * 60 + float(s or 0)
                if not daily:
                    raise
                if wait <= 0 or wait > max_wait:
                    raise DailyLimitError(_clean_error(msg)) from e
                print(f"    [{self.spec}] daily token allowance used up; waiting {wait:.0f}s "
                      f"for it to refill...", flush=True)
                time.sleep(wait + 2)
        raise DailyLimitError("gave up waiting for the daily allowance")

    def _call_anthropic(self, system: str, user: str, max_tokens: int) -> tuple[str, dict]:
        client = self._anthropic_client()
        extra = dict(self.params)
        extra.pop("json_mode", None)
        extra.pop("temperature", None)  # not accepted by current Claude models
        # Server-side refusal fallback (beta): if the model declines, the API re-runs
        # the request on a fallback model inside the same call.
        r = client.beta.messages.create(
            model=self.model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **extra)
        if r.stop_reason == "refusal":
            raise LLMError(f"Claude declined the request: {getattr(r, 'stop_details', None)}")
        text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
        usage = {"input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens}
        return text, usage

    # ---- public API ----------------------------------------------------------
    def json_call(self, system: str, user: str, max_tokens: int = 4000,
                  tag: str = "") -> LLMResult:
        key = hashlib.sha256(json.dumps(
            [self.spec, self.params, system, user, max_tokens], sort_keys=True).encode()).hexdigest()[:24]
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.exists():
            c = json.loads(cache_file.read_text(encoding="utf-8"))
            return LLMResult(data=_extract_json(c["text"]), text=c["text"], model=self.spec,
                             usage=c.get("usage", {}), cached=True, seconds=0.0)

        t0 = time.time()
        if self.p.get("type") == "anthropic":
            text, usage = self._call_anthropic(system, user, max_tokens)
        else:
            text, usage = self._call_openai_compatible(system, user, max_tokens)
        secs = round(time.time() - t0, 2)
        data = _extract_json(text)
        if data is not None:  # only cache good answers, so a bad one gets retried next run
            cache_file.write_text(json.dumps({"text": text, "usage": usage, "tag": tag}),
                                  encoding="utf-8")
        return LLMResult(data=data, text=text, model=self.spec, usage=usage, seconds=secs,
                         error=None if data is not None else "model did not return valid JSON")
