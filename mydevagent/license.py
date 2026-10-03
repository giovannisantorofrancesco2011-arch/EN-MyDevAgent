"""MyDevAgent license: free trial, then a subscription or one-time purchase, Base or Plus.

Codes are created only by the author with his private key, which is not in the repository. Each code holds
name, plan (base or plus) and expiry, signed with Ed25519: the app checks them with the public key below,
offline, and nobody can make up a valid one. Plus also unlocks the Studio Plus features (see `plus_needed`)
and the MyCode download. Same key and same codes as the Italian edition.

While PUBLIC_KEY is empty the check is off and everything, Plus included, is free.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

PUBLIC_KEY = "c9affe19737bcc37f1d46464a9213c3607ffd574f5f873939a33d1e32ca199e4"
BUY_URL = "https://mydevagent.github.io/en/pricing.html"
TRIAL_DAYS = 14
PREFIX = "MDA-"
DAY = 86400


@dataclass
class Status:
    ok: bool  # MyDevAgent can be used
    kind: str  # "free", "trial", "subscription", "lifetime" (+ " Plus"), "expired"
    message: str
    days_left: int | None = None
    plus: bool = False  # Plus: the same code also works for Studio Plus and MyCode


def _path() -> Path:
    return Path(os.environ.get("MYDEVAGENT_STATE_DIR", Path.home() / ".mydevagent")) / "license.json"


def _load() -> dict[str, Any]:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def sign(private: Ed25519PrivateKey, name: str, plan: str, expires: str = "") -> str:
    """Creates a code. `expires` is a YYYY-MM-DD date (subscription) or empty (lifetime)."""
    payload = json.dumps({"n": name, "p": plan, "s": expires}, separators=(",", ":")).encode()
    return PREFIX + base64.urlsafe_b64encode(payload + private.sign(payload)).decode().rstrip("=")


def read(key: str) -> dict[str, str] | None:
    """What a code contains, or None if it was not signed by the author."""
    try:
        raw = base64.urlsafe_b64decode(key.strip().removeprefix(PREFIX) + "==")
        payload, signature = raw[:-64], raw[-64:]
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(PUBLIC_KEY)).verify(signature, payload)
        return json.loads(payload)
    except (ValueError, InvalidSignature):
        return None


def _check(key: str, now: float) -> Status:
    info = read(key)
    if info is None:
        return Status(False, "expired", "This code is not valid: check that you copied all of it.")
    plus = info.get("p") == "plus"
    name = info.get("n") or "you"
    if not info.get("s"):
        kind = "lifetime" + (" Plus" if plus else "")
        return Status(True, kind, f"{kind.capitalize()} license of {name}. Thanks for your support!", plus=plus)
    # ponytail: the date comes from the computer's clock, which can be turned back; fine as a deterrent
    left = (dt.date.fromisoformat(info["s"]) - dt.date.fromtimestamp(now)).days + 1
    if left <= 0:
        return Status(False, "expired", f"The subscription expired on {info['s']}: renew it at {BUY_URL} "
                                        "and type /license <new code>.", 0)
    kind = "subscription" + (" Plus" if plus else "")
    return Status(True, kind, f"{kind.capitalize()} of {name}, valid until {info['s']}.", left, plus)


def activate(key: str) -> Status:
    state = _check(key, time.time())
    if state.ok:
        data = _load()
        data["key"] = key.strip()
        _save(data)
    else:
        state.message = f"Code not activated. {state.message}"
    return state


def deactivate() -> str:
    data = _load()
    if not data.pop("key", None):
        return "There is no code on this computer."
    _save(data)
    return "Code removed from this computer."


def status(now: float | None = None) -> Status:
    """Can MyDevAgent be used?"""
    if not PUBLIC_KEY:
        return Status(True, "free", "MyDevAgent is free.", plus=True)
    now = time.time() if now is None else now
    data = _load()
    if data.get("key"):
        return _check(data["key"], now)
    if "first_run" not in data:
        data["first_run"] = now
        _save(data)
    left = TRIAL_DAYS - int((now - data["first_run"]) // DAY)
    if left > 0:
        return Status(True, "trial", f"Free trial: {'last day' if left == 1 else f'{left} days left'}.",
                      left, plus=True)  # the trial includes Plus too
    return Status(False, "expired", f"The free trial is over. Pick a subscription or one-time purchase at "
                                    f"{BUY_URL}, then type /license <code>.", 0)


def plus_needed() -> str:
    """"" if the Plus features can be used, otherwise why not."""
    if status().plus:
        return ""
    return f"This is a Plus feature. Upgrade to Plus at {BUY_URL} and type /license <code>."
