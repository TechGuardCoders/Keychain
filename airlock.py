"""Airlock: a two-key promotion gate for staging -> production.

An item (change, model, config) may only move to production when two
independent keys turn: automated checks pass AND a human approver
confirms with the approver key.

State machine:
    staged -> checking -> checks-passed -> approved -> promoted
                                  |
                                  +-> rejected (at any gate)
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, List, Optional

# States
STAGED = "staged"
CHECKING = "checking"
CHECKS_PASSED = "checks-passed"
APPROVED = "approved"
PROMOTED = "promoted"
REJECTED = "rejected"

# Where the approver key comes from in production deployments.
APPROVER_KEY_ENV = "AIRLOCK_APPROVER_KEY"


class AirlockError(Exception):
    """Raised on invalid state transitions or bad usage."""


class Airlock:
    """Two-key promotion gate: checks must pass, then a human approves."""

    def __init__(self, name: str = "airlock", now: Optional[Callable[[], float]] = None):
        self.name = name
        self._now = now or time.time
        self._items: Dict[str, Dict[str, Any]] = {}
        self._audit: List[Dict[str, Any]] = []

    # -- public API ----------------------------------------------------

    def propose(self, item_id: str, payload: Any = None) -> Dict[str, Any]:
        """Register a new item; it enters the airlock as 'staged'."""
        if item_id in self._items:
            raise AirlockError(f"item already proposed: {item_id!r}")
        record = {
            "id": item_id,
            "payload": payload,
            "status": STAGED,
            "reasons": [],
        }
        self._items[item_id] = record
        self._log(item_id, None, STAGED, "proposed")
        return dict(record)

    def status(self, item_id: str) -> str:
        return self._get(item_id)["status"]

    def run_checks(self, item_id: str, checks: Dict[str, Callable[[Any], Any]]) -> str:
        """Run every check; all must pass or the item is rejected.

        Each check is a callable taking the item payload. A check passes
        when it returns a truthy value (or raises nothing and returns
        True); a failing check records its reason.
        """
        record = self._get(item_id)
        if record["status"] not in (STAGED, CHECKS_PASSED):
            raise AirlockError(
                f"cannot run checks on item in status {record['status']!r}"
            )
        self._transition(item_id, CHECKING, "checks started")
        reasons: List[str] = []
        for check_name, fn in checks.items():
            try:
                ok = bool(fn(record["payload"]))
                reason = None if ok else f"check failed: {check_name}"
            except Exception as exc:  # a crashing check is a failed check
                ok = False
                reason = f"check raised: {check_name}: {exc}"
            if not ok and reason:
                reasons.append(reason)
        if reasons:
            self._transition(item_id, REJECTED, "; ".join(reasons))
            record["reasons"] = reasons
            return REJECTED
        self._transition(item_id, CHECKS_PASSED, "all checks passed")
        return CHECKS_PASSED

    def approve(self, item_id: str, approver_key: str) -> str:
        """Human key: approve a checks-passed item. Requires the key."""
        record = self._get(item_id)
        if record["status"] != CHECKS_PASSED:
            raise AirlockError(
                f"only checks-passed items can be approved (status: {record['status']!r})"
            )
        expected = os.environ.get(APPROVER_KEY_ENV, "airlock-approver")
        if not approver_key or approver_key != expected:
            self._transition(item_id, record["status"], "approval denied: bad key")
            raise AirlockError("invalid approver key")
        self._transition(item_id, APPROVED, "approved by human key")
        return APPROVED

    def promote(self, item_id: str) -> str:
        """Move an approved item to production. Final gate."""
        record = self._get(item_id)
        if record["status"] != APPROVED:
            raise AirlockError(
                f"only approved items can be promoted (status: {record['status']!r})"
            )
        self._transition(item_id, PROMOTED, "promoted to production")
        return PROMOTED

    def reject(self, item_id: str, reason: str = "rejected by operator") -> str:
        """Manual rejection from any pre-promotion state."""
        record = self._get(item_id)
        if record["status"] == PROMOTED:
            raise AirlockError("cannot reject a promoted item")
        self._transition(item_id, REJECTED, reason)
        record["reasons"] = [reason]
        return REJECTED

    def audit_trail(self, item_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Every transition, with timestamps. Filter by item if given."""
        entries = [e for e in self._audit if item_id is None or e["item"] == item_id]
        return [dict(e) for e in entries]

    def reasons(self, item_id: str) -> List[str]:
        return list(self._get(item_id)["reasons"])

    # -- internals -----------------------------------------------------

    def _get(self, item_id: str) -> Dict[str, Any]:
        try:
            return self._items[item_id]
        except KeyError:
            raise AirlockError(f"unknown item: {item_id!r}") from None

    def _transition(self, item_id: str, new_status: str, note: str) -> None:
        record = self._get(item_id)
        old = record["status"]
        record["status"] = new_status
        self._log(item_id, old, new_status, note)

    def _log(self, item_id: str, old: Optional[str], new: str, note: str) -> None:
        self._audit.append(
            {
                "item": item_id,
                "from": old,
                "to": new,
                "note": note,
                "ts": self._now(),
            }
        )
