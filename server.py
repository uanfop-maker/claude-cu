import asyncio
import base64
import os
import subprocess
import secrets
from typing import Any

from mcp.server.fastmcp import FastMCP
from playwright.async_api import async_playwright, Browser, Page
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

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

# ── MCP Server ───────────────────────────────────────────────────────────────
mcp = FastMCP("claude-cu")


@mcp.tool()
async def screenshot() -> str:
    """Take a screenshot of the current browser page. Returns base64-encoded PNG."""
    page = await get_page()
    buf = await page.screenshot(type="png")
    b64 = base64.b64encode(buf).decode()
    return f"data:image/png;base64,{b64}"


@mcp.tool()
async def navigate(url: str) -> dict:
    """Navigate the browser to a URL."""
    page = await get_page()
    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    return {"url": page.url, "title": await page.title()}


@mcp.tool()
async def click(x: int, y: int) -> dict:
    """Click at pixel coordinates (x, y) on the current page."""
    page = await get_page()
    await page.mouse.click(x, y)
    await page.wait_for_timeout(300)
    return {"clicked": [x, y]}


@mcp.tool()
async def type_text(text: str) -> dict:
    """Type text into the currently focused element."""
    page = await get_page()
    await page.keyboard.type(text)
    return {"typed": len(text)}


@mcp.tool()
async def scroll(delta_y: int = 300) -> dict:
    """Scroll the page by delta_y pixels (positive = down, negative = up)."""
    page = await get_page()
    await page.mouse.wheel(0, delta_y)
    await page.wait_for_timeout(200)
    return {"scrolled": delta_y}


@mcp.tool()
async def get_page_text() -> dict:
    """Get the visible text content of the current browser page."""
    page = await get_page()
    text = await page.evaluate("() => document.body.innerText")
    return {"text": text[:50_000], "url": page.url}


@mcp.tool()
async def bash(command: str) -> dict:
    """Run a bash command on the server and return stdout/stderr."""
    result = subprocess.run(
        command, shell=True, capture_output=True, text=True, timeout=60
    )
    return {
        "stdout": result.stdout[:10_000],
        "stderr": result.stderr[:2_000],
        "returncode": result.returncode,
    }


# ── Auth middleware ──────────────────────────────────────────────────────────
class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        key = request.headers.get("x-api-key", "")
        if key != API_KEY:
            return Response('{"detail":"Invalid API key"}', status_code=401,
                            media_type="application/json")
        return await call_next(request)


# ── ASGI App ─────────────────────────────────────────────────────────────────
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse

async def health(request):
    return JSONResponse({"status": "ok", "api_key_set": bool(API_KEY)})

mcp_app = mcp.streamable_http_app()

app = Starlette(routes=[
    Route("/health", health),
    Mount("/mcp", app=mcp_app),
])
app.add_middleware(ApiKeyMiddleware)
