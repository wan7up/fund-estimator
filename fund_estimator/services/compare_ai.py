from __future__ import annotations

import hashlib
import hmac
import contextlib
import ipaddress
import json
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from fund_estimator.models.schema import (
    CompareAiCommentaryRequest,
    CompareAiCommentaryResponse,
    CompareAiConfigUpdate,
    CompareAiModelsResponse,
    CompareAiPersona,
    CompareAiStatus,
)
from fund_estimator.services.exceptions import AppError, DataSourceError
from fund_estimator.services.http_settings import http_trust_env


DEFAULT_COMPARE_AI_BASE_URL = "https://api.openai.com/v1"
COMPARE_AI_SESSION_COOKIE = "fund_compare_ai_session"
COMPARE_AI_SESSION_DAYS = 15

PERSONAS: dict[str, tuple[str, str, str]] = {
    "researcher": (
        "默认研究员",
        "客观、克制，按数据结构解释结论。",
        "以基金研究员的口吻，清晰解释候选基金之间的相对优劣，不写空泛提示。",
    ),
    "buffett_value": (
        "长期价值风格",
        "偏长期、质量、可持续性和安全边际。",
        "参考长期价值投资的分析风格，强调长期质量、估值纪律和安全边际；不要声称自己是具体投资人。",
    ),
    "lynch_growth": (
        "成长观察风格",
        "偏成长性、行业空间、基金风格是否容易理解。",
        "参考成长投资的观察方式，强调基金是否有清晰主题、增长弹性和可验证逻辑；不要声称自己是具体投资人。",
    ),
    "dalio_balance": (
        "均衡配置风格",
        "偏资产暴露、相关性、组合分散和持有体验。",
        "参考均衡配置的分析方式，强调资产暴露、相关性、波动来源和组合角色；不要声称自己是具体投资人。",
    ),
    "marks_cycle": (
        "周期观察风格",
        "偏周期位置、回撤空间和赔率补偿。",
        "参考周期观察的分析方式，强调潜在回撤、拥挤度和赔率补偿；不要声称自己是具体投资人。",
    ),
    "custom": (
        "自定义风格",
        "使用你填写的分析风格提示。",
        "使用用户提供的分析风格，但必须保持事实边界，只做候选基金之间的相对取舍。",
    ),
}


@dataclass
class CompareAiConfig:
    base_url: str = DEFAULT_COMPARE_AI_BASE_URL
    api_key: str | None = None
    selected_model: str | None = None
    persona_id: str = "researcher"
    custom_persona: str | None = None
    updated_at: str | None = None


