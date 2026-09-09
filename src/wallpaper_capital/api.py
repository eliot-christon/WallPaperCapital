"""Shared HTTP session and error type for the Wikimedia APIs."""

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Wikimedia asks automated clients for a descriptive User-Agent with a contact.
USER_AGENT = "WallpaperDownloader/1.0 (contact: eliot.christon.spam@gmail.com)"


class ApiError(RuntimeError):
    """A Wikimedia endpoint answered, but the answer is unusable."""


def create_session() -> requests.Session:
    """A session that retries the transient failures Wikimedia returns under load."""
    session = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return session
