"""Simple in-memory brute-force protection for the password login.

Per client IP: after LOGIN_MAX_FAILURES consecutive wrong passwords the IP is
locked out for LOGIN_LOCKOUT_SECONDS (doubling on every extra failure, capped
at 15 minutes). Successful login clears the counter. This is deliberately
dependency-free — appropriate for a single-process SQLite deployment.
"""

import time

from fastapi import HTTPException, Request

LOGIN_MAX_FAILURES = 5
LOGIN_BASE_LOCKOUT_SECONDS = 30.0
LOGIN_MAX_LOCKOUT_SECONDS = 900.0

# {ip: {"failures": int, "locked_until": float}}
_attempts: dict[str, dict] = {}


def _client_ip(request: Request) -> str:
    # Direct TCP peer; no X-Forwarded-For trust (spoofable, and the server is
    # meant to sit directly on :8000).
    return request.client.host if request.client else "unknown"


def _remaining_lockout(ip: str) -> float:
    entry = _attempts.get(ip)
    if not entry:
        return 0.0
    return max(0.0, entry.get("locked_until", 0.0) - time.monotonic())


def check_login_allowed(request: Request) -> None:
    """Raises 429 when this IP is currently locked out."""
    ip = _client_ip(request)
    remaining = _remaining_lockout(ip)
    if remaining > 0:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts — try again in {int(remaining)}s",
        )


def record_login_failure(request: Request) -> None:
    ip = _client_ip(request)
    entry = _attempts.setdefault(ip, {"failures": 0, "locked_until": 0.0})
    entry["failures"] += 1
    if entry["failures"] >= LOGIN_MAX_FAILURES:
        over = entry["failures"] - LOGIN_MAX_FAILURES
        lockout = min(
            LOGIN_MAX_LOCKOUT_SECONDS,
            LOGIN_BASE_LOCKOUT_SECONDS * (2**over),
        )
        entry["locked_until"] = time.monotonic() + lockout


def record_login_success(request: Request) -> None:
    _attempts.pop(_client_ip(request), None)