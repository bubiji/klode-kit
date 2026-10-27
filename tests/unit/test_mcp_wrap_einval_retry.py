"""WI-32d / WI-15f: the MCP wrapper's read of ``kb/<id>/current`` retries a transient EINVAL, boundedly.

On a macOS CI runner ``readlink``/``stat`` of ``current`` sometimes fails with EINVAL while ``publish.switch_pointer``
renames a fresh link over it (a system race; the link is intact right after). The reader (``mcp_wrap.resolve_current``)
retries such a failure a few times, a few milliseconds apart; any other error behaves as before (no retry).

Law (S4): the number of reads is bounded - for any run of k EINVALs before a good answer, reads = min(k, RETRIES) + 1
and the sleeps in between total at most RETRIES * RETRY_DELAY_S.
"""

from __future__ import annotations

import errno
import os

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from klode_kit import mcp_wrap as W

pytestmark = pytest.mark.unit

GOOD = "b-20261027T000000Z-0123abcd"


class Stub:
    """A ``readlink`` that raises the scripted errnos, then answers ``builds/<GOOD>`` (or raises for ever)."""

    def __init__(self, errnos, answer=f"builds/{GOOD}"):
        self.errnos, self.answer, self.calls = list(errnos), answer, 0

    def __call__(self, path):
        self.calls += 1
        if self.errnos:
            code = self.errnos.pop(0)
            raise OSError(code, os.strerror(code), str(path))
        return self.answer


@pytest.fixture
def sleeps(monkeypatch):
    slept = []
    monkeypatch.setattr(W, "_sleep", slept.append)
    return slept


def use(monkeypatch, stub):
    monkeypatch.setattr(W, "_readlink", stub)
    return stub


def test_transient_einval_is_retried_and_the_build_is_found(monkeypatch, sleeps, tmp_path):
    stub = use(monkeypatch, Stub([errno.EINVAL, errno.EINVAL]))
    assert W.resolve_current(tmp_path) == GOOD
    assert stub.calls == 3 and len(sleeps) == 2 and all(0 < s <= 0.01 for s in sleeps)


def test_persistent_einval_gives_up_after_the_bound_and_means_not_a_link(monkeypatch, sleeps, tmp_path):
    stub = use(monkeypatch, Stub([errno.EINVAL] * 100))
    assert W.resolve_current(tmp_path) is None
    assert stub.calls == W.RETRIES + 1 and len(sleeps) == W.RETRIES
    assert W.RETRIES == 5


@pytest.mark.parametrize("code", [errno.ENOENT, errno.EACCES, errno.EIO, errno.ELOOP])
def test_other_errors_are_not_retried(monkeypatch, sleeps, tmp_path, code):
    stub = use(monkeypatch, Stub([code, code]))
    assert W.resolve_current(tmp_path) is None
    assert stub.calls == 1 and sleeps == []


def test_a_good_link_is_read_once_without_sleeping(monkeypatch, sleeps, tmp_path):
    stub = use(monkeypatch, Stub([]))
    assert W.resolve_current(tmp_path) == GOOD
    assert stub.calls == 1 and sleeps == []


def test_a_link_that_is_not_ours_is_none_and_not_retried(monkeypatch, sleeps, tmp_path):
    stub = use(monkeypatch, Stub([], answer="somewhere/else"))
    assert W.resolve_current(tmp_path) is None
    assert stub.calls == 1 and sleeps == []


@settings(max_examples=60, deadline=None)
@given(k=st.integers(min_value=0, max_value=20))
def test_reads_are_bounded_for_any_run_of_einval(k):
    slept = []
    stub = Stub([errno.EINVAL] * k)
    result = W.resolve_current("kb", _readlink=stub, _sleep=slept.append)
    assert stub.calls == min(k, W.RETRIES) + 1
    assert (result == GOOD) == (k <= W.RETRIES)
    assert sum(slept) <= W.RETRIES * W.RETRY_DELAY_S + 1e-9


def test_settle_keeps_the_loaded_build_through_a_transient_einval(monkeypatch, sleeps, tmp_path):
    """Without the retry, one EINVAL would read as 'current moved' and drop the build in use."""
    wrapper = W.Wrapper.__new__(W.Wrapper)
    wrapper.kb, wrapper.cached = tmp_path, GOOD
    dropped = []
    wrapper.invalidate = lambda: dropped.append(True)
    use(monkeypatch, Stub([errno.EINVAL]))
    wrapper.settle()
    assert dropped == []
