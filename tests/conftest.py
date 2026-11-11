import pytest


@pytest.fixture(autouse=True)
def no_real_keys(monkeypatch):
    """Tests never call Google Places for real, even if a key is in the environment."""
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", help="also run tests that call OpenCode for real (costs a little credit)")


def pytest_configure(config):
    config.addinivalue_line("markers", "live: calls the real OpenCode API; only runs with --live")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="calls OpenCode for real; run with --live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
