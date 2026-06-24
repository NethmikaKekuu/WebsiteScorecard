# WebsiteScorecard

CLI to scan websites from a CSV and enrich rows with check results (SSL certificate status, and more over time).

## Prerequisites

- Python 3.10 or newer
- A CSV file with a column containing website URLs (domains or full URLs)

## Quick start

### 1. Set up a virtual environment and install

From the project root:

```bash
python3 -m venv .venv
# Unix:    
source .venv/bin/activate  
# Windows: 
.venv\Scripts\activate
pip install -e .
```

For development (includes pytest):

```bash
pip install -e ".[dev]"
```

Activate the virtual environment in any new terminal session before running commands:

```bash
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

### 2. Test data

The repo includes a sample CSV at `data/mins_depts_test.csv` with Sri Lankan ministries and departments:

```csv
Type,Institution Name,URL
Ministry,Ministry of Defence,defence.lk
Ministry,"Ministry of Finance, Planning and Economic Development",treasury.gov.lk
Ministry,Ministry of Digital Economy,midec.gov.lk
...
```

The `URL` column contains bare domains (e.g. `defence.lk`). Full URLs such as `https://example.com/path` also work.

### 3. Run a scan

```bash
websitescorecard scan data/mins_depts_test.csv --column URL --checks ssl
```

This writes `data/mins_depts_test_scored.csv` by default (same name as input with `_scored` inserted).

Specify an output file:

```bash
websitescorecard scan data/mins_depts_test.csv -c URL -o data/mins_depts_scored.csv --checks ssl
```

### 4. Check the output

```csv
Type,Institution Name,URL,ssl_status,ssl_error
Ministry,Ministry of Defence,defence.lk,valid,
Ministry,Ministry of Digital Economy,midec.gov.lk,valid,
...
```

| `ssl_status` | Meaning |
|--------------|---------|
| `valid` | TLS succeeds, cert verifies, not expired |
| `expired` | Cert present but past expiry |
| `invalid` | Cert present but fails verification (hostname mismatch, self-signed, untrusted chain, etc.) |
| `no_certificate` | Server reached over TLS but no certificate presented |
| `unreachable` | Could not connect to evaluate SSL (bad domain, DNS failure, timeout, connection error) |
| `error` | Check raised an unexpected internal error (recorded in `ssl_error`) |

The `ssl_error` column contains the underlying error message when something went wrong. For scorecard reporting, `expired` and `invalid` can be grouped as cert problems; `unreachable` rows can be flagged separately for CSV cleanup.

## CLI reference

```bash
websitescorecard scan INPUT_CSV -c COLUMN [OPTIONS]
```

| Flag | Description |
|------|-------------|
| `INPUT_CSV` | Input CSV file path |
| `-c, --column` | Column containing website URLs (**required**) |
| `-o, --output` | Output path (default: `{input}_scored.csv`) |
| `--checks` | Comma-separated checks to run (e.g. `ssl`) (**required**) |
| `--concurrency` | Parallel workers (default: 5) |
| `--timeout` | Socket timeout per check in seconds (default: 10) |
| `--no-error-columns` | Omit `*_error` detail columns |

Examples:

```bash
# SSL check on the sample data with 10 parallel workers and 15s timeout
websitescorecard scan data/mins_depts_test.csv -c URL --checks ssl --concurrency 10 --timeout 15

# View all options
websitescorecard scan --help
```

At least one check must be specified via `--checks`. Running with an empty value prints an error.

## Development

Activate the virtual environment, then run tests:

```bash
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pytest
```

## Adding a new check

1. Create `src/websitescorecard/checks/your_check.py` implementing the `Check` protocol.
2. Register it in `src/websitescorecard/checks/__init__.py`.
3. Run it via `--checks` (comma-separated for multiple checks).

See `src/websitescorecard/checks/ssl.py` for a reference implementation.
