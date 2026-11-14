"""Transactional email helper.

Delivery order for every email:
  1. Primary  - HTTPS email API, sent from no-reply@<your domain> (EMAIL_FROM_ADDRESS)
  2. Fallback - classic SMTP (SMTP_* settings), used only if the primary is not
                configured or the API call fails.

Callers only deal with ``EmailDeliveryError``; infrastructure details are logged
server-side and never returned to users.
"""
from __future__ import annotations

import html
import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from urllib.parse import urlencode

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

BRAND_COLOR = "#0b63f6"


class EmailDeliveryError(RuntimeError):
    """Raised when a required transactional email cannot be delivered."""


@dataclass(frozen=True)
class OutgoingEmail:
    recipient: str
    subject: str
    text: str
    html: str | None = None
    reply_to: str | None = None


# --------------------------------------------------------------------------- #
# Transports
# --------------------------------------------------------------------------- #
def _api_configured() -> bool:
    return bool(settings.RESEND_API_KEY.strip() and settings.EMAIL_FROM_ADDRESS.strip())


def _smtp_from_address() -> str:
    return (settings.SMTP_FROM or settings.SMTP_USER).strip()


def _smtp_configured() -> bool:
    return bool(settings.SMTP_HOST.strip() and _smtp_from_address())


def _send_via_api(mail: OutgoingEmail) -> None:
    payload: dict = {
        "from": formataddr((settings.SMTP_FROM_NAME, settings.EMAIL_FROM_ADDRESS.strip())),
        "to": [mail.recipient],
        "subject": mail.subject,
        "text": mail.text,
    }
    if mail.html:
        payload["html"] = mail.html
    if mail.reply_to:
        payload["reply_to"] = mail.reply_to

    try:
        response = httpx.post(
            settings.RESEND_API_URL,
            json=payload,
            headers={"Authorization": f"Bearer {settings.RESEND_API_KEY.strip()}"},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise EmailDeliveryError(f"Email API unreachable: {exc}") from exc

    if response.status_code >= 400:
        raise EmailDeliveryError(
            f"Email API rejected the message (HTTP {response.status_code}): {response.text[:300]}"
        )


def _send_via_smtp(mail: OutgoingEmail) -> None:
    message = EmailMessage()
    message["Subject"] = mail.subject
    message["From"] = formataddr((settings.SMTP_FROM_NAME, _smtp_from_address()))
    message["To"] = mail.recipient
    if mail.reply_to:
        message["Reply-To"] = mail.reply_to
    message.set_content(mail.text)
    if mail.html:
        message.add_alternative(mail.html, subtype="html")

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
        raise EmailDeliveryError(f"SMTP delivery failed: {exc}") from exc


def _deliver(mail: OutgoingEmail) -> None:
    """Primary API first, SMTP as fallback."""
    if not _api_configured() and not _smtp_configured():
        raise EmailDeliveryError("Email service is not configured.")

    primary_error: EmailDeliveryError | None = None

    if _api_configured():
        try:
            _send_via_api(mail)
            return
        except EmailDeliveryError as exc:
            primary_error = exc
            if _smtp_configured():
                logger.warning("Primary email API failed, falling back to SMTP: %s", exc)
            else:
                logger.error("Primary email API failed and no SMTP fallback is configured: %s", exc)

    if _smtp_configured():
        try:
            _send_via_smtp(mail)
            return
        except EmailDeliveryError as exc:
            logger.error("SMTP fallback failed as well: %s", exc)
            raise EmailDeliveryError("Unable to deliver transactional email.") from exc

    raise EmailDeliveryError("Unable to deliver transactional email.") from primary_error


# --------------------------------------------------------------------------- #
# HTML layout (table based + inline CSS so it renders in Gmail/Outlook/Apple Mail)
# --------------------------------------------------------------------------- #
def _render_layout(
    *,
    preheader: str,
    heading: str,
    intro_html: str,
    button_label: str,
    button_url: str,
    notice_html: str,
    footer_html: str,
) -> str:
    esc = html.escape
    url = esc(button_url, quote=True)
    brand = esc(settings.SMTP_FROM_NAME)
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <meta name="color-scheme" content="light">
    <title>{esc(heading)}</title>
  </head>
  <body style="margin:0;padding:0;background:#f3f5f9;">
    <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">{esc(preheader)}</div>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f3f5f9;">
      <tr>
        <td align="center" style="padding:32px 16px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:560px;">
            <tr>
              <td align="center" style="padding:0 0 20px;font-family:Arial,Helvetica,sans-serif;font-size:22px;font-weight:700;letter-spacing:1px;color:#172033;">
                <span style="display:inline-block;background:{BRAND_COLOR};color:#ffffff;border-radius:8px;padding:6px 12px;">{brand}</span>
              </td>
            </tr>
            <tr>
              <td style="background:#ffffff;border-radius:16px;border:1px solid #e4e8f0;padding:36px 32px;font-family:Arial,Helvetica,sans-serif;color:#172033;font-size:16px;line-height:1.6;">
                <h1 style="margin:0 0 16px;font-size:24px;line-height:1.3;color:#172033;">{esc(heading)}</h1>
                {intro_html}
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:28px 0;">
                  <tr>
                    <td align="center" bgcolor="{BRAND_COLOR}" style="border-radius:10px;">
                      <a href="{url}" target="_blank" style="display:inline-block;padding:14px 28px;font-family:Arial,Helvetica,sans-serif;font-size:16px;font-weight:700;color:#ffffff;text-decoration:none;border-radius:10px;">{esc(button_label)}</a>
                    </td>
                  </tr>
                </table>
                <div style="background:#f6f8fc;border-left:4px solid {BRAND_COLOR};border-radius:6px;padding:12px 16px;font-size:14px;color:#475467;">{notice_html}</div>
                <p style="margin:24px 0 6px;font-size:13px;color:#667085;">Button not working? Copy and paste this link into your browser:</p>
                <p style="margin:0;font-size:13px;word-break:break-all;"><a href="{url}" style="color:{BRAND_COLOR};text-decoration:underline;">{url}</a></p>
              </td>
            </tr>
            <tr>
              <td align="center" style="padding:20px 16px 0;font-family:Arial,Helvetica,sans-serif;font-size:12px;line-height:1.6;color:#98a2b3;">
                {footer_html}<br>
                This is an automated message from {brand}, please do not reply to this email.
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""


# --------------------------------------------------------------------------- #
# Emails
# --------------------------------------------------------------------------- #
def send_verification_email(*, recipient: str, first_name: str, token: str) -> None:
    """Send a browser-facing verification link to the frontend.

    The frontend consumes the opaque token and calls the POST verification API.
    This keeps users inside the normal SFMS UI and ensures expired/invalid links
    render a friendly application page instead of a raw API JSON response.
    """
    base = settings.PUBLIC_FRONTEND_URL.rstrip("/")
    verification_url = f"{base}/verify-email?{urlencode({'token': token})}"
    name = first_name.strip() or "there"
    esc_name = html.escape(name)

    text = (
        f"Hi {name},\n\n"
        "Welcome to SFMS! Please verify your email address to activate your account:\n\n"
        f"{verification_url}\n\n"
        "This verification link expires in 24 hours.\n\n"
        "If you did not create this account, you can safely ignore this email.\n"
    )
    html_body = _render_layout(
        preheader="Confirm your email address to activate your SFMS account.",
        heading="Verify your email address",
        intro_html=(
            f'<p style="margin:0 0 12px;">Hi {esc_name},</p>'
            '<p style="margin:0;">Welcome to SFMS! Please confirm your email address to '
            "activate your account and sign in.</p>"
        ),
        button_label="Verify email",
        button_url=verification_url,
        notice_html="This link expires in <strong>24 hours</strong>.",
        footer_html="If you did not create an SFMS account, you can safely ignore this email.",
    )
    _deliver(OutgoingEmail(recipient, "Verify your SFMS email address", text, html_body))


def send_password_reset_email(*, recipient: str, first_name: str, token: str) -> None:
    """Send a password-reset link that opens the frontend reset page."""
    base = settings.PUBLIC_FRONTEND_URL.rstrip("/")
    path = "/" + settings.PASSWORD_RESET_PATH.strip().lstrip("/")
    reset_url = f"{base}{path}?{urlencode({'token': token})}"
    name = first_name.strip() or "there"
    esc_name = html.escape(name)

    text = (
        f"Hi {name},\n\n"
        "We received a request to reset the password for your SFMS account. "
        "Use the link below to choose a new password:\n\n"
        f"{reset_url}\n\n"
        "This link expires in 1 hour and can be used only once.\n\n"
        "If you did not request this, you can safely ignore this email - "
        "your password will not be changed.\n"
    )
    html_body = _render_layout(
        preheader="Use this link to choose a new SFMS password.",
        heading="Reset your password",
        intro_html=(
            f'<p style="margin:0 0 12px;">Hi {esc_name},</p>'
            '<p style="margin:0;">We received a request to reset the password for your '
            "SFMS account. Click the button below to choose a new password.</p>"
        ),
        button_label="Reset password",
        button_url=reset_url,
        notice_html=(
            "This link expires in <strong>1 hour</strong> and can be used only once. "
            "If you didn&rsquo;t request a reset, ignore this email &mdash; your password stays the same."
        ),
        footer_html="For your security, never share this link with anyone.",
    )
    _deliver(OutgoingEmail(recipient, "Reset your SFMS password", text, html_body))


def send_contact_reply_email(
    *, recipient: str, recipient_name: str, subject: str, body: str,
    admin_name: str, original_message: str, original_date: str,
) -> None:
    """Email an admin's reply to a contact-form submitter.

    Header values are collapsed to a single line before use (header injection),
    and the body is sent as plain text only.
    """
    clean_subject = " ".join(subject.split())[:200] or "Your message to SFMS"
    reply_to = (getattr(settings, "CONTACT_REPLY_TO", "") or "").strip() or None

    quoted = "\n".join(f"> {line}" for line in original_message[:2000].splitlines())
    text = (
        f"Hi {recipient_name.strip() or 'there'},\n\n"
        f"{body.strip()}\n\n"
        f"-- {admin_name.strip() or 'SFMS Support'}\n"
        f"{settings.SMTP_FROM_NAME}\n\n"
        f"On {original_date} you wrote:\n{quoted}\n"
    )
    _deliver(OutgoingEmail(recipient, clean_subject, text, None, reply_to))
