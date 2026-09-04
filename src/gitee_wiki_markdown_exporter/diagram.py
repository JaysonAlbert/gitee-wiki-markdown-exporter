"""Local draw.io component rendering through the Chrome DevTools Protocol."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ElementTree
from copy import deepcopy
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

import websocket

CHROME_PATH_ENV = "GWME_CHROME_PATH"


class DiagramRenderError(RuntimeError):
    """Raised when a diagram cannot be rendered without exposing its XML."""


class DiagramRenderer(Protocol):
    """Render one draw.io XML component into one SVG per diagram page."""

    def render(self, xml: str) -> tuple[str, ...]: ...


class ChromeDiagramRenderer:
    """Render draw.io XML with the Gitee preview app in an isolated local browser."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float = 30,
        executable: str | None = None,
    ) -> None:
        self.preview_url = base_url.rstrip("/") + "/assets-wiki/diagram/preview.html"
        self.timeout = timeout
        self.executable = executable

    def render(self, xml: str) -> tuple[str, ...]:
        """Return portable SVG pages without writing the source XML to disk."""
        if not xml.strip():
            raise DiagramRenderError("diagram component XML is empty")
        page_documents = _split_mxfile_pages(xml)
        executable = self.executable or _find_chrome()
        if executable is None:
            raise DiagramRenderError(
                f"Chrome, Chromium, or Edge was not found; set {CHROME_PATH_ENV}"
            )

        with tempfile.TemporaryDirectory(prefix="gwme-chrome-") as profile:
            command = [
                executable,
                "--headless=new",
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-default-apps",
                "--disable-extensions",
                "--disable-sync",
                "--metrics-recording-only",
                "--no-default-browser-check",
                "--no-first-run",
                "--remote-allow-origins=*",
                "--remote-debugging-port=0",
                f"--user-data-dir={profile}",
                "about:blank",
            ]
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                command.insert(1, "--no-sandbox")
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            connection: websocket.WebSocket | None = None
            try:
                port, browser_path = _wait_for_devtools(Path(profile), process, self.timeout)
                connection = websocket.create_connection(
                    f"ws://127.0.0.1:{port}{browser_path}", timeout=self.timeout
                )
                cdp = _DevTools(connection, allowed_origin=_origin(self.preview_url))
                target = cdp.call("Target.createTarget", {"url": "about:blank"})["targetId"]
                session = cdp.call("Target.attachToTarget", {"targetId": target, "flatten": True})[
                    "sessionId"
                ]
                for method in ("Page.enable", "Runtime.enable", "Network.enable"):
                    cdp.call(method, session_id=session)
                cdp.call("Page.navigate", {"url": self.preview_url}, session_id=session)
                _wait_for_expression(
                    cdp,
                    session,
                    "typeof window.loadXML === 'function'",
                    self.timeout,
                    "Gitee diagram preview did not load",
                )
                cdp.call(
                    "Fetch.enable",
                    {
                        "patterns": [
                            {"urlPattern": "http://*/*"},
                            {"urlPattern": "https://*/*"},
                        ]
                    },
                    session_id=session,
                )
                pages: list[str] = []
                for page_xml in page_documents:
                    cdp.evaluate(f"window.loadXML({json.dumps(page_xml)}); true", session)
                    _wait_for_expression(
                        cdp,
                        session,
                        "(() => { const svg = document.querySelector('.geDiagramContainer svg'); "
                        "if (!svg) return false; const r = svg.getBoundingClientRect(); "
                        "return r.width > 0 && r.height > 0; })()",
                        self.timeout,
                        "diagram preview did not produce SVG",
                    )
                    value = cdp.evaluate(_SVG_EXPRESSION, session)
                    if not isinstance(value, str) or not value.startswith("<svg"):
                        raise DiagramRenderError("diagram preview returned invalid SVG")
                    pages.append(value)
                return tuple(pages)
            except DiagramRenderError:
                raise
            except (
                KeyError,
                OSError,
                TypeError,
                ValueError,
                websocket.WebSocketException,
            ) as error:
                raise DiagramRenderError(
                    f"local browser could not render the diagram ({type(error).__name__})"
                ) from error
            finally:
                if connection is not None:
                    connection.close()
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)


