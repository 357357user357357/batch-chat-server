"""Direct calls to AWS Bedrock (no OpenRouter in between).

Auth uses a plain IAM access key + secret (not an instance role — this runs on
a rented VPS outside AWS). Models are addressed in the UI as "bedrock:<model>",
e.g. "bedrock:anthropic.claude-3-5-sonnet-20241022-v2:0".

Only Anthropic-family models are supported (Messages API body shape). Other
Bedrock model families (Titan, Llama, Mistral...) use different request/response
shapes and are not wired up here.
"""

import json

from app.config import settings
from app.services.provider_errors import ProviderError

DEFAULT_MODELS = [
    "bedrock:anthropic.claude-3-5-sonnet-20241022-v2:0",
    "bedrock:anthropic.claude-3-5-haiku-20241022-v1:0",
]


class BedrockError(ProviderError):
    pass


def is_configured() -> bool:
    return bool(settings.aws_access_key_id) and bool(settings.aws_secret_access_key)


def _require_config() -> None:
    if not is_configured():
        raise BedrockError(
            "AWS credentials are not configured on the server "
            "(set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY)"
        )


def _client():
    _require_config()
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover
        raise BedrockError("boto3 is not installed on the server") from exc

    kwargs: dict = {
        "region_name": settings.aws_region,
        "aws_access_key_id": settings.aws_access_key_id,
        "aws_secret_access_key": settings.aws_secret_access_key,
    }
    if settings.aws_session_token:
        kwargs["aws_session_token"] = settings.aws_session_token
    return boto3.client("bedrock-runtime", **kwargs)


def chat_completion(
    model: str,
    messages: list[dict[str, str]],
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    """Call a Bedrock (Anthropic-family) model synchronously. Returns the reply text."""
    from botocore.exceptions import BotoCoreError, ClientError

    client = _client()

    system_parts = [m["content"] for m in messages if m.get("role") == "system"]
    anthropic_messages = [
        {"role": "assistant" if m.get("role") == "assistant" else "user", "content": m.get("content", "")}
        for m in messages
        if m.get("role") != "system"
    ]

    body: dict = {
        "anthropic_version": "bedrock-2023-05-31",
        "messages": anthropic_messages,
        "max_tokens": max_tokens or 4096,
    }
    if system_parts:
        body["system"] = "\n".join(system_parts)
    if temperature is not None:
        body["temperature"] = temperature

    try:
        resp = client.invoke_model(
            modelId=model,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        data = json.loads(resp["body"].read())
    except (BotoCoreError, ClientError) as exc:
        raise BedrockError(f"Bedrock request failed: {exc}") from exc

    try:
        return "".join(b.get("text", "") for b in data.get("content", []))
    except (KeyError, TypeError) as exc:
        raise BedrockError(f"Unexpected response from Bedrock: {data!r}") from exc
