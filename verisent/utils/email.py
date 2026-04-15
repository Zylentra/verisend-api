"""
Email service for sending transactional emails via SMTP (Brevo).
"""

import logging

import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from verisend.settings import settings

logger = logging.getLogger(__name__)


async def send_magic_link_email(to_email: str, magic_link: str) -> None:
    """Send magic link email"""
    message = MIMEMultipart("alternative")
    message["Subject"] = "Login to Zylentra"
    message["From"] = f"Zylentra <{settings.smtp_from}>"
    message["To"] = to_email

    text = f"""
Hi,

Click the link below to login:

{magic_link}

This link expires in 15 minutes.

If you didn't request this, ignore this email.
    """

    html = f"""
<html>
  <body>
    <h2>Login to Zylentra</h2>
    <p>Click the button below to login:</p>
    <p>
      <a href="{magic_link}"
         style="background-color: #0066cc; color: white; padding: 12px 24px;
                text-decoration: none; border-radius: 4px; display: inline-block;">
        Login Now
      </a>
    </p>
    <p>Or copy this link: <br><code>{magic_link}</code></p>
    <p style="color: #666; font-size: 12px;">
      This link expires in 15 minutes.
    </p>
  </body>
</html>
    """

    message.attach(MIMEText(text, "plain"))
    message.attach(MIMEText(html, "html"))

    await aiosmtplib.send(
        message,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user,
        password=settings.smtp_password.get_secret_value(),
        start_tls=True,
    )