class _DevTools:
    def __init__(
        self, connection: websocket.WebSocket, *, allowed_origin: tuple[str, str, int | None]
    ) -> None:
        self.connection = connection
        self.allowed_origin = allowed_origin
        self.request_id = 0

    def call(
        self,
        method: str,
        params: dict[str, object] | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        self.request_id += 1
        expected_id = self.request_id
        request: dict[str, object] = {
            "id": expected_id,
            "method": method,
            "params": params or {},
        }
        if session_id is not None:
            request["sessionId"] = session_id
        self.connection.send(json.dumps(request))
        while True:
            response = json.loads(self.connection.recv())
            if response.get("method") == "Fetch.requestPaused":
                self._handle_paused_request(response)
                continue
            if response.get("id") != expected_id:
                continue
            if "error" in response:
                raise DiagramRenderError(f"local browser command {method} failed")
            result = response.get("result")
            if not isinstance(result, dict):
                raise DiagramRenderError(f"local browser command {method} returned no result")
            return result

    def _handle_paused_request(self, event: dict[str, Any]) -> None:
        params = event.get("params")
        if not isinstance(params, dict) or not isinstance(params.get("requestId"), str):
            return
        request = params.get("request")
        url = request.get("url") if isinstance(request, dict) else None
        same_origin = isinstance(url, str) and _origin(url) == self.allowed_origin
        method = "Fetch.continueRequest" if same_origin else "Fetch.failRequest"
        command_params: dict[str, object] = {"requestId": params["requestId"]}
        if method == "Fetch.failRequest":
            command_params["errorReason"] = "BlockedByClient"
        self.request_id += 1
        command: dict[str, object] = {
            "id": self.request_id,
            "method": method,
            "params": command_params,
        }
        if isinstance(event.get("sessionId"), str):
            command["sessionId"] = event["sessionId"]
        self.connection.send(json.dumps(command))

    def evaluate(self, expression: str, session_id: str) -> object:
        result = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            session_id=session_id,
        )
        remote = result.get("result")
        if not isinstance(remote, dict):
            raise DiagramRenderError("local browser evaluation returned no result")
        if remote.get("subtype") == "error" or "exceptionDetails" in result:
            raise DiagramRenderError("local browser evaluation failed")
        return remote.get("value")


def _wait_for_devtools(
    profile: Path, process: subprocess.Popen[bytes], timeout: float
) -> tuple[int, str]:
    marker = profile / "DevToolsActivePort"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise DiagramRenderError("local browser exited before rendering the diagram")
        try:
            lines = marker.read_text(encoding="utf-8").splitlines()
        except OSError:
            time.sleep(0.05)
            continue
        if len(lines) >= 2:
            return int(lines[0]), lines[1]
        time.sleep(0.05)
    raise DiagramRenderError("local browser did not start before the render timeout")


def _wait_for_expression(
    cdp: _DevTools,
    session_id: str,
    expression: str,
    timeout: float,
    message: str,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cdp.evaluate(expression, session_id):
            return
        time.sleep(0.1)
    raise DiagramRenderError(message)


def _split_mxfile_pages(xml: str) -> tuple[str, ...]:
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as error:
        raise DiagramRenderError("diagram component is not valid XML") from error
    if root.tag.rsplit("}", 1)[-1] != "mxfile":
        return (xml,)
    diagrams = [child for child in root if child.tag.rsplit("}", 1)[-1] == "diagram"]
    if not diagrams:
        raise DiagramRenderError("diagram component contains no pages")
    pages: list[str] = []
    for diagram in diagrams:
        page_root = ElementTree.Element(root.tag, root.attrib)
        page_root.append(deepcopy(diagram))
        pages.append(ElementTree.tostring(page_root, encoding="unicode"))
    return tuple(pages)


def _find_chrome() -> str | None:
    configured = os.environ.get(CHROME_PATH_ENV)
    if configured:
        return configured if Path(configured).is_file() else None
    for name in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "microsoft-edge",
        "msedge",
    ):
        if executable := shutil.which(name):
            return executable
    for candidate in (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ):
        if Path(candidate).is_file():
            return candidate
    return None


_SVG_EXPRESSION = r"""
(() => {
  const source = document.querySelector('.geDiagramContainer svg');
  if (!source) return null;
  let box;
  try { box = source.getBBox(); } catch (_) { box = null; }
  const rect = source.getBoundingClientRect();
  const x = box && Number.isFinite(box.x) ? box.x : 0;
  const y = box && Number.isFinite(box.y) ? box.y : 0;
  const width = Math.max(1, box && box.width ? box.width : rect.width);
  const height = Math.max(1, box && box.height ? box.height : rect.height);
  const padding = 8;
  const clone = source.cloneNode(true);
  clone.querySelectorAll('script').forEach(node => node.remove());
  [clone, ...clone.querySelectorAll('*')].forEach(node => {
    for (const attr of Array.from(node.attributes || [])) {
      if (/^on/i.test(attr.name)) node.removeAttribute(attr.name);
      if (/^(?:href|xlink:href)$/i.test(attr.name)) {
        const href = attr.value.trim();
        if (/^javascript:/i.test(href) ||
            (node.localName !== 'a' && href && !href.startsWith('#') &&
             !href.startsWith('data:'))) {
          node.removeAttribute(attr.name);
        }
      }
      if (attr.name.toLowerCase() === 'style') {
        node.setAttribute(attr.name, attr.value.replace(/url\(\s*(['"]?)https?:.*?\)/gi, 'none'));
      }
    }
  });
  clone.querySelectorAll('style').forEach(node => {
    node.textContent = (node.textContent || '')
      .replace(/@import[^;]+;?/gi, '')
      .replace(/url\(\s*(['"]?)https?:.*?\)/gi, 'none');
  });
  clone.querySelectorAll('image').forEach(node => {
    const href = node.getAttribute('href') || node.getAttribute('xlink:href') || '';
    if (href && !href.startsWith('data:')) node.remove();
  });
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  clone.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink');
  clone.setAttribute('viewBox', `${x-padding} ${y-padding} ${width+padding*2} ${height+padding*2}`);
  clone.setAttribute('width', String(Math.ceil(width + padding * 2)));
  clone.setAttribute('height', String(Math.ceil(height + padding * 2)));
  clone.removeAttribute('style');
  return new XMLSerializer().serializeToString(clone);
})()
"""


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlparse(url)
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port
