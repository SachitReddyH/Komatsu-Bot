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

    def notify_rba(self, target: dict, listing: dict):
        """
        Dispatch a notification for a new RB Auction lot.
        Always prints to terminal; sends email and/or WhatsApp if configured.
        """
        self._print_rba_alert(target, listing)

        if self._email:
            try:
                self._email.send_rba_alert(target, listing)
                console.print("[green]✉️  RBA Email alert sent![/green]")
            except Exception as exc:
                logger.error("RBA Email send failed: %s", exc)
                console.print(f"[red]✉️  RBA Email failed: {exc}[/red]")

        if self._whatsapp:
            try:
                ok = self._whatsapp.send_rba_alert(target, listing)
                if ok:
                    console.print("[green]💬  RBA WhatsApp alert sent![/green]")
                else:
                    console.print("[yellow]💬  RBA WhatsApp: delivery failed (check logs)[/yellow]")
            except Exception as exc:
                logger.error("RBA WhatsApp send failed: %s", exc)
                console.print(f"[red]💬  RBA WhatsApp failed: {exc}[/red]")

    # ------------------------------------------------------------------

    def _print_rba_alert(self, target: dict, listing: dict):
        """Rich terminal output for an RBA auction lot alert."""
        console.print()
        console.print(
            Panel.fit(
                f"[bold white]🔨  NEW AUCTION LOT FOUND![/bold white]\n"
                f"[bold yellow]{listing['title']}[/bold yellow]\n"
                f"[dim]via RB Auction  •  {listing.get('auction_event', '')}[/dim]",
                border_style="bright_magenta",
                title="[bold magenta]RBA Watcher – Auction Alert[/bold magenta]",
            )
        )

        t = Table(show_header=False, box=None, padding=(0, 2))
        t.add_column(style="dim", width=16)
        t.add_column(style="bold")

        t.add_row("Current Bid",  f"[green]{listing.get('current_bid', 'N/A')}[/green]")
        t.add_row("Lot #",        listing.get("lot_number", "N/A"))
        t.add_row("Year",         listing.get("year", "N/A"))
        t.add_row("Hours",        listing.get("hours", "N/A"))
        t.add_row("Location",     listing.get("location", "N/A"))
        t.add_row("Auction",      listing.get("auction_event", "N/A"))
        t.add_row("URL",          f"[blue underline]{listing.get('detail_url', '')}[/blue underline]")
        if listing.get("image_url"):
            t.add_row("Image",    f"[blue underline]{listing['image_url']}[/blue underline]")

        console.print(t)
        console.print()

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
