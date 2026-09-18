"""Tests for the public /links page and the /downloads static mount (the APK
hosting) — both registered before the "/" UI mount so they are not shadowed."""

import os

os.environ.setdefault("APP_PASSWORD", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/bc_test_batch.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


def test_links_page_lists_repos_and_apk():
    response = client.get("/links")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "github.com/357357user357357/batch-chat" in body
    assert "github.com/357357user357357/batch-chat-server" in body
    assert "/downloads/batch-chat.apk" in body
    assert "flexchat.top" in body


def test_links_page_not_shadowed_by_ui_mount():
    # The "/" StaticFiles mount matches everything; /links must answer with
    # the generated page, not a 404 from the UI directory.
    assert client.get("/links").status_code == 200


def test_downloads_mount_serves_files():
    from app import main as main_module

    target = main_module.DOWNLOADS_DIR / "probe-test.txt"
    target.write_text("hello", encoding="utf-8")
    try:
        response = client.get("/downloads/probe-test.txt")
        assert response.status_code == 200
        assert response.text == "hello"
    finally:
        target.unlink(missing_ok=True)

    # Unknown files answer 404 from the downloads mount itself.
    assert client.get("/downloads/does-not-exist.bin").status_code == 404
