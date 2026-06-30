import asyncio
import base64
import os
import subprocess
import secrets
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from playwright.async_api import async_playwright, Browser, Page

# ── Auth ────────────────────────────────────────────────────────────────────
API_KEY = os.environ.get("MCP_API_KEY", "")
if not API_KEY:
    API_KEY = secrets.token_urlsafe(32)
    print(f"[claude-cu] Generated API key: {API_KEY}", flush=True)

# ── Browser singleton ───────────────────────────────────────────────────────
_pw = None
_browser: Browser | None = None
_page: Page | None = None
_lock = asyncio.Lock()

async def get_page() -> Page:
    global _pw, _browser, _page
    async with _lock:
        if _browser is None or not _browser.is_connected():
            if _pw is None:
                _pw = await async_playwright().start()
            _browser = await _pw.chromium.launch(
                headless=False,
                executable_path="/usr/bin/chromium",
                args=["--no-sandbox", "--disable-dev-shm-usage",
                      "--disable-blink-features=AutomationControlled",
                      "--window-size=1280,900"],
            )
            _page = await _browser.new_page(viewport={"width": 1280, "height": 900})
        elif _page is None or _page.is_closed():
            _page = await _browser.new_page(viewport={"width": 1280, "height": 900})
    return _page


# ── REST handlers ─────────────────────────────────────────────────────────────

async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "api_key_set": bool(API_KEY)})


async def api_screenshot(request: Request) -> JSONResponse:
    """Take a screenshot of the current browser page. Returns base64-encoded PNG."""
    try:
        page = await get_page()
        buf = await page.screenshot(type="png")
        return JSONResponse({"image": base64.b64encode(buf).decode(), "type": "png"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def api_navigate(request: Request) -> JSONResponse:
    """Navigate the browser to a URL."""
    try:
        body = await request.json()
        url = body.get("url", "")
        if not url:
            return JSONResponse({"error": "url required"}, status_code=400)
        page = await get_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        return JSONResponse({"url": page.url, "title": await page.title()})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def api_click(request: Request) -> JSONResponse:
    """Click at pixel coordinates (x, y) on the current page."""
    try:
        body = await request.json()
        x = int(body.get("x", 0))
        y = int(body.get("y", 0))
        page = await get_page()
        await page.mouse.click(x, y)
        await page.wait_for_timeout(300)
        return JSONResponse({"clicked": [x, y]})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def api_type_text(request: Request) -> JSONResponse:
    """Type text into the currently focused element."""
    try:
        body = await request.json()
        text = body.get("text", "")
        page = await get_page()
        await page.keyboard.type(text)
        return JSONResponse({"typed": len(text)})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def api_key_press(request: Request) -> JSONResponse:
    """Press a keyboard key (e.g. 'Enter', 'Tab', 'Escape')."""
    try:
        body = await request.json()
        key = body.get("key", "")
        page = await get_page()
        await page.keyboard.press(key)
        await page.wait_for_timeout(200)
        return JSONResponse({"pressed": key})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def api_scroll(request: Request) -> JSONResponse:
    """Scroll the page by delta_y pixels (positive=down, negative=up)."""
    try:
        body = await request.json()
        delta_y = int(body.get("delta_y", 300))
        page = await get_page()
        await page.mouse.wheel(0, delta_y)
        await page.wait_for_timeout(200)
        return JSONResponse({"scrolled": delta_y})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def api_get_page_text(request: Request) -> JSONResponse:
    """Get the visible text content of the current browser page."""
    try:
        page = await get_page()
        text = await page.evaluate("() => document.body.innerText")
        return JSONResponse({"text": text[:50_000], "url": page.url})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def api_bash(request: Request) -> JSONResponse:
    """Run a bash command on the server and return stdout/stderr."""
    try:
        body = await request.json()
        command = body.get("command", "")
        if not command:
            return JSONResponse({"error": "command required"}, status_code=400)
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=60
        )
        return JSONResponse({
            "stdout": result.stdout[:10_000],
            "stderr": result.stderr[:2_000],
            "returncode": result.returncode,
        })
    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "command timed out"}, status_code=408)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Auth middleware ──────────────────────────────────────────────────────────
class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in ("/health",):
            return await call_next(request)
        key = request.headers.get("x-api-key", "")
        if key != API_KEY:
            return Response('{"detail":"Invalid API key"}', status_code=401,
                            media_type="application/json")
        return await call_next(request)


# ── Build app ─────────────────────────────────────────────────────────────────
app = Starlette(
    routes=[
        Route("/health",        health,            methods=["GET"]),
        Route("/screenshot",    api_screenshot,    methods=["GET", "POST"]),
        Route("/navigate",      api_navigate,      methods=["POST"]),
        Route("/click",         api_click,         methods=["POST"]),
        Route("/type_text",     api_type_text,     methods=["POST"]),
        Route("/key",           api_key_press,     methods=["POST"]),
        Route("/scroll",        api_scroll,        methods=["POST"]),
        Route("/get_page_text", api_get_page_text, methods=["GET", "POST"]),
        Route("/bash",          api_bash,          methods=["POST"]),
    ],
)
app.add_middleware(ApiKeyMiddleware)
