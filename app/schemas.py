from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    model: str | None = None
    content: str
    created_at: datetime


class ConversationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    last_message: str | None = None


class ConversationDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[MessageOut]


class ConversationCreate(BaseModel):
    title: str = "New chat"


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=255)


class MessageCreate(BaseModel):
    role: str = Field(pattern="^(user|assistant|system)$")
    content: str
    model: str | None = None


class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    token: str
    expires_at: datetime


class ChatRequest(BaseModel):
    """Send one user message to one or several OpenRouter models in parallel."""

    user_message: str = Field(min_length=1)
    models: list[str] = Field(min_length=1, max_length=20)
    conversation_id: int | None = None
    system: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


class ChatResponseItem(BaseModel):
    model: str
    ok: bool
    content: str | None = None
    error: str | None = None


class ChatResponse(BaseModel):
    conversation_id: int
    conversation_title: str
    user_message: MessageOut
    responses: list[ChatResponseItem]


class ImportMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant|system)$")
    content: str
    model: str | None = None


class ImportConversation(BaseModel):
    title: str | None = None
    messages: list[ImportMessage] = []


class ImportRequest(BaseModel):
    conversations: list[ImportConversation]


class ImportResponse(BaseModel):
    conversations_created: int
    messages_created: int


class HealthResponse(BaseModel):
    status: str
    openrouter_configured: bool