"""Quota-aware use of hosted models (free tiers have small per-minute and per-day limits).

Limits come from the ALISA .env, one line per model, as requests-per-minute,
tokens-per-minute, requests-per-day (blank or 0 = no local limit):

    GEMINI_QUOTA_GEMINI_4_HYDROGEN=5,250000,20
    GEMINI_QUOTA_GEMMA_4_31B_IT=30,16000,14400

The tracker:

- refuses a call locally when a per-day count is used up, or when the
  per-minute window is full for longer than ``GEMINI_MAX_WAIT`` seconds
  (default 15); shorter waits are slept through;
- records each call's real token usage once the provider reports it;
- benches a model after the provider says to slow down ("retry in 34s"), or
  until the next reset when it says the daily quota is spent, so calls go
  straight to the local fallback instead of being retried in a loop.

Per-day counts and benches are saved to ``GEMINI_USAGE_FILE`` (default
``~/.alisa/forge/gemini_usage.json``) so they survive between runs. Google's
daily quotas reset at midnight Pacific time; so does this tracker.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .env import env_value, load_local_env
from .llm import ProviderUnavailableError

LOGGER = logging.getLogger(__name__)
DEFAULT_MAX_WAIT = 15.0


class QuotaExhaustedError(ProviderUnavailableError):
    """The local tracker refused a call to stay within the provider's quota."""


@dataclass(frozen=True)
class QuotaLimits:
    rpm: int = 0
    tpm: int = 0
    rpd: int = 0

    @property
    def any(self) -> bool:
        return bool(self.rpm or self.tpm or self.rpd)


def quota_env_var(provider: str, model: str) -> str:
    return f"{provider.upper()}_QUOTA_" + re.sub(r"[^A-Za-z0-9]+", "_", model).upper()


def limits_for(provider: str, model: str) -> QuotaLimits:
    load_local_env()
    var = quota_env_var(provider, model)
    raw = env_value(var)
    if not raw:
        return QuotaLimits()
    try:
        parts = [int(p.strip() or 0) for p in raw.split(",")]
    except ValueError as exc:
        raise ValueError(f"{var} must be 'rpm,tpm,rpd' integers, got {raw!r}") from exc
    parts += [0] * (3 - len(parts))
    return QuotaLimits(*parts[:3])


def pacific_day(now: float | None = None) -> str:
    """The provider's quota day (midnight Pacific), as YYYY-MM-DD."""
    moment = datetime.fromtimestamp(now if now is not None else time.time(), timezone.utc)
    try:
        from zoneinfo import ZoneInfo

        local = moment.astimezone(ZoneInfo("America/Los_Angeles"))
    except Exception:  # no tz database (common on Windows without tzdata): use PST
        local = moment.astimezone(timezone(timedelta(hours=-8)))
    return local.strftime("%Y-%m-%d")


def default_usage_file() -> Path:
    load_local_env()
    configured = env_value("GEMINI_USAGE_FILE")
    return Path(configured).expanduser() if configured else Path.home() / ".alisa" / "forge" / "gemini_usage.json"


class QuotaTracker:
    """Usage per model. One instance per process; per-day state is shared through a file."""

    def __init__(self, path: Path | None = None, *, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.path = path or default_usage_file()
        self.clock = clock
        self.sleep = sleep
        self._minute: dict[str, deque[tuple[float, int]]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ storage
    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            LOGGER.warning("could not save quota usage to %s: %s", self.path, exc)

    def _entry(self, data: dict, key: str) -> dict:
        today = pacific_day(self.clock())
        entry = data.setdefault(key, {})
        if entry.get("day") != today:
            entry.update(day=today, requests=0, tokens=0)
            entry.pop("exhausted", None)
        return entry

    # -------------------------------------------------------------- checks
    def before_call(self, key: str, limits: QuotaLimits, estimated_tokens: int) -> None:
        """Wait briefly or raise QuotaExhaustedError if the call would break a limit."""
        with self._lock:
            now = self.clock()
            data = self._load()
            entry = self._entry(data, key)
            benched = entry.get("benched_until", 0)
            if benched > now:
                raise QuotaExhaustedError(f"{key} is benched for {benched - now:.0f}s more after a provider limit")
            if entry.get("exhausted"):
                raise QuotaExhaustedError(f"{key} daily quota is used up until the next reset (midnight Pacific)")
            if limits.rpd and entry.get("requests", 0) >= limits.rpd:
                raise QuotaExhaustedError(
                    f"{key} has used {entry['requests']}/{limits.rpd} requests today (resets midnight Pacific)")

            window = self._minute.setdefault(key, deque())
            while window and window[0][0] <= now - 60:
                window.popleft()
            wait = 0.0
            if limits.rpm and len(window) >= limits.rpm:
                wait = window[0][0] + 60 - now
            if limits.tpm and window and sum(t for _, t in window) + estimated_tokens > limits.tpm:
                wait = max(wait, window[0][0] + 60 - now)
            max_wait = float(env_value("GEMINI_MAX_WAIT") or DEFAULT_MAX_WAIT)
            if wait > max_wait:
                raise QuotaExhaustedError(f"{key} per-minute limit reached; next slot in {wait:.0f}s")
        if wait > 0:
            LOGGER.info("%s per-minute limit reached; waiting %.0fs", key, wait)
            self.sleep(wait)

    def record(self, key: str, tokens: int) -> None:
        with self._lock:
            now = self.clock()
            self._minute.setdefault(key, deque()).append((now, tokens))
            data = self._load()
            entry = self._entry(data, key)
            entry["requests"] = entry.get("requests", 0) + 1
            entry["tokens"] = entry.get("tokens", 0) + tokens
            self._save(data)

    def bench(self, key: str, seconds: float | None, *, daily: bool = False) -> None:
        """After a provider limit: pause this model for ``seconds``, or for the rest of the day."""
        with self._lock:
            data = self._load()
            entry = self._entry(data, key)
            if daily:
                entry["exhausted"] = True
            entry["benched_until"] = self.clock() + (seconds if seconds is not None else 60.0)
            self._save(data)

    def usage(self, key: str) -> dict:
        with self._lock:
            return dict(self._entry(self._load(), key))


_TRACKER: QuotaTracker | None = None


def tracker() -> QuotaTracker:
    global _TRACKER
    if _TRACKER is None or _TRACKER.path != default_usage_file():
        _TRACKER = QuotaTracker()
    return _TRACKER


def parse_rate_limit(detail: str) -> tuple[float | None, bool]:
    """(seconds to wait, whether a per-day quota is spent) from a 429 error body."""
    delay = None
    match = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', detail)
    if match:
        delay = float(match.group(1))
    daily = bool(re.search(r"PerDay|per day|daily", detail, re.IGNORECASE))
    return delay, daily
