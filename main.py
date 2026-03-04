"""
main.py  –  Komatsu Watcher Bot  •  Entry Point
================================================

Commands
--------
  python main.py watch      Start the watcher (runs every hour automatically)
  python main.py check      Run one check right now
  python main.py list       Show all listings seen so far
  python main.py enquiry    Auto-fill the enquiry form for a specific listing
  python main.py history    Show recent watcher run history

Quick start
-----------
  1.  cp .env.example .env   and fill in your email credentials
  2.  Edit config.yaml       to add the model(s) you want to watch
  3.  python main.py watch   to start the bot
"""

import argparse
import io
import logging
import os
import sys
from pathlib import Path

# Force UTF-8 on Windows so emoji in Rich output doesn't crash
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import yaml
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Load .env before importing anything that reads env vars
load_dotenv()

from agents.informer import InformerAgent
from agents.watcher import WatcherAgent
from bot.enquiry import run_enquiry
from db.database import Database

# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)-8s]  %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("komatsu_bot.log", encoding="utf-8"),
    ],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)
console = Console()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def load_config(path: str = "config.yaml") -> dict:
    cfg_path = Path(path)
    if not cfg_path.exists():
        console.print(f"[red]Config file not found: {path}[/red]")
        sys.exit(1)
    with cfg_path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def print_banner():
    console.print(
        Panel.fit(
            "[bold yellow]🤖  KOMATSU WATCHER BOT[/bold yellow]\n"
            "[dim]Agent 1 (Watcher)  +  Agent 2 (Informer)[/dim]\n"
            "[dim]Watching komatsu.com.au/equipment/used-equipment[/dim]",
            border_style="yellow",
        )
    )


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_watch(config: dict):
    """Start the scheduler – checks every N minutes (default 60)."""
    from apscheduler.schedulers.blocking import BlockingScheduler

    db = Database()
    informer = InformerAgent(config)
    watcher = WatcherAgent(config, db, informer)
    interval = int((config.get("watcher") or {}).get("interval_minutes", 60))

    targets = [t.get("model", "?") for t in config.get("targets", [])]
    console.print(f"[green]Watcher started.[/green]  Interval: [cyan]{interval} min[/cyan]")
    console.print(f"Targets: [cyan]{', '.join(targets)}[/cyan]")
    console.print("[dim]Press Ctrl-C to stop.[/dim]\n")

    # Run immediately, then on schedule
    watcher.run()

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(watcher.run, "interval", minutes=interval, id="watch_job")

    try:
        console.print(f"\n[blue]Next check in {interval} minute(s)…[/blue]")
        scheduler.start()
    except KeyboardInterrupt:
        scheduler.shutdown(wait=False)
        console.print("\n[yellow]Watcher stopped.[/yellow]")


def cmd_check(config: dict):
    """Run exactly one check cycle right now."""
    db = Database()
    informer = InformerAgent(config)
    watcher = WatcherAgent(config, db, informer)
    watcher.run()


def cmd_list(config: dict):
    """Print a table of every seen listing."""
    db = Database()
    seen = db.get_all_seen()

    if not seen:
        console.print("[yellow]No listings recorded yet. Run: python main.py check[/yellow]")
        return

    t = Table(title="Seen Listings", show_lines=True)
    t.add_column("ID",       style="dim",   no_wrap=True)
    t.add_column("Title",    style="bold",  min_width=28)
    t.add_column("Price",    style="green", no_wrap=True)
    t.add_column("Location", min_width=18)
    t.add_column("Phone",    style="cyan",  no_wrap=True)
    t.add_column("First Seen", style="dim", no_wrap=True)

    for item in seen:
        d = item["data"]
        t.add_row(
            d.get("id", ""),
            d.get("title", ""),
            d.get("price", ""),
            d.get("location", ""),
            d.get("seller_phone", ""),
            item["first_seen"][:16],
        )

    console.print(t)
    console.print(f"\nTotal: [bold]{len(seen)}[/bold] listing(s) on record.")


