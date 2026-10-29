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
