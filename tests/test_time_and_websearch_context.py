"""Tests for the current-date/time injection into chat system prompts and
the timestamped web-search context (so stale web snippets can't pose as
'now' when the Web search toggle is used)."""

import os

os.environ.setdefault("APP_PASSWORD", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/bc_test_batch.db")

from app.routers.chat import system_prompt_with_current_time  # noqa: E402
from app.services.tavily import web_search_context  # noqa: E402


def test_system_prompt_contains_current_datetime():
    prompt = system_prompt_with_current_time(None)
    assert prompt.startswith("Current date and time:")
    assert "UTC" in prompt
    assert "never from web snippets" in prompt


def test_system_prompt_keeps_user_system_after_base():
    prompt = system_prompt_with_current_time("You are a pirate.")
    base, _, user = prompt.partition("\n\n")
    assert base.startswith("Current date and time:")
    assert user == "You are a pirate."


def test_web_search_context_stamped_with_search_time():
    results = [
        {"title": "A clock page", "url": "https://example.com",
         "content": "It says 10:02 AM here", "published_date": "2026-09-04"},
    ]
    ctx = web_search_context("what time is it", results)
    assert "searched at" in ctx
    assert "snippets may be outdated" in ctx
    assert "page published: 2026-09-04" in ctx
    assert "It says 10:02 AM here" in ctx


def test_web_search_context_empty_results_still_timestamped():
    ctx = web_search_context("anything", [])
    assert "searched at" in ctx
    assert "none" in ctx