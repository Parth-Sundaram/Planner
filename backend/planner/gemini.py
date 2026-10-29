"""One place that talks to Gemini, so a retired model or a slow reply doesn't break the app.

- Google retires model names (e.g. gemini-2.5-flash-lite for new users). When it answers 404 and
  names the replacement, we switch to that model, remember it for this process, and retry once.
- fast=True turns the model's thinking down as far as that model allows (2.5: budget 0;
  3.x: the lowest thinking level it accepts), so quick tasks like the assistant reply in seconds.
- A timeout or a 5xx is retried; after that the caller gets a plain RuntimeError, not a traceback.
"""
from __future__ import annotations

import re

import requests

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_replacements: dict[str, str] = {}
_thinking_ok: dict[str, dict | None] = {}  # model -> the fast thinking setting it accepted


def _fast_options(model: str) -> list[dict | None]:
    if model in _thinking_ok:
        return [_thinking_ok[model]]
    if "2.5" in model:
        return [{"thinkingBudget": 0}, None]
    if re.search(r"gemini-[3-9]", model):
        return [{"thinkingLevel": "minimal"}, {"thinkingLevel": "low"}, None]
    return [None]


def _with_thinking(body: dict, thinking: dict | None) -> dict:
    gen = {k: v for k, v in (body.get("generationConfig") or {}).items() if k != "thinkingConfig"}
    if thinking:
        gen["thinkingConfig"] = thinking
    return {**body, "generationConfig": gen}


def _send(model: str, key: str, body: dict, timeout: int, retries: int) -> requests.Response:
    last = None
    for _ in range(retries + 1):
        try:
            r = requests.post(URL.format(model=model), headers={"x-goog-api-key": key.strip()}, json=body, timeout=timeout)
        except (requests.Timeout, requests.ConnectionError) as e:
            last = e
            continue
        if r.status_code in (500, 502, 503, 504):
            last = r
            continue
        return r
    if isinstance(last, requests.Response):
        return last
    raise RuntimeError("Gemini took too long to answer. Try again, or split the request into smaller pieces.")


def post(model: str, key: str, json: dict, timeout: int = 60, fast: bool = False, retries: int = 1) -> requests.Response:
    model = _replacements.get(model, model)
    if not fast:
        r = _send(model, key, json, timeout, retries)
    else:
        for thinking in _fast_options(model):
            r = _send(model, key, _with_thinking(json, thinking), timeout, retries)
            if r.status_code == 400 and "thinking" in r.text.lower():
                continue  # this model doesn't take that setting; try the next one
            if r.status_code == 200:
                _thinking_ok[model] = thinking
            break
    if r.status_code == 404:
        m = re.search(r"use (?:models/)?(gemini-[\w.\-]+)", r.text)
        if m and m.group(1).rstrip(".") != model:
            new = m.group(1).rstrip(".")
            _replacements[model] = new
            if fast:
                return post(new, key, json, timeout, fast=True, retries=retries)
            body = dict(json)
            if "thinkingConfig" in body.get("generationConfig", {}) and "2.5" not in new:
                body["generationConfig"] = {k: v for k, v in body["generationConfig"].items() if k != "thinkingConfig"}
            r = _send(new, key, body, timeout, retries)
    return r