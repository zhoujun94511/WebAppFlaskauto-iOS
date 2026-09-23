"""The personalized DDI file is fetched once, off the connect click."""

import threading

import ios.ddi_prefetch as prefetch


def test_prefetch_starts_one_background_fetch(monkeypatch):
    started = threading.Event()
    calls = []

    def fake_fetch():
        calls.append(1)
        started.set()

    monkeypatch.setattr(prefetch, "_run", fake_fetch)
    prefetch.reset_prefetch_for_tests()
    prefetch.schedule_personalized_ddi_prefetch()
    prefetch.schedule_personalized_ddi_prefetch()
    assert started.wait(2)
    assert calls == [1]
