"""Pinned downloads, failure atomicity and real adapter probes, without network."""

import hashlib
import importlib.util
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def binary_body(version="0.9.5", *, rename_exit=0):
    return ("#!/bin/sh\n"
            f'if [ "$1" = --version ]; then echo "herdr {version}";\n'
            'elif [ "$1" = api ]; then echo "{}";\n'
            f'elif [ "$1" = tab ]; then exit {rename_exit};\n'
            "else exit 0; fi\n").encode()


@pytest.fixture
def installer(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("install_herdr", ROOT / "tools/install_herdr.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(module.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    monkeypatch.setattr(module.Path, "home", lambda: tmp_path)
    return module


def download_fixture(module, monkeypatch, payload, *, fail=False, publish=None):
    original = subprocess.run
    calls = []

    def run(argv, **kwargs):
        if argv[0] != "curl":
            return original(argv, **kwargs)
        calls.append(argv)
        if fail:
            raise subprocess.CalledProcessError(22, argv)
        Path(argv[-1]).write_bytes(payload)
        if publish:
            publish()
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(module.subprocess, "run", run)
    return calls


def test_installs_verified_release_and_reports_fingerprint(installer, tmp_path, monkeypatch, capsys):
    payload = binary_body()
    fingerprint = hashlib.sha256(payload).hexdigest()
    monkeypatch.setitem(installer.SHA256, "linux-x86_64", fingerprint)
    calls = download_fixture(installer, monkeypatch, payload)
    installer.ensure_herdr()
    binary = tmp_path / ".local/bin/herdr"
    assert binary.read_bytes() == payload
    assert binary.stat().st_mode & 0o777 == 0o755
    assert installer.verify(binary).available
    assert "/v0.9.5/herdr-linux-x86_64" in calls[0][-3]
    assert fingerprint in capsys.readouterr().out
    assert not list(binary.parent.glob(".herdr-install-*"))


@pytest.mark.parametrize("failure", ["checksum", "protocol", "version", "download"])
def test_download_or_probe_failure_never_publishes_binary(installer, tmp_path, monkeypatch, failure):
    payload = binary_body("0.9.4" if failure == "version" else "0.9.5",
                          rename_exit=1 if failure == "protocol" else 0)
    monkeypatch.setitem(installer.SHA256, "linux-x86_64",
                        "0" * 64 if failure == "checksum" else hashlib.sha256(payload).hexdigest())
    download_fixture(installer, monkeypatch, payload, fail=failure == "download")
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        installer.ensure_herdr()
    binary = tmp_path / ".local/bin/herdr"
    assert not binary.exists()
    assert not list(binary.parent.glob(".herdr-install-*"))


@pytest.mark.parametrize("version,compatible", [("0.9.2", True), ("0.7.0", False)])
def test_existing_binary_is_never_replaced(installer, tmp_path, monkeypatch, version, compatible):
    binary = tmp_path / "existing-herdr"
    payload = binary_body(version)
    binary.write_bytes(payload)
    binary.chmod(0o755)
    monkeypatch.setattr(installer.shutil, "which", lambda name: str(binary))
    calls = download_fixture(installer, monkeypatch, b"must-not-download", fail=True)
    if compatible:
        installer.ensure_herdr(check_only=True)
    else:
        with pytest.raises(ValueError, match="Incompatible"):
            installer.ensure_herdr()
    assert binary.read_bytes() == payload and not calls


def test_check_only_missing_does_not_download(installer, monkeypatch):
    calls = download_fixture(installer, monkeypatch, b"must-not-download", fail=True)
    with pytest.raises(ValueError, match="skip-deps"):
        installer.ensure_herdr(check_only=True)
    assert not calls


def test_broken_destination_and_concurrent_install_are_preserved(installer, tmp_path, monkeypatch):
    binary = tmp_path / ".local/bin/herdr"
    binary.parent.mkdir(parents=True)
    binary.symlink_to(tmp_path / "missing")
    calls = download_fixture(installer, monkeypatch, b"must-not-download", fail=True)
    with pytest.raises(ValueError, match="will not be overwritten"):
        installer.ensure_herdr()
    assert binary.is_symlink() and not calls
    binary.unlink()
    payload = binary_body()
    monkeypatch.setitem(installer.SHA256, "linux-x86_64", hashlib.sha256(payload).hexdigest())
    download_fixture(installer, monkeypatch, payload, publish=lambda: binary.write_bytes(b"another installer"))
    with pytest.raises(FileExistsError):
        installer.ensure_herdr()
    assert binary.read_bytes() == b"another installer"


def test_unsupported_host_does_not_download(installer, monkeypatch):
    monkeypatch.setattr(installer.platform, "system", lambda: "Windows")
    calls = download_fixture(installer, monkeypatch, b"must-not-download", fail=True)
    with pytest.raises(ValueError, match="supports"):
        installer.ensure_herdr()
    assert not calls
