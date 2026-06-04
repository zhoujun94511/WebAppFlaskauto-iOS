"""White-box: in-memory sliding-window rate limiter (login lockout)."""

from __future__ import annotations

import time

from services.rate_limit import RateLimiter, login_limiter


def test_blocks_after_max_attempts():
    rl = RateLimiter(max_attempts=3, window_seconds=300, block_seconds=300)
    assert rl.record("k") == 0          # 1st
    assert rl.record("k") == 0          # 2nd
    assert rl.record("k") == 300        # 3rd trips the block → returns block secs


def test_retry_after_positive_while_blocked():
    rl = RateLimiter(max_attempts=2, window_seconds=300, block_seconds=300)
    rl.record("k")
    rl.record("k")  # blocked now
    after = rl.retry_after("k")
    assert 0 < after <= 301


def test_retry_after_zero_when_not_blocked():
    rl = RateLimiter(max_attempts=3, window_seconds=300, block_seconds=300)
    assert rl.retry_after("never-seen") == 0
    rl.record("k")
    assert rl.retry_after("k") == 0  # one hit, not yet blocked


def test_reset_clears_block_and_hits():
    rl = RateLimiter(max_attempts=2, window_seconds=300, block_seconds=300)
    rl.record("k")
    rl.record("k")
    assert rl.retry_after("k") > 0
    rl.reset("k")
    assert rl.retry_after("k") == 0
    assert rl.record("k") == 0  # counter started over


def test_keys_are_independent():
    rl = RateLimiter(max_attempts=2, window_seconds=300, block_seconds=300)
    rl.record("a")
    rl.record("a")  # a blocked
    assert rl.retry_after("a") > 0
    assert rl.retry_after("b") == 0  # b untouched


def test_window_expiry_drops_old_hits():
    # window of 0s → every prior hit is already "old" when the next arrives,
    # so the counter never reaches the limit.
    rl = RateLimiter(max_attempts=2, window_seconds=0, block_seconds=300)
    assert rl.record("k") == 0
    time.sleep(0.01)
    assert rl.record("k") == 0  # previous hit pruned, still only 1 in window


def test_login_limiter_is_configured_3_5min():
    assert login_limiter.max_attempts == 3
    assert login_limiter.window == 300
    assert login_limiter.block == 300
