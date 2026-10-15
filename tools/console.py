#!/usr/bin/env python3
"""Shared terminal output for the pipeline tools.

The pipeline is long and mostly spent waiting on other people's servers, so
"what is it doing right now" is the thing the output has to answer. Two kinds of
line do that:

  permanent   a result that belongs in the record: a source finished, a stage
              reported its counts, something failed.
  live        a task that is in flight, redrawn in place so a slow fetch reads
              as progress instead of silence.

The live region is only drawn on a terminal. Redirected output gets the
permanent lines only — a log file with carriage returns in it is worse than no
progress at all — so anything that matters is also stated in a permanent line.

Nothing here is imported by a collector: the tools own their output, and a
collector that wants to report progress takes a plain callback.
"""
from __future__ import annotations

import io
import os
import re
import shutil
import sys
import threading
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime

_ANSI = re.compile(r"\x1b\[[0-9;]*m")

_CODES = {
    "reset": "\x1b[0m",
    "dim": "\x1b[2m",
    "bold": "\x1b[1m",
    "red": "\x1b[31m",
    "green": "\x1b[32m",
    "yellow": "\x1b[33m",
    "blue": "\x1b[34m",
    "cyan": "\x1b[36m",
    "magenta": "\x1b[35m",
}

OK = "✓"
BAD = "✗"
WARN = "!"

# A step finishes in one of three states. "warn" is the case that matters most
# in practice: the source worked, but not entirely.
STATES = {
    "ok": (OK, "green"),
    "warn": (WARN, "yellow"),
    "fail": (BAD, "red"),
}


def display_width(text: str) -> int:
    """Terminal columns a string occupies, ignoring colour codes."""
    width = 0
    for ch in _ANSI.sub("", text):
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def fit(text: str, width: int) -> str:
    """Truncate to `width` columns. Used for live lines, which must stay on one
    line: a wrapped line would break the cursor arithmetic that redraws them."""
    if display_width(text) <= width:
        return text
    out, seen = "", 0
    for ch in text:
        step = 0 if unicodedata.combining(ch) else (
            2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1)
        if seen + step > width - 1:
            return out + "…"
        out += ch
        seen += step
    return out


def _split_at(text: str, room: int) -> tuple[str, str]:
    """(head, tail) where head is at most `room` columns wide."""
    seen = 0
    for index, char in enumerate(text):
        step = 0 if unicodedata.combining(char) else (
            2 if unicodedata.east_asian_width(char) in ("W", "F") else 1)
        if seen + step > room:
            return text[:index], text[index:]
        seen += step
    return text, ""


def wrap(text: str, width: int, first: str = "", rest: str = "") -> list[str]:
    """Break `text` into lines no wider than `width`, prefixes included.

    A continuation that keeps a hanging indent stays visibly attached to the
    label it belongs to. Without one a wrapped exception message reads as a new
    entry and the column structure is lost, which is exactly when the output is
    being read most carefully. Over-long tokens — a URL inside an exception — are
    broken rather than allowed to overflow.
    """
    lines: list[str] = []
    prefix, current = first, ""
    for word in text.split():
        room = max(1, width - display_width(prefix))
        while display_width(word) > room and not current:
            head, word = _split_at(word, room)
            lines.append(prefix + head)
            prefix = rest
            room = max(1, width - display_width(prefix))
        candidate = word if not current else f"{current} {word}"
        if display_width(prefix + candidate) <= width:
            current = candidate
        else:
            lines.append(prefix + current)
            prefix, current = rest, word
    if current:
        lines.append(prefix + current)
    return lines or [first]


def pad(text: str, width: int, right: bool = False) -> str:
    """Pad to `width` columns. Colour codes are measured as zero width, so a
    styled cell still lines up with its neighbours."""
    gap = " " * max(0, width - display_width(text))
    return gap + text if right else text + gap


def fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f"{minutes}m{rest:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def _is_tty(stream) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


