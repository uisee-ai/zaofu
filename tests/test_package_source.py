from __future__ import annotations

import json
from pathlib import Path

from zf.core import package_source


def test_installed_local_source_root_reads_pep610_file_url(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "frozen source"

    class InstalledDistribution:
        @staticmethod
        def read_text(name: str) -> str | None:
            assert name == "direct_url.json"
            return json.dumps({"url": source.as_uri(), "dir_info": {}})

    monkeypatch.setattr(
        package_source,
        "distribution",
        lambda name: InstalledDistribution(),
    )

    assert package_source.installed_local_source_root() == source.resolve()


def test_installed_local_source_root_rejects_remote_url(monkeypatch) -> None:
    class InstalledDistribution:
        @staticmethod
        def read_text(name: str) -> str | None:
            return json.dumps({"url": "https://example.com/zaofu.git"})

    monkeypatch.setattr(
        package_source,
        "distribution",
        lambda name: InstalledDistribution(),
    )

    assert package_source.installed_local_source_root() is None
