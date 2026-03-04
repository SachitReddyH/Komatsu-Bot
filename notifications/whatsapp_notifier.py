"""
notifications/whatsapp_notifier.py
-----------------------------------
Sends WhatsApp alert messages via the CallMeBot free API.
No WhatsApp Business account needed – works with personal WhatsApp numbers.

── ONE-TIME SETUP (per recipient) ───────────────────────────────────────────
Each person who wants to receive alerts must activate their number once:

  1. Save this number in your WhatsApp contacts:  +34 644 60 32 01  (name it "CallMeBot")
  2. Send this exact message to that contact:
         I allow callmebot to send me messages
  3. You will receive a reply with your personal API key (e.g.  1234567)
  4. Add your phone number + API key to config.yaml under notifications.whatsapp.recipients

── config.yaml example ──────────────────────────────────────────────────────
  notifications:
    whatsapp:
      enabled: true
      recipients:
        - phone: "+61412345678"
          apikey: "1234567"
        - phone: "+61498765432"
          apikey: "7654321"

── API reference ─────────────────────────────────────────────────────────────
  https://www.callmebot.com/blog/free-api-whatsapp-messages/
"""

import logging
import time
import urllib.parse

import httpx

logger = logging.getLogger(__name__)

_CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php"


class WhatsAppNotifier:
    """Dispatches WhatsApp alerts to one or more recipients via CallMeBot."""

    def __init__(self, config: dict):
        """
        Args:
            config: the `notifications.whatsapp` sub-dict from config.yaml
        """
        self.enabled: bool = config.get("enabled", False)
        self.recipients: list[dict] = config.get("recipients", [])

    # ------------------------------------------------------------------

    def send_alert(self, target: dict, listing: dict) -> bool:
        """
        Send a WhatsApp alert to every configured recipient.
        Returns True if at least one message was delivered successfully.
        """
        if not self.enabled:
            return False

        valid = [
            r for r in self.recipients
            if r.get("phone", "").strip() and r.get("apikey", "").strip()
        ]
        if not valid:
            logger.warning(
                "WhatsApp enabled but no valid recipients configured "
                "(check notifications.whatsapp.recipients in config.yaml)"
            )
            return False

        message = _compose_message(target, listing)
        any_success = False

        for i, recipient in enumerate(valid):
            phone  = recipient["phone"].strip()
            apikey = recipient["apikey"].strip()

            ok = self._send_one(phone, apikey, message)
            if ok:
                any_success = True
            # Small delay between messages to respect CallMeBot rate limits
            if i < len(valid) - 1:
                time.sleep(1)

        return any_success

    # ------------------------------------------------------------------

    def _send_one(self, phone: str, apikey: str, message: str) -> bool:
        """
        Make a single HTTP call to the CallMeBot API.
        Returns True on HTTP 200, False otherwise.
        """
        params = {
            "phone":  phone,
            "text":   message,
            "apikey": apikey,
            "lang":   "en",
        }
        try:
            with httpx.Client(timeout=20, follow_redirects=True) as client:
                resp = client.get(_CALLMEBOT_URL, params=params)

            if resp.status_code == 200:
                logger.info("WhatsApp alert sent to %s", phone)
                return True

            logger.error(
                "WhatsApp failed for %s – HTTP %d: %s",
                phone, resp.status_code, resp.text[:200],
            )
            return False

        except httpx.TimeoutException:
            logger.error("WhatsApp timed out for %s", phone)
            return False
        except Exception as exc:
            logger.exception("WhatsApp unexpected error for %s: %s", phone, exc)
            return False


# ── Message composer ────────────────────────────────────────────────────────

def _compose_message(target: dict, listing: dict) -> str:
    """
    Build a clean, emoji-rich plain-text WhatsApp message.
    CallMeBot supports basic WhatsApp formatting:
      *bold*  _italic_
    """
    title    = listing.get("title", "N/A")
    price    = listing.get("price", "N/A")
    location = listing.get("location", "N/A")
    seller   = listing.get("seller_name", "N/A")
    phone    = listing.get("seller_phone", "N/A")
    url      = listing.get("detail_url", "")

    # --- Watch criteria line ---
    criteria_parts: list[str] = []
    y_min = target.get("year_min")
    y_max = target.get("year_max")
    p_min = target.get("price_min")
    p_max = target.get("price_max")

    if y_min and y_max:
        criteria_parts.append(f"Year {y_min}–{y_max}")
    elif y_min:
        criteria_parts.append(f"Year ≥{y_min}")
    elif y_max:
        criteria_parts.append(f"Year ≤{y_max}")

    if p_min and p_max:
        criteria_parts.append(f"${p_min:,}–${p_max:,}")
    elif p_max:
        criteria_parts.append(f"Budget ≤${p_max:,}")
    elif p_min:
        criteria_parts.append(f"Price ≥${p_min:,}")

    criteria_str = " | ".join(criteria_parts) if criteria_parts else "All listings"

    lines = [
        "🚨 *NEW KOMATSU LISTING FOUND!*",
        "",
        f"📌 *{title}*",
        f"💰 Price: *{price}*",
        f"📍 {location}",
        f"🏢 {seller}",
        f"📞 {phone}",
        "",
        f"🎯 Watching: {target.get('model', '')} | {criteria_str}",
    ]

    if url:
        lines += ["", f"🔗 {url}"]

    lines += ["", "_YANTRA LIVE Watcher Bot_"]

    return "\n".join(lines)
