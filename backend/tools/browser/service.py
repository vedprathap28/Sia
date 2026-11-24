"""
SIA — Playwright browser service
================================
One persistent, authenticated browser (its own profile in data/browser_profile, so the user
logs in once and sessions persist). Runs in a dedicated thread with its own event loop so
browser work never blocks the API, and Playwright's subprocess needs are met on Windows.

Interaction is DOM-first: `snapshot()` tags every visible interactive element with a short
ref (e1, e2 …) that the LLM uses with click/fill/select. Screenshots + vision are the
fallback (tools/computer).

Safety:
  • password / OTP fields are never filled by SIA — the user types them (needs_user)
  • login, CAPTCHA and 2-step pages are detected and reported (never bypassed)
  • clicks on submit / pay / send / book / apply / delete buttons are escalated to risk 3
"""
from __future__ import annotations

import asyncio
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from backend.core.config import settings
from backend.core.logger import get_logger

log = get_logger("browser_service")

_SNAPSHOT_JS = r"""
(maxItems) => {
  const sel = 'a[href], button, input:not([type=hidden]), select, textarea, [role=button], [role=link], '
            + '[role=tab], [role=menuitem], [role=checkbox], [role=radio], [role=option], [role=combobox], '
            + '[role=textbox], [contenteditable=true], [onclick], summary, label[for]';
  document.querySelectorAll('[data-sia-ref]').forEach(e => e.removeAttribute('data-sia-ref'));
  const out = []; let n = 0;
  const vis = (el) => {
    const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 1 && r.height > 1 && s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0';
  };
  for (const el of document.querySelectorAll(sel)) {
    if (!vis(el)) continue;
    if (n >= maxItems) break;
    n += 1; const ref = 'e' + n; el.setAttribute('data-sia-ref', ref);
    const r = el.getBoundingClientRect();
    const label = (el.getAttribute('aria-label') || el.innerText || el.value || el.getAttribute('placeholder')
                  || el.getAttribute('title') || el.getAttribute('alt') || el.getAttribute('name') || '').trim();
    out.push({
      ref, tag: el.tagName.toLowerCase(), role: el.getAttribute('role') || '',
      type: (el.getAttribute('type') || '').toLowerCase(), text: label.replace(/\s+/g, ' ').slice(0, 90),
      href: el.tagName === 'A' ? (el.getAttribute('href') || '').slice(0, 120) : '',
      value: (el.tagName === 'INPUT' && el.type !== 'password') ? (el.value || '').slice(0, 60) : '',
      disabled: !!el.disabled, inView: r.top < innerHeight && r.bottom > 0,
      options: el.tagName === 'SELECT' ? Array.from(el.options).slice(0, 25).map(o => o.text.trim()) : undefined,
    });
  }
  const text = (document.querySelector('main, article, [role=main]') || document.body).innerText || '';
  const pw = !!Array.from(document.querySelectorAll('input[type=password]')).find(vis);
  const frames = Array.from(document.querySelectorAll('iframe')).map(f => f.src || '').join(' ');
  return { url: location.href, title: document.title, elements: out, text: text.slice(0, 6000),
           hasPassword: pw, frames: frames.slice(0, 2000), scrollY: scrollY, scrollH: document.body.scrollHeight };
}
"""

_RISKY_CLICK = re.compile(
    r"\b(submit|send|pay|payment|place order|buy|purchase|checkout|book|reserve|confirm|apply|"
    r"post|publish|tweet|share|delete|remove|unsubscribe|transfer|sign up|register|proceed to pay)\b", re.I)
_CAPTCHA = re.compile(r"(recaptcha|hcaptcha|turnstile|captcha|verify you are human|are you a robot|unusual traffic)", re.I)
_MFA = re.compile(r"(2-step verification|two-factor|verification code|enter the code|one-time password|\botp\b)", re.I)
_PAYMENT_FIELD = re.compile(
    r"(card\s*number|card\s*no|cardnumber|cc-?num|credit\s*card|debit\s*card|\bcvv\b|\bcvc\b|security\s*code|"
    r"name\s*on\s*card|expiry|exp(iry)?\s*date|mm\s*/\s*yy|\bupi\b|vpa|ifsc|account\s*number|net\s*banking)", re.I)
_LOGIN_URL = re.compile(r"(accounts\.google\.com|/login|/signin|/sign-in|/auth|/sso)", re.I)