class Reporter:
    """Permanent lines plus a live region, on stderr by default."""

    def __init__(self, stream=None, color=None, live=None, width=None):
        self.stream = sys.stderr if stream is None else stream
        tty = _is_tty(self.stream)
        if color is None:
            color = tty and not os.environ.get("NO_COLOR")
        self.color = color
        # NO_COLOR conventionally disables colour only, but a terminal that has
        # asked for plain output is not a good place to draw a moving region.
        self.live_enabled = (tty and not os.environ.get("NO_COLOR")) if live is None else live
        self.width = width or shutil.get_terminal_size((100, 24)).columns
        self._tasks: dict[str, str] = {}
        self._order: list[str] = []
        self._drawn = 0
        self._lock = threading.RLock()
        self._started = time.time()

    # ------------------------------------------------------------------ style

    def style(self, text: str, *names: str) -> str:
        if not self.color or not names:
            return text
        return "".join(_CODES[n] for n in names) + text + _CODES["reset"]

    def glyph(self, ok: bool) -> str:
        return self.style(OK if ok else BAD, "green" if ok else "red")

    # -------------------------------------------------------------- permanent

    def write(self, text: str = "") -> None:
        """Print one permanent line, keeping the live region below it."""
        with self._lock:
            self._clear()
            self.stream.write(text + "\n")
            self.stream.flush()
            self._redraw()

    def banner(self, stage: str, *facts: str) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.write()
        self.write(f"{self.style('NewsYou', 'bold')} · "
                   f"{self.style(stage, 'cyan')} · {self.style(stamp, 'dim')}")
        shown = [fact for fact in facts if fact]
        if shown:
            self.write("  " + self.style(" · ".join(shown), "dim"))

    def note(self, text: str) -> None:
        self.write("  " + text)

    def warn(self, text: str) -> None:
        self.write("  " + self.style(WARN, "yellow") + " " + text)

    def error(self, text: str) -> None:
        self.write("  " + self.style(BAD, "red") + " " + text)

    def elapsed(self) -> str:
        return fmt_duration(time.time() - self._started)

    # ------------------------------------------------------------------- live

    def task(self, key: str, line: str) -> None:
        with self._lock:
            if key not in self._tasks:
                self._order.append(key)
            self._tasks[key] = line
            self._redraw()

    def update(self, key: str, line: str) -> None:
        with self._lock:
            if key in self._tasks:
                self._tasks[key] = line
                self._redraw()

    def finish(self, key: str, line: str | None = None) -> None:
        """Retire a task; `line` replaces it as a permanent line."""
        with self._lock:
            if key in self._tasks:
                self._order.remove(key)
                del self._tasks[key]
            self._clear()
            if line:
                self.stream.write(line + "\n")
            self.stream.flush()
            self._redraw()

    def close(self) -> None:
        with self._lock:
            self._clear()
            self.stream.flush()

    def _redraw(self) -> None:
        """Draw the live block from wherever the cursor is.

        Only cursor movement and line-clears are emitted, never a newline that
        is not paid for by moving up first: the block always occupies the same
        lines, so redrawing it does not scroll the terminal or leave blanks in
        the scrollback.
        """
        if not self.live_enabled:
            return
        lines = [fit(self._tasks[key], self.width - 1) for key in self._order]
        if not lines and not self._drawn:
            return
        buf = [f"\x1b[{self._drawn}A"] if self._drawn else []
        for line in lines:
            buf.append("\x1b[2K" + line + "\n")
        self._drawn = len(lines)
        self.stream.write("".join(buf))
        self.stream.flush()

    def _clear(self) -> None:
        """Blank the live block and leave the cursor at its **first** line.

        Ending at the top is what lets the next permanent line take the region
        over instead of being printed underneath it, which would strand the
        redrawn block one line too low and leave a gap behind.
        """
        if not self.live_enabled or not self._drawn:
            return
        count = self._drawn
        buf = [f"\x1b[{count}A", "\x1b[2K"]
        for _ in range(count - 1):
            buf.append("\x1b[1B\x1b[2K")
        if count > 1:
            buf.append(f"\x1b[{count - 1}A")
        self._drawn = 0
        self.stream.write("".join(buf))
        self.stream.flush()


class Step:
    """One line of progress: a label, a live detail, then a result.

    `detail` is rebuilt while the task runs; `done()` replaces the live line
    with the permanent one, so the terminal ends up showing exactly one line per
    step and a log shows the same line without the intermediate states.
    """

    def __init__(self, reporter: Reporter, key: str, index: int, total: int,
                 name: str, name_width: int = 11):
        self.reporter = reporter
        self.key = key
        self.prefix = f"{index:>2}/{total}  {pad(name, name_width)}"
        self.started = time.time()
        reporter.task(key, self._line(""))

    def _line(self, detail: str) -> str:
        return f"  {self.prefix} {detail}".rstrip()

    def progress(self, detail: str) -> None:
        self.reporter.update(self.key, self._line(detail))

    def done(self, state: str, detail: str) -> None:
        glyph, color = STATES[state]
        took = fmt_duration(time.time() - self.started)
        self.reporter.finish(
            self.key,
            f"  {self.prefix} {self.reporter.style(glyph, color)} "
            f"{pad(took, 7, right=True)}  {detail}")


