import datetime as dt

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mydevagent import license

DAY = license.DAY


@pytest.fixture
def private(monkeypatch):
    """Sales open: a test key pair instead of the real one."""
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    monkeypatch.setattr(license, "PUBLIC_KEY", public)
    return key


def in_days(n: int) -> str:
    return (dt.date.today() + dt.timedelta(days=n)).isoformat()


def test_free_while_sales_are_closed():
    state = license.status()
    assert state.ok and state.kind == "free" and state.plus
    assert license.plus_needed() == ""


def test_trial_then_blocked(private):
    start = license.status(now=1000.0)
    assert start.ok and start.kind == "trial" and start.days_left == 14 and start.plus
    assert license.status(now=1000.0 + 13 * DAY).message.endswith("last day.")
    late = license.status(now=1000.0 + 14 * DAY)
    assert not late.ok and "/license" in late.message


def test_lifetime_and_plus_keys(private):
    state = license.activate(" " + license.sign(private, "Mario", "base") + " ")
    assert state.ok and state.kind == "lifetime" and not state.plus and "Mario" in state.message
    assert license.status().ok and license.plus_needed()  # base: no Plus features
    state = license.activate(license.sign(private, "Mario", "plus", in_days(31)))
    assert state.ok and state.kind == "subscription Plus" and state.plus and state.days_left == 32
    assert license.plus_needed() == ""


def test_subscription_expires(private):
    license.activate(license.sign(private, "Ada", "base", in_days(0)))
    assert license.status().ok  # valid until the end of the day
    assert not license.status(now=license.time.time() + DAY).ok


def test_forged_or_broken_keys_are_rejected(private):
    other = Ed25519PrivateKey.generate()
    assert not license.activate(license.sign(other, "Sneaky", "plus")).ok
    good = license.sign(private, "Mario", "plus")
    assert not license.activate(good[:-3]).ok
    assert not license.activate("MDA-hello").ok
    assert "key" not in license._load()


def test_deactivate(private):
    license.activate(license.sign(private, "Mario", "base"))
    assert "removed" in license.deactivate()
    assert license.status().kind == "trial"

