"""
bot/enquiry.py
--------------
Playwright-powered form filler for the dealer enquiry form.

Detail page URL pattern:
  https://iframe.tradeearthmovers.com.au/externaldetail/
  dd74cd3a-7423-41a3-8227-e1a92b01925a/<model-slug>-<id>

Enquiry form fields (all required by the site):
  • Name
  • Phone
  • Email Address
  • Your Message
  → SEND ENQUIRY button
"""

import asyncio
import logging
from typing import Optional

from playwright.async_api import Page, async_playwright
from rich.console import Console
from rich.panel import Panel

logger = logging.getLogger(__name__)
console = Console()


# --------------------------------------------------------------------------

async def _fill_and_submit(
    page: Page,
    name: str,
    phone: str,
    email: str,
    message: str,
    auto_submit: bool,
) -> bool:
    """Internal coroutine – assumes the detail page is already loaded."""

    # Wait for the enquiry form to be visible
    await page.wait_for_selector('input[placeholder="Name"]', timeout=15_000)

    console.print("[yellow]  Filling in enquiry form...[/yellow]")

    # Clear + fill each field
    await page.get_by_placeholder("Name").fill(name)
    await asyncio.sleep(0.4)

    await page.get_by_placeholder("Phone").fill(phone)
    await asyncio.sleep(0.4)

    await page.get_by_placeholder("Email Address").fill(email)
    await asyncio.sleep(0.4)

    await page.get_by_placeholder("Your Message").fill(message)
    await asyncio.sleep(0.4)

    # Print summary
    console.print("  [green]✔[/green]  Name:    ", name)
    console.print("  [green]✔[/green]  Phone:   ", phone)
    console.print("  [green]✔[/green]  Email:   ", email)
    console.print("  [green]✔[/green]  Message: ", message[:80] + ("…" if len(message) > 80 else ""))

    # --- Submission ---
    if auto_submit:
        console.print("\n[bold yellow]  Auto-submitting...[/bold yellow]")
        await page.get_by_role("button", name="SEND ENQUIRY").click()
        await asyncio.sleep(3)
        console.print("[bold green]  ✅ Enquiry submitted![/bold green]")
        return True
    else:
        console.print(
            Panel.fit(
                "[bold]Form is ready.[/bold]\n"
                "The browser window is open so you can review everything.\n\n"
                "Press [bold cyan]ENTER[/bold cyan] in this terminal to SEND the enquiry,\n"
                "or [bold red]Ctrl-C[/bold red] to cancel.",
                border_style="yellow",
            )
        )
        try:
            await asyncio.get_event_loop().run_in_executor(None, input)
        except (EOFError, KeyboardInterrupt):
            console.print("[red]Cancelled.[/red]")
            return False

        await page.get_by_role("button", name="SEND ENQUIRY").click()
        await asyncio.sleep(3)
        console.print("[bold green]  ✅ Enquiry submitted![/bold green]")
        return True


async def fill_enquiry_form(
    detail_url: str,
    name: str,
    phone: str,
    email: str,
    message: str,
    listing_info: Optional[dict] = None,
    headless: bool = False,
    auto_submit: bool = False,
) -> bool:
    """
    Open a Chromium browser, navigate to the listing detail page, and fill
    the enquiry form with YANTRA LIVE company details.

    Args
    ----
    detail_url   : Full URL of the tradeearthmovers detail page.
    name         : Company/person name (default: YANTRA LIVE).
    phone        : Phone number to use in the form.
    email        : Email address to use in the form.
    message      : User-supplied message (listing details are appended).
    listing_info : Dict from format_listing(); details appended to message.
    headless     : Run browser without a visible window (default False so
                   the operator can see the form before submitting).
    auto_submit  : Click SEND ENQUIRY automatically without waiting for
                   operator confirmation (default False – safety first).

    Returns True on successful submission, False otherwise.
    """
    full_message = _compose_message(message, listing_info)

    console.print(f"\n[bold blue]Opening browser for enquiry...[/bold blue]")
    console.print(f"  URL: {detail_url}\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = await ctx.new_page()

        try:
            await page.goto(detail_url, wait_until="domcontentloaded", timeout=30_000)
            result = await _fill_and_submit(
                page, name, phone, email, full_message, auto_submit
            )
            # Keep the browser open briefly so the operator can see the result
            await asyncio.sleep(4)
            return result

        except Exception as exc:
            logger.exception("Enquiry bot error: %s", exc)
            console.print(f"[red]Error during enquiry: {exc}[/red]")
            await asyncio.sleep(3)
            return False

        finally:
            await browser.close()


def _compose_message(user_message: str, listing_info: Optional[dict]) -> str:
    """Build the final message: user text + machine-readable listing details."""
    parts: list[str] = []

    if user_message:
        parts.append(user_message.strip())

    if listing_info:
        parts.append("\n--- Equipment of Interest ---")
        if listing_info.get("title"):
            parts.append(f"Model:    {listing_info['title']}")
        if listing_info.get("price"):
            parts.append(f"Price:    {listing_info['price']}")
        if listing_info.get("location"):
            parts.append(f"Location: {listing_info['location']}")
        if listing_info.get("seller_name"):
            parts.append(f"Dealer:   {listing_info['seller_name']}")
        desc = (listing_info.get("short_description") or "")[:300]
        if desc:
            parts.append(f"\n{desc}")

    return "\n".join(parts)


# Sync convenience wrapper -----------------------------------------------

def run_enquiry(
    detail_url: str,
    name: str,
    phone: str,
    email: str,
    message: str,
    listing_info: Optional[dict] = None,
    auto_submit: bool = False,
) -> bool:
    """Synchronous entry point – wraps the async fill_enquiry_form."""
    return asyncio.run(
        fill_enquiry_form(
            detail_url=detail_url,
            name=name,
            phone=phone,
            email=email,
            message=message,
            listing_info=listing_info,
            headless=False,
            auto_submit=auto_submit,
        )
    )
