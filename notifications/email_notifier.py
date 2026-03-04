"""
notifications/email_notifier.py
--------------------------------
Sends HTML email alerts when a new matching listing is found.

Credentials come from environment variables (set in .env):
  EMAIL_FROM      – sender Gmail address
  EMAIL_TO        – recipient address (can be comma-separated)
  EMAIL_PASSWORD  – Gmail App Password (NOT your account password)
"""

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


class EmailNotifier:
    def __init__(self, config: dict):
        self.smtp_server = config.get("smtp_server", "smtp.gmail.com")
        self.smtp_port = int(config.get("smtp_port", 587))
        self.from_email = os.getenv("EMAIL_FROM", config.get("from_email", "")).strip()
        self.to_email = os.getenv("EMAIL_TO", config.get("to_email", "")).strip()
        self.password = os.getenv("EMAIL_PASSWORD", "").strip()

    def _ready(self) -> bool:
        if not all([self.from_email, self.to_email, self.password]):
            logger.warning(
                "Email not configured – set EMAIL_FROM, EMAIL_TO, EMAIL_PASSWORD in .env"
            )
            return False
        return True

    def send_alert(self, target: dict, listing: dict):
        """Send a new-listing alert email."""
        if not self._ready():
            return

        subject = f"🚨 Komatsu Bot Alert: {listing['title']} is Available!"
        recipients = [r.strip() for r in self.to_email.split(",")]

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"Komatsu Watcher Bot <{self.from_email}>"
        msg["To"] = ", ".join(recipients)

        msg.attach(MIMEText(_plain_body(target, listing), "plain", "utf-8"))
        msg.attach(MIMEText(_html_body(target, listing), "html", "utf-8"))

        with smtplib.SMTP(self.smtp_server, self.smtp_port) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(self.from_email, self.password)
            smtp.sendmail(self.from_email, recipients, msg.as_string())

        logger.info("Alert email sent to %s", self.to_email)


# ---- Email body builders -------------------------------------------------

def _plain_body(target: dict, listing: dict) -> str:
    year_range = f"{target.get('year_min', 'Any')} – {target.get('year_max', 'Any')}"
    price_range = (
        f"${target.get('price_min', 'Any'):,}" if target.get("price_min") else "Any"
    ) + " – " + (
        f"${target.get('price_max', 'Any'):,}" if target.get("price_max") else "Any"
    )

    return f"""\
🚨 KOMATSU WATCHER BOT — NEW LISTING FOUND!
============================================

Model:    {listing['title']}
Price:    {listing['price']}
Dealer:   {listing['seller_name']}
Phone:    {listing['seller_phone']}
Location: {listing['location']}

Description:
{listing['short_description']}

-- LINKS --
View Listing : {listing['detail_url']}
Komatsu Page : {listing['komatsu_url']}

-- WATCH CRITERIA --
Target Model  : {target.get('model')}
Year Range    : {year_range}
Price Range   : {price_range}

Sent by Komatsu Watcher Bot
"""


def _html_body(target: dict, listing: dict) -> str:
    year_range = f"{target.get('year_min', 'Any')} – {target.get('year_max', 'Any')}"
    price_min_str = f"${target.get('price_min'):,}" if target.get("price_min") else "Any"
    price_max_str = f"${target.get('price_max'):,}" if target.get("price_max") else "Any"
    desc_html = (listing["short_description"] or "").replace("\n", "<br>")

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Komatsu Bot Alert</title></head>
<body style="margin:0;padding:0;background:#f0f2f5;font-family:Arial,Helvetica,sans-serif;">

<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f2f5;padding:30px 0;">
  <tr><td align="center">
  <table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.12);">

    <!-- Header -->
    <tr>
      <td style="background:#1a237e;padding:28px 32px;">
        <h1 style="margin:0;color:#fff;font-size:22px;">🤖 Komatsu Watcher Bot</h1>
        <p style="margin:6px 0 0;color:#9fa8da;font-size:14px;">New Matching Equipment Found!</p>
      </td>
    </tr>

    <!-- Alert banner -->
    <tr>
      <td style="background:#e65100;padding:14px 32px;text-align:center;">
        <span style="color:#fff;font-size:18px;font-weight:bold;">🚨 {listing['title']}</span>
      </td>
    </tr>

    <!-- Key details -->
    <tr>
      <td style="padding:28px 32px 0;">
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td style="padding:12px 16px;background:#fff8e1;border-radius:8px;text-align:center;">
              <div style="font-size:13px;color:#888;margin-bottom:4px;">ASKING PRICE</div>
              <div style="font-size:28px;font-weight:bold;color:#e65100;">{listing['price']}</div>
            </td>
          </tr>
        </table>
      </td>
    </tr>

    <tr>
      <td style="padding:20px 32px 0;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
          {_row("🏢 Dealer", listing["seller_name"])}
          {_row("📞 Call Dealer", f'<a href="tel:{listing["seller_phone"]}" style="color:#1a237e;font-weight:bold;">{listing["seller_phone"]}</a>')}
          {_row("📍 Location", listing["location"])}
          {_row("🏷️ Category", listing.get("category_type","") + (" – " + listing.get("category_subtype","") if listing.get("category_subtype") else ""))}
        </table>
      </td>
    </tr>

    <!-- Description -->
    <tr>
      <td style="padding:20px 32px 0;">
        <div style="background:#f5f5f5;border-left:4px solid #e65100;padding:16px;border-radius:0 8px 8px 0;">
          <h3 style="margin:0 0 10px;color:#333;font-size:14px;text-transform:uppercase;letter-spacing:.5px;">Dealer Description</h3>
          <p style="margin:0;color:#555;font-size:13px;line-height:1.6;">{desc_html}</p>
        </div>
      </td>
    </tr>

    <!-- CTA buttons -->
    <tr>
      <td style="padding:28px 32px;text-align:center;">
        <a href="{listing['detail_url']}"
           style="display:inline-block;padding:14px 28px;background:#e65100;color:#fff;
                  text-decoration:none;border-radius:6px;font-weight:bold;font-size:15px;margin:4px;">
          View Listing &amp; Enquire
        </a>
        <a href="{listing['komatsu_url']}"
           style="display:inline-block;padding:14px 28px;background:#1a237e;color:#fff;
                  text-decoration:none;border-radius:6px;font-weight:bold;font-size:15px;margin:4px;">
          Komatsu Website
        </a>
      </td>
    </tr>

    <!-- Watch criteria footer -->
    <tr>
      <td style="background:#f5f5f5;padding:16px 32px;border-top:1px solid #eee;">
        <p style="margin:0;font-size:12px;color:#999;text-align:center;">
          Watching for: <strong>{target.get('model')}</strong> &nbsp;|&nbsp;
          Year: <strong>{year_range}</strong> &nbsp;|&nbsp;
          Price: <strong>{price_min_str} – {price_max_str}</strong>
          <br>Komatsu Watcher Bot &nbsp;•&nbsp; YANTRA LIVE
        </p>
      </td>
    </tr>

  </table>
  </td></tr>
</table>

</body>
</html>
"""


def _row(label: str, value: str) -> str:
    return (
        f'<tr style="border-bottom:1px solid #f0f0f0;">'
        f'<td style="padding:10px 8px;color:#888;font-size:13px;width:35%;vertical-align:top;">{label}</td>'
        f'<td style="padding:10px 8px;color:#333;font-size:14px;font-weight:500;">{value}</td>'
        f"</tr>"
    )
