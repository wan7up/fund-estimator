from __future__ import annotations

import importlib

from fastapi.testclient import TestClient

from fund_estimator.services.cache import SQLiteCache


def make_client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("FUND_ESTIMATOR_FORCE_MOCK", "1")
    monkeypatch.setenv("FUND_ESTIMATOR_DB", str(tmp_path / "api.sqlite3"))
    module = importlib.import_module("fund_estimator.api.app")
    return TestClient(module.create_app())


def test_health(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"

    source_response = client.get("/api/source/status")
    assert source_response.status_code == 200
    assert source_response.json()["mode"] == "mock"


def test_shell_and_tool_page_routes(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    root_response = client.get("/")
    estimate_response = client.get("/estimate")
    arbitrage_response = client.get("/arbitrage")
    monitor_response = client.get("/monitor")
    compare_response = client.get("/compare")
    estimate_tool_response = client.get("/tool/estimate")
    arbitrage_tool_response = client.get("/tool/arbitrage")
    compare_tool_response = client.get("/tool/compare")

    for response in [root_response, estimate_response, arbitrage_response, monitor_response, compare_response]:
        assert response.status_code == 200
        assert "基金工具箱" in response.text
        assert 'data-src="/tool/estimate"' in response.text
        assert 'data-src="/tool/arbitrage"' in response.text
        assert 'data-src="/tool/compare"' in response.text

    assert estimate_tool_response.status_code == 200
    assert "/static/app.js" in estimate_tool_response.text
    assert arbitrage_tool_response.status_code == 200
    assert "/static/lof_app.js" in arbitrage_tool_response.text
    assert compare_tool_response.status_code == 200
    assert "/static/compare_app.js" in compare_tool_response.text


def test_estimate_and_watchlist(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    add_response = client.post("/api/watchlist/001438")
    assert add_response.status_code == 200
    assert add_response.json()["code"] == "001438"

    watch_response = client.get("/api/watchlist")
    assert watch_response.status_code == 200
    assert len(watch_response.json()) == 1

    estimate_response = client.get("/api/estimate?code=001438&mode=both")
    body = estimate_response.json()
    assert estimate_response.status_code == 200
    assert body["fund_code"] == "001438"
    assert body["raw"] is not None
    assert body["normalized"] is not None
    assert body["confidence"] in {"high", "medium", "low"}
    assert body["actual_change_pct"] == 3.03
    assert body["fund_details"]["stage_returns"]["one_month_pct"] == 18.6
    assert body["fund_details"]["asset_allocation"]["stock_pct"] == 86.2
    assert body["fund_details"]["managers"][0]["name"] == "示例经理"


def test_watchlist_reorder(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    cache = SQLiteCache(tmp_path / "api.sqlite3")
    cache.add_watchlist("000001", "测试一号")
    cache.add_watchlist("001438", "测试二号")

    response = client.put("/api/watchlist/order", json={"codes": ["001438", "000001"]})
    assert response.status_code == 200
    assert [item["code"] for item in response.json()] == ["001438", "000001"]

    watch_response = client.get("/api/watchlist")
    assert [item["code"] for item in watch_response.json()] == ["001438", "000001"]


def test_watchlist_is_scoped_by_device(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    response = client.post("/api/watchlist/001438", headers={"X-Device-Id": "phone-a"})
    assert response.status_code == 200

    phone_a = client.get("/api/watchlist", headers={"X-Device-Id": "phone-a"})
    phone_b = client.get("/api/watchlist", headers={"X-Device-Id": "phone-b"})

    assert [item["code"] for item in phone_a.json()] == ["001438"]
    assert phone_b.json() == []


def test_new_device_copies_legacy_default_watchlist_once(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    cache = SQLiteCache(tmp_path / "api.sqlite3")
    cache.add_watchlist("001438", "旧自选")

    first_open = client.get("/api/watchlist", headers={"X-Device-Id": "phone-a"})
    assert [item["code"] for item in first_open.json()] == ["001438"]

    delete_response = client.delete("/api/watchlist/001438", headers={"X-Device-Id": "phone-a"})
    assert delete_response.status_code == 200

    phone_a = client.get("/api/watchlist", headers={"X-Device-Id": "phone-a"})
    default_list = client.get("/api/watchlist")
    phone_b = client.get("/api/watchlist", headers={"X-Device-Id": "phone-b"})

    assert phone_a.json() == []
    assert [item["code"] for item in default_list.json()] == ["001438"]
    assert [item["code"] for item in phone_b.json()] == ["001438"]


def test_batch_estimate_returns_item_errors(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    response = client.post("/api/estimate/batch", json={"codes": ["001438", "999999"], "mode": "both"})
    body = response.json()

    assert response.status_code == 200
    assert body[0]["ok"] is True
    assert body[1]["ok"] is False
    assert body[1]["error"]["code"] == "FUND_NOT_FOUND"


def test_compare_api_returns_mock_comparison(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    response = client.post(
        "/api/compare",
        json={"codes": ["001438", "001439"], "strategy": "balanced", "theme_hint": "混合"},
    )
    body = response.json()

    assert response.status_code == 200
    assert body["conclusion"] == "very_similar"
    assert body["recommendation_code"] in {"001438", "001439"}
    assert body["theme_analysis"]["theme_hint"] == "混合"
    assert body["theme_analysis"]["exposures"]
    assert "板块匹配" in body["recommendation"]
    assert len(body["funds"]) == 2
    assert body["pair_similarities"][0]["holdings_similarity"] > 90
    assert "estimated_move" not in body["funds"][0]["score_breakdown"]
    assert "fee" not in body["funds"][0]["score_breakdown"]
    assert any(item["key"] == "manager" for item in body["score_factors"])
    assert all(item["key"] != "fee" for item in body["score_factors"])
    assert body["funds"][0]["snapshot"]["current_rate_pct"] is not None
    assert "purchase_limit_yuan" in body["funds"][0]["snapshot"]


def test_compare_api_validates_codes(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    duplicate_response = client.post("/api/compare", json={"codes": ["001438", "001438"]})
    missing_response = client.post("/api/compare", json={"codes": ["001438", "999999"]})

    assert duplicate_response.status_code == 422
    assert missing_response.status_code == 404
    assert missing_response.json()["error"]["code"] == "FUND_NOT_FOUND"


def test_compare_ai_disabled_without_password(tmp_path, monkeypatch):
    monkeypatch.delenv("FUND_ESTIMATOR_COMPARE_AI_PASSWORD", raising=False)
    client = make_client(tmp_path, monkeypatch)

    status_response = client.get("/api/compare/ai/status")
    login_response = client.post("/api/compare/ai/login", json={"password": "pw"})

    assert status_response.status_code == 200
    assert status_response.json()["enabled"] is False
    assert login_response.status_code == 403
    assert login_response.json()["error"]["code"] == "COMPARE_AI_DISABLED"


def test_compare_ai_login_config_models_and_commentary(tmp_path, monkeypatch):
    from fund_estimator.services import compare_ai as compare_ai_module

    class FakeAiClient:
        def __init__(self, *_, **__):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url: str):
            import httpx

            return httpx.Response(
                200,
                json={"data": [{"id": "gpt-test"}, {"id": "gpt-test-mini"}]},
                request=httpx.Request("GET", url),
            )

        async def post(self, url: str, json: dict):
            import httpx

            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "结论：候选基金短评。"}}]},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setenv("FUND_ESTIMATOR_COMPARE_AI_PASSWORD", "secret")
    monkeypatch.setattr(compare_ai_module.httpx, "AsyncClient", FakeAiClient)
    client = make_client(tmp_path, monkeypatch)

    wrong_login = client.post("/api/compare/ai/login", json={"password": "bad"})
    login = client.post("/api/compare/ai/login", json={"password": "secret"})
    invalid_config = client.put(
        "/api/compare/ai/config",
        json={"base_url": "http://127.0.0.1:8000/v1", "api_key": "test-api-key-unit"},
    )
    config = client.put(
        "/api/compare/ai/config",
        json={"base_url": "http://api.example.com/v1", "api_key": "test-api-key-123456"},
    )
    models = client.get("/api/compare/ai/models")
    configured = client.put(
        "/api/compare/ai/config",
        json={"selected_model": "gpt-test-mini", "persona_id": "dalio_balance"},
    )
    compare = client.post("/api/compare", json={"codes": ["001438", "001439"], "theme_hint": "混合"})
    commentary = client.post("/api/compare/ai/commentary", json={"compare_result": compare.json()})

    assert wrong_login.status_code == 401
    assert login.status_code == 200
    assert invalid_config.status_code == 422
    assert config.status_code == 200
    assert config.json()["base_url_is_http"] is True
    assert config.json()["api_key_masked"] == "test-a...3456"
    assert config.json()["configured"] is False
    assert models.json()["models"] == ["gpt-test", "gpt-test-mini"]
    assert configured.json()["configured"] is True
    assert commentary.status_code == 200
    assert commentary.json()["model"] == "gpt-test-mini"
    assert "候选基金短评" in commentary.json()["commentary"]


def test_lof_monitor_refresh_and_cache_fallback(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    refresh_response = client.get("/api/lof/opportunities?refresh=true&limit=80")
    assert refresh_response.status_code == 200
    refresh_body = refresh_response.json()
    assert {item["code"] for item in refresh_body["items"]} >= {"161128", "501018", "164906", "160644", "160717"}
    assert any(item["is_opportunity"] for item in refresh_body["items"])

    cached_response = client.get("/api/lof/opportunities?limit=80", headers={"X-Device-Id": "phone-a"})
    assert cached_response.status_code == 200
    assert {item["code"] for item in cached_response.json()["items"]} >= {"161128", "501018"}


def test_etf_monitor_refresh_and_cache_fallback(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    refresh_response = client.get("/api/etf/opportunities?refresh=true&limit=20")
    assert refresh_response.status_code == 200
    refresh_body = refresh_response.json()
    assert {item["code"] for item in refresh_body["items"]} >= {"159605", "513500", "159941"}
    assert any(item["iopv_premium_pct"] is not None for item in refresh_body["items"])

    cached_response = client.get("/api/etf/opportunities?limit=20")
    assert cached_response.status_code == 200
    assert {item["code"] for item in cached_response.json()["items"]} >= {"159605", "513500"}


def test_lof_notice_settings_api_persists_page_settings(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    update_response = client.put(
        "/api/lof/notice/settings",
        json={"enabled": False, "daily_summary_time": "10:30", "ipo_reminder_enabled": True},
    )
    assert update_response.status_code == 200
    body = update_response.json()
    assert body["enabled"] is False
    assert body["daily_summary_time"] == "10:30"
    assert body["ipo_reminder_enabled"] is True

    status_response = client.get("/api/lof/notice/status")
    assert status_response.status_code == 200
    status = status_response.json()
    assert status["enabled"] is False
    assert status["daily_summary_time"] == "10:30"
    assert status["ipo_reminder_enabled"] is True

    invalid_response = client.put("/api/lof/notice/settings", json={"daily_summary_time": "25:00"})
    assert invalid_response.status_code == 422
    assert invalid_response.json()["error"]["code"] == "INVALID_LOF_NOTICE_TIME"
