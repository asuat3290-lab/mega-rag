#!/usr/bin/env python3
"""Small OpenAI-compatible gateway with strict JSON parsing and token accounting."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any


class ModelUnavailable(RuntimeError):
    """Raised when an optional model cannot be called."""


class ModelResponseError(RuntimeError):
    """Raised when a model response cannot be parsed safely."""


@dataclass
class ModelResult:
    text: str
    model: str
    usage: dict[str, int]


def resolve_api_key(model_config: dict) -> str:
    """Read a key from the configured environment variable without logging it."""
    env_name = str(model_config.get("api_key_env") or "")
    key = os.environ.get(env_name, "") if env_name else ""
    if key:
        return key
    if sys.platform == "win32" and env_name:
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as handle:
                value, _ = winreg.QueryValueEx(handle, env_name)
                return str(value or "")
        except (FileNotFoundError, OSError):
            pass
    return ""


def empty_usage() -> dict[str, int]:
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def merge_usage(*items: dict | None) -> dict[str, int]:
    output = empty_usage()
    for item in items:
        for key in output:
            output[key] += int((item or {}).get(key, 0) or 0)
    return output


def _usage_dict(usage: Any) -> dict[str, int]:
    if usage is None:
        return empty_usage()
    return {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }


def call_text(
    model_config: dict,
    prompt: str,
    *,
    system: str = "",
    max_tokens: int = 1600,
    temperature: float = 0.2,
) -> ModelResult:
    """Call an OpenAI-compatible chat endpoint and retain exact reported usage."""
    api_key = resolve_api_key(model_config)
    if not api_key:
        raise ModelUnavailable(
            f"{model_config.get('api_key_env', 'API key')} is not configured"
        )
    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=api_key,
            base_url=str(model_config.get("base_url") or ""),
        )
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = client.chat.completions.create(
            model=str(model_config["model"]),
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        text = str(response.choices[0].message.content or "").strip()
        if not text:
            raise ModelResponseError("model returned an empty response")
        return ModelResult(
            text=text,
            model=str(model_config["model"]),
            usage=_usage_dict(getattr(response, "usage", None)),
        )
    except (ModelUnavailable, ModelResponseError):
        raise
    except Exception as exc:
        raise ModelUnavailable(f"model call failed: {type(exc).__name__}: {exc}") from exc


def parse_json_response(text: str) -> Any:
    """Decode the first complete JSON value, tolerating Markdown fences."""
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    decoder = json.JSONDecoder()
    starts = [index for index, char in enumerate(cleaned) if char in "{["]
    for index in starts:
        try:
            value, _ = decoder.raw_decode(cleaned[index:])
            return value
        except json.JSONDecodeError:
            continue
    raise ModelResponseError("response does not contain valid JSON")


def call_json(
    model_config: dict,
    prompt: str,
    *,
    system: str = "",
    max_tokens: int = 1800,
    temperature: float = 0.1,
    repair: bool = True,
) -> tuple[Any, dict[str, int], str]:
    """Call a model for JSON, with at most one bounded repair attempt."""
    first = call_text(
        model_config,
        prompt,
        system=system,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    try:
        return parse_json_response(first.text), first.usage, first.model
    except ModelResponseError:
        if not repair:
            raise
    repair_prompt = (
        "Convert the following invalid response into valid JSON only. Preserve its factual "
        "content, do not add evidence, comments, or Markdown.\n\n"
        + first.text[:6000]
    )
    second = call_text(
        model_config,
        repair_prompt,
        max_tokens=max_tokens,
        temperature=0.0,
    )
    return (
        parse_json_response(second.text),
        merge_usage(first.usage, second.usage),
        second.model,
    )
