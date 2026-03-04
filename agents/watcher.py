"""
agents/watcher.py  –  Agent 1 : Watcher
-----------------------------------------
Scrapes the Komatsu Used Equipment page (powered by tradeearthmovers.com.au)
for every configured target model, compares with the local DB to find NEW
listings, and hands them off to the Informer agent.
"""

import logging

import httpx
from rich.console import Console
from rich.rule import Rule

from agents.informer import InformerAgent
from bot.scraper import fetch_listings, filter_listings, format_listing
from db.database import Database

logger = logging.getLogger(__name__)
console = Console()


class WatcherAgent:
    """
    Agent 1: periodically fetches and filters equipment listings.

    For each configured target it:
      1. Fetches all matching pages from tradeearthmovers iframe
      2. Applies model / year / price filters
      3. Detects listings not yet in the local DB
      4. Passes new listings to InformerAgent
    """

    def __init__(self, config: dict, db: Database, informer: InformerAgent):
        self.config = config
        self.db = db
        self.informer = informer
        self.targets: list[dict] = config.get("targets", [])

    # ------------------------------------------------------------------

    def run(self) -> list[tuple[dict, dict]]:
        """
        Execute one full watch cycle.

        Returns a list of (target, formatted_listing) tuples for newly
        discovered listings (useful for testing / scripting).
        """
        console.print(Rule("[bold blue]Agent 1 – Watcher  •  Starting cycle[/bold blue]"))

        new_findings: list[tuple[dict, dict]] = []
        models_checked: list[str] = []
        total_found = 0

        if not self.targets:
            console.print("[yellow]No targets configured. Edit config.yaml and add models to watch.[/yellow]")
            return new_findings

        with httpx.Client(follow_redirects=True, timeout=30) as client:
            for target in self.targets:
                model = (target.get("model") or "").strip()
                if not model:
                    console.print("[yellow]Skipping target with empty model.[/yellow]")
                    continue

                models_checked.append(model)
                console.print(f"\n[bold]Searching:[/bold] [cyan]{model}[/cyan]")

                try:
                    raw = fetch_listings(model, client)
                    matched = filter_listings(
                        raw,
                        model=model,
                        year_min=target.get("year_min"),
                        year_max=target.get("year_max"),
                        price_min=target.get("price_min"),
                        price_max=target.get("price_max"),
                    )

                    console.print(
                        f"  Fetched [white]{len(raw)}[/white] results, "
                        f"[white]{len(matched)}[/white] matched filters"
                    )
                    total_found += len(matched)

                    for raw_item in matched:
                        listing = format_listing(raw_item)
                        lid = listing["id"]

                        if self.db.is_seen(lid):
                            console.print(f"  [dim]Already seen: {listing['title']}[/dim]")
                        else:
                            console.print(
                                f"  [bold green]NEW:[/bold green] {listing['title']} "
                                f"– {listing['price']} @ {listing['location']}"
                            )
                            self.db.mark_seen(lid, listing, notified=True)
                            new_findings.append((target, listing))
                            self.informer.notify(target, listing)

                    if not matched:
                        console.print(f"  [dim]No listings found for {model} right now.[/dim]")

                except Exception as exc:
                    logger.exception("Error while fetching '%s': %s", model, exc)
                    console.print(f"  [red]Error: {exc}[/red]")

        # --- Summary ---

        new_count = len(new_findings)
        self.db.log_run(models_checked, total_found, new_count)

        console.print()
        if new_count:
            console.print(
                Rule(f"[bold green]✅  {new_count} new listing(s) found and alerted[/bold green]")
            )
        else:
            console.print(Rule("[dim]No new listings this cycle[/dim]"))

        return new_findings

    # ------------------------------------------------------------------

    def seed_target(self, target: dict) -> int:
        """
        Fetch current listings for a single target and mark them as SEEN
        in the DB without sending any notifications.

        Called when a new target is added from the dashboard so that:
          - Existing listings appear in the UI immediately.
          - The hourly watcher will NOT re-alert for pre-existing listings
            (notified=True prevents duplicate notifications).
          - Only truly NEW listings that appear later will trigger an alert.

        Returns the count of listings newly seeded.
        """
        model = (target.get("model") or "").strip()
        if not model:
            return 0

        seeded = 0
        logger.info("Seeding listings for new target: %s", model)

        with httpx.Client(follow_redirects=True, timeout=30) as client:
            try:
                raw = fetch_listings(model, client)
                matched = filter_listings(
                    raw,
                    model=model,
                    year_min=target.get("year_min"),
                    year_max=target.get("year_max"),
                    price_min=target.get("price_min"),
                    price_max=target.get("price_max"),
                )
                for raw_item in matched:
                    listing = format_listing(raw_item)
                    if not self.db.is_seen(listing["id"]):
                        # notified=True suppresses future duplicate alerts
                        self.db.mark_seen(listing["id"], listing, notified=True)
                        seeded += 1
                        logger.info("Seeded: %s", listing["title"])
            except Exception as exc:
                logger.exception("Seed failed for '%s': %s", model, exc)

        logger.info("Seed complete for %s – %d listing(s) added", model, seeded)
        return seeded