class CompareAiService:
    def __init__(
        self,
        *,
        data_dir: str | Path,
        admin_password: str | None,
        client_factory: Any | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.config_path = self.data_dir / "compare_ai_config.json"
        self.sessions_path = self.data_dir / "compare_ai_sessions.json"
        self.admin_password = (admin_password or "").strip() or None
        self.client_factory = client_factory or httpx.AsyncClient

    @property
    def enabled(self) -> bool:
        return bool(self.admin_password)

    def status(self, token: str | None = None) -> CompareAiStatus:
        authenticated = self.is_authenticated(token)
        config = self._read_config()
        return self._status_from_config(config, authenticated=authenticated)

    def login(self, password: str) -> str:
        self._require_enabled()
        if not hmac.compare_digest(password, self.admin_password or ""):
            raise AppError("COMPARE_AI_AUTH_FAILED", "AI 管理密码不正确", status_code=401)
        token = secrets.token_urlsafe(32)
        sessions = self._read_sessions()
        now = datetime.now(UTC)
        sessions = [item for item in sessions if self._parse_time(item.get("expires_at")) > now]
        sessions.append(
            {
                "token_hash": self._token_hash(token),
                "expires_at": (now + timedelta(days=COMPARE_AI_SESSION_DAYS)).isoformat(),
            }
        )
        self._write_sessions(sessions)
        return token

    def is_authenticated(self, token: str | None) -> bool:
        if not self.enabled or not token:
            return False
        now = datetime.now(UTC)
        target = self._token_hash(token)
        sessions = self._read_sessions()
        active = [item for item in sessions if self._parse_time(item.get("expires_at")) > now]
        if len(active) != len(sessions):
            self._write_sessions(active)
        return any(hmac.compare_digest(item.get("token_hash", ""), target) for item in active)

    def update_config(self, request: CompareAiConfigUpdate, token: str | None) -> CompareAiStatus:
        self._require_session(token)
        existing = self._read_config()
        base_url = self._validate_base_url(request.base_url or existing.base_url or DEFAULT_COMPARE_AI_BASE_URL)
        persona_id = request.persona_id or existing.persona_id or "researcher"
        if persona_id not in PERSONAS:
            raise AppError("INVALID_COMPARE_AI_PERSONA", "未知的 AI 分析风格", status_code=422)
        custom_persona = request.custom_persona if request.custom_persona is not None else existing.custom_persona
        if persona_id == "custom" and not custom_persona:
            raise AppError("INVALID_COMPARE_AI_PERSONA", "自定义风格需要填写说明", status_code=422)
        api_key = request.api_key if request.api_key is not None else existing.api_key
        if not api_key:
            raise AppError("INVALID_COMPARE_AI_CONFIG", "请先填写 API Key", status_code=422)
        config = CompareAiConfig(
            base_url=base_url,
            api_key=api_key,
            selected_model=request.selected_model if request.selected_model is not None else existing.selected_model,
            persona_id=persona_id,
            custom_persona=custom_persona,
            updated_at=datetime.now(UTC).isoformat(),
        )
        self._write_config(config)
        return self._status_from_config(config, authenticated=True)

    async def list_models(self, token: str | None) -> CompareAiModelsResponse:
        self._require_session(token)
        config = self._require_config(require_model=False)
        url = f"{config.base_url.rstrip('/')}/models"
        try:
            async with self.client_factory(
                timeout=12.0,
                headers={"Authorization": f"Bearer {config.api_key}"},
                trust_env=http_trust_env(),
            ) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            raise DataSourceError("COMPARE_AI_MODELS_FAILED", "模型列表获取失败") from exc
        if response.status_code >= 400:
            raise DataSourceError("COMPARE_AI_MODELS_FAILED", f"模型列表获取失败：HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise DataSourceError("COMPARE_AI_MODELS_FAILED", "模型列表返回不是有效 JSON") from exc
        models = [str(item.get("id", "")).strip() for item in data.get("data", []) if isinstance(item, dict)]
        models = [model for model in models if model]
        if not models:
            raise DataSourceError("COMPARE_AI_MODELS_EMPTY", "未从模型接口读取到可用模型")
        return CompareAiModelsResponse(models=models)

    async def create_commentary(self, request: CompareAiCommentaryRequest, token: str | None) -> CompareAiCommentaryResponse:
        self._require_session(token)
        config = self._require_config(require_model=True)
        messages = self._build_messages(request, config)
        url = f"{config.base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": config.selected_model,
            "messages": messages,
            "temperature": 0.25,
            "max_tokens": 700,
        }
        try:
            async with self.client_factory(
                timeout=40.0,
                headers={
                    "Authorization": f"Bearer {config.api_key}",
                    "Content-Type": "application/json",
                },
                trust_env=http_trust_env(),
            ) as client:
                response = await client.post(url, json=payload)
        except httpx.HTTPError as exc:
            raise DataSourceError("COMPARE_AI_COMMENTARY_FAILED", "AI 评价生成失败") from exc
        if response.status_code >= 400:
            raise DataSourceError("COMPARE_AI_COMMENTARY_FAILED", f"AI 评价生成失败：HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise DataSourceError("COMPARE_AI_COMMENTARY_FAILED", "AI 评价返回不是有效 JSON") from exc
        content = self._extract_chat_content(data)
        if not content:
            raise DataSourceError("COMPARE_AI_COMMENTARY_EMPTY", "AI 没有返回有效评价内容")
        return CompareAiCommentaryResponse(
            generated_at=datetime.now(UTC),
            model=config.selected_model or "",
            persona_id=config.persona_id,
            commentary=content,
        )

    def _build_messages(self, request: CompareAiCommentaryRequest, config: CompareAiConfig) -> list[dict[str, str]]:
        persona_prompt = PERSONAS.get(config.persona_id, PERSONAS["researcher"])[2]
        if config.persona_id == "custom" and config.custom_persona:
            persona_prompt = config.custom_persona
        compare_json = json.dumps(request.compare_result.model_dump(mode="json"), ensure_ascii=False)
        system = (
            "你是基金对比页内置的候选基金研究员，不是聊天助手。"
            "你的任务是在页面里给出短评，只针对用户当前候选基金说话。"
            "你必须遵守：不重新打分、不改变排序、不改变 recommendation_code、不编造未提供的数据、"
            "不编造未来收益、不输出泛泛免责声明。"
            "写作方式：中文，简短、克制、像研究备注；不要寒暄，不要说“以下是”，不要提 JSON、模型或 AI，"
            "不要输出 Markdown 表格或长标题。你的价值是补充解读板块匹配、风格差异和不可比原因，"
            "要明确给出相对优选和相对稳妥，不要复述规则评价原文或评分表。"
            f"风格偏好只作为侧重点参考：{persona_prompt}"
        )
        user = (
            "请基于下面 CompareResponse JSON 输出页面短评。\n"
            "固定输出 4 行纯文本，每行不超过 45 个中文字符，总字数控制在 120-220 字。\n"
            "推荐格式：\n"
            "结论：一句话说明可比性和规则结论。\n"
            "板块：必须结合 theme_hint/theme_analysis 说明谁匹配目标板块、谁偏离或只是相关。\n"
            "风格：点名候选基金，概括每只或每组的风格差异。\n"
            "取舍：说明相对优选和相对稳妥，只点具体差异，如仓位、集中度、规模、限购、板块偏离。\n\n"
            "要求：如果 conclusion 是 not_comparable，不要跨组硬排；可以指出同组或目标板块内的相对优选/相对稳妥，偏离目标板块的单独说明；"
            "如果 conclusion 是 very_similar，可以解释规则推荐的基金为什么更优；"
            "如果存在多只基金，只说明哪几只相似、哪几只相关性低。"
            "不要复述评分表，不要写泛泛的基金投资科普，不要写泛泛免责声明。\n\n"
            f"CompareResponse JSON：\n{compare_json}"
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise AppError("COMPARE_AI_DISABLED", "未配置 AI 管理密码，AI 功能未启用", status_code=403)

    def _require_session(self, token: str | None) -> None:
        self._require_enabled()
        if not self.is_authenticated(token):
            raise AppError("COMPARE_AI_UNAUTHORIZED", "请先验证 AI 管理密码", status_code=401)

    def _require_config(self, *, require_model: bool) -> CompareAiConfig:
        config = self._read_config()
        if not config.api_key:
            raise AppError("COMPARE_AI_NOT_CONFIGURED", "请先保存 API Key", status_code=400)
        if require_model and not config.selected_model:
            raise AppError("COMPARE_AI_MODEL_REQUIRED", "请先选择 AI 模型", status_code=400)
        config.base_url = self._validate_base_url(config.base_url or DEFAULT_COMPARE_AI_BASE_URL)
        return config

    def _status_from_config(self, config: CompareAiConfig, *, authenticated: bool) -> CompareAiStatus:
        configured = bool(config.api_key and config.selected_model)
        base_url = config.base_url or DEFAULT_COMPARE_AI_BASE_URL
        return CompareAiStatus(
            enabled=self.enabled,
            authenticated=authenticated,
            configured=configured if authenticated else bool(config.api_key),
            base_url=base_url if authenticated else DEFAULT_COMPARE_AI_BASE_URL,
            base_url_is_http=base_url.lower().startswith("http://") if authenticated else False,
            selected_model=config.selected_model if authenticated else None,
            persona_id=config.persona_id if authenticated else "researcher",
            custom_persona=config.custom_persona if authenticated else None,
            api_key_masked=self._mask_key(config.api_key) if authenticated else None,
            personas=self._personas(),
        )

    @staticmethod
    def _personas() -> list[CompareAiPersona]:
        return [
            CompareAiPersona(id=key, label=value[0], description=value[1])
            for key, value in PERSONAS.items()
        ]

    @staticmethod
    def _validate_base_url(value: str) -> str:
        raw = value.strip().rstrip("/")
        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"}:
            raise AppError("INVALID_COMPARE_AI_BASE_URL", "API URL 只支持 http:// 或 https://", status_code=422)
        host = parsed.hostname
        if not host:
            raise AppError("INVALID_COMPARE_AI_BASE_URL", "API URL 缺少主机名", status_code=422)
        normalized_host = host.strip().lower().rstrip(".")
        if normalized_host in {"localhost", "ip6-localhost"} or normalized_host.endswith(".localhost"):
            raise AppError("INVALID_COMPARE_AI_BASE_URL", "API URL 不能指向本机地址", status_code=422)
        if normalized_host in {"metadata.google.internal"}:
            raise AppError("INVALID_COMPARE_AI_BASE_URL", "API URL 不能指向云元数据地址", status_code=422)
        try:
            ip = ipaddress.ip_address(normalized_host)
        except ValueError:
            return raw
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise AppError("INVALID_COMPARE_AI_BASE_URL", "API URL 不能指向本机、内网或保留地址", status_code=422)
        return raw

    @staticmethod
    def _extract_chat_content(data: dict[str, Any]) -> str:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        first = choices[0]
        if not isinstance(first, dict):
            return ""
        message = first.get("message")
        if isinstance(message, dict):
            return str(message.get("content") or "").strip()
        return str(first.get("text") or "").strip()

    def _read_config(self) -> CompareAiConfig:
        data = self._read_json(self.config_path, {})
        if not isinstance(data, dict):
            data = {}
        return CompareAiConfig(
            base_url=str(data.get("base_url") or DEFAULT_COMPARE_AI_BASE_URL),
            api_key=data.get("api_key") or None,
            selected_model=data.get("selected_model") or None,
            persona_id=data.get("persona_id") or "researcher",
            custom_persona=data.get("custom_persona") or None,
            updated_at=data.get("updated_at") or None,
        )

    def _write_config(self, config: CompareAiConfig) -> None:
        self._write_json(
            self.config_path,
            {
                "base_url": config.base_url,
                "api_key": config.api_key,
                "selected_model": config.selected_model,
                "persona_id": config.persona_id,
                "custom_persona": config.custom_persona,
                "updated_at": config.updated_at,
            },
        )

    def _read_sessions(self) -> list[dict[str, str]]:
        data = self._read_json(self.sessions_path, {"sessions": []})
        sessions = data.get("sessions") if isinstance(data, dict) else []
        return [item for item in sessions if isinstance(item, dict)]

    def _write_sessions(self, sessions: list[dict[str, str]]) -> None:
        self._write_json(self.sessions_path, {"sessions": sessions})

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _parse_time(value: str | None) -> datetime:
        if not value:
            return datetime.min.replace(tzinfo=UTC)
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return datetime.min.replace(tzinfo=UTC)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed

    @staticmethod
    def _mask_key(value: str | None) -> str | None:
        if not value:
            return None
        if len(value) <= 10:
            return value[:2] + "..." + value[-2:]
        return value[:6] + "..." + value[-4:]

    def _read_json(self, path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    def _write_json(self, path: Path, data: Any) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        if os.name == "posix":
            with contextlib.suppress(OSError):
                path.chmod(0o600)
