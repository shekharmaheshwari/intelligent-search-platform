# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

import pytest
from isp.config import settings
from isp.services.infra import telemetry


@pytest.fixture
def allowed(monkeypatch):
    """Telemetry on, endpoint set, not under CI/tests — the baseline every test flips one knob of."""
    monkeypatch.setattr(settings, "telemetry_enabled", True)
    monkeypatch.setattr(settings, "telemetry_url", "https://collector.example/ping")
    monkeypatch.setattr(telemetry, "_in_ci_or_tests", lambda: False)
    monkeypatch.setattr(settings, "do_not_track", "")


def test_enabled_when_allowed(allowed):
    assert telemetry.is_enabled()


def test_disabled_by_flag(allowed, monkeypatch):
    monkeypatch.setattr(settings, "telemetry_enabled", False)
    assert not telemetry.is_enabled()


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_disabled_by_do_not_track(allowed, monkeypatch, value):
    monkeypatch.setattr(settings, "do_not_track", value)
    assert not telemetry.is_enabled()


def test_do_not_track_read_from_env_file(tmp_path, monkeypatch):
    """DO_NOT_TRACK in .env must count even though pydantic doesn't export it to os.environ."""
    from isp.config import Settings
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("DO_NOT_TRACK=1\n")
    assert Settings(_env_file=env_file).do_not_track == "1"


async def test_typesense_version_runs_off_event_loop(monkeypatch):
    """The sync Typesense call must run in a worker thread, not on the event loop."""
    import sys
    import threading
    import types

    async def no_indexes():
        return []

    async def fixed_id():
        return "test-id"

    # Lightweight stand-ins so the test needs no DB, Typesense, or provider SDKs.
    fakes = {
        "isp.services.domain.index_service": {"list_indexes": no_indexes},
        "isp.services.infra.embedding_service": {"get_current_model": lambda: "voyage-3",
                                                 "_get_provider": lambda m: "voyage"},
        "isp.services.infra.chat": {},
        "isp.services.infra.chat.service": {"get_provider": lambda: "none"},
    }
    import importlib
    for name, attrs in fakes.items():
        fake = types.SimpleNamespace(**attrs)
        monkeypatch.setitem(sys.modules, name, fake)
        # Also replace the parent package attribute, which `from pkg import mod` checks first.
        parent, _, child = name.rpartition(".")
        parent_mod = sys.modules.get(parent) or importlib.import_module(parent)
        monkeypatch.setattr(parent_mod, child, fake, raising=False)

    main_thread = threading.get_ident()
    seen = {}

    def fake_version():
        seen["thread"] = threading.get_ident()
        return "30.2"

    monkeypatch.setattr(telemetry, "_typesense_version", fake_version)
    monkeypatch.setattr(telemetry, "_install_id", fixed_id)

    payload = await telemetry.build_payload("platform_start")
    assert payload["typesense_version"] == "30.2"
    assert seen["thread"] != main_thread


def test_disabled_without_endpoint(allowed, monkeypatch):
    monkeypatch.setattr(settings, "telemetry_url", "")
    monkeypatch.setattr(telemetry, "DEFAULT_TELEMETRY_URL", "")
    assert not telemetry.is_enabled()


def test_disabled_under_tests_by_default(monkeypatch):
    monkeypatch.setattr(settings, "telemetry_enabled", True)
    monkeypatch.setattr(settings, "telemetry_url", "https://collector.example/ping")
    assert not telemetry.is_enabled()


@pytest.mark.parametrize("n, bucket", [(0, "0"), (1, "1-10"), (10, "1-10"), (11, "11-50"), (50, "11-50"), (51, "50+")])
def test_index_count_bucket(n, bucket):
    assert telemetry.index_count_bucket(n) == bucket
