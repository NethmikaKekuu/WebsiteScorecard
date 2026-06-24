"""Orchestrates running checks against CSV rows."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeElapsedColumn

from websitescorecard.checks.base import Check
from websitescorecard.checks.footer_vendor import FooterVendorCheck, SCRAPE_URL_COLUMN
from websitescorecard.csv_io import read_csv, write_csv


@dataclass
class ScanConfig:
    input_path: Path
    output_path: Path
    url_column: str
    checks: list[Check]
    concurrency: int = 5
    include_error_columns: bool = True


def _output_columns(original: list[str], checks: list[Check], include_errors: bool) -> list[str]:
    columns = list(original)
    for check in checks:
        columns.append(check.column)
        if include_errors and check.error_column:
            columns.append(check.error_column)
    return columns


def _run_checks_for_row(url: str, checks: list[Check], row: dict[str, str]) -> dict[str, str]:
    results: dict[str, str] = {}
    for check in checks:
        try:
            if isinstance(check, FooterVendorCheck):
                scrape_url = row.get(SCRAPE_URL_COLUMN, "") or None
                result = check.run(url, scrape_url=scrape_url)
            else:
                result = check.run(url)

            results[check.column] = result.status
            if check.error_column:
                results[check.error_column] = result.error or ""
        except Exception as exc:
            results[check.column] = "error"
            if check.error_column:
                results[check.error_column] = f"Unexpected error: {exc}"
    return results


def _scan_row(index: int, row: dict[str, str], url_column: str, checks: list[Check]) -> tuple[int, dict[str, str]]:
    url = row.get(url_column, "")
    check_results = _run_checks_for_row(url, checks, row)
    enriched = {**row, **check_results}
    return index, enriched


def run_scan(config: ScanConfig) -> None:
    original_columns, rows = read_csv(config.input_path)

    if config.url_column not in original_columns:
        raise ValueError(
            f"Column {config.url_column!r} not found in CSV. "
            f"Available columns: {', '.join(original_columns)}"
        )

    output_columns = _output_columns(
        original_columns, config.checks, config.include_error_columns
    )
    enriched_rows: list[dict[str, str] | None] = [None] * len(rows)

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
    ) as progress:
        task = progress.add_task("Scanning websites...", total=len(rows))

        with ThreadPoolExecutor(max_workers=config.concurrency) as executor:
            futures = {
                executor.submit(_scan_row, i, row, config.url_column, config.checks): i
                for i, row in enumerate(rows)
            }
            for future in as_completed(futures):
                index, enriched = future.result()
                enriched_rows[index] = enriched
                progress.advance(task)

    write_csv(config.output_path, output_columns, enriched_rows)  # type: ignore[arg-type]