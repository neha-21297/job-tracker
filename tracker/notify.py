import html
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", "")


def send_email(subject: str, html_content: str) -> None:
  if not SMTP_USER or not SMTP_PASS or not NOTIFY_EMAIL:
    print("Email credentials missing — skipping email alert.")
    return

  msg = MIMEMultipart("alternative")
  msg["Subject"] = subject
  msg["From"] = f"Strata Job Tracker <{SMTP_USER}>"
  msg["To"] = NOTIFY_EMAIL

  part = MIMEText(html_content, "html")
  msg.attach(part)

  try:
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
      server.starttls()
      server.login(SMTP_USER, SMTP_PASS)
      server.sendmail(SMTP_USER, NOTIFY_EMAIL, msg.as_string())
    print(f"Email notification sent to {NOTIFY_EMAIL}")
  except Exception as e:
    print(f"Failed to send email alert: {e}")


def alert_batch(company: str, jobs: list) -> None:
  if not jobs:
    return

  subject = f"🚨 New Graduate Scheme Alert: {company} ({len(jobs)} new role{'s' if len(jobs) > 1 else ''})"

  items_html = ""
  for j in jobs:
    title = html.escape(j.get("title", "Graduate Role"))
    location = html.escape(j.get("location") or "UK / Hybrid")
    url = j.get("url", "#")
    items_html += f"""
    <div style="margin-bottom: 16px; padding: 12px; background: #f9fafb; border-left: 4px solid #0f766e; border-radius: 4px;">
      <h3 style="margin: 0 0 4px; color: #111827; font-size: 15px;">{title}</h3>
      <p style="margin: 0 0 8px; color: #4b5563; font-size: 13px;">📍 <strong>Location:</strong> {location}</p>
      <a href="{url}" style="display: inline-block; padding: 6px 12px; background: #0f766e; color: #ffffff; text-decoration: none; border-radius: 4px; font-size: 12px; font-weight: bold;">Apply Now →</a>
    </div>
    """

  body_html = f"""
  <!DOCTYPE html>
  <html>
  <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.5; color: #111827; max-width: 600px; margin: 0 auto; padding: 20px;">
    <h2 style="color: #0f766e; margin-bottom: 4px;">Strata Job Tracker Alert</h2>
    <p style="color: #4b5563; margin-top: 0;">New graduate/early-career openings were detected for <strong>{html.escape(company)}</strong>:</p>
    {items_html}
    <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 20px 0;" />
    <p style="font-size: 11px; color: #9ca3af;">Sent automatically by your £0 GitHub Actions Job Tracker.</p>
  </body>
  </html>
  """

  send_email(subject, body_html)