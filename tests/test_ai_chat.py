from __future__ import annotations

import importlib
import json

import httpx
from fastapi.testclient import TestClient


class FakeStreamResponse:
    status_code = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def aiter_lines(self):
        yield 'data: {"choices":[{"delta":{"role":"assistant"}}]}'
        yield 'data: {"choices":[{"delta":{"content":"结合自选基金"}}]}'
        yield 'data: {"choices":[{"delta":{"content":"可以继续观察。"}}]}'
        yield "data: [DONE]"


class FakeAiClient:
    stream_payloads: list[dict] = []

    def __init__(self, *_, **__):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def stream(self, method: str, url: str, json: dict):
        assert method == "POST"
        assert url.endswith("/chat/completions")
        self.stream_payloads.append(json)
        return FakeStreamResponse()

    async def post(self, url: str, **kwargs):
        if url.endswith("/audio/transcriptions"):
            assert kwargs["data"]["model"] == "whisper-test"
            assert kwargs["files"]["file"][2] == "audio/webm"
            return httpx.Response(200, json={"text": "请分析我的自选基金"}, request=httpx.Request("POST", url))
        return httpx.Response(200, json={"choices": [{"message": {"content": "短评"}}]}, request=httpx.Request("POST", url))


def make_client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("FUND_ESTIMATOR_FORCE_MOCK", "1")
    monkeypatch.setenv("FUND_ESTIMATOR_DB", str(tmp_path / "api.sqlite3"))
    monkeypatch.setenv("FUND_ESTIMATOR_COMPARE_AI_PASSWORD", "compare-admin")
    monkeypatch.setenv("FUND_ESTIMATOR_AI_CHAT_PASSWORD", "chat-password")
    monkeypatch.setenv("FUND_ESTIMATOR_AI_TRANSCRIPTION_MODEL", "whisper-test")
    (tmp_path / "compare_ai_config.json").write_text(
        json.dumps(
            {
                "base_url": "https://api.example.com/v1",
                "api_key": "test-ai-key-123456",
                "selected_model": "gpt-test",
            }
        ),
        encoding="utf-8",
    )
    module = importlib.import_module("fund_estimator.api.app")
    from fund_estimator.services import compare_ai as compare_ai_module

    FakeAiClient.stream_payloads = []
    monkeypatch.setattr(compare_ai_module.httpx, "AsyncClient", FakeAiClient)
    return TestClient(module.create_app())


def test_ai_chat_requires_its_own_password(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    status = client.get("/api/ai-chat/status")
    denied = client.post("/api/ai-chat/stream", json={"message": "你好"})
    wrong = client.post("/api/ai-chat/login", json={"password": "wrong"})
    login = client.post("/api/ai-chat/login", json={"password": "chat-password"})

    assert status.status_code == 200
    assert status.json()["enabled"] is True
    assert status.json()["authenticated"] is False
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "AI_CHAT_UNAUTHORIZED"
    assert wrong.status_code == 401
    assert login.status_code == 200
    assert login.json()["authenticated"] is True
    assert login.json()["model_configured"] is True


def test_ai_chat_stream_uses_device_watchlist_and_keeps_dates(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    headers = {"X-Device-Id": "phone-a"}
    client.post("/api/watchlist/001438", headers=headers)
    client.post("/api/ai-chat/login", json={"password": "chat-password"})

    response = client.post(
        "/api/ai-chat/stream",
        headers=headers,
        json={"message": "001438 今天的预估和官方净值有什么区别？", "history": []},
    )

    assert response.status_code == 200
    assert "event: delta" in response.text
    assert '"text": "结合自选基金"' in response.text
    assert '"text": "可以继续观察。"' in response.text
    assert "event: done" in response.text
    messages = FakeAiClient.stream_payloads[-1]["messages"]
    context = messages[0]["content"]
    assert "001438" in context
    assert "official_nav_date" in context
    assert "estimated_nav_date" in context
    assert "top10_holdings" in context


def test_ai_chat_does_not_read_another_device_watchlist(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    client.post("/api/watchlist/001438", headers={"X-Device-Id": "phone-a"})
    client.post("/api/ai-chat/login", json={"password": "chat-password"})

    response = client.post(
        "/api/ai-chat/stream",
        headers={"X-Device-Id": "phone-b"},
        json={"message": "我的自选有哪些？"},
    )

    assert response.status_code == 200
    context = FakeAiClient.stream_payloads[-1]["messages"][0]["content"]
    assert '"watchlist_count": 0' in context
    assert "001438" not in context


def test_ai_chat_transcription_returns_text_and_rejects_invalid_audio(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    client.post("/api/ai-chat/login", json={"password": "chat-password"})

    response = client.post(
        "/api/ai-chat/transcription",
        files={"file": ("voice.webm", b"test-audio", "audio/webm")},
    )
    invalid = client.post(
        "/api/ai-chat/transcription",
        files={"file": ("voice.txt", b"test", "text/plain")},
    )

    assert response.status_code == 200
    assert response.json()["text"] == "请分析我的自选基金"
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "AI_CHAT_AUDIO_TYPE_INVALID"
