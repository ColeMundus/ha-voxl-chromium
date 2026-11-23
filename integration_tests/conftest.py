import copy
import importlib
import os
import shutil
import socketserver
import tempfile
import threading

import pytest

# Load the local package before the HA fixture isolates its config directory.
importlib.import_module("custom_components.voxl_chromium.client")

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True, scope="module")
def reset_state_after_module(request):
    env = dict(os.environ)
    temp_root = tempfile.gettempdir()
    names_before = set(os.listdir(temp_root))
    threads = {thread.ident for thread in threading.enumerate()}
    saved = {}
    for name, value in vars(request.module).items():
        if not name.startswith("__") and isinstance(value, (dict, list, set)):
            saved[name] = copy.deepcopy(value)
    yield
    os.environ.clear()
    os.environ.update(env)
    for name in os.listdir(temp_root):
        if name in names_before or name.startswith("pytest-"):
            continue
        path = os.path.join(temp_root, name)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    for name, value in saved.items():
        setattr(request.module, name, copy.deepcopy(value))
    for thread in list(threading.enumerate()):
        if thread.ident in threads or not thread.daemon or not thread.is_alive():
            continue
        target = getattr(thread, "_target", None)
        server = getattr(target, "__self__", None)
        if isinstance(server, socketserver.BaseServer):
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)
