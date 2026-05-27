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