def cmd_history(config: dict):
    """Print recent watcher run history."""
    db = Database()
    runs = db.get_recent_runs(limit=20)

    if not runs:
        console.print("[yellow]No runs recorded yet.[/yellow]")
        return

    t = Table(title="Watcher Run History", show_lines=True)
    t.add_column("Timestamp",    style="dim")
    t.add_column("Models",       style="cyan")
    t.add_column("Total Found",  justify="right")
    t.add_column("New Found",    justify="right", style="green")

    for r in runs:
        t.add_row(
            r["timestamp"][:16],
            r["models"] or "",
            str(r["total_found"]),
            str(r["new_found"]),
        )

    console.print(t)


def cmd_enquiry(
    config: dict,
    listing_id: str,
    phone: str,
    email: str,
    message: str,
    auto_submit: bool,
):
    """Open browser, navigate to listing, and auto-fill the enquiry form."""
    db = Database()
    record = db.get_listing(listing_id)

    if not record:
        console.print(
            f"[red]Listing ID '{listing_id}' not found in local database.[/red]\n"
            "Run [cyan]python main.py list[/cyan] to see available IDs."
        )
        return

    listing = record["data"]
    enquiry_cfg = config.get("enquiry") or {}

    # Name is always YANTRA LIVE
    name = enquiry_cfg.get("company_name", "YANTRA LIVE")

    # Phone / email: CLI flag > .env > config.yaml > interactive prompt
    phone = (
        phone
        or os.getenv("ENQUIRY_PHONE", "")
        or enquiry_cfg.get("phone", "")
        or console.input("[yellow]Enter your phone number: [/yellow]")
    )
    email = (
        email
        or os.getenv("ENQUIRY_EMAIL", "")
        or enquiry_cfg.get("email", "")
        or console.input("[yellow]Enter your email address: [/yellow]")
    )
    if not message:
        message = console.input(
            "[yellow]Enter your message (optional – press Enter to skip): [/yellow]"
        )

    console.print(
        Panel.fit(
            f"[bold]Enquiry for:[/bold] [cyan]{listing['title']}[/cyan]\n"
            f"[bold]Name:[/bold]  {name}\n"
            f"[bold]Phone:[/bold] {phone}\n"
            f"[bold]Email:[/bold] {email}",
            title="About to fill enquiry form",
            border_style="blue",
        )
    )

    success = run_enquiry(
        detail_url=listing["detail_url"],
        name=name,
        phone=phone,
        email=email,
        message=message,
        listing_info=listing,
        auto_submit=auto_submit,
    )

    if success:
        console.print("[bold green]✅  Enquiry complete.[/bold green]")
    else:
        console.print("[bold red]❌  Enquiry was not sent.[/bold red]")


# --------------------------------------------------------------------------
# CLI parser
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="komatsu-bot",
        description="Komatsu Used Equipment Watcher & Enquiry Bot",
    )
    p.add_argument("--config", default="config.yaml", metavar="FILE",
                   help="Path to config YAML (default: config.yaml)")

    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    # watch
    sub.add_parser("watch",   help="Start the watcher – checks every hour automatically")

    # check
    sub.add_parser("check",   help="Run a single check right now")

    # list
    sub.add_parser("list",    help="Show all listings seen so far")

    # history
    sub.add_parser("history", help="Show recent watcher run log")

    # enquiry
    eq = sub.add_parser("enquiry", help="Auto-fill enquiry form for a listing")
    eq.add_argument("listing_id",   help="Listing ID shown by 'list' command")
    eq.add_argument("--phone",      default="", help="Your phone number")
    eq.add_argument("--email",      default="", help="Your email address")
    eq.add_argument("--message",    default="", help="Custom message to include")
    eq.add_argument("--auto-submit", dest="auto_submit", action="store_true",
                    help="Click SEND ENQUIRY automatically (no manual confirmation)")

    return p


def main():
    print_banner()
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    config = load_config(args.config)

    dispatch = {
        "watch":   lambda: cmd_watch(config),
        "check":   lambda: cmd_check(config),
        "list":    lambda: cmd_list(config),
        "history": lambda: cmd_history(config),
        "enquiry": lambda: cmd_enquiry(
            config=config,
            listing_id=args.listing_id,
            phone=args.phone,
            email=args.email,
            message=args.message,
            auto_submit=args.auto_submit,
        ),
    }

    dispatch[args.command]()


if __name__ == "__main__":
    main()
