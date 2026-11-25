from typing import List, Optional, Literal
from pydantic import BaseModel, Field, field_validator


class HealthResponse(BaseModel):
    status: str = "ok"
    keys_configured: int
    cache_enabled: bool


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128, description="Client generated session ID")
    message: str = Field(..., description="User message content")
    system_prompt: Optional[str] = Field(None, max_length=4000, description="Optional override for session system prompt")

    @field_validator("message")
    @classmethod
    def validate_message(cls, v: str) -> str:
        trimmed = v.strip()
        if len(trimmed) < 1 or len(trimmed) > 4000:
            raise ValueError("Message must be between 1 and 4000 characters after trimming whitespace")
        return trimmed

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, v: str) -> str:
        trimmed = v.strip()
        if not (1 <= len(trimmed) <= 128):
            raise ValueError("session_id must be between 1 and 128 characters")
        return trimmed


class ChatMetadata(BaseModel):
    resolution_source: Literal["LIVE_INFERENCE", "LLM_CACHE"]
    model: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    key_id: Optional[str] = None
    fallback_attempts: int = 0


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    metadata: ChatMetadata


class MessageMetadata(BaseModel):
    resolution_source: Literal["LIVE_INFERENCE", "LLM_CACHE"]
    input_tokens: int
    output_tokens: int
    model: Optional[str] = None
    latency_ms: Optional[int] = None
    key_id: Optional[str] = None
    fallback_attempts: Optional[int] = None


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    metadata: Optional[MessageMetadata] = None


class SessionHistoryResponse(BaseModel):
    session_id: str
    messages: List[HistoryMessage]


class ErrorResponse(BaseModel):
    detail: str
