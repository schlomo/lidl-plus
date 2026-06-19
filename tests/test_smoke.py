from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.conftest import assert_sample_archive_www

from lidlplus.archive.site import write_site
from lidlplus.auth.oauth import parse_oauth_code


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "lidlplus", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_main_help():
    result = run_cli("-h")
    assert result.returncode == 0
    assert "browser-auth" in result.stdout
    assert "backup" in result.stdout


def test_cli_backup_help():
    result = run_cli("backup", "-h")
    assert result.returncode == 0
    assert "sync" in result.stdout
    assert "index" in result.stdout
    assert "serve" in result.stdout


def test_parse_oauth_code_from_raw_value():
    assert parse_oauth_code("ABCD1234") == "ABCD1234"


def test_parse_oauth_code_from_callback_url():
    url = "com.lidlplus.app://callback?code=DEADBEEF&state=lidlplus-browser-auth"
    assert parse_oauth_code(url) == "DEADBEEF"


def test_write_site_builds_www(data_dir: Path):
    assert write_site(data_dir) == 1
    assert_sample_archive_www(data_dir / "www")


def test_write_site_wipes_stale_www(data_dir: Path):
    www = data_dir / "www"
    www.mkdir()
    stale = www / "stale-old-asset.js"
    stale.write_text("old", encoding="utf-8")

    write_site(data_dir)

    assert not stale.exists()
    assert_sample_archive_www(www)


def test_cli_backup_index(data_dir: Path):
    result = run_cli("backup", "index", "--data-dir", str(data_dir))
    assert result.returncode == 0, result.stderr
    assert "Indexed 1 receipts" in result.stdout
    assert_sample_archive_www(data_dir / "www")
