"""openFDA HTTP client: rate limiting, pagination, retries, fixtures, provenance.

Pure mechanism. No MCP, no framework imports (transport-agnostic core rule).

Empirical constraints this client encodes (verified 2026-08-24, see spec S10):
- limit max 1000; limit=1001 returns API_KEY_MISSING, a misleading error.
- skip max 25000.
- count= is NOT supported on /drug/shortages (returns NOT_FOUND "Nothing to count").
- 404 NOT_FOUND means zero matches, not an error.
- Query syntax: '+' and brackets in ranges are significant; do not blanket-urlencode.
- Rate: 240 req/min per IP; 1,000/day without key, 120,000/day with FDA_API_KEY.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

BASE_URL = "https://api.fda.gov"
MAX_LIMIT = 1000
MAX_SKIP = 25000
MIN_REQUEST_INTERVAL = 0.26  # ~230/min, under the 240/min cap


class FDAClientError(Exception):
    """A real API failure. Zero results is never raised as this."""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def phrase(field: str, value: str) -> str:
    """Build a quoted-phrase search term. Spaces become '+', which openFDA
    treats as the phrase separator. Quotes any embedded double quotes away."""
    cleaned = value.replace('"', "").strip()
    return f'{field}:"{cleaned.replace(" ", "+")}"'


class FDAClient:
    """Thin, careful wrapper over api.fda.gov.

    fixtures_mode:
      'live'   - hit the API (default)
      'record' - hit the API and save every response under fixtures_dir
      'replay' - serve only from fixtures_dir; raise if a request is missing
    """

    def __init__(
        self,
        api_key: str | None = None,
        fixtures_dir: str | Path | None = None,
        fixtures_mode: str | None = None,
        timeout: float = 30.0,
    ):
        self.api_key = api_key or os.environ.get("FDA_API_KEY")
        self.fixtures_mode = fixtures_mode or os.environ.get("FDATRACK_FIXTURES", "live")
        default_dir = Path(__file__).resolve().parent.parent / "fixtures"
        self.fixtures_dir = Path(fixtures_dir) if fixtures_dir else default_dir
        self._client = httpx.Client(timeout=timeout)
        self._last_request_at = 0.0
        self.query_log: list[str] = []  # every actual query string issued

    # ------------------------------------------------------------- fixtures

    def _fixture_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode()).hexdigest()[:20]
        slug = re.sub(r"[^a-z0-9]+", "_", url.split("api.fda.gov/")[-1].split("?")[0])
        return self.fixtures_dir / f"{slug}_{digest}.json"

    # ------------------------------------------------------------- requests

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        self._last_request_at = time.monotonic()

    def _get(self, url: str) -> dict:
        """One GET with retries. Returns parsed JSON body.
        NOT_FOUND (zero matches) is returned as {"results": [], "meta": ...}."""
        # The url passed here never includes the api key; fixtures key on it.
        if self.fixtures_mode == "replay":
            path = self._fixture_path(url)
            if not path.exists():
                raise FDAClientError(
                    f"replay mode: no fixture for {url} (expected {path.name})"
                )
            return json.loads(path.read_text())

        fetch_url = url
        if self.api_key:
            sep = "&" if "?" in url else "?"
            fetch_url = f"{url}{sep}api_key={self.api_key}"

        body: dict | None = None
        last_err: Exception | None = None
        for attempt in range(4):
            self._throttle()
            try:
                resp = self._client.get(fetch_url)
            except httpx.HTTPError as exc:
                last_err = exc
                time.sleep(1.5 * (attempt + 1))
                continue

            if resp.status_code == 404:
                # openFDA returns 404 NOT_FOUND for zero matches. Normal result.
                body = {"results": [], "meta": {"results": {"total": 0}}}
                break
            if resp.status_code == 429:
                time.sleep(3.0 * (attempt + 1))
                last_err = FDAClientError("rate limited (429)")
                continue
            if resp.status_code >= 500:
                time.sleep(1.5 * (attempt + 1))
                last_err = FDAClientError(f"server error {resp.status_code}")
                continue
            if resp.status_code != 200:
                # Includes the misleading API_KEY_MISSING that fires on limit>1000.
                # We never send limit>1000, so surface whatever this really is.
                try:
                    detail = resp.json().get("error", {})
                except Exception:
                    detail = {"message": resp.text[:200]}
                raise FDAClientError(
                    f"HTTP {resp.status_code} from openFDA: {detail}. "
                    "Note: openFDA returns API_KEY_MISSING for limit>1000; "
                    "that error usually means a malformed query, not a key problem."
                )
            body = resp.json()
            break

        if body is None:
            raise FDAClientError(f"request failed after retries: {url} ({last_err})")

        if self.fixtures_mode == "record":
            self.fixtures_dir.mkdir(parents=True, exist_ok=True)
            self._fixture_path(url).write_text(json.dumps(body, indent=1))
        return body

    # ------------------------------------------------------------- public

    def search(
        self,
        endpoint: str,
        search: str | None = None,
        count: str | None = None,
        limit: int = 100,
        skip: int = 0,
    ) -> dict:
        """Single request. `search` must be pre-built (see phrase()); it is NOT
        url-encoded here because '+', ':', brackets and quotes are significant."""
        if limit > MAX_LIMIT:
            raise ValueError(f"limit {limit} > API max {MAX_LIMIT}")
        if skip > MAX_SKIP:
            raise ValueError(f"skip {skip} > API max {MAX_SKIP}")
        params = []
        if search:
            params.append(f"search={search}")
        if count:
            params.append(f"count={count}")
        else:
            params.append(f"limit={limit}")
            if skip:
                params.append(f"skip={skip}")
        url = f"{BASE_URL}{endpoint}?{'&'.join(params)}"
        self.query_log.append(url)
        return self._get(url)

    def search_all(
        self,
        endpoint: str,
        search: str,
        max_records: int = MAX_SKIP + MAX_LIMIT,
    ) -> tuple[list[dict], int, list[str]]:
        """Paginate to completion. Returns (records, total_matched, warnings)."""
        warnings: list[str] = []
        results: list[dict] = []
        skip = 0
        total = 0
        while True:
            body = self.search(endpoint, search=search, limit=MAX_LIMIT, skip=skip)
            batch = body.get("results", [])
            total = body.get("meta", {}).get("results", {}).get("total", len(batch))
            results.extend(batch)
            if len(results) >= min(total, max_records):
                break
            skip += MAX_LIMIT
            if skip > MAX_SKIP:
                warnings.append(
                    f"result set of {total} exceeds the API skip cap "
                    f"({MAX_SKIP + MAX_LIMIT}); returned first {len(results)}"
                )
                break
        return results, total, warnings

    def close(self) -> None:
        self._client.close()
