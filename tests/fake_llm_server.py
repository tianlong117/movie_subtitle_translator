from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _FakeHTTPServer(ThreadingHTTPServer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.request_count = 0
        self.request_lock = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path.rstrip("/") != "/v1/chat/completions":
            self.send_error(404)
            return
        with self.server.request_lock:
            self.server.request_count += 1
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            self.send_error(400)
            return
        messages = body.get("messages", [])
        user_text = "\n".join(
            message.get("content", "")
            for message in messages
            if message.get("role") == "user"
        )
        all_text = "\n".join(
            message.get("content", "") for message in messages
        )
        content = self._translate(user_text, all_text)
        payload = {
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "created": 0,
            "model": body.get("model", "fake"),
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": content,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _translate(user_text: str, all_text: str = "") -> str:
        if "只输出一个代码" in all_text:
            return "ja"
        if "Reply with OK" in user_text:
            return "OK"
        lines = []
        for line in user_text.splitlines():
            match = re.match(r"^(\d+)\|(.*)$", line.strip())
            if match:
                lines.append(f"{match.group(1)}|中{match.group(2)}")
        return "\n".join(lines) if lines else ""

    def log_message(self, format, *args):
        pass


class FakeLLMServer:
    def __init__(self) -> None:
        self._server = _FakeHTTPServer(
            ("127.0.0.1", 0), _Handler
        )
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )

    @property
    def request_count(self) -> int:
        return self._server.request_count

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def __enter__(self) -> "FakeLLMServer":
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._server.shutdown()
        self._server.server_close()
