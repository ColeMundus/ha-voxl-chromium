"""TEST ONLY: emulate Supervisor's prefix proxy from its trusted source IP."""

import json
import select
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

PREFIX = "/api/hassio_ingress/smoke-token/"
STATE = {"typed": "", "clicks": 0, "visits": 0, "previousStorage": None}
FIXTURE = b"""<!doctype html><title>Voxl integration fixture</title>
<style>html,body{margin:0;height:100%;overflow:hidden}body{display:grid;
place-items:center;background:#125694}input{font-size:32px;position:fixed;
inset:0;width:100%;height:100%;border:0;box-sizing:border-box;background:transparent;
text-align:center;color:white}#tick{position:absolute;top:10px;left:10px;
font-size:40px;z-index:2;pointer-events:none}</style>
<div id=tick></div><input autofocus placeholder='Type here'
onclick="fetch('/clicked');if(!window.tone){window.tone=new AudioContext();
let o=tone.createOscillator();o.connect(tone.destination);o.start()}"
oninput="fetch('/typed?value='+encodeURIComponent(this.value))">
<script>fetch('/storage?value='+localStorage.getItem('voxl-test'));
localStorage.setItem('voxl-test','persisted');
let n=0;function tick(){document.querySelector('#tick').textContent=n++;
requestAnimationFrame(tick)}tick()</script>"""


class Proxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def do_GET(self):
        path = urlsplit(self.path)
        if path.path == "/fixture":
            self.reply(FIXTURE, "text/html")
        elif path.path == "/typed":
            STATE["typed"] = parse_qs(path.query).get("value", [""])[0]
            self.reply(b"ok")
        elif path.path == "/clicked":
            STATE["clicks"] += 1
            self.reply(b"ok")
        elif path.path == "/storage":
            STATE["previousStorage"] = parse_qs(path.query).get("value", [""])[0]
            STATE["visits"] += 1
            self.reply(b"ok")
        elif path.path == "/state":
            self.reply(json.dumps(STATE).encode(), "application/json")
        elif self.path.startswith(PREFIX):
            self.proxy()
        else:
            self.send_error(404)

    def reply(self, body, content_type="text/plain"):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_PUT(self):
        if self.path.startswith(PREFIX):
            self.proxy()
        else:
            self.send_error(404)

    def proxy(self):
        path = "/" + self.path[len(PREFIX) :]
        headers = {
            k: v
            for k, v in self.headers.items()
            if k.lower() not in ("host", "connection")
        }
        headers["Host"] = self.headers.get("Host", "localhost")
        upgrade = self.headers.get("Upgrade", "").lower() == "websocket"
        headers["Connection"] = "Upgrade" if upgrade else "close"
        headers["X-Ingress-Path"] = PREFIX.rstrip("/")
        request = f"{self.command} {path} HTTP/1.1\r\n"
        request += "".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n"
        self.close_connection = True
        with socket.create_connection(("172.30.32.3", 8099), timeout=10) as backend:
            backend.sendall(request.encode())
            size = int(self.headers.get("Content-Length", 0))
            if size:
                backend.sendall(self.rfile.read(size))
            backend.settimeout(None)
            sockets = [backend, self.connection] if upgrade else [backend]
            while True:
                ready, _, _ = select.select(sockets, [], [], 30)
                if not ready:
                    continue
                for source in ready:
                    data = source.recv(65536)
                    if not data:
                        return
                    target = self.connection if source is backend else backend
                    target.sendall(data)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Proxy).serve_forever()
