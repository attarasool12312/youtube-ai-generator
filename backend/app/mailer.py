from email.message import EmailMessage

import aiosmtplib

from app.config import settings


async def send_reset_email(to_email: str, reset_url: str) -> None:
    message = EmailMessage()
    message["From"] = settings.smtp_username
    message["To"] = to_email
    message["Subject"] = "Reset your YouTube AI Generator password"
    message.set_content(f"Open this link to reset your password:\n\n{reset_url}\n\nThis link expires in 1 hour.")

    await aiosmtplib.send(
        message,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_username,
        password=settings.smtp_password,
        start_tls=settings.smtp_use_tls,
    )
