"""Tests run against the 12 fixed sample places, not the real data file,
so results don't change when the real places or their hours change."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matching  # noqa: E402

SAMPLE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "sample_resources.json")


@pytest.fixture(autouse=True)
def sample_resources(monkeypatch):
    with open(SAMPLE_PATH, encoding="utf-8") as f:
        monkeypatch.setattr(matching, "_cache", json.load(f))
    yield
