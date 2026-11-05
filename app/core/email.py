"""Small SMTP mail helper used for account email verification."""
from __future__ import annotations

import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from urllib.parse import urlencode

from app.core.config import settings


class EmailDeliveryError(RuntimeError):
    """Raised when a required transactional email cannot be delivered."""


def _smtp_from_address() -> str:
    return (settings.SMTP_FROM or settings.SMTP_USER).strip()


def _ensure_configured() -> None:
    if not settings.SMTP_HOST.strip() or not _smtp_from_address():
        raise EmailDeliveryError("Email service is not configured.")


def _send_message(message: EmailMessage) -> None:
    _ensure_configured()

    try:
        if settings.SMTP_USE_SSL:
            client = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20)
        else:
            client = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20)

        with client:
            client.ehlo()
            if settings.SMTP_USE_TLS and not settings.SMTP_USE_SSL:
                client.starttls()
                client.ehlo()
            if settings.SMTP_USER:
                client.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            client.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        # Keep infrastructure details out of HTTP responses. The caller may log
        # the original exception server-side, but users receive a generic error.
        raise EmailDeliveryError("Unable to deliver transactional email.") from exc


def send_verification_email(*, recipient: str, first_name: str, token: str) -> None:
    """Send a browser-facing verification link to the frontend.

    The frontend consumes the opaque token and calls the POST verification API.
    This keeps users inside the normal SFMS UI and ensures expired/invalid links
    render a friendly application page instead of a raw API JSON response.
    """
    base = settings.PUBLIC_FRONTEND_URL.rstrip("/")
    query = urlencode({"token": token})
    verification_url = f"{base}/verify-email?{query}"

    message = EmailMessage()
    message["Subject"] = "Verify your SFMS email address"
    message["From"] = formataddr((settings.SMTP_FROM_NAME, _smtp_from_address()))
    message["To"] = recipient

    greeting_name = first_name.strip() or "there"
    message.set_content(
        f"Hi {greeting_name},\n\n"
        "Thanks for creating your SFMS account. Verify your email address before signing in:\n\n"
        f"{verification_url}\n\n"
        "This verification link expires in 24 hours.\n\n"
        "If you did not create this account, you can ignore this email.\n"
    )
    message.add_alternative(
        f"""
        <!doctype html>
        <html>
          <body style="font-family:Arial,sans-serif;color:#172033;line-height:1.6">
            <div style="max-width:560px;margin:0 auto;padding:32px">
              <h2 style="margin:0 0 16px">Verify your SFMS email</h2>
              <p>Hi {greeting_name},</p>
              <p>Thanks for creating your SFMS account. Please verify your email address before signing in.</p>
              <p style="margin:28px 0">
                <a href="{verification_url}" style="background:#0b63f6;color:#fff;text-decoration:none;padding:12px 20px;border-radius:8px;display:inline-block">Verify email</a>
              </p>
              <p style="color:#667085">This link expires in 24 hours.</p>
              <p style="color:#667085">If you did not create this account, you can ignore this email.</p>
            </div>
          </body>
        </html>
        """,
        subtype="html",
    )
    _send_message(message)


def send_contact_reply_email(
    *, recipient: str, recipient_name: str, subject: str, body: str,
    admin_name: str, original_message: str, original_date: str,
) -> None:
    """Email an admin's reply to a contact-form submitter.

    Header values are collapsed to a single line before use (header injection),
    and the body is sent as plain text only.
    """
    clean_subject = " ".join(subject.split())[:200] or "Your message to SFMS"
    message = EmailMessage()
    message["Subject"] = clean_subject
    message["From"] = formataddr((settings.SMTP_FROM_NAME, _smtp_from_address()))
    message["To"] = recipient
    reply_to = (getattr(settings, "CONTACT_REPLY_TO", "") or "").strip()
    if reply_to:
        message["Reply-To"] = reply_to

    quoted = "\n".join(f"> {line}" for line in original_message[:2000].splitlines())
    message.set_content(
        f"Hi {recipient_name.strip() or 'there'},\n\n"
        f"{body.strip()}\n\n"
        f"-- {admin_name.strip() or 'SFMS Support'}\n"
        f"{settings.SMTP_FROM_NAME}\n\n"
        f"On {original_date} you wrote:\n{quoted}\n"
    )
    _send_message(message)
