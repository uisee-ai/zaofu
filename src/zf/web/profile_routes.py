"""Project profile Web API (doc 102 B6) — read-only detect + recommend.

Sibling router (server oversized 纪律). Read-only projections, no token: they
only *read* the filesystem to detect stack. Materialize/init mutations go through
the server's existing token-gated init action, not here.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse


def build_profile_router() -> APIRouter:
    router = APIRouter()

    @router.get("/profile-bootstrap", response_class=HTMLResponse)
    def profile_bootstrap_ui() -> str:
        return _BOOTSTRAP_HTML

    @router.get("/api/presets")
    def presets() -> dict:
        """Archetype catalog for the wizard: validated prod flows + lightweight
        preset fallback (doc 102 §6.2)."""
        from zf.core.config.presets import PRESET_DESCRIPTIONS, get_preset
        from zf.core.profile.flows import list_flows_detailed

        items: list[dict] = []
        for f in list_flows_detailed():
            items.append({
                "name": f["id"], "description": f["description"],
                "roleCount": f["roles"], "kind": "flow", "backend": f["backend"],
            })
        for name in ("minimal", "code-assist"):
            roles = [r.get("name", "") for r in get_preset(name).get("roles", [])
                     if isinstance(r, dict) and r.get("name")]
            items.append({
                "name": name, "description": PRESET_DESCRIPTIONS.get(name, ""),
                "roleCount": len(roles), "kind": "preset", "backend": "",
            })
        return {"presets": items}

    @router.get("/api/profile/detect")
    def profile_detect(path: str = Query(default=".")) -> dict:
        root = Path(path).expanduser()
        if not root.exists() or not root.is_dir():
            raise HTTPException(status_code=404, detail="path not found")
        from zf.core.profile.detector import detect

        return detect(root).to_dict()

    @router.get("/api/profile/recommend")
    def profile_recommend(
        path: str = Query(default="."),
        intent: str = Query(default="build"),
        stack: str | None = Query(default=None),
        surface: str | None = Query(default=None),
        scale: str | None = Query(default=None),
        backend: str = Query(default="claude"),
    ) -> dict:
        from zf.core.profile.detector import declared_profile, detect
        from zf.core.profile.recommender import recommend

        if stack:
            try:
                profile = declared_profile(stack, surface or "")
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            declared = True
        else:
            root = Path(path).expanduser()
            if not root.exists() or not root.is_dir():
                raise HTTPException(status_code=404, detail="path not found")
            profile = detect(root)
            declared = False
        rec = recommend(profile, intent, declared=declared, scale=scale, backend=backend)
        return {"profile": profile.to_dict(), "recommendation": rec.to_dict()}

    return router


_BOOTSTRAP_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8"/>
<title>ZaoFu — Project Bootstrap</title>
<style>
 body{font:14px/1.5 system-ui,sans-serif;max-width:840px;margin:2rem auto;padding:0 1rem;color:#1a1a2e}
 h1{font-size:20px}
 .row{display:flex;gap:.5rem;align-items:center;margin:.5rem 0;flex-wrap:wrap}
 input,select{padding:.4rem;border:1px solid #ccc;border-radius:6px;font:inherit}
 input[type=text]{flex:1;min-width:260px}
 button{padding:.45rem .9rem;border:0;border-radius:6px;background:#3b3b98;color:#fff;cursor:pointer}
 button:disabled{opacity:.5}
 .card{border:1px solid #e0e0ec;border-radius:10px;padding:1rem;margin-top:1rem;background:#fafaff}
 .pill{display:inline-block;background:#e8e8ff;border-radius:99px;padding:.1rem .6rem;margin:.1rem;font-size:12px}
 .warn{color:#b00020;background:#ffeef0;border-radius:6px;padding:.5rem;margin-top:.5rem}
 .muted{color:#666;font-size:12px}
 code{background:#eee;padding:.05rem .3rem;border-radius:4px}
</style>
</head>
<body>
<h1>ZaoFu — Project Bootstrap 推荐器</h1>
<p class="muted">探测项目栈 → 推荐 zf.yaml(几个 stage / 几个 role)→ 初始化。CLI 等价:<code>zf profile bootstrap</code></p>
<div class="row">
  <input type="text" id="profile-path" placeholder="项目目录路径,如 /path/to/zaofu" />
  <select id="profile-intent">
    <option value="build">build</option>
    <option value="refactor">refactor</option>
    <option value="review">review</option>
    <option value="maintain">maintain</option>
  </select>
  <button id="detect-btn">探测 + 推荐</button>
</div>
<div id="result"></div>

<div class="card" id="init-card" style="display:none">
  <h3>初始化(Web 入口)</h3>
  <div class="row">
    <input type="text" id="init-token" placeholder="X-Zf-Web-Token(token-gated)" />
    <button id="init-btn">Initialize（含 profile overlay）</button>
  </div>
  <div id="init-result" class="muted"></div>
</div>

<script>
const $ = (id) => document.getElementById(id);
async function detect() {
  const path = $('profile-path').value.trim();
  const intent = $('profile-intent').value;
  $('result').innerHTML = '<p class="muted">探测中…</p>';
  const resp = await fetch(`/api/profile/recommend?path=${encodeURIComponent(path)}&intent=${intent}`);
  if (!resp.ok) { $('result').innerHTML = `<div class="warn">探测失败:${resp.status}</div>`; return; }
  const { profile, recommendation } = await resp.json();
  const units = profile.units.map(u => `<span class="pill" data-testid="unit">${u.root}: ${u.language}/${u.surface}</span>`).join('');
  const checks = recommendation.required_checks.map(c => `<code>${c}</code>`).join(' ');
  const roles = recommendation.roles.join(', ');
  $('result').innerHTML = `
    <div class="card">
      <div>探测: layout=<b>${profile.layout}</b> · confidence=<b>${profile.confidence}</b> · fullstack=<b data-testid="fullstack">${profile.is_fullstack}</b></div>
      <div style="margin:.4rem 0">${units}</div>
      <hr/>
      <div>推荐 archetype: <b data-testid="archetype">${recommendation.archetype}</b>
        <span class="muted">(${profile.units.length} 个单元)</span></div>
      <div>roles(<b data-testid="role-count">${recommendation.roles.length}</b> 个): ${roles}</div>
      <div>harness_profile: <b data-testid="harness-profile">${recommendation.harness_profile}</b></div>
      <div>required_checks: ${checks || '(空)'}</div>
      ${recommendation.misroute ? `<div class="warn" data-testid="misroute">⚠ ${recommendation.misroute}</div>` : ''}
    </div>`;
  $('init-card').style.display = 'block';
}
async function doInit() {
  const root = $('profile-path').value.trim();
  const token = $('init-token').value.trim();
  $('init-result').textContent = '初始化中…';
  const resp = await fetch('/api/workspace/projects/init', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Zf-Web-Token': token },
    body: JSON.stringify({ root, preset: $('result').querySelector('[data-testid=archetype]')?.textContent, apply_profile: true })
  });
  const body = await resp.json().catch(() => ({}));
  $('init-result').innerHTML = resp.ok
    ? `<span data-testid="init-ok">✅ initialized: ${body.state_dir || ''}</span>`
    : `<span class="warn" data-testid="init-err">❌ ${resp.status}: ${body.reason || body.reason || 'unauthorized'}</span>`;
}
$('detect-btn').addEventListener('click', detect);
$('init-btn').addEventListener('click', doInit);
</script>
</body>
</html>
"""