_report: Reporter | None = None


def reporter() -> Reporter:
    """The process-wide reporter. Tools build on this so one run has one live
    region; a second Reporter would fight the first for the cursor."""
    global _report
    if _report is None:
        _report = Reporter()
    return _report


@contextmanager
def capture():
    """Collect whatever a collector writes to stderr inside the block.

    Collectors print their own notices — a fallback that fired, an article body
    that would not parse — and several run in worker threads, where returning a
    message would take more plumbing than the message is worth. A direct write
    while a live line is on screen lands on top of it, and the live line is
    rewritten over the message on the next redraw, so the message is lost and
    the screen drifts a row. Swapping the stream for a buffer keeps the live
    region intact and lets the caller decide where the messages belong.

    The buffer is per thread, not per process: sources are fetched concurrently
    now, and a single shared swap would have one source capturing another's
    output. The reporter is unaffected either way — it captured its own stream
    when it was built.
    """
    buffer = io.StringIO()
    _stderr_router().push(buffer)
    try:
        yield buffer
    finally:
        _stderr_router().pop()


class _StderrRouter:
    """A stand-in for sys.stderr that redirects per thread.

    Installed once, in place of the real stream, so that `print(..., file=sys.stderr)`
    inside a worker thread goes to that thread's buffer while the reporter keeps
    writing to the terminal. The alternative — swapping the global for the
    duration of a fetch — works only while one source is in flight.
    """

    def __init__(self, real) -> None:
        self._real = real
        self._local = threading.local()

    def push(self, stream) -> None:
        self._local.stream = stream

    def pop(self) -> None:
        self._local.stream = None

    def current(self):
        """This thread's buffer, or None when it is not capturing."""
        return getattr(self._local, "stream", None)

    def _target(self):
        return getattr(self._local, "stream", None) or self._real

    def write(self, text: str):
        return self._target().write(text)

    def writelines(self, lines):
        return self._target().writelines(lines)

    def flush(self):
        return self._target().flush()

    def isatty(self) -> bool:
        return bool(getattr(self._real, "isatty", lambda: False)())

    def fileno(self):
        return self._real.fileno()

    def __getattr__(self, name):
        return getattr(self._real, name)


_router: _StderrRouter | None = None


def _stderr_router() -> _StderrRouter:
    global _router
    if _router is None:
        _router = _StderrRouter(sys.stderr)
        sys.stderr = _router
    return _router


class _InheritedCapture:
    """Carries the submitting thread's capture into a worker thread.

    A worker thread starts with no capture of its own, so a collector that
    prints from inside `parallel_map` — a body that would not parse, an image
    that would not download — would write straight over the live progress
    region, which is the very thing the capture exists to prevent. The value is
    read when this object is created, in the submitting thread, and applied in
    each worker.

    One instance is shared by every worker of a batch. That is safe because
    push and pop act on the calling thread's own slot.
    """

    def __init__(self, stream) -> None:
        self._stream = stream

    def __enter__(self) -> "_InheritedCapture":
        if self._stream is not None:
            _stderr_router().push(self._stream)
        return self

    def __exit__(self, *exc) -> bool:
        if self._stream is not None:
            _stderr_router().pop()
        return False


def inherited():
    """A context manager giving a worker thread the current thread's capture.

    A no-op when nothing is being captured, so it costs a collector nothing to
    use it unconditionally.
    """
    return _InheritedCapture(_router.current() if _router else None)


def captured_lines(buffer) -> list[str]:
    """The messages from a `capture()` block, with their leading markers gone."""
    out = []
    for line in buffer.getvalue().splitlines():
        text = line.strip()
        if not text:
            continue
        out.append(text[2:] if text.startswith(("! ", "· ")) else text)
    return out


def _install_thread_context() -> None:
    """Let base.parallel_map carry a capture into its worker threads.

    Installed here rather than imported there: `base` is a collector helper and
    has no business knowing what a capture is. It exposes a hook that defaults
    to a no-op, and this replaces it.
    """
    try:
        from collectors import base
    except ImportError:                      # used outside the tools/ tree
        return
    base.inherit_context = inherited


_install_thread_context()
