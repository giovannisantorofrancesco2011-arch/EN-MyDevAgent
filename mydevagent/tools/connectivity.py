"""Cached online/offline detection (web features switch themselves off when offline)."""

from __future__ import annotations

import os
import time

import httpx

from ..config import ConnectivityConfig


class Connectivity:
    def __init__(self, cfg: ConnectivityConfig) -> None:
        self.cfg = cfg
        self._state: bool | None = None
        self._checked_at = 0.0

    def online(self) -> bool:
        if os.environ.get("MYDEVAGENT_OFFLINE") == "1":
            return False
        now = time.monotonic()
        if self._state is not None and now - self._checked_at < self.cfg.cache_s:
            return self._state
        try:
            resp = httpx.get(self.cfg.check_url, timeout=self.cfg.timeout_s, follow_redirects=True)
            self._state = resp.status_code < 500
        except httpx.HTTPError:
            self._state = False
        self._checked_at = now
        return self._state
