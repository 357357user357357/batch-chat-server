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
    # 🔥 Cache keep-alive toggle state (web UI button)
    keepalive: bool = False
    messages: list[MessageOut]


class ConversationCreate(BaseModel):
    title: str = "New chat"


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=255)


class MessageCreate(BaseModel):
    role: str = Field(pattern="^(user|assistant|system)$")
    content: str
    model: str | None = None


class KeepaliveToggle(BaseModel):
    enabled: bool


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
    # Reasoning effort (OpenRouter unified `reasoning` param): "none" disables
    # thinking; low/medium/high/xhigh/max set the effort level. Default (None)
    # leaves the model's own default.
    reasoning_effort: str | None = Field(
        default=None, pattern="^(none|low|medium|high|xhigh|max)$"
    )
    # When true, run a Tavily web search and inject the top results into the
    # system prompt (like the Android app's web-search feature).
    web_search: bool = False


class ChatResponseItem(BaseModel):
    model: str
    ok: bool
    content: str | None = None
    error: str | None = None
    # DB id of the persisted assistant message (lets the web UI delete a
    # freshly received answer without reloading the conversation).
    message_id: int | None = None


class ChatResponse(BaseModel):
    conversation_id: int
    conversation_title: str
    user_message: MessageOut
    responses: list[ChatResponseItem]
    # True when Tavily web results were injected into the prompt for this
    # message — lets the web UI badge the question so the answer's "according
    # to the latest web results" wording is never a surprise.
    web_search_used: bool = False


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
    # ms epoch (old builds) or ISO string (new builds) — used by the master
    # server's message merge to tell stale copies from fresh ones.
    updatedAt: str | int | float | None = None


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
    # ms epoch or ISO string — used by the message merge (see PhoneDialog).
    updatedAt: str | int | float | None = None
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
    tavily_configured: bool = False


# ---------------------------------------------------------------
# Runtime-configurable provider credentials (web UI Settings modal)
# ---------------------------------------------------------------


class SettingsUpdate(BaseModel):
    """All fields optional — only the ones sent are updated (others untouched)."""

    openrouter_api_key: str | None = None
    tavily_api_key: str | None = None
    google_project_id: str | None = None
    google_location: str | None = None
    google_service_account_json: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_session_token: str | None = None
    aws_region: str | None = None
    cache_duration_seconds: int | None = None
    cache_keepalive_hours: int | None = None


class SettingsBackup(BaseModel):
    """Raw (unmasked) credential export/import file — download before
    decommissioning a server, upload on the new one to restore in one step."""

    model_config = ConfigDict(extra="allow")

    openrouter_api_key: str = ""
    tavily_api_key: str = ""
    google_project_id: str = ""
    google_location: str = ""
    google_service_account_json: str = ""
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_session_token: str = ""
    aws_region: str = ""
    cache_duration_seconds: int = 3600
    cache_keepalive_hours: int = 3


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

    model: str = "anthropic/claude-fable-5.1:batch"
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


# ---------------------------------------------------------------
# Multi-device sync (Android app + PCs sharing one server account)
#
# Devices authenticate the same way as the web UI (POST /api/auth/login with
# the shared password → bearer token); that token doubles as the "sync key".
# Sync is keyed by `external_id` (the id a dialog/batch was first created
# with, wherever that was) so the same dialog is recognized everywhere.
# ---------------------------------------------------------------


class SyncMessage(BaseModel):
    # Server-side message id — lets the phone delete a specific Q/A.
    id: int | None = None
    role: str = Field(pattern="^(user|assistant|system)$")
    content: str
    model: str | None = None
    # UTC timestamp — lets the phone show per-message dates (DD.MM.YY HH.MM).
    created_at: datetime | None = None


class SyncConversationOut(BaseModel):
    """One dialog as returned by a sync pull. `deleted` tombstones removals so
    other devices can drop their local copy instead of keeping a stale one.

    Audit trail (master-server archive): origin_device = whose record it was
    (first creator), modified_by = the last device that changed it, deleted_at
    + deleted_by = when and by which device it was deleted.
    """

    external_id: str
    kind: str = "chat"
    model: str | None = None
    title: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted: bool = False
    origin_device: str | None = None
    modified_by: str | None = None
    deleted_at: datetime | None = None
    deleted_by: str | None = None
    messages: list[SyncMessage] = []


class SyncPullResponse(BaseModel):
    # Echoed back so the client can pass it as `since` on the next pull
    # without depending on its own (possibly skewed) clock.
    server_time: datetime
    conversations: list[SyncConversationOut]
    # Provider keys held by the server, so devices can adopt any they lack
    # (unified keys across phone + server). Values are raw (the pull is an
    # authenticated, password-gated endpoint just like the web UI).
    keys: dict[str, str] = {}


class SyncPushRequest(BaseModel):
    """Dialogs/batches created or changed locally since the last sync, plus
    the external_ids of any deleted locally (soft-deleted on the server).

    `keys` lets a device offer its provider keys so the server can adopt any
    it is missing (unified keys across phone + server).
    """

    dialogs: list[PhoneDialog] = []
    batches: list[PhoneBatch] = []
    deleted_external_ids: list[str] = []
    keys: dict[str, str] = {}


class SyncPushResponse(BaseModel):
    created: int
    updated: int
    deleted: int
    # Stale pushes for dialogs already tombstoned on the master server were
    # ignored (never resurrected); the pushing device drops them on its pull.
    skipped_deleted: int = 0
    server_time: datetime