class _LoopThread:
    """A private asyncio loop in a daemon thread (Proactor on Windows for Playwright)."""

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()

    def start(self) -> None:
        if self.loop:
            return

        def run():
            if sys.platform == "win32":
                self.loop = asyncio.ProactorEventLoop()
            else:
                self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self._ready.set()
            self.loop.run_forever()

        threading.Thread(target=run, name="sia-browser", daemon=True).start()
        self._ready.wait(10)

    async def call(self, fn: Callable[..., Awaitable[Any]], *a, **kw) -> Any:
        self.start()
        fut = asyncio.run_coroutine_threadsafe(fn(*a, **kw), self.loop)  # type: ignore[arg-type]
        return await asyncio.wrap_future(fut)


class BrowserService:
    def __init__(self) -> None:
        self._thread = _LoopThread()
        self._pw = None
        self._ctx = None
        self._active = 0
        self.ref_info: dict[str, dict] = {}       # last snapshot: ref → element info (used for risk)
        self._lock: asyncio.Lock | None = None

    # ── lifecycle (runs on the browser thread) ────────────────────────────
    async def _ensure(self):
        if self._ctx is not None:
            try:
                _ = self._ctx.pages
                return
            except Exception:
                self._ctx = None
        from playwright.async_api import async_playwright
        if self._pw is None:
            self._pw = await async_playwright().start()
        prof = Path(settings.BROWSER_PROFILE_DIR)
        prof.mkdir(parents=True, exist_ok=True)
        kw = dict(headless=settings.BROWSER_HEADLESS, accept_downloads=True, viewport={"width": 1280, "height": 860},
                  args=["--disable-blink-features=AutomationControlled"])
        channel = settings.BROWSER_CHANNEL if settings.BROWSER_CHANNEL in ("chrome", "msedge") else None
        try:
            self._ctx = await self._pw.chromium.launch_persistent_context(str(prof), channel=channel, **kw)
        except Exception as e:
            log.warning("Launch with channel=%s failed (%s) — trying bundled Chromium", channel, e)
            self._ctx = await self._pw.chromium.launch_persistent_context(str(prof), **kw)
        self._ctx.set_default_timeout(15000)
        if not self._ctx.pages:
            await self._ctx.new_page()
        self._active = 0

    def _page(self):
        pages = self._ctx.pages
        if not pages:
            raise RuntimeError("No open tabs")
        self._active = max(0, min(self._active, len(pages) - 1))
        return pages[self._active]

    async def _settle(self, page):
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
            await page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass

    # ── public API (each method is called through the thread bridge) ─────
    async def run(self, fn_name: str, *a, **kw):
        return await self._thread.call(getattr(self, "_" + fn_name), *a, **kw)

    async def _snapshot(self, max_items: int = 150) -> dict:
        await self._ensure()
        page = self._page()
        snap = await page.evaluate(_SNAPSHOT_JS, max_items)
        self.ref_info = {e["ref"]: e for e in snap["elements"]}
        blob = f"{snap['title']} {snap['text'][:3000]} {snap['frames']}"
        snap["needs_user"] = ""
        if _CAPTCHA.search(blob):
            snap["needs_user"] = "captcha"
        elif _MFA.search(snap["text"][:3000]) and any(e["tag"] == "input" for e in snap["elements"]):
            snap["needs_user"] = "mfa"
        elif snap["hasPassword"] or _LOGIN_URL.search(snap["url"]):
            snap["needs_user"] = "login_required"
        snap["tabs"] = [{"index": i, "title": "", "url": p.url, "active": i == self._active}
                        for i, p in enumerate(self._ctx.pages)]
        return snap

    async def _goto(self, url: str) -> dict:
        await self._ensure()
        if not re.match(r"^[a-z]+://", url):
            url = "https://" + url
        page = self._page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            # one retry for transient navigation failures
            log.warning("goto failed (%s), retrying once", e)
            await asyncio.sleep(1.5)
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await self._settle(page)
        return await self._snapshot()

    def _loc(self, page, ref: str):
        return page.locator(f'[data-sia-ref="{ref}"]').first

    async def _click(self, ref: str, double: bool = False) -> dict:
        page = self._page()
        sig_js = "() => document.body.innerText.length + ':' + document.body.innerText.slice(-4000) + ':' + document.querySelectorAll('*').length"
        before_url, before_sig = page.url, await page.evaluate(sig_js)
        loc = self._loc(page, ref)
        if await loc.count() == 0:
            raise LookupError(f"Element {ref} not found — take a new snapshot (refs change after navigation)")
        n_pages = len(self._ctx.pages)
        await loc.scroll_into_view_if_needed()
        await (loc.dblclick() if double else loc.click())
        await asyncio.sleep(0.6)
        if len(self._ctx.pages) > n_pages:        # click opened a new tab → follow it
            self._active = len(self._ctx.pages) - 1
        page = self._page()
        await self._settle(page)
        after_sig = await page.evaluate(sig_js)
        snap = await self._snapshot()
        snap["changed"] = page.url != before_url or after_sig != before_sig or len(self._ctx.pages) != n_pages
        return snap

    async def _fill(self, ref: str, text: str, submit: bool = False, clear: bool = True) -> dict:
        page = self._page()
        loc = self._loc(page, ref)
        if await loc.count() == 0:
            raise LookupError(f"Element {ref} not found — take a new snapshot")
        typ = (await loc.get_attribute("type") or "").lower()
        auto = (await loc.get_attribute("autocomplete") or "").lower()
        if typ == "password" or "one-time-code" in auto or "password" in auto:
            raise PermissionError("password_field")
        ident = " ".join([auto, *(str(x or "") for x in await loc.evaluate(
            "e => [e.name, e.id, e.placeholder, e.getAttribute('aria-label'), "
            "(e.labels && e.labels[0] ? e.labels[0].innerText : '')]"))]).lower()
        if auto.startswith("cc-") or _PAYMENT_FIELD.search(ident):
            raise PermissionError("payment_field")
        editable = await loc.evaluate("e => e.isContentEditable")
        if editable:
            await loc.click()
            if clear:
                await page.keyboard.press("Control+A")
            await page.keyboard.type(text, delay=5)
        else:
            if clear:
                await loc.fill(text)
            else:
                await loc.press_sequentially(text, delay=5)
        if submit:
            await loc.press("Enter")
            await self._settle(page)
        try:
            value = "" if editable else await loc.input_value()
        except Exception:
            value = ""
        snap = await self._snapshot()
        snap["filled_value_matches"] = editable or value == text or submit
        return snap

    async def _select(self, ref: str, option: str) -> dict:
        page = self._page()
        loc = self._loc(page, ref)
        try:
            await loc.select_option(label=option)
        except Exception:
            await loc.select_option(value=option)
        return await self._snapshot()

    async def _press(self, key: str) -> dict:
        page = self._page()
        await page.keyboard.press(key)
        await asyncio.sleep(0.4)
        await self._settle(page)
        return await self._snapshot()

    async def _scroll(self, direction: str = "down", amount: int = 800) -> dict:
        page = self._page()
        dy = {"down": amount, "up": -amount, "bottom": 10**6, "top": -10**6}.get(direction, amount)
        await page.mouse.wheel(0, dy)
        await asyncio.sleep(0.5)
        return await self._snapshot()

    async def _extract(self, max_chars: int = 15000) -> dict:
        await self._ensure()
        page = self._page()
        text = await page.evaluate("""() => {
            const root = document.querySelector('main, article, [role=main]') || document.body;
            return root.innerText; }""")
        links = await page.evaluate("""() => Array.from(document.querySelectorAll('a[href]')).slice(0, 60)
            .map(a => [a.innerText.trim().slice(0, 80), a.href]).filter(x => x[0])""")
        return {"url": page.url, "title": await page.title(), "text": text[:max_chars], "links": links}

    async def _tabs(self, action: str = "list", index: int = 0, url: str = "") -> dict:
        await self._ensure()
        if action == "new":
            p = await self._ctx.new_page()
            self._active = len(self._ctx.pages) - 1
            if url:
                return await self._goto(url)
        elif action == "switch":
            self._active = index
            await self._page().bring_to_front()
        elif action == "close":
            pages = self._ctx.pages
            if 0 <= index < len(pages) and len(pages) > 1:
                await pages[index].close()
                self._active = max(0, self._active - (1 if index <= self._active else 0))
        tabs = []
        for i, p in enumerate(self._ctx.pages):
            try:
                t = await p.title()
            except Exception:
                t = ""
            tabs.append({"index": i, "title": t[:80], "url": p.url, "active": i == self._active})
        return {"tabs": tabs}

    async def _back(self) -> dict:
        page = self._page()
        await page.go_back()
        await self._settle(page)
        return await self._snapshot()

    async def _download(self, ref: str, folder: str) -> dict:
        page = self._page()
        async with page.expect_download(timeout=60000) as dl_info:
            await self._loc(page, ref).click()
        dl = await dl_info.value
        dest = Path(folder) / dl.suggested_filename
        i = 1
        while dest.exists():
            dest = dest.with_name(f"{dest.stem} ({i}){dest.suffix}"); i += 1
        await dl.save_as(str(dest))
        return {"path": str(dest), "size": dest.stat().st_size if dest.exists() else 0}

    async def _upload(self, ref: str, paths: list[str]) -> dict:
        page = self._page()
        loc = self._loc(page, ref)
        if await loc.evaluate("e => e.tagName === 'INPUT' && e.type === 'file'"):
            await loc.set_input_files(paths)
        else:
            async with page.expect_file_chooser() as fc:
                await loc.click()
            await (await fc.value).set_files(paths)
        await asyncio.sleep(1)
        return await self._snapshot()

    async def _screenshot(self, path: str, full_page: bool = False) -> dict:
        page = self._page()
        await page.screenshot(path=path, full_page=full_page)
        return {"path": path}

    async def _wait_text(self, text: str, timeout_s: int = 20) -> dict:
        page = self._page()
        await page.get_by_text(text, exact=False).first.wait_for(timeout=timeout_s * 1000)
        return await self._snapshot()

    async def _eval(self, js: str, arg: Any = None) -> Any:
        """Internal use by site helpers (Gmail/Calendar). Never exposed to the LLM."""
        await self._ensure()
        return await self._page().evaluate(js, arg)

    async def _wait_selector(self, selector: str, timeout_s: int = 15) -> bool:
        try:
            await self._page().wait_for_selector(selector, timeout=timeout_s * 1000)
            return True
        except Exception:
            return False

    async def _click_selector(self, selector: str) -> bool:
        loc = self._page().locator(selector).first
        if await loc.count() == 0:
            return False
        await loc.click()
        await asyncio.sleep(0.8)
        return True

    async def _close(self):
        try:
            if self._ctx:
                await self._ctx.close()
            if self._pw:
                await self._pw.stop()
        finally:
            self._ctx = self._pw = None

    # ── sync helpers used by risk functions (no browser I/O) ─────────────
    def click_risk(self, ref: str) -> int:
        info = self.ref_info.get(ref, {})
        label = f"{info.get('text', '')} {info.get('type', '')}"
        if info.get("type") == "submit" or _RISKY_CLICK.search(label):
            return 3
        return 2

    def describe_ref(self, ref: str) -> str:
        i = self.ref_info.get(ref)
        if not i:
            return ref
        return f"{i.get('tag')}{'['+i['type']+']' if i.get('type') else ''} “{i.get('text','')[:60]}” ({ref})"


