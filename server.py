import asyncio
import base64
import os
import subprocess
import secrets

import anyio
from mcp.server.fastmcp import FastMCP
from playwright.async_api import async_playwright, Browser, Page
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, Mount

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

# ── MCP tools ────────────────────────────────────────────────────────────────
mcp = FastMCP("claude-cu")


@mcp.tool()
async def screenshot() -> str:
    """Take a screenshot of the current browser page. Returns base64-encoded PNG."""
    page = await get_page()
    buf = await page.screenshot(type="png")
    return base64.b64encode(buf).decode()


@mcp.tool()
async def screenshot_display() -> str:
    """Take a full virtual display screenshot (captures all windows, not just browser). Returns base64-encoded PNG."""
    result = subprocess.run(
        "import -window root /tmp/display_screenshot.png && base64 /tmp/display_screenshot.png",
        shell=True, capture_output=True, text=True, timeout=10,
        env={**os.environ, "DISPLAY": ":99"}
    )
    if result.returncode == 0:
        return result.stdout.strip()
    # Fallback: xwd
    result2 = subprocess.run(
        "xwd -root -silent -display :99 | convert xwd:- png:- | base64",
        shell=True, capture_output=True, text=True, timeout=10
    )
    if result2.returncode == 0:
        return result2.stdout.strip()
    return f"ERROR: {result.stderr} | {result2.stderr}"


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
        command, shell=True, capture_output=True, text=True, timeout=60,
        env={**os.environ, "DISPLAY": ":99"}
    )
    return {
        "stdout": result.stdout[:10_000],
        "stderr": result.stderr[:2_000],
        "returncode": result.returncode,
    }


@mcp.tool()
async def xdotool(args: str) -> dict:
    """Run xdotool command for GUI automation (click, type, key, getactivewindow, etc)."""
    result = subprocess.run(
        f"xdotool {args}",
        shell=True, capture_output=True, text=True, timeout=30,
        env={**os.environ, "DISPLAY": ":99"}
    )
    return {
        "stdout": result.stdout[:5_000],
        "stderr": result.stderr[:1_000],
        "returncode": result.returncode,
    }


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


# ── Health endpoint ───────────────────────────────────────────────────────────
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "api_key_set": bool(API_KEY)})


# ── REST endpoints (for direct curl access, no MCP protocol needed) ───────────
async def rest_screenshot(request: Request) -> JSONResponse:
    """REST: Take browser screenshot. GET or POST."""
    try:
        page = await get_page()
        buf = await page.screenshot(type="png")
        return JSONResponse({"image": base64.b64encode(buf).decode(), "type": "png"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def rest_bash(request: Request) -> JSONResponse:
    """REST: Run a bash command. POST with JSON body {"command": "..."}"""
    try:
        body = await request.json()
        command = body.get("command", "")
        if not command:
            return JSONResponse({"error": "command required"}, status_code=400)
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=60,
            env={**os.environ, "DISPLAY": ":99"}
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


async def rest_click(request: Request) -> JSONResponse:
    """REST: Click at (x,y). POST with JSON body {"x": 100, "y": 200}"""
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


async def rest_type_text(request: Request) -> JSONResponse:
    """REST: Type text. POST with JSON body {"text": "..."}"""
    try:
        body = await request.json()
        text = body.get("text", "")
        page = await get_page()
        await page.keyboard.type(text)
        return JSONResponse({"typed": len(text)})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def rest_key(request: Request) -> JSONResponse:
    """REST: Press a key. POST with JSON body {"key": "Enter"}"""
    try:
        body = await request.json()
        key = body.get("key", "")
        page = await get_page()
        await page.keyboard.press(key)
        await page.wait_for_timeout(200)
        return JSONResponse({"pressed": key})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def rest_navigate(request: Request) -> JSONResponse:
    """REST: Navigate browser. POST with JSON body {"url": "..."}"""
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


async def rest_xdotool(request: Request) -> JSONResponse:
    """REST: Run xdotool. POST with JSON body {"args": "mousemove 100 200"}"""
    try:
        body = await request.json()
        args = body.get("args", "")
        result = subprocess.run(
            f"xdotool {args}", shell=True, capture_output=True, text=True, timeout=30,
            env={**os.environ, "DISPLAY": ":99"}
        )
        return JSONResponse({
            "stdout": result.stdout[:5_000],
            "stderr": result.stderr[:1_000],
            "returncode": result.returncode,
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def rest_display_screenshot(request: Request) -> JSONResponse:
    """REST: Take full X display screenshot (not just browser). GET or POST."""
    try:
        result = subprocess.run(
            "python3 -c \"import base64; import subprocess; r=subprocess.run(['scrot','/tmp/dss.png'], capture_output=True); print(base64.b64encode(open('/tmp/dss.png','rb').read()).decode())\"",
            shell=True, capture_output=True, text=True, timeout=15,
            env={**os.environ, "DISPLAY": ":99"}
        )
        if result.returncode == 0 and result.stdout.strip():
            return JSONResponse({"image": result.stdout.strip(), "type": "png"})
        return JSONResponse({"error": result.stderr or "scrot failed"}, status_code=500)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Build app: Mount MCP sub-app so its lifespan is properly called ───────────
mcp_sub_app = mcp.streamable_http_app()

app = Starlette(
    routes=[
        Route("/health",             health,                    methods=["GET"]),
        # REST endpoints (no MCP protocol needed)
        Route("/screenshot",         rest_screenshot,           methods=["GET", "POST"]),
        Route("/display_screenshot", rest_display_screenshot,   methods=["GET", "POST"]),
        Route("/bash",               rest_bash,                 methods=["POST"]),
        Route("/click",              rest_click,                methods=["POST"]),
        Route("/type_text",          rest_type_text,            methods=["POST"]),
        Route("/key",                rest_key,                  methods=["POST"]),
        Route("/navigate",           rest_navigate,             methods=["POST"]),
        Route("/xdotool",            rest_xdotool,              methods=["POST"]),
        # MCP sub-app (for MCP protocol clients)
        Mount("/mcp", app=mcp_sub_app),
    ],
)
app.add_middleware(ApiKeyMiddleware)
