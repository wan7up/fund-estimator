from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class AiChatStatus(BaseModel):
    enabled: bool
    authenticated: bool = False
    model_configured: bool = False
    voice_input_available: bool = False


class AiChatLoginRequest(BaseModel):
    password: str = Field(..., min_length=1, max_length=256)


class AiChatHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=4_000)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("对话内容不能为空")
        return value


class AiChatStreamRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2_000)
    history: list[AiChatHistoryMessage] = Field(default_factory=list, max_length=16)

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("问题不能为空")
        return value


class AiChatTranscriptionResponse(BaseModel):
    text: str
