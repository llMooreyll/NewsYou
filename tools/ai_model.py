#!/usr/bin/env python3
"""External model backends for the AI stage.

The pipeline never talks to a model API directly. It shells out to a local CLI,
so credentials and model choice stay in the user's own tool configuration.

    none   (default) no model call at all — the pipeline runs end to end and the
           digest simply has no AI layer
    pi     the local `pi` CLI in non-interactive print mode

Only `pi` is implemented. Adding another backend means adding one entry to
BACKENDS and one branch in `_invoke`; nothing else in the pipeline changes,
because a backend only has to turn (prompt, input JSON) into (output JSON).

How the call is made
--------------------
The input JSON is 250 KB or more, which is far past Linux's 128 KB per-argument
limit (MAX_ARG_STRLEN), so the **input goes over stdin** and only the short
instruction is an argument. The real prompt is not duplicated either: it is
already embedded in the input as the `instructions` field, so the argument just
points the model at it.

Isolation flags, none of which are optional:

  --no-context-files  Without it the user's global ~/.pi/agent/AGENTS.md is
                      loaded, which instructs the model to answer in English and
                      to append a "> Recap:" block. Both corrupt the JSON.
  --no-session        Do not write these runs into the user's pi session history.
  --no-tools          Everything needed arrives on stdin; no file or shell access.
  -p / --print        Run once and exit; never open the interactive UI.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

BACKENDS = ("none", "pi")
DEFAULT_BACKEND = "none"

# The instruction passed as an argument. Deliberately short: the full prompt is
# the `instructions` field inside the input JSON.
PI_PROMPT = ("Read the `instructions` field in the input and follow it exactly. "
             "Return only the JSON object described by `response_schema`, "
             "with no prose and no markdown fence.")

PI_BASE_FLAGS = ("-p", "--no-session", "--no-tools", "--no-context-files")

DEFAULT_TIMEOUT = 900

class ModelError(RuntimeError):
    pass

# --------------------------------------------------------------------------
# Availability
# --------------------------------------------------------------------------

def available(backend: str) -> tuple[bool, str]:
    """Return (ok, reason). Checked before a run so failures are clear."""
    if backend in ("none", "", None):
        return True, ""
    if backend == "pi":
        if not shutil.which("pi"):
            return False, "backend 'pi' needs the pi CLI on PATH"
        return True, ""
    return False, f"unknown AI backend {backend!r} (known: {', '.join(BACKENDS)})"

def resolve_backend(requested: str) -> tuple[str, str]:
    """Return (backend to use, warning). Unknown or unusable falls back to none."""
    ok, reason = available(requested)
    if ok:
        return requested, ""
    return "none", f"{reason}; continuing without the AI layer"

# --------------------------------------------------------------------------
# Output parsing
# --------------------------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)

def extract_json(text: str) -> dict | None:
    """Pull one JSON object out of model output, or return None.

    Models occasionally wrap the object in a markdown fence even when told not
    to, so the raw text is tried first and then the usual fallbacks. Returning
    None rather than raising lets the caller degrade instead of crashing.
    """
    if not text or not text.strip():
        return None

    candidates: list[str] = [text]
    candidates += _FENCE.findall(text)

    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start:end + 1])

    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None

# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------

def _invoke(backend: str, input_text: str, *, model: str | None,
            timeout: int, cwd: Path | None) -> str:
    """Run one backend and return its raw stdout."""
    if backend != "pi":
        raise ModelError(f"backend {backend!r} has no implementation")

    exe = shutil.which("pi")
    if not exe:
        raise ModelError("the pi CLI is not on PATH")

    cmd = [exe, *PI_BASE_FLAGS]
    if model:
        cmd += ["--model", model]
    cmd.append(PI_PROMPT)

    # Run somewhere neutral so project-local pi resources cannot be discovered.
    workdir = cwd or Path(__file__).resolve().parent.parent
    try:
        result = subprocess.run(
            cmd, input=input_text, capture_output=True, text=True,
            timeout=timeout, cwd=str(workdir), check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ModelError(f"pi timed out after {timeout}s") from exc
    except OSError as exc:
        raise ModelError(f"could not run pi: {exc}") from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        tail = " | ".join(detail[-3:]) if detail else "no output"
        raise ModelError(f"pi exited {result.returncode}: {tail}")
    return result.stdout

def generate(backend: str, input_path: Path, output_path: Path, *,
             model: str | None = None, timeout: int = DEFAULT_TIMEOUT,
             force: bool = False, dry_run: bool = False,
             verbose: bool = True) -> dict:
    """Ask the backend for decisions and write them to `output_path`.

    Existing output is reused unless `force`, so re-running the pipeline does not
    silently spend another model call.
    """
    report: dict = {"backend": backend, "input": str(input_path),
                    "output": str(output_path), "model": model, "called": False}

    if backend in ("none", "", None):
        report["note"] = "no AI backend selected"
        return report

    if dry_run:
        report["note"] = "dry run: backend not invoked"
        return report

    if output_path.exists() and not force:
        report["note"] = "decisions file already exists; reuse it (--ai-force to regenerate)"
        report["reused"] = True
        return report

    if not input_path.exists():
        report["error"] = f"{input_path} not found"
        return report

    input_text = input_path.read_text(encoding="utf-8")
    if verbose:
        print(f"  ai: calling {backend} on {input_path.name} "
              f"({len(input_text) / 1024:.0f} KiB){' model=' + model if model else ''}",
              file=sys.stderr)

    try:
        raw = _invoke(backend, input_text, model=model, timeout=timeout, cwd=None)
    except ModelError as exc:
        report["error"] = str(exc)
        if verbose:
            print(f"  ! ai backend failed: {exc}", file=sys.stderr)
        return report

    parsed = extract_json(raw)
    if parsed is None:
        report["error"] = "model output was not a JSON object"
        report["raw_excerpt"] = raw[:500]
        if verbose:
            print(f"  ! ai output was not JSON; first 200 chars: {raw[:200]!r}",
                  file=sys.stderr)
        return report

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    report["called"] = True
    report["bytes"] = len(raw)
    report["wrote"] = str(output_path)
    if verbose:
        print(f"  ai: wrote {output_path}", file=sys.stderr)
    return report
