import os

import pytest

from keychain import (
    APPROVED,
    APPROVER_KEY_ENV,
    CHECKS_PASSED,
    PROMOTED,
    REJECTED,
    STAGED,
    Keychain,
    KeychainError,
)


@pytest.fixture
def gate():
    return Keychain(name="test")


def _ok(payload):
    return True


def _fail(payload):
    return False


def test_propose_starts_staged(gate):
    gate.propose("m1", {"model": "v2"})
    assert gate.status("m1") == STAGED


def test_duplicate_propose_rejected(gate):
    gate.propose("m1")
    with pytest.raises(KeychainError):
        gate.propose("m1")


def test_unknown_item_raises(gate):
    with pytest.raises(KeychainError):
        gate.status("ghost")


def test_all_checks_pass(gate):
    gate.propose("m1", {"x": 1})
    assert gate.run_checks("m1", {"a": _ok, "b": _ok}) == CHECKS_PASSED
    assert gate.status("m1") == CHECKS_PASSED


def test_any_failed_check_rejects_with_reasons(gate):
    gate.propose("m1")
    assert gate.run_checks("m1", {"good": _ok, "bad": _fail}) == REJECTED
    assert gate.status("m1") == REJECTED
    assert "check failed: bad" in gate.reasons("m1")[0]


def test_crashing_check_rejects(gate):
    def boom(payload):
        raise ValueError("nope")

    gate.propose("m1")
    assert gate.run_checks("m1", {"boom": boom}) == REJECTED
    assert "check raised: boom" in gate.reasons("m1")[0]


def test_cannot_run_checks_after_rejection(gate):
    gate.propose("m1")
    gate.run_checks("m1", {"bad": _fail})
    with pytest.raises(KeychainError):
        gate.run_checks("m1", {"good": _ok})


def test_approve_requires_checks_passed(gate):
    gate.propose("m1")
    with pytest.raises(KeychainError):
        gate.approve("m1", "keychain-approver")


def test_approve_requires_key(gate, monkeypatch):
    monkeypatch.setenv(APPROVER_KEY_ENV, "s3cret")
    gate.propose("m1")
    gate.run_checks("m1", {"ok": _ok})
    with pytest.raises(KeychainError):
        gate.approve("m1", "wrong-key")
    assert gate.status("m1") == CHECKS_PASSED  # unchanged


def test_approve_with_env_key(gate, monkeypatch):
    monkeypatch.setenv(APPROVER_KEY_ENV, "s3cret")
    gate.propose("m1")
    gate.run_checks("m1", {"ok": _ok})
    assert gate.approve("m1", "s3cret") == APPROVED


def test_promote_requires_approved(gate):
    gate.propose("m1")
    gate.run_checks("m1", {"ok": _ok})
    with pytest.raises(KeychainError):
        gate.promote("m1")  # checks passed but no human key yet


def test_full_happy_path(gate):
    gate.propose("m1", {"config": "canary"})
    gate.run_checks("m1", {"lint": _ok, "tests": _ok, "scan": _ok})
    gate.approve("m1", "keychain-approver")
    assert gate.promote("m1") == PROMOTED
    assert gate.status("m1") == PROMOTED


def test_manual_rejection(gate):
    gate.propose("m1")
    assert gate.reject("m1", "not ready") == REJECTED
    assert gate.reasons("m1") == ["not ready"]


def test_cannot_reject_promoted(gate):
    gate.propose("m1")
    gate.run_checks("m1", {"ok": _ok})
    gate.approve("m1", "keychain-approver")
    gate.promote("m1")
    with pytest.raises(KeychainError):
        gate.reject("m1")


def test_audit_trail_records_every_transition(gate):
    gate.propose("m1")
    gate.run_checks("m1", {"ok": _ok})
    gate.approve("m1", "keychain-approver")
    gate.promote("m1")
    trail = gate.audit_trail("m1")
    assert [(e["from"], e["to"]) for e in trail] == [
        (None, STAGED),
        (STAGED, "checking"),
        ("checking", CHECKS_PASSED),
        (CHECKS_PASSED, APPROVED),
        (APPROVED, PROMOTED),
    ]
    assert all(isinstance(e["ts"], float) for e in trail)


def test_audit_trail_filters_by_item(gate):
    gate.propose("a")
    gate.propose("b")
    assert len(gate.audit_trail("a")) == 1
    assert len(gate.audit_trail()) == 2


def test_audit_uses_injected_clock():
    ticks = iter([100.0, 200.0, 300.0, 400.0, 500.0])
    gate = Keychain(now=lambda: next(ticks))
    gate.propose("m1")
    gate.run_checks("m1", {"ok": _ok})
    gate.approve("m1", "keychain-approver")
    gate.promote("m1")
    ts = [e["ts"] for e in gate.audit_trail("m1")]
    assert ts == [100.0, 200.0, 300.0, 400.0, 500.0]
