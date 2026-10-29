"""One place that talks to Gemini, so a retired model doesn't break the app.

Google retires model names (e.g. gemini-2.5-flash-lite for new users). When it answers 404 and
names the replacement, we switch to that model, remember it for this process, and retry once.
"""
from __future__ import annotations

import re

import requests

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_replacements: dict[str, str] = {}


def post(model: str, key: str, json: dict, timeout: int = 60) -> requests.Response:
    model = _replacements.get(model, model)
    r = requests.post(URL.format(model=model), headers={"x-goog-api-key": key.strip()}, json=json, timeout=timeout)
    if r.status_code == 404:
        m = re.search(r"use (?:models/)?(gemini-[\w.\-]+)", r.text)
        if m and m.group(1) != model:
            new = m.group(1).rstrip(".")
            _replacements[model] = new
            body = dict(json)
            if "thinkingConfig" in body.get("generationConfig", {}) and "2.5" not in new:
                body["generationConfig"] = {k: v for k, v in body["generationConfig"].items() if k != "thinkingConfig"}
            r = requests.post(URL.format(model=new), headers={"x-goog-api-key": key.strip()}, json=body, timeout=timeout)
    return r
