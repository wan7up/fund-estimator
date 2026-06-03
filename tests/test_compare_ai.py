from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from fund_estimator.models.schema import (
    CompareAiCommentaryRequest,
    CompareAiConfigUpdate,
    CompareResponse,
)
from fund_estimator.services.compare_ai import CompareAiService
from fund_estimator.services.exceptions import AppError


class FakeAiClient:
    posts: list[dict] = []

    def __init__(self, *_, **__):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def get(self, url: str):
        return httpx.Response(
            200,
            json={"data": [{"id": "gpt-test"}, {"id": "gpt-test-mini"}]},
            request=httpx.Request("GET", url),
        )

    async def post(self, url: str, json: dict):
        self.posts.append(json)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "总体判断\n这是一段 AI 评价。"}}]},
            request=httpx.Request("POST", url),
        )


def compare_response(conclusion="not_comparable", recommendation_code=None) -> CompareResponse:
    return CompareResponse(
        generated_at=datetime.now(UTC),
        strategy="balanced",
        conclusion=conclusion,
        conclusion_title="测试结论",
        recommendation_code=recommendation_code,
        recommendation="规则评价",
        funds=[],
        pair_similarities=[],
        score_factors=[],
        warnings=[],
    )


def test_base_url_validation_allows_public_http_and_rejects_private(tmp_path):
    service = CompareAiService(data_dir=tmp_path, admin_password="pw")

    assert service._validate_base_url("http://api.example.com/v1") == "http://api.example.com/v1"
    assert service._validate_base_url("https://api.openai.com/v1") == "https://api.openai.com/v1"

    for value in ["ftp://api.example.com/v1", "http://localhost:8000/v1", "http://127.0.0.1/v1", "http://10.0.0.5/v1", "http://169.254.169.254/v1"]:
        with pytest.raises(AppError):
            service._validate_base_url(value)


def test_config_masks_key_and_session_expires(tmp_path):
    service = CompareAiService(data_dir=tmp_path, admin_password="pw")
    token = service.login("pw")

    status = service.update_config(
        CompareAiConfigUpdate(base_url="http://api.example.com/v1", api_key="sk-abcdef123456"),
        token,
    )

    assert status.authenticated is True
    assert status.base_url_is_http is True
    assert status.api_key_masked == "sk-abc...3456"
    assert "api_key" not in status.model_dump()

    service._write_sessions(
        [{"token_hash": service._token_hash(token), "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()}]
    )
    assert service.is_authenticated(token) is False


def test_models_and_commentary_use_saved_config(tmp_path):
    FakeAiClient.posts = []
    service = CompareAiService(data_dir=tmp_path, admin_password="pw", client_factory=FakeAiClient)
    token = service.login("pw")
    service.update_config(
        CompareAiConfigUpdate(
            base_url="http://api.example.com/v1",
            api_key="sk-test",
            selected_model="gpt-test",
            persona_id="marks_cycle",
        ),
        token,
    )

    models = asyncio.run(service.list_models(token))
    commentary = asyncio.run(
        service.create_commentary(
            CompareAiCommentaryRequest(compare_result=compare_response("very_similar", "100001")),
            token,
        )
    )

    assert models.models == ["gpt-test", "gpt-test-mini"]
    assert commentary.model == "gpt-test"
    assert "AI 评价" in commentary.commentary
    assert FakeAiClient.posts[0]["model"] == "gpt-test"


def test_prompt_preserves_rule_boundaries(tmp_path):
    service = CompareAiService(data_dir=tmp_path, admin_password="pw")
    config = service._read_config()
    config.selected_model = "gpt-test"
    config.api_key = "sk-test"

    messages = service._build_messages(
        CompareAiCommentaryRequest(compare_result=compare_response("not_comparable")),
        config,
    )
    prompt = "\n".join(item["content"] for item in messages)

    assert "不重新打分" in prompt
    assert "不改变排序" in prompt
    assert "not_comparable" in prompt
    assert "不要给出谁更好的强推荐" in prompt
