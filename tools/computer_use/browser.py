"""Playwright execution engine. Does not decide business intent."""
import asyncio
import hashlib
import logging
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import uuid4

from playwright.async_api import async_playwright, Browser as PlaywrightBrowser, BrowserContext, Page, Playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
import yaml

SKILL_ROOT = Path(__file__).resolve().parents[2] / "agents" / "market" / "skills"


def load_browser_policy(skill: str) -> dict:
    """Only repository-owned Skill metadata may grant hosts or bind credentials."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", skill):
        raise ValueError("Invalid skill name")
    path = SKILL_ROOT / skill / "SKILL.md"
    if not path.resolve().is_relative_to(SKILL_ROOT.resolve()):
        raise ValueError("Invalid skill path")
    text = path.read_text(encoding="utf-8")
    frontmatter = text.split("---", 2)
    if len(frontmatter) != 3 or frontmatter[0].strip():
        raise ValueError("Skill frontmatter required")
    policy = (yaml.safe_load(frontmatter[1]) or {}).get("metadata", {}).get("browser")
    if not isinstance(policy, dict) or not policy.get("allowed_hosts"):
        raise ValueError("Skill has no browser host policy")
    policy = dict(policy)
    hosts = policy["allowed_hosts"]
    if not isinstance(hosts, list) or any(not isinstance(host, str) or not host.strip() for host in hosts):
        raise ValueError("Skill allowed_hosts must be an explicit list of hostnames")
    policy["allowed_hosts"] = [host.lower().rstrip(".") for host in hosts]
    policy["skill"] = skill
    policy["start_url"] = os.getenv(policy.get("start_url_env", ""), "").strip() or policy.get("start_url", "")
    policy["selectors"] = {key: setting(value) for key, value in policy.get("selectors", {}).items()}
    validate_url(policy["start_url"], policy)
    return policy


def setting(value):
    if not isinstance(value, dict):
        return value
    for key in ("env", "fallback_env"):
        if value.get(key) and os.getenv(value[key], "").strip():
            return os.environ[value[key]].strip()
    return value.get("default", "")

logger = logging.getLogger(__name__)


def validate_url(url: str, policy: dict) -> str:
    """Keep all frame, popup and redirect navigation on Skill-approved HTTPS hosts."""
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.port not in (None, 443)
            or parsed.hostname.lower().rstrip(".") not in policy["allowed_hosts"]):
        raise ValueError("Navigation outside the Skill HTTPS host policy")
    return url


class Browser:
    def __init__(self, policy: dict):
        self.policy = policy
        self.playwright: Playwright | None = None
        self.browser: PlaywrightBrowser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.last_navigation_failure: str | None = None

    async def start(self, url: str) -> dict:
        validate_url(url, self.policy)
        if self.playwright is not None:
            raise RuntimeError("Browser already started")
        loop = asyncio.get_running_loop()
        if os.name == "nt" and "Selector" in type(loop).__name__:
            raise RuntimeError("Windows ProactorEventLoop로 실행해야 합니다.")
        try:
            self.playwright = await async_playwright().start()
            headless = os.getenv("MARKET_BROWSER_HEADLESS", "true").lower() == "true"
            self.browser = await self.playwright.chromium.launch(headless=headless)
            self.context = await self.browser.new_context(
                viewport={"width": 1280, "height": 800}, accept_downloads=True, service_workers="block"
            )
            self.context.on("page", self._on_new_page)
            await self.context.route("**/*", self._route_request)
            self.page = await self.context.new_page()
            await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return await self.get_state()
        except BaseException:
            await self.close()
            raise

    def _on_new_page(self, page: Page) -> None:
        self.page = page
        logger.debug("[BROWSER NEW PAGE] skill=%s", self.policy["skill"])

    async def _route_request(self, route) -> None:
        request = route.request
        if request.is_navigation_request():
            try:
                validate_url(request.url, self.policy)
                logger.debug("[NAVIGATION ALLOWED] skill=%s", self.policy["skill"])
            except (ValueError, TypeError) as exc:
                self.last_navigation_failure = "Navigation outside the Skill HTTPS host policy"
                logger.warning("[NAVIGATION BLOCKED] %s", self.last_navigation_failure)
                await route.abort()
                return
        await route.continue_()

    def require_page(self) -> Page:
        if self.page is None or self.page.is_closed():
            if self.context is not None:
                live = [p for p in self.context.pages if not p.is_closed()]
                if live:
                    self.page = live[-1]
            if self.page is None or self.page.is_closed():
                raise RuntimeError("Browser page not available")
        return self.page

    async def get_state(self) -> dict:
        try:
            page = self.require_page()
        except RuntimeError:
            return {"status": "not_started", "url": None, "title": None}
        url = page.url
        if url.startswith("chrome-error://"):
            return {"status": "navigation_error", "url": url, "title": await page.title(),
                    "reason": self.last_navigation_failure or "Chromium navigation failure"}
        if url != "about:blank":
            try:
                validate_url(url, self.policy)
            except ValueError as exc:
                return {"status": "navigation_blocked", "url": url, "title": await page.title(),
                        "reason": str(exc)}
        return {"status": "ready", "url": url, "title": await page.title(),
                "open_pages": len([p for p in self.context.pages if not p.is_closed()]) if self.context else 0}

    async def navigate(self, url: str) -> dict:
        validate_url(url, self.policy)
        page = self.require_page()
        self.last_navigation_failure = None
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return await self.get_state()

    async def screenshot(self) -> bytes:
        return await self.require_page().screenshot(type="png", animations="disabled")

    async def extract_links(self, limit: int = 50) -> list[dict]:
        """Read-only visible anchor metadata; caller defines what 'first result' means."""
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        page = self.require_page()
        return await page.locator("a[href]").evaluate_all("""(nodes, limit) => nodes
            .filter(a => { const r = a.getBoundingClientRect(); const s = getComputedStyle(a);
              return r.width > 0 && r.height > 0 && s.visibility !== 'hidden'; })
            .slice(0, limit).map(a => ({text: (a.innerText || a.getAttribute('aria-label') || '').trim().slice(0, 300), href: a.href}))""", limit)

    async def read_text(self, max_chars: int = 12000) -> str:
        if not 1 <= max_chars <= 30000:
            raise ValueError("max_chars out of range")
        return (await self.require_page().locator("body").inner_text())[:max_chars]

    async def unique(self, selectors):
        for selector in ([selectors] if isinstance(selectors, str) else selectors or []):
            locator = self.require_page().locator(selector)
            if await locator.count() == 1 and await locator.is_visible():
                return locator
        raise ValueError("Visible unique element not found; verify the selector")

    
    async def authenticated(self) -> bool:
        """설정된 로그인 성공 선택자로 인증 여부를 확인한다."""
        selector = self.policy["selectors"].get("authenticated")

        if not selector:
            return False

        locator = self.require_page().locator(selector)

        return (
            await locator.count() > 0
            and await locator.first.is_visible()
        )

    async def login(self) -> dict:
        """서버 환경변수 기반 로그인 및 인증 상태 확인."""
        page = self.require_page()
        validate_url(page.url, self.policy)

        selectors = self.policy["selectors"]

        # 이미 로그인된 경우
        if await self.authenticated():
            return {
                "status": "authenticated",
                "url": page.url,
            }

        credentials = self.policy.get("credentials", {})

        username_env = credentials.get("username_env")
        password_env = credentials.get("password_env")

        if not username_env or not password_env:
            return {
                "status": "credentials_required",
                "message": "Skill의 계정 환경변수 설정이 없습니다.",
            }

        username = os.getenv(username_env, "").strip()
        password = os.getenv(password_env, "")

        if not username or not password:
            return {
                "status": "credentials_required",
                "message": "서버 계정정보가 설정되지 않았습니다.",
            }

        # 로그인 폼 확인
        username_input = await self.unique(
            selectors.get("username")
        )
        password_input = await self.unique(
            selectors.get("password")
        )
        button = await self.unique(
            selectors.get("submit")
        )

        try:
            # 계정정보 입력
            await username_input.fill(username)
            await password_input.fill(password)

            # 로그인 제출
            await button.click(timeout=15000)

            auth_selector = selectors.get("authenticated")

            # 성공 검증 선택자가 없는 경우
            if not auth_selector:
                return {
                    "status": "login_submitted_unverified",
                    "url": self.require_page().url,
                    "message": (
                        "로그인 요청은 제출됐지만 "
                        "인증 성공 선택자가 설정되지 않았습니다."
                    ),
                }

            # 로그인 성공 요소가 나타날 때까지 대기
            try:
                await self.require_page().locator(
                    auth_selector
                ).first.wait_for(
                    state="visible",
                    timeout=10000,
                )
            except PlaywrightTimeoutError:
                pass

            # 인증 성공 여부 최종 확인
            if await self.authenticated():
                return {
                    "status": "authenticated",
                    "url": self.require_page().url,
                }

            return {
                "status": "login_unconfirmed",
                "url": self.require_page().url,
                "message": (
                    "로그인 요청은 제출됐지만 "
                    "인증 성공을 확인하지 못했습니다."
                ),
            }

        finally:
            # 로그인 폼에 계정정보가 남아 있으면 제거
            for locator in (username_input, password_input):
                try:
                    if await locator.is_visible():
                        await locator.fill("")
                except Exception:
                    pass

    async def read_rows(self, limit: int) -> dict:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if not await self.authenticated():
            return {"status": "authentication_unverified"}
        selectors = self.policy["selectors"]
        required = ("rows", "order_id", "product_name", "status")
        if any(not selectors.get(key) for key in required):
            raise ValueError("Configure verified row and column selectors in the Skill")
        rows = self.require_page().locator(selectors["rows"])
        total = await rows.count()
        orders = []
        for index in range(min(total, limit)):
            row = rows.nth(index)
            if not await row.is_visible():
                continue
            values = {key: (await row.locator(selectors[key]).first.inner_text(timeout=5000)).strip()
                      for key in required[1:]}
            if not values["order_id"]:
                raise ValueError("Empty order ID in rendered row")
            orders.append({"market_id": self.policy["skill"], **values})
        return {"status": "completed", "orders": orders, "visible_row_count": total,
                "returned_count": len(orders), "note": "Rendered rows only; pagination is not included."}

    async def download(self, folder: Path, selector: str | None = None) -> dict:
        if not await self.authenticated():
            return {"status": "authentication_unverified"}
        button = await self.unique(selector or self.policy["selectors"].get("download"))
        page = self.require_page()
        async with page.expect_download(timeout=30000) as info:
            await button.click(timeout=10000)
        download = await info.value
        if await download.failure():
            raise RuntimeError("Browser download failed")
        folder.mkdir(parents=True, exist_ok=True)
        # Browser-supplied names must never control a server path.
        suffix = Path(download.suggested_filename).suffix.lower()
        if suffix not in {".xlsx", ".xls", ".csv", ".zip"}:
            suffix = ".bin"
        path = folder / f"{uuid4().hex}{suffix}"
        try:
            await download.save_as(str(path))
            size = path.stat().st_size
            if not size:
                raise ValueError("Empty download")
            digest = await asyncio.to_thread(lambda: hashlib.sha256(path.read_bytes()).hexdigest())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return {"status": "completed", "file_path": str(path), "bytes": size, "sha256": digest,
                "note": "Validate file format and content before processing."}

    async def action(self, action: str, params: dict, folder: Path) -> dict:
        """Execute one generic operation; menu and filter choices come from the Skill."""
        page = self.require_page()
        validate_url(page.url, self.policy)
        if action == "navigate":
            url = params.get("url") or self.policy["selectors"].get("orders_url")
            return await self.navigate(url)
        if action == "read":
            return {"status": "completed", "links": await self.extract_links(params.get("limit", 50)),
                    "visible_text": await self.read_text()}
        
        if action == "check_login":
            if await self.authenticated():
                return {
                    "status": "authenticated",
                    "url": page.url,
                }

            return {
                "status": "authentication_unverified",
                "url": page.url,
                "reason": (
                    "authenticated_selector_missing"
                    if not self.policy["selectors"].get(
                        "authenticated"
                    )
                    else "authenticated_marker_not_visible"
                ),
            }

        if action == "login":
            return await self.login()
        if action == "read_rows":
            return await self.read_rows(params.get("limit", 50))
        if action == "download":
            return await self.download(folder, params.get("selector"))
        if action in {"click", "fill", "select"}:
            locator = await self.unique(params.get("selector"))
            if action == "click":
                await locator.click(timeout=10000)
            elif action == "fill":
                if await locator.get_attribute("type") == "password":
                    raise PermissionError("Use the server-side login action for credentials")
                await locator.fill(params["text"])
            else:
                await locator.select_option(params["value"])
        elif action in {"scroll", "keypress", "wait"}:
            await self.execute_action({**params, "type": action})
        else:
            raise ValueError("Unsupported browser action")
        return await self.get_state()

    async def execute_action(self, action: dict) -> None:
        page = self.require_page()
        kind = action.get("type")
        if kind == "screenshot":
            return
        if kind == "wait":
            await asyncio.sleep(0.5)
        elif kind == "click":
            button = action.get("button", "left")
            if button not in ("left", "right", "middle"):
                raise ValueError("Unsupported mouse button")
            await page.mouse.click(action["x"], action["y"], button=button)
        elif kind == "double_click":
            await page.mouse.dblclick(action["x"], action["y"])
        elif kind == "move":
            await page.mouse.move(action["x"], action["y"])
        elif kind == "scroll":
            await page.mouse.move(action["x"], action["y"])
            await page.mouse.wheel(action.get("scroll_x", 0), action.get("scroll_y", 0))
        elif kind == "type":
            await page.keyboard.insert_text(action["text"])
        elif kind == "keypress":
            keys = action.get("keys", [])
            if not keys:
                raise ValueError("Missing keys")
            key_map = {"CTRL": "Control", "CONTROL": "Control", "SHIFT": "Shift", "ALT": "Alt",
                       "META": "Meta", "CMD": "Meta", "ENTER": "Enter", "RETURN": "Enter",
                       "ESC": "Escape", "ESCAPE": "Escape", "BACKSPACE": "Backspace",
                       "SPACE": "Space", "ARROWUP": "ArrowUp", "ARROWDOWN": "ArrowDown",
                       "ARROWLEFT": "ArrowLeft", "ARROWRIGHT": "ArrowRight"}
            normalized = [key_map.get(str(k).upper(), str(k)) for k in keys]
            await page.keyboard.press("+".join(normalized))
        elif kind == "drag":
            path = action.get("path", [])
            if len(path) < 2:
                raise ValueError("Invalid drag path")
            def point(p):
                return (p["x"], p["y"]) if isinstance(p, dict) else (p[0], p[1])
            x, y = point(path[0])
            await page.mouse.move(x, y)
            await page.mouse.down()
            try:
                for p in path[1:]:
                    x, y = point(p)
                    await page.mouse.move(x, y)
            finally:
                await page.mouse.up()
        else:
            raise ValueError(f"Unsupported computer action: {kind}")
        await asyncio.sleep(0.2)
        if self.context:
            live = [p for p in self.context.pages if not p.is_closed()]
            if live:
                self.page = live[-1]

    async def close(self) -> None:
        context, browser, playwright = self.context, self.browser, self.playwright
        self.context = self.browser = self.playwright = self.page = None
        try:
            if context:
                await context.close()
        finally:
            try:
                if browser:
                    await browser.close()
            finally:
                if playwright:
                    await playwright.stop()
