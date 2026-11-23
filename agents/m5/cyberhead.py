"""Read approved programmes from ENISA's public CYBERHEAD GraphQL API."""

from __future__ import annotations

import json
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = "https://tools.enisa.europa.eu/topics/education/cyberhead/"
_PAGE_SIZE = 10
_TIMEOUT_SECONDS = 30
_BROWSER_HEADERS = {
    "Referer": BASE_URL,
    "Origin": "https://tools.enisa.europa.eu",
    "User-Agent": "Mozilla/5.0",
}

_PROGRAMMES_QUERY = """
query A {
  programmes {
    all_programmes(perPage: __PAGE_SIZE__, page: __PAGE_NUMBER__) {
      total
      numpages
      results {
        ID
        state { public state }
        fields {
          programme_name
          programme_description
          city
          country
          programme_type
          institution_name
          related_urls { kind link }
        }
      }
    }
  }
}
"""


class _AppTokenParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.token: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "div" and attributes.get("id") == "app":
            self.token = attributes.get("data-protect")


def _read_json(url: str, *, payload: dict[str, Any] | None = None) -> Any:
    headers = dict(_BROWSER_HEADERS)
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    request = Request(url, data=data, headers=headers)
    try:
        with urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read(300).decode("utf-8", errors="replace")
        raise RuntimeError(
            f"CYBERHEAD request failed with HTTP {error.code}: {detail}"
        ) from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RuntimeError(f"CYBERHEAD request failed: {url}") from error


def _get_app_token() -> str:
    request = Request(
        BASE_URL,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    try:
        with urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            html = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError) as error:
        raise RuntimeError("Could not load the public CYBERHEAD page") from error

    parser = _AppTokenParser()
    parser.feed(html)
    if not parser.token:
        raise RuntimeError("CYBERHEAD page did not provide its API token")
    return parser.token


def _page(token: str, page_number: int) -> dict[str, Any]:
    result = _read_json(
        f"{BASE_URL}gql?_authenticator={token}",
        payload={
            "query": _PROGRAMMES_QUERY.replace(
                "__PAGE_SIZE__", str(_PAGE_SIZE)
            ).replace("__PAGE_NUMBER__", str(page_number))
        },
    )
    if not isinstance(result, dict):
        raise RuntimeError("CYBERHEAD API returned an invalid response")
    if result.get("errors"):
        raise RuntimeError(f"CYBERHEAD API query failed: {result['errors']}")
    try:
        page = result["data"]["programmes"]["all_programmes"]
        rows = page["results"]
        total = page["total"]
        pages = page["numpages"]
    except (KeyError, TypeError) as error:
        raise RuntimeError("CYBERHEAD API response is missing programme data") from error
    if (
        not isinstance(rows, list)
        or not isinstance(total, int)
        or not isinstance(pages, int)
        or pages < 1
    ):
        raise RuntimeError("CYBERHEAD API returned invalid programme pagination")
    return {"results": rows, "total": total, "numpages": pages}


def fetch_public_programmes() -> list[dict[str, Any]]:
    """Fetch every publicly approved CYBERHEAD programme."""
    token = _get_app_token()
    first_page = _page(token, 1)
    rows = list(first_page["results"])
    for page_number in range(2, first_page["numpages"] + 1):
        page = _page(token, page_number)
        if page["total"] != first_page["total"]:
            raise RuntimeError("CYBERHEAD programme count changed during retrieval")
        rows.extend(page["results"])

    if len(rows) != first_page["total"]:
        raise RuntimeError(
            "CYBERHEAD returned an incomplete result set "
            f"({len(rows)} of {first_page['total']} programmes)"
        )

    approved = []
    for row in rows:
        try:
            state = row["state"]
        except (KeyError, TypeError) as error:
            raise RuntimeError("CYBERHEAD programme has no publication state") from error
        if state.get("public") is True and state.get("state") == "approved":
            approved.append(row)
    return approved
