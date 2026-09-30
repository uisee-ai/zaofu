"""Kernel-owned, passive Playwright capture for local Self-Issue evidence."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

from zf.runtime.self_issue_intake import sanitize_attachment_for_disclosure


_DOCKER_IMAGE = "mcp/playwright:latest"
_CAPTURE_TIMEOUT_SECONDS = 20
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass(frozen=True)
class BrowserCaptureResult:
    status: str
    reason: str
    screenshot_ref: dict[str, Any] | None = None


def capture_self_issue_browser_evidence(
    *,
    state_dir: Path,
    draft_id: str,
    run_id: str,
    reporter_context: dict[str, Any],
    enabled: bool,
    configured_base_url: str = "",
    denied_urls: list[str] | None = None,
    runner: Any = subprocess.run,
) -> BrowserCaptureResult:
    """Capture one clean local viewport without clicks, input, cookies, or auth."""
    if not enabled:
        return BrowserCaptureResult("not_requested", "Automatic browser capture is disabled.")
    hint = reporter_context.get("browser_capture")
    hint = hint if isinstance(hint, dict) else {}
    if not bool(hint.get("requested")) or hint.get("target") != "kanban_board":
        return BrowserCaptureResult(
            "not_requested", "The incident reporter did not request a browser capture.",
        )
    base_url = str(hint.get("base_url") or configured_base_url or "").strip()
    project_id = str(hint.get("project_id") or "").strip()
    try:
        from zf.core.security.capture_urls import validate_capture_urls, blocked_capture_url
        rules = validate_capture_urls(denied_urls or [])
        url = _local_kanban_url(base_url, project_id=project_id)
        if blocked_capture_url(url, rules):
            return BrowserCaptureResult("blocked_by_policy", "This URL is excluded from browser evidence by project policy.")
    except ValueError as exc:
        return BrowserCaptureResult("not_available", str(exc))

    root = Path(state_dir).resolve()
    output_dir = root / "artifacts" / "self-issues" / draft_id / "browser" / run_id
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(output_dir, 0o700)
    script = output_dir / "capture.js"
    output = output_dir / "playwright-clean-incident.png"
    script.write_text(_capture_script(rules), encoding="utf-8")
    os.chmod(script, 0o600)
    command = [
        "docker", "run", "--rm", "--network", "host",
        "--user", f"{os.getuid()}:{os.getgid()}", "--entrypoint", "node",
        "-v", f"{output_dir}:/evidence", _DOCKER_IMAGE,
        "/evidence/capture.js", url, "/evidence/playwright-clean-incident.png",
    ]
    try:
        completed = runner(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=_CAPTURE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        output.unlink(missing_ok=True)
        script.unlink(missing_ok=True)
        return BrowserCaptureResult(
            "timeout", f"Passive Playwright capture timed out after {_CAPTURE_TIMEOUT_SECONDS}s.",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        output.unlink(missing_ok=True)
        script.unlink(missing_ok=True)
        return BrowserCaptureResult(
            "not_available", f"Passive Playwright capture unavailable: {type(exc).__name__}.",
        )
    script.unlink(missing_ok=True)
    if completed.returncode != 0 or not output.is_file():
        output.unlink(missing_ok=True)
        category = "unsafe_page" if completed.returncode == 78 else "capture_failed"
        return BrowserCaptureResult(
            category,
            "The local page could not be captured safely; no screenshot was retained.",
        )
    try:
        sanitized, _ = sanitize_attachment_for_disclosure(
            output.read_bytes(), suffix=".png", content_type="image/png",
        )
    except ValueError:
        output.unlink(missing_ok=True)
        return BrowserCaptureResult(
            "invalid_output", "Playwright returned an invalid image; it was discarded.",
        )
    output.write_bytes(sanitized)
    os.chmod(output, 0o600)
    digest = hashlib.sha256(sanitized).hexdigest()
    descriptor = {
        "ref": output.relative_to(root).as_posix(),
        "sha256": digest,
        "byte_count": len(sanitized),
        "capture_source": "playwright",
        "capture_kind": "playwright_clean_reproduction",
        "content_type": "image/png",
        "target": "kanban_board",
    }
    return BrowserCaptureResult(
        "captured",
        "A clean, passive local Kanban viewport was captured; it is not the user's existing tab.",
        descriptor,
    )


def _local_kanban_url(base_url: str, *, project_id: str) -> str:
    parsed = urlsplit(base_url)
    if parsed.scheme != "http" or parsed.hostname not in _LOOPBACK_HOSTS:
        raise ValueError("Browser capture requires an explicit loopback HTTP Web URL.")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Browser capture URL contains unsupported authority data.")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("Browser capture URL has an invalid port.") from exc
    query = urlencode({
        **({"project": project_id} if project_id else {}),
        "page": "board",
        "view": "board",
    })
    return urlunsplit((parsed.scheme, parsed.netloc, "/", query, ""))


def _capture_script(denied_urls: list[str] | None = None) -> str:
    config = json.dumps({
        "viewport": {"width": 1440, "height": 900},
        "denied": denied_urls or [],
        "mask": (
            "input,textarea,[contenteditable=true],[data-sensitive],"
            "[class*=self-issue],[class*=agent-message],[class*=chat-message],"
            "[class*=operator-output],[aria-label*=token i],[aria-label*=secret i]"
        ),
    })
    return f'''const {{ chromium }} = require('/app/node_modules/playwright');
const cfg = {config};
(async () => {{
  const target = new URL(process.argv[2]);
  const output = process.argv[3];
  const browser = await chromium.launch({{
    executablePath: chromium.executablePath(),
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  }});
  const context = await browser.newContext({{ viewport: cfg.viewport, serviceWorkers: 'block' }});
  let excludedContent = false;
  function denied(raw) {{
    try {{
      const url = new URL(raw);
      const path = decodeURIComponent(url.pathname).replace(/\\/+$/, '') || '/';
      if (/%(?:2f|5c|00|25)/i.test(url.pathname)) return true;
      return cfg.denied.some(rawRule => {{
        const rule = new URL(rawRule);
        const prefix = decodeURIComponent(rule.pathname).replace(/\\/+$/, '') || '/';
        return url.origin === rule.origin && (prefix === '/' || path === prefix || path.startsWith(prefix + '/'));
      }});
    }} catch {{ return true; }}
  }}
  const page = await context.newPage();
  await context.route('**/*', async route => {{
    const raw = route.request().url();
    if (raw.startsWith('http:') || raw.startsWith('https:')) {{
      if (denied(raw)) {{ excludedContent = true; return route.abort('blockedbyclient'); }}
    }} else if (route.request().isNavigationRequest()) {{
      excludedContent = true; return route.abort('blockedbyclient');
    }}
    if (raw.startsWith('data:') || raw.startsWith('blob:')) return route.continue();
    const requested = new URL(raw);
    if (requested.protocol === target.protocol && requested.hostname === target.hostname && requested.port === target.port) {{
      // Fulfill a single hop so redirected navigation is checked again.
      const response = await route.fetch({{ maxRedirects: 0 }});
      return route.fulfill({{ response }});
    }}
    return route.abort('blockedbyclient');
  }});
  await page.goto(target.href, {{ waitUntil: 'domcontentloaded', timeout: 10000 }});
  await page.waitForTimeout(1000);
  const text = await page.locator('body').innerText().catch(() => '');
  const unsafe = /(?:Bearer\\s+[A-Za-z0-9._~-]{{12,}}|-----BEGIN [A-Z ]+PRIVATE KEY-----|(?:token|secret|password|api[_-]?key)\\s*[:=]\\s*\\S{{8,}})/i;
  if (unsafe.test(text)) {{ await browser.close(); process.exit(78); }}
  await page.addStyleTag({{ content: `${{cfg.mask}} {{ color: transparent !important; text-shadow: none !important; background: #d8dde6 !important; border-color: #d8dde6 !important; }} * {{ animation: none !important; transition: none !important; caret-color: transparent !important; }}` }});
  if (excludedContent || page.frames().some(frame => denied(frame.url()))) {{ await browser.close(); process.exit(78); }}
  await page.screenshot({{ path: output, fullPage: false, animations: 'disabled' }});
  await browser.close();
}})().catch(async error => {{
  const detail = String(error && error.message || '').replace(/\\s+/g, ' ').slice(0, 300);
  console.error(`${{error && error.name || 'Error'}}: ${{detail}}`);
  process.exit(1);
}});
'''


__all__ = ["BrowserCaptureResult", "capture_self_issue_browser_evidence"]
