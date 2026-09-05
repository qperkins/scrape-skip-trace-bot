"""Email job results as CSV attachments."""

import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

DEFAULT_RECIPIENT = "niadennis1@outlook.com"


class EmailError(Exception):
    pass


def is_email_configured() -> bool:
    return bool(
        os.environ.get("SMTP_HOST")
        and os.environ.get("SMTP_USER")
        and os.environ.get("SMTP_PASSWORD")
    )


def send_results_email(
    *,
    csv_path: Path,
    subject: str,
    body: str,
    to: str | None = None,
) -> str:
    if not is_email_configured():
        raise EmailError("SMTP is not configured (set SMTP_HOST, SMTP_USER, SMTP_PASSWORD)")

    recipient = to or os.environ.get("RESULTS_EMAIL_TO", DEFAULT_RECIPIENT)
    sender = os.environ.get("SMTP_FROM") or os.environ["SMTP_USER"]
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    use_tls = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"

    message = MIMEMultipart()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message.attach(MIMEText(body, "plain"))

    attachment = MIMEApplication(csv_path.read_bytes(), Name=csv_path.name)
    attachment["Content-Disposition"] = f'attachment; filename="{csv_path.name}"'
    message.attach(attachment)

    with smtplib.SMTP(host, port, timeout=30) as server:
        if use_tls:
            server.starttls()
        server.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        server.sendmail(sender, [recipient], message.as_string())

    return recipient
