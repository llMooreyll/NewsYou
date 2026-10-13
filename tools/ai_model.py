#!/usr/bin/env python3
"""External model backends for the AI stage.

The pipeline never talks to a model API directly. It shells out to a local CLI,
so credentials and model choice stay in the user's own tool configuration.

    pi     (default) the local `pi` CLI in non-interactive print mode, using
           DEFAULT_MODEL unless the model is overridden
    none   no model call at all — the pipeline runs end to end and the digest
           simply has no AI layer

Why a backend is more than (name, model)
-----------------------------------------
Two fields are enough to *identify* what to run but not to *build the command*:
every CLI spells non-interactive mode, isolation and model selection
differently.

    pi    -p --no-session --no-tools --no-context-files --model <m> "<prompt>"
    codex exec --skip-git-repo-check --sandbox read-only --model <m> "<prompt>"

So the command shape lives in one declarative record per backend (BACKEND_TABLE
below) rather than in scattered conditionals. Adding a backend is one entry
there plus, if its input plumbing differs, one line in `_invoke`; nothing else
in the pipeline changes.

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
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BACKEND = "pi"
DEFAULT_MODEL = "deepseek-v4.1-flash"

# The instruction passed as an argument. Deliberately short: the full prompt is
# the `instructions` field inside the input JSON.
PI_PROMPT = ("Read the `instructions` field in the input and follow it exactly. "
             "Return only the JSON object described by `response_schema`, "
             "with no prose and no markdown fence.")

DEFAULT_TIMEOUT = 900


# --------------------------------------------------------------------------
# Backend profiles
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Backend:
    """How to drive one CLI.

    `base_args` are the flags that make the tool non-interactive and isolated
    from the user's own configuration — for pi these are not optional: without
    --no-context-files the user's global AGENTS.md is loaded, which instructs
    the model to answer in English and append a "> Recap:" block, and both
    corrupt the JSON.

    `prompt` is the text handed to the tool; the real prompt lives in the input
    JSON, so this only points the model at it.
    """
    name: str
    exe: str
    base_args: tuple[str, ...]
    model_flag: str | None = None       # None = the CLI has no model selection
    experimental: bool = False          # selected only if explicitly asked for
    notes: str = ""


BACKEND_TABLE: dict[str, Backend] = {
    "pi": Backend(
        name="pi",
        exe="pi",
        base_args=("-p", "--no-session", "--no-tools", "--no-context-files"),
        model_flag="--model",
        notes="pi CLI, non-interactive print mode",
    ),
    # Kept as a worked example of the extension point, and gated: `available()`
    # refuses it unless the binary exists and the caller names it explicitly.
    # Verify the flags against your codex version before trusting the output.
    "codex": Backend(
        name="codex",
        exe="codex",
        base_args=("exec", "--skip-git-repo-check", "--sandbox", "read-only"),
        model_flag="--model",
        experimental=True,
        notes="Codex CLI, non-interactive exec mode (flags unverified)",
    ),
}

BACKENDS = ("none", *BACKEND_TABLE)

# What the model is told, per backend. The prompt is the same everywhere; only
# the wrapper differs, and it stays short because the real prompt is in the
# input JSON.
PROMPT_ARG = PI_PROMPT


def profile(name: str) -> Backend | None:
    """The profile for a backend name, or None for `none`/unknown."""
    return BACKEND_TABLE.get(name)


class ModelError(RuntimeError):
    pass

# --------------------------------------------------------------------------
# Availability
# --------------------------------------------------------------------------

def available(backend: str) -> tuple[bool, str]:
    """Return (ok, reason). Checked before a run so failures are clear."""
    if backend in ("none", "", None):
        return True, ""
    prof = profile(backend)
    if prof is None:
        return False, (f"unknown AI backend {backend!r} "
                       f"(known: {', '.join(BACKENDS)})")
    if not shutil.which(prof.exe):
        return False, f"backend {backend!r} needs the {prof.exe!r} CLI on PATH"
    return True, ""


def resolve_backend(requested: str) -> tuple[str, str]:
    """Return (backend to use, warning). Unknown or unusable falls back to none."""
    ok, reason = available(requested)
    if ok:
        prof = profile(requested)
        if prof is not None and prof.experimental:
            return requested, (f"backend {requested!r} is experimental and its "
                               f"CLI flags are unverified")
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

def build_command(backend_name: str, model: str | None,
                  extra_args: list[str] | None = None) -> list[str]:
    """The argv for one backend call, prompt included.

    Separated from `_invoke` so it can be asserted on directly — the command
    shape is the part that differs between backends, and it is worth a test.
    """
    prof = profile(backend_name)
    if prof is None:
        raise ModelError(f"backend {backend_name!r} has no implementation")
    exe = shutil.which(prof.exe) or prof.exe
    cmd = [exe, *prof.base_args]
    if model and prof.model_flag:
        cmd += [prof.model_flag, model]
    cmd += list(extra_args or ())
    cmd.append(PROMPT_ARG)
    return cmd


def _invoke(backend: str, input_text: str, *, model: str | None,
            timeout: int, cwd: Path | None,
            extra_args: list[str] | None = None) -> str:
    """Run one backend and return its raw stdout.

    Every backend so far reads the input JSON on stdin and takes the prompt as
    a positional argument. A backend that differs needs a branch here; the
    command shape itself comes from its profile.
    """
    prof = profile(backend)
    if prof is None:
        raise ModelError(f"backend {backend!r} has no implementation")

    exe = shutil.which(prof.exe)
    if not exe:
        raise ModelError(f"the {prof.exe} CLI is not on PATH")

    cmd = build_command(backend, model, extra_args)

    # Run somewhere neutral so project-local resources cannot be discovered.
    workdir = cwd or Path(__file__).resolve().parent.parent
    try:
        result = subprocess.run(
            cmd, input=input_text, capture_output=True, text=True,
            timeout=timeout, cwd=str(workdir), check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ModelError(f"{prof.exe} timed out after {timeout}s") from exc
    except OSError as exc:
        raise ModelError(f"could not run {prof.exe}: {exc}") from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        tail = " | ".join(detail[-3:]) if detail else "no output"
        raise ModelError(f"{prof.exe} exited {result.returncode}: {tail}")
    return result.stdout

def generate(backend: str, input_path: Path, output_path: Path, *,
             model: str | None = None, timeout: int = DEFAULT_TIMEOUT,
             force: bool = False, dry_run: bool = False,
             extra_args: list[str] | None = None,
             day: str | None = None,
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
        raw = _invoke(backend, input_text, model=model, timeout=timeout, cwd=None,
                      extra_args=extra_args)
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

    # Provenance: the decisions file must say what actually produced it. Models
    # routinely self-report an identity — deepseek-v4.1-flash answers as
    # "claude-sonnet-4.5" — so the claim is kept for audit but the recorded
    # model is the one we really invoked. Filling only when empty is not enough:
    # a wrong-but-present value would survive and end up printed in the digest.
    claimed = str(parsed.get("model") or "").strip()
    actual = f"{backend}:{model}" if model else backend
    if claimed and claimed != actual:
        parsed["model_self_reported"] = claimed
    parsed["model"] = actual
    report["model_filled"] = actual

    # The day gets the same treatment. A decisions file is applied only to the
    # day it records (ai_apply.load_decisions), so that has to be the day of
    # the input it answered. The prompts show an example day, and a model that
    # echoes the example back would otherwise have its fresh answer discarded.
    if day:
        parsed["day"] = day

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    report["called"] = True
    report["bytes"] = len(raw)
    report["wrote"] = str(output_path)
    if verbose:
        print(f"  ai: wrote {output_path}", file=sys.stderr)
    return report
