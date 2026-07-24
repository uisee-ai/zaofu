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
  AGENTS.md
  CLAUDE.md
  zf.yaml
  feishu.yaml
  .python-version
  .env.example
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
  assets/readme
  docs/manual
  docs/design
  .claude/rules
  .claude/commands
  .claude/skills
  .codex/skills
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

if [[ -f "$tmp/feishu.yaml" ]]; then
  perl -0pi -e 's/\$\{([A-Za-z_][A-Za-z0-9_]*)\:-[^}]*\}/"\${".$1."}"/ge' "$tmp/feishu.yaml"
  set +e
  perl -ne '
    if (/^\s*(app_id|app_secret|encrypt_key|verification_token)\s*:\s*(.+?)\s*(?:#.*)?$/) {
      my $value = $2;
      $value =~ s/^\s+|\s+$//g;
      $value =~ s/^["'\'']|["'\'']$//g;
      if ($value ne "" && $value !~ /^\$\{[A-Za-z_][A-Za-z0-9_]*\}$/ && $value !~ /^[A-Z][A-Z0-9_]*$/) {
        exit 42;
      }
    }
  ' "$tmp/feishu.yaml"
  feishu_scan_status=$?
  set -e
  if [[ "$feishu_scan_status" -eq 42 ]]; then
    echo "literal Feishu credential field exported" >&2
    exit 1
  elif [[ "$feishu_scan_status" -ne 0 ]]; then
    echo "Feishu export sanitization scan failed" >&2
    exit 1
  fi
fi

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
