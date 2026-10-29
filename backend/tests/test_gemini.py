from planner import gemini


class Resp:
    def __init__(self, code, text=""):
        self.status_code, self.text = code, text


def test_retired_model_switches_and_remembers(monkeypatch):
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append((url, json))
        if "2.5-flash-lite" in url:
            return Resp(404, '{"error": {"code": 404, "message": "This model models/gemini-2.5-flash-lite is no longer '
                             'available to new users. Please update your code to use models/gemini-3.5-flash-lite for the latest"}}')
        return Resp(200, "{}")
    monkeypatch.setattr(gemini.requests, "post", fake_post)
    gemini._replacements.clear()
    body = {"contents": [], "generationConfig": {"temperature": 0, "thinkingConfig": {"thinkingBudget": 0}}}
    r = gemini.post("gemini-2.5-flash-lite", " key ", body)
    assert r.status_code == 200 and "gemini-3.5-flash-lite" in calls[-1][0]
    assert "thinkingConfig" not in calls[-1][1]["generationConfig"]
    gemini.post("gemini-2.5-flash-lite", "key", body)
    assert "gemini-3.5-flash-lite" in calls[-1][0] and len(calls) == 3  # no second 404 round trip


def test_fast_mode_finds_lowest_thinking_setting_and_remembers(monkeypatch):
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(json["generationConfig"].get("thinkingConfig"))
        if json["generationConfig"].get("thinkingConfig") == {"thinkingLevel": "minimal"}:
            return Resp(400, "thinking_level minimal is not supported for this model")
        return Resp(200, "{}")
    monkeypatch.setattr(gemini.requests, "post", fake_post)
    gemini._replacements.clear(); gemini._thinking_ok.clear()
    gemini.post("gemini-3.5-flash-lite", "k", {"contents": [], "generationConfig": {}}, fast=True)
    assert calls == [{"thinkingLevel": "minimal"}, {"thinkingLevel": "low"}]
    gemini.post("gemini-3.5-flash-lite", "k", {"contents": [], "generationConfig": {}}, fast=True)
    assert calls[-1] == {"thinkingLevel": "low"} and len(calls) == 3


def test_timeout_retries_then_plain_error(monkeypatch):
    import pytest
    n = []

    def slow(url, headers, json, timeout):
        n.append(1)
        if len(n) == 1:
            raise gemini.requests.ReadTimeout("read timed out")
        return Resp(200, "{}")
    monkeypatch.setattr(gemini.requests, "post", slow)
    gemini._replacements.clear()
    assert gemini.post("gemini-x", "k", {"contents": []}).status_code == 200 and len(n) == 2

    def always_slow(url, headers, json, timeout):
        raise gemini.requests.ReadTimeout("read timed out")
    monkeypatch.setattr(gemini.requests, "post", always_slow)
    with pytest.raises(RuntimeError, match="too long"):
        gemini.post("gemini-x", "k", {"contents": []})