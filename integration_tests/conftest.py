import importlib

import pytest

# Load the local package before the HA fixture isolates its config directory.
importlib.import_module("custom_components.voxl_chromium.client")

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    yield
