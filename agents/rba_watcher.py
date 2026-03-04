"""
agents/rba_watcher.py  –  Agent: RB Auction Watcher
-----------------------------------------------------
Scrapes rbauction.com.au for "Bidding Open" auction events,
filters every lot against user-configured targets, and hands
new findings to the InformerAgent immediately.

Targets come from config.yaml under the key 'targets'
(shared with the Komatsu watcher so you only configure once).
"""

import asyncio
import logging

from rich.console import Console
from rich.rule import Rule

from bot.rba_scraper import (
    fetch_bidding_open_events,
    fetch_event_lots,
    filter_lots,
    format_lot,
)
from db.database import Database

logger  = logging.getLogger(__name__)
console = Console()


class RBAWatcherAgent:
    """
    Agent: RB Auction Watcher

    For each 'Bidding Open' event on rbauction.com.au it:
      1. Scrapes all lot items via Playwright (stealth mode)
      2. Filters by configured target models / year / price
      3. Detects lots not yet recorded in the local DB
      4. Passes NEW lots to InformerAgent immediately
    """

    def __init__(self, config: dict, db: Database, informer):
        self.config  = config
        self.db      = db
        self.informer = informer
        # Shares the same targets list as the Komatsu watcher
        self.targets: list[dict] = config.get("targets", [])

    # ──────────────────────────────────────────────────────────────────────────

    def run(self) -> list[dict]:
        """
        Execute one full RBA watch cycle.
        Returns a list of new listing dicts that were found and alerted.
        """
        return asyncio.run(self._async_run())

    async def _async_run(self) -> list[dict]:
        console.print(Rule("[bold magenta]RBA Watcher  •  Starting cycle[/bold magenta]"))

        if not self.targets:
            console.print("[yellow]No targets configured. "
                          "Add models via the dashboard.[/yellow]")
            return []

        # ── 1. Get all Bidding Open events ────────────────────────────────────
        console.print("[bold]Fetching Bidding Open events …[/bold]")
        events = await fetch_bidding_open_events()

        if not events:
            console.print("[yellow]  No Bidding Open events found right now.[/yellow]")
            self.db.log_rba_run([], 0, 0)
            return []

        console.print(
            f"  Found [white]{len(events)}[/white] Bidding Open event(s)"
        )
        for ev in events:
            console.print(f"    • [cyan]{ev['title']}[/cyan]  →  {ev['url']}")

        # ── 2. For each event, scrape lots and filter ─────────────────────────
        new_findings: list[dict]  = []
        models_checked: list[str] = [t.get("model", "") for t in self.targets]
        total_found   = 0

        for event in events:
            console.print(f"\n[bold]Event:[/bold] [cyan]{event['title']}[/cyan]")
            try:
                raw_lots = await fetch_event_lots(event["url"])
                console.print(
                    f"  Scraped [white]{len(raw_lots)}[/white] lots"
                )
                total_found += len(raw_lots)

                for target in self.targets:
                    model = (target.get("model") or "").strip()
                    if not model:
                        continue

                    matched = filter_lots(raw_lots, target)
                    if matched:
                        console.print(
                            f"  [bold green]{len(matched)} match(es)[/bold green] "
                            f"for target [yellow]{model}[/yellow]"
                        )

                    for lot in matched:
                        listing = format_lot(lot, event)
                        lot_id  = _make_lot_id(lot, event)

                        if self.db.is_seen_rba(lot_id):
                            console.print(
                                f"    [dim]Already seen: {listing['title']}[/dim]"
                            )
                        else:
                            console.print(
                                f"    [bold green]NEW:[/bold green] "
                                f"{listing['title']} – "
                                f"[green]{listing['current_bid']}[/green] "
                                f"@ {listing['location']}"
                            )
                            self.db.mark_seen_rba(lot_id, listing, notified=True)
                            # ── Notify immediately ────────────────────────────
                            self.informer.notify_rba(target, listing)
                            new_findings.append(listing)

            except Exception as exc:
                logger.exception("Error processing event '%s': %s", event["url"], exc)
                console.print(f"  [red]Error: {exc}[/red]")

        # ── 3. Summary ────────────────────────────────────────────────────────
        new_count = len(new_findings)
        self.db.log_rba_run(models_checked, total_found, new_count)

        console.print()
        if new_count:
            console.print(Rule(
                f"[bold green]✅  {new_count} new RBA lot(s) found and alerted[/bold green]"
            ))
        else:
            console.print(Rule("[dim]No new RBA lots this cycle[/dim]"))

        return new_findings

    # ──────────────────────────────────────────────────────────────────────────

    def seed_target(self, target: dict) -> int:
        """
        Fetch current lots for a single target and mark them as SEEN
        in the DB WITHOUT sending any notifications.

        Called when a new target is added from the dashboard so that:
          - Existing lots appear in the UI immediately.
          - Only truly NEW lots (found after this point) trigger alerts.
        """
        return asyncio.run(self._async_seed(target))

    async def _async_seed(self, target: dict) -> int:
        model = (target.get("model") or "").strip()
        if not model:
            return 0

        seeded = 0
        logger.info("RBA: Seeding lots for new target: %s", model)

        events = await fetch_bidding_open_events()
        for event in events:
            try:
                raw_lots = await fetch_event_lots(event["url"])
                matched  = filter_lots(raw_lots, target)
                for lot in matched:
                    listing = format_lot(lot, event)
                    lot_id  = _make_lot_id(lot, event)
                    if not self.db.is_seen_rba(lot_id):
                        # notified=True prevents duplicate alerts later
                        self.db.mark_seen_rba(lot_id, listing, notified=True)
                        seeded += 1
                        logger.info("RBA Seeded: %s", listing["title"])
            except Exception as exc:
                logger.exception("RBA seed error for event '%s': %s", event["url"], exc)

        logger.info("RBA seed complete for %s – %d lot(s) added", model, seeded)
        return seeded


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_lot_id(lot: dict, event: dict) -> str:
    """Build a stable unique ID for a lot: rba_<event_slug>_lot<number>."""
    event_slug = event.get("url", "").rstrip("/").split("/")[-1]
    lot_num    = lot.get("lot_number", "")
    title_slug = lot.get("title", "")[:30].lower()
    title_slug = "".join(c if c.isalnum() else "_" for c in title_slug).strip("_")

    if lot_num:
        return f"rba_{event_slug}_lot{lot_num}"
    # Fall back to title-based ID when lot number isn't available
    return f"rba_{event_slug}_{title_slug}"
