"""Tests must never inherit a developer's live credentials or quota configuration."""
import os
import pytest
from inferflow.config import Settings


@pytest.fixture(autouse=True)
def isolate_live_configuration(monkeypatch):
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for name in list(os.environ):
        if name.startswith("INFERFLOW_"):
            monkeypatch.delenv(name)
