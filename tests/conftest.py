"""Reset process state after each test module.

Order is randomized. A module must not leave environment variables, mutated
globals, socket servers, or temp directories for the next file.
"""

import copy
import os
import shutil
import socketserver
import sys
import tempfile
import threading

import pytest


def _snapshot(module):
    saved = {}
    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        if isinstance(value, (dict, list, set)):
            saved[name] = copy.deepcopy(value)
    return saved


def _restore(module, saved):
    for name, value in saved.items():
        setattr(module, name, copy.deepcopy(value))


def _proxy_snapshot():
    proxy = sys.modules.get("ingress_proxy")
    if proxy is None:
        return None
    return (
        copy.deepcopy(proxy.STATE),
        copy.deepcopy(proxy.NAVIGATION),
        copy.deepcopy(dict(proxy.INPUT_STATE)),
    )


def _proxy_restore(saved):
    if saved is None:
        return
    proxy = sys.modules.get("ingress_proxy")
    if proxy is None:
        return
    state, navigation, inputs = saved
    proxy.STATE.clear()
    proxy.STATE.update(state)
    proxy.NAVIGATION.clear()
    proxy.NAVIGATION.update(navigation)
    proxy.INPUT_STATE.clear()
    proxy.INPUT_STATE.update(inputs)


def _stop_new_servers(already):
    for thread in list(threading.enumerate()):
        if thread.ident in already or not thread.daemon or not thread.is_alive():
            continue
        target = getattr(thread, "_target", None)
        server = getattr(target, "__self__", None)
        if not isinstance(server, socketserver.BaseServer):
            continue
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


@pytest.fixture(autouse=True, scope="module")
def reset_state_after_module(request):
    env = dict(os.environ)
    temp_root = tempfile.gettempdir()
    names_before = set(os.listdir(temp_root))
    threads = {thread.ident for thread in threading.enumerate()}
    saved = _snapshot(request.module)
    proxy = _proxy_snapshot()
    yield
    os.environ.clear()
    os.environ.update(env)
    for name in os.listdir(temp_root):
        if name in names_before or name.startswith("pytest-"):
            continue
        path = os.path.join(temp_root, name)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    _restore(request.module, saved)
    _proxy_restore(proxy)
    _stop_new_servers(threads)
