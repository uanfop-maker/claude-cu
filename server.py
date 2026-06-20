import asyncio
import base64
import os
import subprocess
import secrets
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from playwright.async_api import async_playwright, Browser, Page

# ── Auth ────────────────────────────────────────────────────────────────────
API_KEY = os.environ.get("MCP_API_KEY", "")
if not API_KEY:
    API_KEY = secrets.token_urlsafe(32)
    print(f"[claude-cu] Generated API key: {API_KEY}", flush=True)

def check_auth(x_api_key: str = Header(default="")):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

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

# ── App ─────────────────────────────────────────────────────────────────────
app = FastAPI(title="claude-cu MCP server")

@app.get("/health")
async def health():
    return {"status": "ok", "api_key_set": bool(API_KEY)}


# ── MCP tool models ─────────────────────────────────────────────────────────
class NavigateReq(BaseModel):
    url: str

class ClickReq(BaseModel):
    x: int
    y: int

class TypeReq(BaseModel):
    text: str

class ScrollReq(BaseModel):
    x: int
    y: int
    delta_y: int = 300

class BashReq(BaseModel):
    command: str


# ── Tools ───────────────────────────────────────────────────────────────────
@app.post("/tools/screenshot")
async def screenshot(x_api_key: str = Header(default="")):
    check_auth(x_api_key)
    page = await get_page()
    buf = await page.screenshot(type="png")
    return {"image_b64": base64.b64encode(buf).decode(), "content_type": "image/png"}


@app.post("/tools/navigate")
async def navigate(req: NavigateReq, x_api_key: str = Header(default="")):
    check_auth(x_api_key)
    page = await get_page()
    await page.goto(req.url, wait_until="domcontentloaded", timeout=30_000)
    return {"url": page.url, "title": await page.title()}


@app.post("/tools/click")
async def click(req: ClickReq, x_api_key: str = Header(default="")):
    check_auth(x_api_key)
    page = await get_page()
    await page.mouse.click(req.x, req.y)
    await page.wait_for_timeout(300)
    return {"clicked": [req.x, req.y]}


@app.post("/tools/type_text")
async def type_text(req: TypeReq, x_api_key: str = Header(default="")):
    check_auth(x_api_key)
    page = await get_page()
    await page.keyboard.type(req.text)
    return {"typed": len(req.text)}


@app.post("/tools/scroll")
async def scroll(req: ScrollReq, x_api_key: str = Header(default="")):
    check_auth(x_api_key)
    page = await get_page()
    await page.mouse.wheel(0, req.delta_y)
    await page.wait_for_timeout(200)
    return {"scrolled": req.delta_y}


@app.post("/tools/get_page_text")
async def get_page_text(x_api_key: str = Header(default="")):
    check_auth(x_api_key)
    page = await get_page()
    text = await page.evaluate("() => document.body.innerText")
    return {"text": text[:50_000], "url": page.url}


@app.post("/tools/bash")
async def bash(req: BashReq, x_api_key: str = Header(default="")):
    check_auth(x_api_key)
    result = subprocess.run(
        req.command, shell=True, capture_output=True, text=True, timeout=60
    )
    return {
        "stdout": result.stdout[:10_000],
        "stderr": result.stderr[:2_000],
        "returncode": result.returncode,
    }


# ── MCP manifest (for claude mcp add) ────────────────────────────────────────
@app.get("/mcp")
async def mcp_manifest():
    base = os.environ.get("SERVICE_URL", "http://localhost:8099")
    tools = [
        {"name": "screenshot",    "description": "Take a screenshot of the current browser page", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "navigate",      "description": "Navigate browser to a URL", "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
        {"name": "click",         "description": "Click at (x, y) pixel coordinates", "inputSchema": {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}}, "required": ["x", "y"]}},
        {"name": "type_text",     "description": "Type text into the currently focused element", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
        {"name": "scroll",        "description": "Scroll the page by delta_y pixels", "inputSchema": {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}, "delta_y": {"type": "integer"}}}},
        {"name": "get_page_text", "description": "Get visible text content of the current page", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "bash",          "description": "Run a bash command and return output", "inputSchema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    ]
    return {"schema_version": "v1", "name": "claude-cu", "tools": tools, "base_url": base + "/tools"}
