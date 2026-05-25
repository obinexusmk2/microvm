"""PolyCall runtime detection and adapter-fallback event stream."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _magic(path: Path, size: int = 4) -> bytes:
    try:
        return path.read_bytes()[:size]
    except OSError:
        return b""


def _is_windows_executable(path: Path) -> bool:
    return _magic(path, 2) == b"MZ"


def _is_elf(path: Path) -> bool:
    return _magic(path, 4) == b"\x7fELF"


@dataclass
class PolyCallRuntime:
    executable: str | None
    library: str | None
    adapter_fallback: bool
    reason: str
    platform: str

    def as_dict(self) -> dict[str, object]:
        return {
            "executable": self.executable,
            "library": self.library,
            "adapterFallback": self.adapter_fallback,
            "reason": self.reason,
            "platform": self.platform,
        }


def detect_polycall(base_dir: Path | None = None) -> PolyCallRuntime:
    root = base_dir or _repo_root()
    system = platform.system().lower()
    candidates: list[Path] = []

    env_exe = os.environ.get("POLYCALL_EXE")
    if env_exe:
        candidates.append(Path(env_exe))

    path_exe = shutil.which("polycall.exe") or shutil.which("polycall")
    if path_exe:
        candidates.append(Path(path_exe))

    candidates.append(root / "examples" / "polycalld" / "bin" / "polycall")

    library_candidates = [
        root / "examples" / "polycalld" / "lib" / "polycall.dll",
        root / "examples" / "polycalld" / "lib" / "libpolycall.so",
        root / "examples" / "polycalld" / "lib" / "libpolycall.dylib",
    ]
    library = next((path for path in library_candidates if path.exists()), None)

    for candidate in candidates:
        if not candidate.exists():
            continue
        if system == "windows":
            if candidate.suffix.lower() == ".exe" or _is_windows_executable(candidate):
                return PolyCallRuntime(str(candidate), str(library) if library else None, False, "compatible-windows-binary", system)
            if _is_elf(candidate):
                return PolyCallRuntime(str(candidate), str(library) if library else None, True, "elf-binary-on-windows", system)
        else:
            if _is_elf(candidate) or os.access(candidate, os.X_OK):
                return PolyCallRuntime(str(candidate), str(library) if library else None, False, "compatible-posix-binary", system)

    return PolyCallRuntime(None, str(library) if library else None, True, "no-compatible-polycall-runtime", system)


def health(base_dir: Path | None = None) -> dict[str, object]:
    runtime = detect_polycall(base_dir)
    payload = runtime.as_dict()
    payload.update({"ok": True, "service": "polycall-adapter"})
    if runtime.adapter_fallback or runtime.executable is None:
        payload["version"] = "adapter-fallback-v1"
        return payload

    try:
        result = subprocess.run(
            [runtime.executable, "--version"],
            text=True,
            capture_output=True,
            timeout=3,
            check=False,
        )
        payload["version"] = (result.stdout or result.stderr or "polycall-runtime").strip()[:200]
        payload["exitCode"] = result.returncode
    except OSError as exc:
        payload["adapterFallback"] = True
        payload["reason"] = f"runtime-exec-failed: {exc}"
        payload["version"] = "adapter-fallback-v1"
    except subprocess.TimeoutExpired:
        payload["adapterFallback"] = True
        payload["reason"] = "runtime-version-timeout"
        payload["version"] = "adapter-fallback-v1"
    return payload


def stream_events(base_dir: Path | None = None, count: int = 5, delay: float = 0.2) -> Iterator[dict[str, object]]:
    runtime = detect_polycall(base_dir)
    yield {"event": "polycall.detected", **runtime.as_dict()}
    for index in range(count):
        yield {
            "event": "polycall.telemetry",
            "sequence": index,
            "adapterFallback": runtime.adapter_fallback,
            "state": "SIGNAL" if index % 2 == 0 else "NONOISE",
            "timestamp": time.time(),
        }
        if delay:
            time.sleep(delay)


def invoke(action: str, payload: dict[str, object] | None = None, base_dir: Path | None = None) -> dict[str, object]:
    runtime = detect_polycall(base_dir)
    body = payload or {}
    return {
        "ok": True,
        "action": action,
        "adapterFallback": runtime.adapter_fallback,
        "runtime": runtime.as_dict(),
        "echo": body,
        "result": f"polycall:{action}:accepted",
    }


def encode_sse(event: dict[str, object]) -> bytes:
    return f"data: {json.dumps(event, separators=(',', ':'))}\n\n".encode("utf-8")
