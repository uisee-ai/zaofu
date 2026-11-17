from pathlib import Path

from zf.autoresearch.orchestrator import AutoresearchRunConfig, _apply_autoresearch_controller_overrides


def test_controller_skills_follow_experiment_checkout_without_duplicate_roots(tmp_path: Path):
    worktree = tmp_path / "experiment"
    (worktree / "skills").mkdir(parents=True)
    (worktree / "yoke").mkdir()
    data = {"skill_sources": [
        {"name": "zaofu-skills", "path": "/old/harness/skills"},
        {"name": "yoke", "path": "/old/harness/yoke"},
        {"name": "customer", "path": "/customer/skills"},
    ]}
    _apply_autoresearch_controller_overrides(data, cfg=AutoresearchRunConfig(worktree=worktree), run_id="diagnosis")
    # Project skills are loaded implicitly; adding the root duplicates candidates.
    assert data["skill_sources"] == [
        {"name": "yoke", "path": str(worktree / "yoke")},
        {"name": "customer", "path": "/customer/skills"},
    ]


def test_controller_pins_local_yoke_instead_of_implicit_driver_fallback(tmp_path: Path):
    (tmp_path / "yoke").mkdir()
    data = {}
    _apply_autoresearch_controller_overrides(data, cfg=AutoresearchRunConfig(worktree=tmp_path), run_id="diagnosis")
    assert data["skill_sources"] == [{"name": "yoke", "path": str(tmp_path / "yoke"), "mode": "readonly"}]


def test_explicit_zero_token_budget_survives_inner_template(tmp_path):
    data = {"workflow": {"run_limits": {"token_budget": 10_000_000}}}
    cfg = AutoresearchRunConfig(worktree=tmp_path, token_budget=0)
    _apply_autoresearch_controller_overrides(data, cfg=cfg, run_id="diagnosis")
    assert data["workflow"]["run_limits"]["token_budget"] == 0


def test_unspecified_token_budget_keeps_template_limit(tmp_path):
    data = {"workflow": {"run_limits": {"token_budget": 10_000_000}}}
    cfg = AutoresearchRunConfig(worktree=tmp_path)
    _apply_autoresearch_controller_overrides(data, cfg=cfg, run_id="diagnosis")
    assert data["workflow"]["run_limits"]["token_budget"] == 10_000_000
