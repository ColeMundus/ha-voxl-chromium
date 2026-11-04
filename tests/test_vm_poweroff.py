import importlib.util
import json
import socket
import threading
from pathlib import Path


def test_qmp_shutdown_waits_for_guest_exit(tmp_path):
    path = tmp_path / "qmp.sock"
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(path))
    listener.listen(1)
    commands = []

    def server():
        conn, _ = listener.accept()
        with conn, conn.makefile("rb") as stream:
            conn.sendall(b'{"QMP":{"version":{}}}\n')
            for _ in range(2):
                commands.append(json.loads(stream.readline())["execute"])
                conn.sendall(b'{"return":{}}\n')
            conn.sendall(b'{"event":"SHUTDOWN"}\n')
        listener.close()

    worker = threading.Thread(target=server, daemon=True)
    worker.start()
    code = Path(__file__).parents[1] / "runner/poweroff.py"
    assert code.exists(), "QMP shutdown helper is not implemented"
    spec = importlib.util.spec_from_file_location("poweroff", code)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.request_poweroff(str(path), timeout=2)
    worker.join(timeout=2)
    assert commands == ["qmp_capabilities", "system_powerdown"]
    assert not worker.is_alive()
