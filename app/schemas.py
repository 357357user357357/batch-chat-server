from datetime import datetime

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    model: str | None = None
    content: str
    created_at: datetime | None = None


class ConversationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str = "chat"
    model: str | None = None
    title: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    message_count: int = 0
    last_message: str | None = None


class ConversationDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    external_id: str | None = None
    kind: str = "chat"
    model: str | None = None
    title: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
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
    external_id: str | None = None
    kind: str = Field(default="chat", pattern="^(chat|batch)$")
    model: str | None = None
    title: str | None = None
    messages: list[ImportMessage] = []


class ImportRequest(BaseModel):
    conversations: list[ImportConversation]


class ImportResponse(BaseModel):
    conversations_created: int
    messages_created: int


# ---------------------------------------------------------------
# Native Android-app formats (AsyncStorage JSON)
#   openrouter.dialogs.v1         → list[PhoneDialog]
#   openrouter.batches.history.v1 → list[PhoneBatch]
# ---------------------------------------------------------------


class PhoneDialog(BaseModel):
    """One entry of openrouter.dialogs.v1 from the app."""

    id: str | None = None
    title: str | None = None
    model: str | None = None
    messages: list[ImportMessage] = []
    createdAt: int | None = None
    updatedAt: int | None = None


class PhoneBatchResult(BaseModel):
    """One result inside a batch (custom_id keys a prompt, e.g. req-1)."""

    custom_id: str | None = None
    ok: bool = True
    answer: str | None = None
    error: str | None = None
    status: int | None = None


class PhoneBatch(BaseModel):
    """One entry of openrouter.batches.history.v1 from the app."""

    id: str | None = None
    title: str | None = None
    model: str | None = None
    prompts: list[str] = []
    createdAt: int | None = None
    # Raw OpenRouter batch object stored inside the item (id/status/results).
    # We only use its "results" list; everything else is ignored.
    batch: dict | None = None


class PhoneImportRequest(BaseModel):
    """Payload of the "import from the Android app" endpoint.

    Accepts lists of dialogs and/or batch-history items. The canonical phone
    storage keys (openrouter.dialogs.v1, openrouter.batches.history.v1) are
    accepted as aliases, so you can paste a whole AsyncStorage dump directly.
    """

    model_config = ConfigDict(populate_by_name=True)

    dialogs: list[PhoneDialog] | None = Field(
        default=None,
        validation_alias=AliasChoices("dialogs", "openrouter.dialogs.v1"),
    )
    batches: list[PhoneBatch] | None = Field(
        default=None,
        validation_alias=AliasChoices("batches", "openrouter.batches.history.v1"),
    )


class HealthResponse(BaseModel):
    status: str
    openrouter_configured: bool
    vertex_configured: bool = False
    bedrock_configured: bool = False


# ---------------------------------------------------------------
# JSONL batch jobs (OpenRouter async Batch API)
# ---------------------------------------------------------------


class BatchSubmitRequest(BaseModel):
    """Submit a JSONL batch. `jsonl` is the raw file content (multiple lines).

    Accepted line schemas (like GCP Vertex AI / AWS Bedrock but driven by
    OpenRouter's async Batch API on the server):
      - OpenRouter/OpenAI : {"custom_id": "...", "body": {"messages": [...]}}
      - Shorthand         : {"custom_id": "...", "messages": [...]}
      - Simple prompt     : {"custom_id": "...", "prompt": "text"}
      - Vertex AI (Gemini): {"request": {"contents": [{"role": "user",
                            "parts": [{"text": "..."}]}]}}
      - Bedrock (Anthr.)  : {"recordId": "...", "modelInput": {"messages": [...]}}
    """

    model: str = "anthropic/claude-fable-5:batch"
    jsonl: str = Field(min_length=1)
    title: str | None = None
    system: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


class BatchItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    custom_id: str
    prompt: str
    status: str
    answer: str | None = None
    error: str | None = None


class BatchJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    external_id: str | None = None
    model: str
    title: str
    status: str
    error: str | None = None
    total_items: int
    completed_items: int
    failed_items: int
    created_at: datetime | None = None
    finalized_at: datetime | None = None
    conversation_id: int | None = None
    items: list[BatchItemOut] = []