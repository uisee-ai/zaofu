"""zf project — project-scoped review and insight commands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from zf.core.config.loader import ConfigError
from zf.runtime.project_spine_review import (
    SpineReviewError,
    build_project_spine_review,
    create_spine_review_proposal,
    render_spine_review_markdown,
    resolve_spine_review_context,
    write_spine_review_artifact,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "project",
        help="Project-scoped review and insight commands",
    )
    sub = parser.add_subparsers(dest="project_cmd")

    review = sub.add_parser(
        "review-spine",
        help="Review project design/delivery/runtime spine",
    )
    _add_review_args(review)
    review_nested = review.add_subparsers(dest="review_spine_cmd")

    propose = review_nested.add_parser(
        "propose",
        help="Create a pending proposal from a spine review corrective action",
    )
    propose.add_argument("--project-root", type=Path, default=None)
    propose.add_argument("--state-dir", default=None)
    propose.add_argument("--review-id", required=True)
    propose.add_argument(
        "--action",
        required=True,
        help="1-based action index or action_id from the persisted review",
    )
    propose.add_argument("--json", action="store_true", help="Emit JSON")
    propose.set_defaults(func=_run_review_spine_propose)

    review.set_defaults(func=_run_review_spine)
    parser.set_defaults(func=_run_help)


def _add_review_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", type=Path, default=None)
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--since", default=None)
    parser.add_argument("--format", choices=["md", "json"], default="md")
    parser.add_argument(
        "--write-artifact",
        action="store_true",
        help="Persist report/reflection artifacts and append an artifact event",
    )


def _run_help(args: argparse.Namespace) -> int:
    del args
    print("Usage: zf project review-spine [--format md|json]", file=sys.stderr)
    return 2


def _run_review_spine(args: argparse.Namespace) -> int:
    try:
        context = resolve_spine_review_context(
            project_root=args.project_root,
            explicit_state_dir=args.state_dir,
        )
        review = build_project_spine_review(context, since=args.since)
        artifact = None
        if args.write_artifact:
            artifact = write_spine_review_artifact(context, review)
    except (ConfigError, SpineReviewError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        out = dict(review)
        if artifact is not None:
            out["artifact"] = artifact
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    print(render_spine_review_markdown(review), end="")
    if artifact is not None:
        print(f"\nArtifact: {artifact['artifact_dir']}")
        print(f"Artifact event: {artifact['event_id']}")
    return 0


def _run_review_spine_propose(args: argparse.Namespace) -> int:
    try:
        context = resolve_spine_review_context(
            project_root=args.project_root,
            explicit_state_dir=args.state_dir,
        )
        result = create_spine_review_proposal(
            context,
            review_id=args.review_id,
            action=args.action,
        )
    except (ConfigError, SpineReviewError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        proposal = result.get("proposal", {})
        print(f"Created spine review proposal: {result.get('event_id')}")
        print(f"- review_id: {proposal.get('review_id')}")
        print(f"- action_id: {proposal.get('action_id')}")
        print(f"- kind: {proposal.get('kind')}")
    return 0
