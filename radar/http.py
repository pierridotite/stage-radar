"""Session HTTP polie : User-Agent explicite, timeouts, retries et pause entre requêtes."""
from __future__ import annotations

import logging
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

USER_AGENT = "stage-radar/1.0 (agregateur de stages etudiants; usage non commercial)"


class PoliteSession(requests.Session):
    def __init__(self, delay: float = 0.4, timeout: float = 20, retries: int = 3, budget: float | None = None):
        super().__init__()
        self.deadline = time.monotonic() + budget if budget else None
        self.delay = delay
        self.timeout = timeout
        self.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json, */*"})
        retry = Retry(total=retries, backoff_factor=1.5, status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=frozenset({"GET", "POST"}),
                      respect_retry_after_header=False)  # un Retry-After d'une heure bloquerait tout le run
        self.mount("https://", HTTPAdapter(max_retries=retry))
        self._last = 0.0

    def request(self, method, url, **kwargs):
        if self.deadline and time.monotonic() > self.deadline:
            raise TimeoutError("budget de temps dépassé pour cette source")
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        kwargs.setdefault("timeout", (8, self.timeout))
        try:
            return super().request(method, url, **kwargs)
        finally:
            self._last = time.monotonic()
