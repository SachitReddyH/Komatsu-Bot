"""
agents/informer.py  –  Agent 2 : Informer
------------------------------------------
Receives a new listing dict from the Watcher and dispatches alerts.

Currently supports:
  • Rich terminal output (always on)
  • Email via Gmail SMTP      (configure in .env + config.yaml)
  • WhatsApp via CallMeBot    (configure in config.yaml)
"""

import logging

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from notifications.email_notifier import EmailNotifier
from notifications.whatsapp_notifier import WhatsAppNotifier

logger = logging.getLogger(__name__)
console = Console()


class InformerAgent:
    """Agent 2: receives new listing findings and notifies the team."""

    def __init__(self, config: dict):
        self.config = config
        notif_cfg = config.get("notifications", {})

        # Email
        email_cfg = notif_cfg.get("email", {})
        self._email: EmailNotifier | None = None
        if email_cfg.get("enabled", False):
            self._email = EmailNotifier(email_cfg)
            logger.info("Email notifications enabled")

        # WhatsApp
        wa_cfg = notif_cfg.get("whatsapp", {})
        self._whatsapp: WhatsAppNotifier | None = None
        if wa_cfg.get("enabled", False):
            self._whatsapp = WhatsAppNotifier(wa_cfg)
            logger.info(
                "WhatsApp notifications enabled – %d recipient(s)",
                len(wa_cfg.get("recipients", [])),
            )

    # ------------------------------------------------------------------

    def notify(self, target: dict, listing: dict):
        """
        Dispatch a notification for one new listing.
        Always prints to terminal; sends email and/or WhatsApp if configured.
        """
        self._print_alert(target, listing)

        if self._email:
            try:
                self._email.send_alert(target, listing)
                console.print("[green]✉️  Email alert sent![/green]")
            except Exception as exc:
                logger.error("Email send failed: %s", exc)
                console.print(f"[red]✉️  Email failed: {exc}[/red]")

        if self._whatsapp:
            try:
                ok = self._whatsapp.send_alert(target, listing)
                if ok:
                    console.print("[green]💬  WhatsApp alert sent![/green]")
                else:
                    console.print("[yellow]💬  WhatsApp: delivery failed (check logs)[/yellow]")
            except Exception as exc:
                logger.error("WhatsApp send failed: %s", exc)
                console.print(f"[red]💬  WhatsApp failed: {exc}[/red]")

    # ------------------------------------------------------------------

    def _print_alert(self, target: dict, listing: dict):
        # Top-level panel
        console.print()
        console.print(
            Panel.fit(
                f"[bold white]🚨  NEW LISTING FOUND![/bold white]\n"
                f"[bold yellow]{listing['title']}[/bold yellow]",
                border_style="bright_red",
                title="[bold red]Agent 2 – Informer[/bold red]",
            )
        )

        # Details table
        t = Table(show_header=False, box=None, padding=(0, 2))
        t.add_column(style="dim", width=14)
        t.add_column(style="bold")

        t.add_row("Price",    f"[green]{listing['price']}[/green]")
        t.add_row("Dealer",   listing["seller_name"])
        t.add_row("Phone",    f"[cyan]{listing['seller_phone']}[/cyan]")
        t.add_row("Location", listing["location"])
        t.add_row("URL",      f"[blue underline]{listing['detail_url']}[/blue underline]")

        console.print(t)
        console.print()
