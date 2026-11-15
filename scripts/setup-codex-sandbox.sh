#!/usr/bin/env bash
# Ubuntu 24.04 host prerequisites. Invoke as the intended non-root service user.
set -euo pipefail

mode="${1:---check}"
if [[ $# -gt 1 || ( "$mode" != "--check" && "$mode" != "--apply" ) ]]; then
  printf '%s\n' 'Usage: bash scripts/setup-codex-sandbox.sh [--check|--apply]' >&2
  exit 2
fi
if [[ "$EUID" -eq 0 ]]; then
  printf '%s\n' 'Run as the intended ordinary service user; --apply uses sudo only for host packages/profile.' >&2
  exit 2
fi

if [[ "$mode" == "--apply" ]]; then
  # /etc/os-release is the host-owned distribution identity, never project config.
  . /etc/os-release
  if [[ "${ID:-}" != ubuntu || "${VERSION_ID:-}" != 24.04 ]]; then
    printf '%s\n' 'Automatic repair is qualified for Ubuntu 24.04 only. Use the distro bubblewrap package and run --check.' >&2
    exit 2
  fi
  packages=(bubblewrap apparmor-profiles apparmor-utils)
  missing=0
  for package in "${packages[@]}"; do
    if [[ "$(dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null || true)" != installed ]]; then
      missing=1
    fi
  done
  if [[ "$missing" -eq 1 ]]; then
    sudo apt-get update
    sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l apt-get install -y --no-install-recommends "${packages[@]}"
  fi
  source_profile=/usr/share/apparmor/extra-profiles/bwrap-userns-restrict
  target_profile=/etc/apparmor.d/bwrap-userns-restrict
  if [[ ! -f "$source_profile" || -L "$source_profile" || -L "$target_profile" ]]; then
    printf '%s\n' 'Expected regular distribution profile unavailable; no custom policy will be generated.' >&2
    exit 1
  fi
  if [[ -e "$target_profile" ]]; then
    if ! cmp -s "$source_profile" "$target_profile"; then
      printf '%s\n' 'Existing profile differs from the distribution file; preserve it for administrator review.' >&2
      exit 1
    fi
  else
    sudo install -m 0644 "$source_profile" "$target_profile"
  fi
  sudo apparmor_parser -r "$target_profile"
fi

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if command -v uv >/dev/null 2>&1; then
  exec uv run --project "$repo_root" zf doctor provider --backend codex --json
fi
if command -v zf >/dev/null 2>&1; then
  exec zf doctor provider --backend codex --json
fi
printf '%s\n' 'Install the project dependencies (uv sync --extra dev --extra web), then rerun --check.' >&2
exit 1
