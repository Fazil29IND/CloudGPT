"""Transactional email service for CloudGPT.

Sends email asynchronously via aiosmtplib when EMAIL_ENABLED=true. When email
is disabled (default in development) the rendered message is logged instead so
flows such as password reset remain testable end-to-end without an SMTP server.
"""

from __future__ import annotations

import logging
from email.message import EmailMessage

from config import get_settings

logger = logging.getLogger(__name__)


class EmailService:
    """Async SMTP email sender with a log-only development fallback."""

    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.email_enabled and self.settings.smtp_host)

    async def send_email(
        self,
        to: str,
        subject: str,
        html_body: str,
        text_body: str | None = None,
    ) -> bool:
        """Send an email. Returns True when dispatched, False when skipped.

        Never raises for SMTP failures — delivery problems are logged with the
        recipient and subject so callers (password reset) can stay silent about
        account existence.
        """
        text_body = text_body or _strip_html(html_body)

        if not self.enabled:
            logger.info(
                "Email disabled (EMAIL_ENABLED=false) — would send to=%s subject=%r\n%s",
                to,
                subject,
                text_body,
            )
            return False

        message = EmailMessage()
        message["From"] = self.settings.smtp_from_email
        message["To"] = to
        message["Subject"] = subject
        message.set_content(text_body)
        message.add_alternative(html_body, subtype="html")

        try:
            import aiosmtplib

            await aiosmtplib.send(
                message,
                hostname=self.settings.smtp_host,
                port=self.settings.smtp_port,
                username=self.settings.smtp_user or None,
                password=self.settings.smtp_password,
                start_tls=self.settings.smtp_tls,
            )
            logger.info("Email sent", extra={"to": to, "subject": subject})
            return True
        except Exception:
            logger.exception("Failed to send email to=%s subject=%r", to, subject)
            return False


def _strip_html(html: str) -> str:
    """Crude HTML→text fallback for the plain-text alternative part."""
    import re

    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def render_password_reset_email(reset_url: str, expires_minutes: int) -> tuple[str, str]:
    """Render (html_body, text_body) for a password reset email."""
    html = f"""\
<!doctype html>
<html>
  <body style="font-family: Arial, sans-serif; color: #1a1a2e; max-width: 560px; margin: 0 auto;">
    <h2 style="color: #16213e;">Reset your CloudGPT password</h2>
    <p>We received a request to reset the password for your CloudGPT account.</p>
    <p>
      <a href="{reset_url}"
         style="display: inline-block; background: #0f3460; color: #ffffff; padding: 12px 24px;
                border-radius: 6px; text-decoration: none; font-weight: bold;">
        Reset password
      </a>
    </p>
    <p>Or paste this link into your browser:</p>
    <p style="word-break: break-all;"><a href="{reset_url}">{reset_url}</a></p>
    <p style="color: #555;">This link expires in {expires_minutes} minutes and can be used only once.
       If you did not request a reset, you can safely ignore this email — your password is unchanged.</p>
  </body>
</html>
"""
    text = f"""\
Reset your CloudGPT password

We received a request to reset the password for your CloudGPT account.

Open this link to choose a new password (expires in {expires_minutes} minutes, single use):

{reset_url}

If you did not request a reset, you can safely ignore this email — your password is unchanged.
"""
    return html, text


def render_verification_email(code: str, verify_url: str, expires_minutes: int = 15) -> tuple[str, str]:
    """Render (html_body, text_body) for an email address verification message."""
    html = f"""<!doctype html>
<html>
  <body style="font-family: Arial, sans-serif; background-color: #121212; color: #e0e0e0; padding: 24px; margin: 0;">
    <div style="max-width: 520px; margin: 0 auto; background: #1e1e1e; border: 1px solid #3a3a3a; border-radius: 12px; padding: 32px;">
      <h2 style="color: #ffffff; margin-top: 0;">Verify your CloudGPT Account</h2>
      <p style="color: #9ca3af; font-size: 15px; line-height: 1.5;">
        Thank you for signing up for CloudGPT. Please enter the following 6-digit verification code to complete your registration:
      </p>
      <div style="margin: 28px 0; text-align: center;">
        <div style="display: inline-block; font-size: 32px; font-weight: 700; letter-spacing: 8px; color: #ffffff; background: #2a2a2a; border: 1px solid #4b4b4b; border-radius: 8px; padding: 12px 24px;">
          {code}
        </div>
      </div>
      <p style="color: #9ca3af; font-size: 14px; text-align: center;">
        Or verify instantly by clicking the button below:
      </p>
      <div style="text-align: center; margin: 20px 0;">
        <a href="{verify_url}" style="display: inline-block; background: #ffffff; color: #111827; font-weight: 600; padding: 12px 28px; border-radius: 8px; text-decoration: none;">
          Verify Email
        </a>
      </div>
      <p style="color: #6b7280; font-size: 13px; margin-top: 24px; border-top: 1px solid #3a3a3a; padding-top: 16px;">
        This code expires in {expires_minutes} minutes. If you did not create an account, you can safely disregard this email.
      </p>
    </div>
  </body>
</html>"""
    text = f"""Verify your CloudGPT Account

Thank you for signing up for CloudGPT.

Your 6-digit verification code is: {code}

Or verify directly by visiting:
{verify_url}

This code expires in {expires_minutes} minutes. If you did not create an account, you can safely disregard this email.
"""
    return html, text


email_service = EmailService()
