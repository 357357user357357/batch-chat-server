"""Outgoing e-mail for signup confirmation links (stdlib smtplib, no deps)."""

import smtplib
from email.message import EmailMessage

from app.config import settings


def smtp_configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_from)


def send_message(to: str, subject: str, body: str) -> None:
    """Send a plain-text e-mail via the configured SMTP server."""
    if not smtp_configured():
        raise RuntimeError("SMTP is not configured on this server")
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
        if settings.smtp_tls:
            server.starttls()
        if settings.smtp_user and settings.smtp_password:
            server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(message)