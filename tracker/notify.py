# tracker/notify.py
import html
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.office365.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASSWORD = os.environ["SMTP_PASSWORD"]
NOTIFY_EMAIL = os.environ["NOTIFY_EMAIL"]


def send_email(subject: str, html_content: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Job Tracker <{SMTP_USER}>"
    msg["To"] = NOTIFY_EMAIL

    msg.attach(MIMEText(html_content, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, [NOTIFY_EMAIL], msg.as_string())


def alert_batch(company: str, jobs: list[dict], priority: int) -> None:
    if not jobs:
        return

    priority_label = "🔥 High Priority" if priority == 1 else "🚨 New Roles"
    subject = f"[{priority_label}] {company} ({len(jobs)} new roles)"

    job_rows = "".join(
        f"""
        <li style="margin-bottom: 10px;">
            <strong>{html.escape(str(j.get('title', 'Untitled role')))}</strong><br/>
            📍 Location: {html.escape(str(j.get('location') or '—'))}<br/>
            <a href="{html.escape(str(j.get('url', '')), quote=True)}" style="color: #0066cc;">Apply Here &rarr;</a>
        </li>
        """
        for j in jobs
    )

    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.5; color: #222;">
        <h2>{html.escape(company)}</h2>
        <p>Found <strong>{len(jobs)}</strong> new role(s):</p>
        <ul>{job_rows}</ul>
      </body>
    </html>
    """

    send_email(subject, html_content)
