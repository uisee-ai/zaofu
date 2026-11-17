"""Install the pinned Herdr release and verify the real ZaoFu terminal adapter."""

import argparse
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile

from zf.web.terminal_backend_herdr import HerdrTerminalBackend


# Official release / latest.json checksums, verified 2026-11-17.
# Update the pin only with a real terminal smoke for the new release.
VERSION = "0.9.5"
SHA256 = {
    "linux-x86_64": "14d9e8bc8b4a9e0118d77c32266f76d461f09b40342eb23752960bd89f59929d",
    "linux-aarch64": "17d7536f2da50859523680ef279dffcac8e4a235f4e46fe8da6c2ec44141897c",
    "macos-x86_64": "5fb0a908ec7f6745e3e245738b06bfad56c46391c391ab3c0955fbead01f1369",
    "macos-aarch64": "fb77c2232a05a8035421382c894392f4174e4e38a18e48af3dd0290157c3a7e2",
}


def verify(binary: Path):
    capability = HerdrTerminalBackend(str(binary)).probe()
    if not capability.available or not capability.tab_rename:
        raise ValueError("Incompatible Herdr version or protocol (requires >=0.8.0, schema, observe/control and tab rename); repair the existing installation first")
    return capability


def digest(binary: Path) -> str:
    with binary.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def ensure_herdr(*, check_only: bool = False) -> None:
    existing = shutil.which("herdr")
    if existing:
        binary = Path(existing)
        capability = verify(binary)
    else:
        if check_only:
            raise ValueError("Herdr is missing; rerun without --skip-deps")
        system = {"Linux": "linux", "Darwin": "macos"}.get(platform.system())
        arch = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}.get(platform.machine())
        target = f"{system}-{arch}"
        if target not in SHA256:
            raise ValueError("Automatic Herdr installation supports Linux/macOS x64/arm64 only")
        binary = Path.home() / ".local/bin/herdr"
        if binary.exists() or binary.is_symlink():
            raise ValueError("Existing ~/.local/bin/herdr is unusable; repair it first. It will not be overwritten")
        binary.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".herdr-install-", dir=binary.parent) as directory:
            candidate = Path(directory) / "herdr"
            url = f"https://github.com/herdrdev/herdr/releases/download/v{VERSION}/herdr-{target}"
            print(f"Installing Herdr {VERSION} ({target}) with pinned SHA256 verification", flush=True)
            subprocess.run([
                "curl", "--proto", "=https", "--proto-redir", "=https", "-fsSL",
                "--retry", "2", "--connect-timeout", "10", "--max-time", "180",
                url, "-o", str(candidate),
            ], check=True, timeout=560)
            if digest(candidate) != SHA256[target]:
                raise ValueError("Herdr SHA256 verification failed; the downloaded file was not installed")
            candidate.chmod(0o755)
            capability = verify(candidate)
            if capability.version != VERSION:
                raise ValueError("Downloaded Herdr version does not match the pinned release")
            # Publish only a verified executable; never replace a concurrent install.
            os.link(candidate, binary)
    print(f"Herdr ready: {binary}; version={capability.version}; sha256={digest(binary)}")
    print("Web Terminal protocol checks passed; provider authentication and live sessions require separate verification")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    try:
        ensure_herdr(check_only=args.check_only)
    except ValueError as exc:
        print(f"Herdr not ready: {exc}")
        return 1
    except (OSError, subprocess.SubprocessError):
        print("Herdr not ready: Download or local file operation failed; existing installations are preserved. Check network access and directory permissions")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
