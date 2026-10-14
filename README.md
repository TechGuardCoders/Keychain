# Keychain

A two-key promotion gate: changes, models, and configs move **staging → production** only when two independent keys turn — automated checks pass **and** a human approver confirms.

## Why

Single-signal pipelines fail in one of two ways: a green CI gets waved straight to prod, or a human rubber-stamps without checks. Airlock enforces both keys, in order, with no bypass:

```
staged → checking → checks-passed → approved → promoted
                          │
                          └──→ rejected (at any gate)
```

- `propose(item)` — register an item; it enters as `staged`.
- `run_checks(item, checks)` — every check must pass, or the item is `rejected` with reasons. A crashing check counts as a failure.
- `approve(item, approver_key)` — the human key. Only works on `checks-passed` items; a wrong key is a no-op with an audit entry.
- `promote(item)` — the final gate. Only from `approved`.
- `audit_trail()` — every transition, timestamped.

## Results

- 17 tests, all passing (`uv run pytest`), pure Python stdlib, no network.
- State machine is total: every illegal transition raises `AirlockError` instead of silently proceeding.
- The approver key is read from the `AIRLOCK_APPROVER_KEY` environment variable — never hardcoded.

## Architecture

One module (`airlock.py`), one class, zero dependencies:

- `Airlock` holds items, their statuses, and an append-only audit log.
- Checks are plain callables taking the item payload — truthy return passes, falsy or exception fails with a reason.
- A monotonic injected clock (`now=`) makes the audit trail deterministic and testable.

## Usage

```python
from airlock import Airlock

gate = Airlock()
gate.propose("model-v2", {"weights": "s3://..."})
gate.run_checks("model-v2", {
    "eval_score": lambda p: p["eval"] > 0.9,
    "size_limit": lambda p: p["size_mb"] < 500,
})
gate.approve("model-v2", os.environ["AIRLOCK_APPROVER_KEY"])
gate.promote("model-v2")
```

Set the key out-of-band (CI secret, shell profile):

```bash
export AIRLOCK_APPROVER_KEY="..."   # never commit this
```

## Development

```bash
uv venv && uv pip install pytest && uv run pytest -q
```

CI runs the same suite on every push (`.github/workflows/ci.yml`).
