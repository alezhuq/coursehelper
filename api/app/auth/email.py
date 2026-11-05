"""Magic-link email dispatch via stdlib smtplib (dev: Mailpit).

A single plain-text email with a link is the entire requirement here, so
`smtplib` is used directly rather than pulling in a templating/attachment
library (fastapi-mail) that this project doesn't need.
"""

import asyncio
import smtplib
from email.message import EmailMessage

from app.settings import get_settings


async def send_magic_link_email(to: str, link: str) -> None:
    """Send the magic-link email over SMTP.

    Deliberately does not swallow SMTP errors: a relay/connection failure
    must surface as a request failure (mapped to 502 by the route), not a
    silently-dropped email that leaves the user waiting forever. Tests mock
    `smtplib.SMTP` (see `tests/conftest.py`) rather than requiring a real
    Mailpit process.
    """
    settings = get_settings()

    message = EmailMessage()
    message["Subject"] = "Sign in to Student Course Library"
    message["From"] = "no-reply@student-course-library.local"
    message["To"] = to
    message.set_content(
        f"Click the link below to sign in. This link expires in 15 minutes "
        f"and can only be used once.\n\n{link}\n"
    )

    def _send() -> None:
        # smtplib is synchronous; run it in a worker thread so the connect
        # timeout / send never blocks the event loop (and every concurrent
        # request with it).
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=5) as smtp:
            smtp.send_message(message)

    await asyncio.to_thread(_send)
