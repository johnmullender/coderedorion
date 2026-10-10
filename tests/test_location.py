"""Tests for location.py. No real network: httpx.get is replaced by a fake."""

import time

import httpx
import pytest

import location


class FakeResponse:
    """Stands in for an httpx response."""

    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad status", request=None, response=None)

    def json(self):
        return self._data


class FakeHttp:
    """Records every call and returns a canned answer (or raises an error)."""

    def __init__(self):
        self.calls = []
        self.answer = []
        self.error = None
        self.status_code = 200

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        if self.error is not None:
            raise self.error
        return FakeResponse(self.answer, self.status_code)


@pytest.fixture(autouse=True)
def fake_http(monkeypatch):
    """Every test gets a fake network, a fast sleep, and a clean cache."""
    fake = FakeHttp()
    monkeypatch.setattr(location.httpx, "get", fake.get)
    monkeypatch.setattr(location.time, "sleep", lambda seconds: None)
    location._geocode_cache.clear()
    location._last_request_time = None
    return fake


def test_zip_hit_uses_table():
    result = location.resolve_location("I am near 77004")
    assert result["source"] == "zip"
    assert result["label"] == "Third Ward / Midtown"
    assert result["lat"] == 29.73
    assert result["lng"] == -95.367


def test_zip_not_in_table_falls_through_without_network(fake_http):
    # 77005 is not in our table and has no letters, so nothing is looked up.
    assert location.resolve_location("77005") is None
    assert fake_http.calls == []


def test_landmark_is_case_insensitive(fake_http):
    for text in ["near UH", "near uh", "near Uh"]:
        result = location.resolve_location(text)
        assert result["source"] == "landmark"
        assert result["label"] == "University of Houston"
    assert fake_http.calls == []


def test_landmark_multiword_name(fake_http):
    result = location.resolve_location("by Emancipation Park please")
    assert result["source"] == "landmark"
    assert result["label"] == "Emancipation Park"
    assert fake_http.calls == []


def test_landmark_needs_word_boundary(fake_http):
    # "uh" sits inside "huh", so it must not count as the UH landmark.
    # The text has letters, so Nominatim is asked, and the fake answers nothing.
    assert location.resolve_location("huh, what") is None
    assert len(fake_http.calls) == 1


def test_longest_landmark_wins(fake_http):
    result = location.resolve_location("east downtown")
    assert result["label"] == "EaDo (East Downtown)"
    result = location.resolve_location("downtown")
    assert result["label"] == "Downtown Houston"
    assert fake_http.calls == []


def test_none_and_empty_input_return_none_without_network(fake_http):
    for text in [None, "", "   ", "ab", "77"]:
        assert location.resolve_location(text) is None
    assert fake_http.calls == []


def test_nominatim_success(fake_http):
    fake_http.answer = [{"lat": "29.75", "lon": "-95.36", "display_name": "Some Place, Houston"}]
    result = location.resolve_location("some place")
    assert result == {"lat": 29.75, "lng": -95.36, "label": "Some Place, Houston", "source": "geocode"}
    call = fake_http.calls[0]
    assert call["url"] == "https://nominatim.openstreetmap.org/search"
    assert call["params"]["q"] == "some place, Houston, TX"
    assert call["params"]["limit"] == 1
    assert call["params"]["bounded"] == 1
    assert call["params"]["viewbox"] == "-95.8,30.1,-95.0,29.5"
    assert call["headers"]["User-Agent"] == "HelpLineHouston/0.1 (hackathon)"
    assert call["timeout"] == 5.0


def test_nominatim_empty_result_is_none(fake_http):
    fake_http.answer = []
    assert location.resolve_location("nowhere special") is None


def test_nominatim_network_error_is_none(fake_http):
    fake_http.error = httpx.ConnectError("boom")
    assert location.resolve_location("some place") is None


def test_nominatim_http_error_is_none(fake_http):
    fake_http.status_code = 500
    fake_http.answer = [{"lat": "29.75", "lon": "-95.36"}]
    assert location.resolve_location("some place") is None


def test_nominatim_results_are_cached(fake_http):
    fake_http.answer = [{"lat": "29.75", "lon": "-95.36"}]
    first = location.resolve_location("some place")
    second = location.resolve_location("  some place  ")
    assert first == second
    assert len(fake_http.calls) == 1


def test_rate_limit_waits_before_second_request(fake_http, monkeypatch):
    sleeps = []
    monkeypatch.setattr(location.time, "sleep", sleeps.append)
    fake_http.answer = []
    location._last_request_time = time.monotonic()
    location.resolve_location("some place")
    assert len(sleeps) == 1
    assert 0 < sleeps[0] <= 1.0
