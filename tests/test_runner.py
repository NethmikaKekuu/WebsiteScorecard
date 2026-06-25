"""Tests for scan runner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from websitescorecard.checks.base import CheckResult
from websitescorecard.checks.footer_vendor import FooterVendorCheck, SCRAPE_URL_COLUMN
from websitescorecard.runner import ScanConfig, _run_checks_for_row, run_scan


@dataclass
class _OkCheck:
    name = "ok"
    column = "ok_status"
    error_column = "ok_error"

    def run(self, url: str) -> CheckResult:
        return CheckResult(status="pass", error=None)


@dataclass
class _RaisingCheck:
    name = "boom"
    column = "boom_status"
    error_column = "boom_error"

    def run(self, url: str) -> CheckResult:
        raise RuntimeError("check exploded")


@dataclass
class _NoErrorColumnCheck:
    """A check that doesn't report a separate error column."""

    name = "noerr"
    column = "noerr_status"
    error_column = None

    def run(self, url: str) -> CheckResult:
        return CheckResult(status="pass", error=None)


def test_run_checks_for_row_records_error_when_check_raises():
    results = _run_checks_for_row("example.com", [_RaisingCheck()], row={})

    assert results == {
        "boom_status": "error",
        "boom_error": "Unexpected error: check exploded",
    }


def test_run_checks_for_row_continues_after_one_check_raises():
    results = _run_checks_for_row(
        "example.com", [_OkCheck(), _RaisingCheck()], row={}
    )

    assert results["ok_status"] == "pass"
    assert results["ok_error"] == ""
    assert results["boom_status"] == "error"
    assert results["boom_error"] == "Unexpected error: check exploded"
    
def test_run_scan_completes_when_row_check_raises(tmp_path: Path):
    input_csv = tmp_path / "input.csv"
    output_csv = tmp_path / "output.csv"
    input_csv.write_text("name,website\nGood,good.example\nBad,bad.example\n")

    config = ScanConfig(
        input_path=input_csv,
        output_path=output_csv,
        url_column="website",
        checks=[_RaisingCheck()],
        concurrency=1,
    )

    run_scan(config)

    lines = output_csv.read_text().strip().splitlines()
    assert lines[0] == "name,website,boom_status,boom_error"
    assert "error" in lines[1]
    assert "error" in lines[2]