browser_service = BrowserService()


def format_snapshot(s: dict, max_elements: int = 120, text_chars: int = 2500) -> str:
    lines = [f"URL: {s.get('url')}", f"Title: {s.get('title')}"]
    if s.get("needs_user"):
        lines.append(f"!! needs_user: {s['needs_user']} — ask the user to complete it in the browser window, "
                     "then continue. Never type passwords/OTPs yourself.")
    if "changed" in s:
        lines.append(f"Page changed after action: {s['changed']}")
    lines.append(f"Scroll: {s.get('scrollY', 0)}/{s.get('scrollH', 0)}px")
    lines.append("Interactive elements (ref: tag[type] text):")
    for e in s.get("elements", [])[:max_elements]:
        extra = f" → {e['href']}" if e.get("href") else ""
        if e.get("value"):
            extra += f" (value: {e['value']})"
        if e.get("options"):
            extra += f" options={e['options']}"
        if e.get("disabled"):
            extra += " [disabled]"
        lines.append(f"  {e['ref']}: {e['tag']}{'['+e['type']+']' if e.get('type') else ''}"
                     f"{'('+e['role']+')' if e.get('role') else ''} {e['text']}{extra}")
    lines.append("Visible text:\n" + (s.get("text") or "")[:text_chars])
    return "\n".join(lines)
