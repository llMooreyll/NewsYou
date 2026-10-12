#!/usr/bin/env python3
"""Small dependency-free Chrome DevTools Protocol probe.

This intentionally uses only Python's standard library. It creates a new tab,
loads a URL, saves a bounded rendered-text capture, and closes only the tab it
created unless `--keep-open` is used. It does not read or export cookies and
does not close pre-existing tabs.

Usage:
    python tools/cdp_capture.py https://example.com --output data/browser-captures/example.json
    python tools/cdp_capture.py --list

The default endpoint is the already-running local Chromium/Helium CDP endpoint.
No packages are installed by this project.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import socket
import struct
import time
import urllib.parse
import urllib.request
from pathlib import Path


class CDPError(RuntimeError):
    pass


def http_request(endpoint: str, path: str, method: str = "GET") -> str:
    request = urllib.request.Request(endpoint.rstrip("/") + path, method=method)
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read().decode("utf-8")


def http_json(endpoint: str, path: str, method: str = "GET"):
    return json.loads(http_request(endpoint, path, method))


def frame(payload: str) -> bytes:
    raw = payload.encode("utf-8")
    mask = secrets.token_bytes(4)
    masked = bytes(value ^ mask[index % 4] for index, value in enumerate(raw))
    length = len(masked)
    if length < 126:
        header = bytes([0x81, 0x80 | length])
    elif length < 65536:
        header = bytes([0x81, 0x80 | 126]) + struct.pack("!H", length)
    else:
        header = bytes([0x81, 0x80 | 127]) + struct.pack("!Q", length)
    return header + mask + masked


def read_exact(sock: socket.socket, length: int) -> bytes:
    chunks = []
    remaining = length
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise CDPError("browser closed the DevTools socket")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(sock: socket.socket):
    first, second = read_exact(sock, 2)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", read_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", read_exact(sock, 8))[0]
    mask = read_exact(sock, 4) if masked else b""
    data = read_exact(sock, length)
    if masked:
        data = bytes(value ^ mask[index % 4] for index, value in enumerate(data))
    return opcode, data


def connect_websocket(websocket_url: str) -> socket.socket:
    parsed = urllib.parse.urlparse(websocket_url)
    if parsed.scheme != "ws":
        raise CDPError(f"only ws:// endpoints are supported, got {parsed.scheme}")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    sock = socket.create_connection((host, port), timeout=10)
    key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(request.encode("ascii"))
    response = b""
    while b"\r\n\r\n" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            raise CDPError("incomplete WebSocket handshake")
        response += chunk
    if not response.startswith(b"HTTP/1.1 101"):
        raise CDPError(response.decode("latin1", errors="replace"))
    return sock


def cdp_call(sock: socket.socket, method: str, params: dict | None = None, command_id: int = 1):
    sock.sendall(frame(json.dumps({"id": command_id, "method": method, "params": params or {}})))
    while True:
        opcode, data = read_frame(sock)
        if opcode == 0x9:  # ping
            sock.sendall(bytes([0x8A, 0]))
            continue
        if opcode == 0x8:
            raise CDPError("browser closed the DevTools socket")
        if opcode != 0x1:
            continue
        message = json.loads(data.decode("utf-8"))
        if message.get("id") == command_id:
            if "error" in message:
                raise CDPError(json.dumps(message["error"], ensure_ascii=False))
            return message.get("result", {})


def new_tab(endpoint: str, url: str):
    encoded = urllib.parse.quote(url, safe=":/?=&%#")
    # Chrome's /json/new endpoint is commonly PUT-only.
    return http_json(endpoint, "/json/new?" + encoded, method="PUT")


def capture(
    endpoint: str,
    url: str,
    output: Path,
    wait_seconds: float,
    min_text: int,
    keep_open: bool,
):
    target = new_tab(endpoint, url)
    sock = connect_websocket(target["webSocketDebuggerUrl"])
    try:
        cdp_call(sock, "Page.enable", command_id=1)
        cdp_call(sock, "Runtime.enable", command_id=2)
        expression = r"""
(() => {
  const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
  const links = [...document.querySelectorAll('a[href]')].slice(0, 80).map(a => ({
    text: clean(a.innerText).slice(0, 240),
    href: a.href
  }));
  return {
    url: location.href,
    title: document.title,
    language: document.documentElement.lang || null,
    ready_state: document.readyState,
    text: (document.body?.innerText || '').slice(0, 30000),
    links,
    captured_at: new Date().toISOString()
  };
})()
"""
        deadline = time.monotonic() + wait_seconds
        command_id = 3
        value = None
        while True:
            result = cdp_call(
                sock,
                "Runtime.evaluate",
                {"expression": expression, "returnByValue": True, "awaitPromise": True},
                command_id=command_id,
            )
            command_id += 1
            value = result.get("result", {}).get("value")
            if not isinstance(value, dict):
                raise CDPError("page did not return a capture object")
            enough_text = len(value.get("text", "")) >= min_text
            settled = value.get("ready_state") == "complete"
            if (enough_text and settled) or time.monotonic() >= deadline:
                break
            time.sleep(0.5)
        value["requested_url"] = url
        value["capture_method"] = "browser_cdp_rendered_page"
        value["browser_endpoint"] = endpoint
        body_text = value.get("text", "")
        final_url = value.get("url", "")
        challenge_markers = ("环境异常", "完成验证", "captcha", "verification required", "robot check")
        if final_url.startswith(("chrome-error://", "about:blank")):
            value["capture_status"] = "navigation_error"
        elif any(marker.lower() in body_text.lower() for marker in challenge_markers):
            value["capture_status"] = "access_challenge"
        elif final_url.startswith(("http://", "https://")) and len(body_text) > 0:
            value["capture_status"] = "ok"
        else:
            value["capture_status"] = "empty_or_unknown"
        value["capture_status_detail"] = {
            "text_length": len(body_text),
            "challenge_detected": value["capture_status"] == "access_challenge",
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finally:
        sock.close()
        if not keep_open:
            try:
                http_request(endpoint, "/json/close/" + target["id"])
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", nargs="?", help="URL to open in a new browser tab")
    parser.add_argument("--endpoint", default=os.environ.get("NEWSLETTER_CDP_ENDPOINT", "http://127.0.0.1:9222"))
    parser.add_argument("--output", type=Path, default=Path("data/browser-captures/capture.json"))
    parser.add_argument("--wait", type=float, default=5.0, help="maximum seconds to wait for rendered content")
    parser.add_argument("--min-text", type=int, default=500, help="desired minimum rendered text length")
    parser.add_argument("--keep-open", action="store_true", help="leave the newly created tab open")
    parser.add_argument("--list", action="store_true", help="list current browser targets")
    args = parser.parse_args()

    if args.list:
        print(json.dumps(http_json(args.endpoint, "/json/list"), ensure_ascii=False, indent=2))
        return
    if not args.url:
        parser.error("a URL is required unless --list is used")
    capture(args.endpoint, args.url, args.output, args.wait, args.min_text, args.keep_open)
    print(args.output)


if __name__ == "__main__":
    main()
