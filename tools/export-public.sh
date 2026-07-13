#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: export-public.sh --target PATH [--ref REF]
EOF
}

target=""
ref="HEAD"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      if [[ $# -lt 2 ]]; then
        usage
        exit 2
      fi
      target="$2"
      shift 2
      ;;
    --ref)
      if [[ $# -lt 2 ]]; then
        usage
        exit 2
      fi
      ref="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

if [[ -z "$target" ]]; then
  usage
  exit 2
fi

source_root="$(git rev-parse --show-toplevel)"
target_parent="$(dirname "$target")"
mkdir -p "$target_parent"
target_abs="$(realpath -m "$target")"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

includes=(
  README.md
  README.zh-CN.md
  LICENSE
  DISCLAIMER.md
  .python-version
  pyproject.toml
  uv.lock
  src
  web
  examples
  tests
  tools
  scripts
  skills
  yoke
  channel_roles
  docs/manual
)

existing=()
if [[ "$ref" == "HEAD" ]]; then
  for path in "${includes[@]}"; do
    if [[ -e "$source_root/$path" ]]; then
      existing+=("$path")
    fi
  done
else
  for path in "${includes[@]}"; do
    if git cat-file -e "$ref:$path" 2>/dev/null; then
      existing+=("$path")
    fi
  done
fi

if [[ ${#existing[@]} -eq 0 ]]; then
  echo "no public export paths exist at ref: $ref" >&2
  exit 1
fi

if [[ "$ref" == "HEAD" ]]; then
  (
    cd "$source_root"
    tar -cf - -- "${existing[@]}"
  ) | tar -x -C "$tmp"
else
  git archive --format=tar "$ref" -- "${existing[@]}" | tar -x -C "$tmp"
fi

escaped_source_root="$(printf '%s\n' "$source_root" | sed 's/[\/&]/\\&/g')"
while IFS= read -r -d '' file; do
  if grep -Iq . "$file"; then
    sed -i "s/${escaped_source_root}/\/path\/to\/zaofu/g" "$file"
  fi
done < <(find "$tmp" -type f -print0)

scan_pattern="${ZF_EXPORT_PRIVATE_RG_PATTERN:-}"
if [[ -n "$scan_pattern" ]]; then
  if command -v rg >/dev/null 2>&1; then
    set +e
    rg -n --hidden --no-ignore -e "$scan_pattern" "$tmp"
    scan_status=$?
    set -e
  else
    set +e
    grep -RInE -- "$scan_pattern" "$tmp"
    scan_status=$?
    set -e
  fi
  case "$scan_status" in
    0)
      echo "private pattern matched exported content" >&2
      exit 1
      ;;
    1)
      ;;
    *)
      echo "private sanitization scan failed" >&2
      exit 1
      ;;
  esac
fi

rm -rf "$target_abs"
mkdir -p "$target_abs"
(
  cd "$tmp"
  tar -cf - .
) | (
  cd "$target_abs"
  tar -xf -
)
