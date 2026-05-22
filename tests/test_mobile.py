"""
Mobile responsiveness tests using Playwright.
Tests key pages at desktop (1280x800) and mobile (375x812 - iPhone SE/14) viewports.
Run: python tests/test_mobile.py
"""
import asyncio
import os
from pathlib import Path
from playwright.async_api import async_playwright

BASE_URL = "http://localhost:8000"
SCREENSHOT_DIR = Path(__file__).parent / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)

VIEWPORTS = {
    "desktop": {"width": 1280, "height": 800},
    "mobile":  {"width": 375,  "height": 812},
    "tablet":  {"width": 768,  "height": 1024},
}

PASS = "✓"
FAIL = "✗"
results = []

def log(status, label, detail=""):
    icon = PASS if status else FAIL
    msg = f"  {icon} {label}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    results.append((status, label))


async def check_no_horizontal_scroll(page, label):
    overflow = await page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth"
    )
    log(not overflow, label, "horizontal scroll detected" if overflow else "no horizontal scroll")


async def check_font_size(page, selector, min_px, label):
    try:
        size = await page.eval_on_selector(
            selector,
            f"el => parseFloat(window.getComputedStyle(el).fontSize)"
        )
        ok = size >= min_px
        log(ok, label, f"{size:.1f}px (min {min_px}px)")
    except Exception as e:
        log(False, label, f"selector not found: {e}")


async def check_element_visible(page, selector, label):
    try:
        el = page.locator(selector).first
        visible = await el.is_visible()
        log(visible, label)
    except Exception as e:
        log(False, label, str(e))


async def check_no_overlap(page, sel_a, sel_b, label):
    """Check two elements don't visually overlap."""
    try:
        box_a = await page.locator(sel_a).first.bounding_box()
        box_b = await page.locator(sel_b).first.bounding_box()
        if not box_a or not box_b:
            log(False, label, "element not found")
            return
        # Check vertical separation (no overlap in y axis)
        a_bottom = box_a["y"] + box_a["height"]
        b_top = box_b["y"]
        ok = a_bottom <= b_top + 5  # 5px tolerance
        log(ok, label, f"overlap: {max(0, a_bottom - b_top):.0f}px" if not ok else "no overlap")
    except Exception as e:
        log(False, label, str(e))


async def check_element_in_viewport(page, selector, label):
    """Check element is within the viewport width (no clipping)."""
    try:
        vw = await page.evaluate("() => window.innerWidth")
        box = await page.locator(selector).first.bounding_box()
        if not box:
            log(False, label, "element not found")
            return
        ok = box["x"] >= 0 and (box["x"] + box["width"]) <= vw + 2
        log(ok, label, f"right edge: {box['x'] + box['width']:.0f}px vs viewport {vw}px")
    except Exception as e:
        log(False, label, str(e))


async def run_tests():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)

        for vp_name, vp in VIEWPORTS.items():
            print(f"\n{'='*50}")
            print(f" Viewport: {vp_name} ({vp['width']}x{vp['height']})")
            print(f"{'='*50}")

            context = await browser.new_context(viewport=vp)
            page = await context.new_page()

            # ── HOME PAGE ──────────────────────────────────────────
            print("\n[home]")
            await page.goto(f"{BASE_URL}/", wait_until="networkidle")
            await page.screenshot(path=SCREENSHOT_DIR / f"home_{vp_name}.png", full_page=True)

            await check_no_horizontal_scroll(page, f"{vp_name}: home — no horizontal scroll")

            # Hero text readable
            await check_font_size(page, ".hero .lead", 14, f"{vp_name}: home hero text ≥14px")

            # Stat table: col headers present, not overflowing
            if vp_name == "mobile":
                # Short labels should be visible on mobile
                short_visible = await page.locator(".d-sm-none").first.is_visible()
                log(short_visible, f"{vp_name}: home stats — short column labels shown on mobile")
            else:
                # Long labels should be visible on desktop/tablet
                long_visible = await page.locator(".d-none.d-sm-inline").first.is_visible()
                log(long_visible, f"{vp_name}: home stats — full column labels shown on {vp_name}")

            # Stat cards in viewport
            await check_element_in_viewport(page, "#stats-row-gpt5", f"{vp_name}: home stats row in viewport")

            # Video aspect ratio container present
            has_ratio = await page.locator(".ratio.ratio-16x9 video").count() > 0
            log(has_ratio, f"{vp_name}: home video — ratio container present")

            # ── ABOUT PAGE ─────────────────────────────────────────
            print("\n[about]")
            await page.goto(f"{BASE_URL}/about", wait_until="networkidle")
            await page.screenshot(path=SCREENSHOT_DIR / f"about_{vp_name}.png", full_page=True)
            await check_no_horizontal_scroll(page, f"{vp_name}: about — no horizontal scroll")
            await check_font_size(page, ".about-section p", 13, f"{vp_name}: about body text ≥13px")

            # ── EVALS PAGE ─────────────────────────────────────────
            print("\n[evals]")
            await page.goto(f"{BASE_URL}/evals", wait_until="networkidle")
            await page.screenshot(path=SCREENSHOT_DIR / f"evals_{vp_name}.png", full_page=True)
            await check_no_horizontal_scroll(page, f"{vp_name}: evals — no horizontal scroll")
            await check_font_size(page, ".finding-card .finding-label", 11, f"{vp_name}: evals finding label ≥11px")

            if vp_name == "mobile":
                # NEEDS REVIEW column should be hidden
                nr_hidden = await page.locator("th.d-none.d-md-table-cell").first.is_hidden()
                log(nr_hidden, f"{vp_name}: evals — NEEDS REVIEW column hidden on mobile")

            # ── DEMO PAGE ──────────────────────────────────────────
            print("\n[demo]")
            await page.goto(f"{BASE_URL}/demo", wait_until="networkidle")
            # Give WebSocket a moment to connect
            await page.wait_for_timeout(1500)
            await page.screenshot(path=SCREENSHOT_DIR / f"demo_{vp_name}.png", full_page=True)
            await check_no_horizontal_scroll(page, f"{vp_name}: demo — no horizontal scroll")

            # Model selector should not overlap chat messages
            if vp_name == "mobile":
                selector_box = await page.locator("#model-selector").bounding_box()
                chat_box = await page.locator("#chat-container").bounding_box()
                if selector_box and chat_box:
                    ok = selector_box["y"] + selector_box["height"] <= chat_box["y"] + 5
                    log(ok, f"{vp_name}: demo — model selector above chat messages")
                else:
                    log(False, f"{vp_name}: demo — model selector position check (element not found)")

            # Input area in viewport
            await check_element_in_viewport(page, ".chat-input-wrapper", f"{vp_name}: demo — input in viewport")

            await context.close()

        await browser.close()

    # Summary
    print(f"\n{'='*50}")
    passed = sum(1 for ok, _ in results if ok)
    failed = sum(1 for ok, _ in results if not ok)
    print(f" Results: {passed} passed, {failed} failed")
    if failed:
        print("\n Failed tests:")
        for ok, label in results:
            if not ok:
                print(f"   {FAIL} {label}")
    print(f"\n Screenshots saved to: {SCREENSHOT_DIR}/")
    print(f"{'='*50}")
    return failed == 0


if __name__ == "__main__":
    ok = asyncio.run(run_tests())
    exit(0 if ok else 1)
