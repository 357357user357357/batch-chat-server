from app.services.openrouter import _max_token_limit_from_error


def test_parses_provider_cap_from_wrapped_400():
    wrapped = (
        'Provider returned error (HTTP 400, metadata.raw=[{"error": '
        '{"code": 400, "message": "Requested maximum tokens of 131072 exceeds '
        'the maximum output tokens limit: 102400.", "status": "INVALID_ARGUMENT"}}])'
    )
    assert _max_token_limit_from_error(wrapped) == 102400


def test_parses_plain_and_loose_variants():
    assert (
        _max_token_limit_from_error(
            "Requested maximum tokens of 131072 exceeds the maximum output tokens limit: 8192."
        )
        == 8192
    )
    assert _max_token_limit_from_error("max output tokens limit:  4096") == 4096


def test_returns_none_for_other_errors():
    assert _max_token_limit_from_error("Provider returned error") is None
    assert _max_token_limit_from_error("") is None
