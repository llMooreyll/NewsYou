#!/usr/bin/env python3
"""Local mock of an OpenAI Responses endpoint, used only to capture what Pi sends."""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = "/tmp/pi-diag/request-log.jsonl"


def sse(event: str, payload: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode()


def good_stream(model: str) -> list[bytes]:
    item = {
        "id": "msg_mock",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": "OK", "annotations": []}],
    }
    return [
        sse("response.created", {"type": "response.created", "response": {
            "id": "resp_mock", "object": "response", "status": "in_progress",
            "model": model, "output": []}}),
        sse("response.output_item.added", {"type": "response.output_item.added", "output_index": 0,
            "item": {"id": "msg_mock", "type": "message", "role": "assistant",
                     "status": "in_progress", "content": []}}),
        sse("response.content_part.added", {"type": "response.content_part.added", "item_id": "msg_mock",
            "output_index": 0, "content_index": 0,
            "part": {"type": "output_text", "text": "", "annotations": []}}),
        sse("response.output_text.delta", {"type": "response.output_text.delta", "item_id": "msg_mock",
            "output_index": 0, "content_index": 0, "delta": "OK"}),
        sse("response.output_text.done", {"type": "response.output_text.done", "item_id": "msg_mock",
            "output_index": 0, "content_index": 0, "text": "OK"}),
        sse("response.content_part.done", {"type": "response.content_part.done", "item_id": "msg_mock",
            "output_index": 0, "content_index": 0,
            "part": {"type": "output_text", "text": "OK", "annotations": []}}),
        sse("response.output_item.done", {"type": "response.output_item.done", "output_index": 0,
            "item": item}),
        sse("response.completed", {"type": "response.completed", "response": {
            "id": "resp_mock", "object": "response", "status": "completed", "model": model,
            "output": [item],
            "usage": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}}}),
    ]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def _record(self, body: bytes):
        record = {
            "method": self.command,
            "path": self.path,
            "headers": {k: v for k, v in self.headers.items()},
            "body_bytes": len(body),
            "body": body.decode("utf-8", "replace")[:20000],
        }
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def do_GET(self):
        self._record(b"")
        payload = json.dumps({"data": [{"id": "deepseek-v4.1-flash", "object": "model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            body = self.rfile.read(length)
        elif (self.headers.get("Transfer-Encoding") or "").lower() == "chunked":
            parts = []
            while True:
                size_line = self.rfile.readline().strip()
                size = int(size_line or b"0", 16)
                if size == 0:
                    self.rfile.readline()
                    break
                parts.append(self.rfile.read(size))
                self.rfile.readline()
            body = b"".join(parts)
        else:
            body = b""
        self._record(body)
        try:
            model = json.loads(body or b"{}").get("model", "unknown")
        except Exception:
            model = "unknown"

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for chunk in good_stream(model):
            self.wfile.write(f"{len(chunk):X}\r\n".encode() + chunk + b"\r\n")
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
    open(LOG, "w").close()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"mock listening on http://127.0.0.1:{port}", flush=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
