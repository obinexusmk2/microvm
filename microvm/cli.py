#!/usr/bin/env python3
"""MicroVM v1 prototype CLI.

This is a practical implementation of the architecture in ARCHITECTURE.md. It
packages workspaces, validates manifests, safely extracts archives, starts native
Python/Node components as child processes, and records enough state for stop/swap.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .resources import BundleError, extract_bundle, list_bundle_resources


MANIFEST_NAME = "microvm.workspace.json"
SCHEMA_VERSION = "0.1"
SUPPORTED_RUNTIMES = {
    "python": {"python", "python3", "py"},
    "node": {"node"},
}
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
LIFECYCLE_STATES = {
    "packed",
    "validated",
    "extracted",
    "starting",
    "running",
    "failed",
    "stopped",
    "swapped",
}
PROCESS_HANDLES: dict[int, subprocess.Popen[Any]] = {}


class MicroVMError(Exception):
    """Expected CLI/runtime failure."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_runtime_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "MicroVM"
    return Path.home() / ".microvm"


def emit_event(
    *,
    state: str,
    message: str,
    workspace: str | None = None,
    instance_id: str | None = None,
    component: str | None = None,
    **details: Any,
) -> None:
    event: dict[str, Any] = {
        "timestamp": utc_now(),
        "workspace": workspace,
        "instanceId": instance_id,
        "component": component,
        "state": state,
        "message": message,
    }
    event.update({key: value for key, value in details.items() if value is not None})
    print(json.dumps(event, separators=(",", ":")), flush=True)


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MicroVMError(f"{path} is not valid JSON: {exc}") from exc


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def is_safe_identifier(value: Any) -> bool:
    return isinstance(value, str) and bool(SAFE_NAME_RE.fullmatch(value))


def normalized_manifest_path(value: str) -> PurePosixPath:
    normalized = value.replace("\\", "/")
    return PurePosixPath(normalized)


def ensure_relative_safe_path(value: Any, field: str) -> PurePosixPath:
    if not isinstance(value, str) or not value.strip():
        raise MicroVMError(f"{field} must be a non-empty relative path")

    posix_path = normalized_manifest_path(value)
    windows_path = PureWindowsPath(value)
    raw = value.replace("\\", "/")

    if posix_path.is_absolute() or windows_path.is_absolute():
        raise MicroVMError(f"{field} must not be absolute: {value}")
    if raw.startswith("/") or raw.startswith("//"):
        raise MicroVMError(f"{field} must not be absolute or UNC-like: {value}")
    if windows_path.drive:
        raise MicroVMError(f"{field} must not include a drive letter: {value}")
    if any(part in ("", ".", "..") for part in posix_path.parts):
        raise MicroVMError(f"{field} must not include empty, current, or parent segments: {value}")

    return posix_path


def resolve_inside(root: Path, relative_value: str, field: str) -> Path:
    safe = ensure_relative_safe_path(relative_value, field)
    target = (root / Path(*safe.parts)).resolve()
    root_resolved = root.resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise MicroVMError(f"{field} escapes workspace root: {relative_value}") from exc
    return target


def validate_manifest(manifest: dict[str, Any], workspace_root: Path | None = None) -> list[str]:
    errors: list[str] = []

    def add(message: str) -> None:
        errors.append(message)

    if not isinstance(manifest, dict):
        return ["manifest must be a JSON object"]

    for key in ("schemaVersion", "name", "version", "components"):
        if key not in manifest:
            add(f"missing required field: {key}")

    if manifest.get("schemaVersion") != SCHEMA_VERSION:
        add(f"schemaVersion must be {SCHEMA_VERSION!r}")

    workspace_name = manifest.get("name")
    if not is_safe_identifier(workspace_name):
        add("name must contain only letters, numbers, dots, underscores, and hyphens")

    version = manifest.get("version")
    if not isinstance(version, str) or not version.strip():
        add("version must be a non-empty string")

    allowed_runtimes = manifest.get("allowedRuntimes", [])
    if not isinstance(allowed_runtimes, list) or not all(isinstance(item, str) for item in allowed_runtimes):
        add("allowedRuntimes must be an array of strings")
        allowed_runtimes = []

    allowed_runtime_set = set(allowed_runtimes)
    unsupported_allowed = allowed_runtime_set - set(SUPPORTED_RUNTIMES)
    if unsupported_allowed:
        add(f"allowedRuntimes contains unsupported v1 runtime(s): {', '.join(sorted(unsupported_allowed))}")

    permissions = manifest.get("permissions", {})
    network_ports: set[int] = set()
    if permissions:
        if not isinstance(permissions, dict):
            add("permissions must be an object")
        else:
            network = permissions.get("network", {})
            if network:
                if not isinstance(network, dict):
                    add("permissions.network must be an object")
                else:
                    raw_ports = network.get("ports", [])
                    if raw_ports is None:
                        raw_ports = []
                    if not isinstance(raw_ports, list) or not all(isinstance(port, int) for port in raw_ports):
                        add("permissions.network.ports must be an array of integers")
                    else:
                        network_ports = set(raw_ports)
                        for port in network_ports:
                            if port < 1 or port > 65535:
                                add(f"network port out of range: {port}")

            filesystem = permissions.get("filesystem", [])
            if filesystem is None:
                filesystem = []
            if not isinstance(filesystem, list):
                add("permissions.filesystem must be an array")
            else:
                for index, entry in enumerate(filesystem):
                    if not isinstance(entry, dict):
                        add(f"permissions.filesystem[{index}] must be an object")
                        continue
                    try:
                        ensure_relative_safe_path(entry.get("path"), f"permissions.filesystem[{index}].path")
                    except MicroVMError as exc:
                        add(str(exc))
                    if entry.get("access") not in ("read", "read-write"):
                        add(f"permissions.filesystem[{index}].access must be 'read' or 'read-write'")

    components = manifest.get("components")
    if not isinstance(components, list) or not components:
        add("components must be a non-empty array")
        components = []

    component_names: set[str] = set()
    component_declared_names: list[str] = []

    for index, component in enumerate(components):
        prefix = f"components[{index}]"
        if not isinstance(component, dict):
            add(f"{prefix} must be an object")
            continue

        name = component.get("name")
        if not is_safe_identifier(name):
            add(f"{prefix}.name must contain only letters, numbers, dots, underscores, and hyphens")
        elif name in component_names:
            add(f"duplicate component name: {name}")
        else:
            component_names.add(name)
            component_declared_names.append(name)

        runtime = component.get("runtime")
        if runtime not in SUPPORTED_RUNTIMES:
            add(f"{prefix}.runtime must be one of: {', '.join(sorted(SUPPORTED_RUNTIMES))}")
        elif runtime not in allowed_runtime_set:
            add(f"{prefix}.runtime {runtime!r} is not listed in allowedRuntimes")

        command = component.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
            add(f"{prefix}.command must be a non-empty array of strings")
        elif runtime in SUPPORTED_RUNTIMES:
            executable_name = Path(command[0]).name.lower()
            executable_stem = executable_name.removesuffix(".exe")
            aliases = SUPPORTED_RUNTIMES[runtime]
            if executable_stem not in aliases:
                add(f"{prefix}.command[0] must match runtime {runtime!r}; got {command[0]!r}")

        working_directory = component.get("workingDirectory")
        try:
            if isinstance(working_directory, str):
                resolved = resolve_inside(workspace_root, working_directory, f"{prefix}.workingDirectory") if workspace_root else None
                if resolved and not resolved.is_dir():
                    add(f"{prefix}.workingDirectory does not exist: {working_directory}")
            else:
                ensure_relative_safe_path(working_directory, f"{prefix}.workingDirectory")
        except MicroVMError as exc:
            add(str(exc))

        ports = component.get("ports", [])
        if ports is None:
            ports = []
        if not isinstance(ports, list) or not all(isinstance(port, int) for port in ports):
            add(f"{prefix}.ports must be an array of integers")
        else:
            for port in ports:
                if port < 1 or port > 65535:
                    add(f"{prefix}.ports contains out-of-range port: {port}")
                if network_ports and port not in network_ports:
                    add(f"{prefix}.ports contains undeclared network permission port: {port}")

        stdio = component.get("stdio", False)
        if not isinstance(stdio, bool):
            add(f"{prefix}.stdio must be a boolean")

        depends_on = component.get("dependsOn", [])
        if depends_on is None:
            depends_on = []
        if not isinstance(depends_on, list) or not all(isinstance(item, str) for item in depends_on):
            add(f"{prefix}.dependsOn must be an array of component names")

        environment = component.get("environment", {})
        if environment is None:
            environment = {}
        if not isinstance(environment, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in environment.items()):
            add(f"{prefix}.environment must be an object with string values")

        health_check = component.get("healthCheck")
        if health_check is not None:
            if not isinstance(health_check, dict):
                add(f"{prefix}.healthCheck must be an object")
            elif health_check.get("type") != "http":
                add(f"{prefix}.healthCheck.type must be 'http' in v1")
            elif not isinstance(health_check.get("url"), str):
                add(f"{prefix}.healthCheck.url must be a string")

    declared = set(component_declared_names)
    for index, component in enumerate(components):
        if not isinstance(component, dict):
            continue
        for dependency in component.get("dependsOn") or []:
            if isinstance(dependency, str) and dependency not in declared:
                add(f"components[{index}].dependsOn references unknown component: {dependency}")

    return errors


def load_workspace_manifest(workspace: Path) -> dict[str, Any]:
    manifest_path = workspace / MANIFEST_NAME
    if not manifest_path.is_file():
        raise MicroVMError(f"missing {MANIFEST_NAME} in {workspace}")
    return read_json(manifest_path)


def validate_workspace(workspace: Path) -> dict[str, Any]:
    workspace = workspace.resolve()
    if not workspace.is_dir():
        raise MicroVMError(f"workspace is not a directory: {workspace}")
    manifest = load_workspace_manifest(workspace)
    errors = validate_manifest(manifest, workspace)
    if errors:
        raise MicroVMError("manifest validation failed:\n- " + "\n- ".join(errors))
    return manifest


def is_zip_package(path: Path) -> bool:
    return path.is_file() and zipfile.is_zipfile(path)


def validate_zip_member_name(name: str) -> None:
    normalized = name.replace("\\", "/").rstrip("/")
    posix_path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(name)
    if not normalized:
        raise MicroVMError("archive contains empty path")
    if normalized.startswith("/") or normalized.startswith("//"):
        raise MicroVMError(f"archive contains absolute or UNC-like path: {name}")
    if posix_path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
        raise MicroVMError(f"archive contains absolute path: {name}")
    if any(part in ("", ".", "..") for part in posix_path.parts):
        raise MicroVMError(f"archive contains unsafe path segment: {name}")


def read_manifest_from_zip(package: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(package) as archive:
            names = archive.namelist()
            for name in names:
                validate_zip_member_name(name)
            if MANIFEST_NAME not in names:
                raise MicroVMError(f"package is missing root {MANIFEST_NAME}")
            with archive.open(MANIFEST_NAME) as manifest_file:
                return json.loads(manifest_file.read().decode("utf-8"))
    except zipfile.BadZipFile as exc:
        raise MicroVMError(f"not a valid zip package: {package}") from exc
    except json.JSONDecodeError as exc:
        raise MicroVMError(f"{MANIFEST_NAME} in package is not valid JSON: {exc}") from exc


def validate_package(package: Path) -> dict[str, Any]:
    if not is_zip_package(package):
        raise MicroVMError(f"package is not a zip archive: {package}")
    manifest = read_manifest_from_zip(package)
    errors = validate_manifest(manifest, None)
    if not errors:
        errors.extend(validate_package_entries(package, manifest))
    if errors:
        raise MicroVMError("package manifest validation failed:\n- " + "\n- ".join(errors))
    return manifest


def validate_package_entries(package: Path, manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    with zipfile.ZipFile(package) as archive:
        names = {name.replace("\\", "/").rstrip("/") for name in archive.namelist()}
    if MANIFEST_NAME not in names:
        errors.append(f"package is missing root {MANIFEST_NAME}")

    for index, component in enumerate(manifest.get("components") or []):
        if not isinstance(component, dict):
            continue
        working_directory = component.get("workingDirectory")
        try:
            safe_workdir = ensure_relative_safe_path(working_directory, f"components[{index}].workingDirectory").as_posix()
        except MicroVMError as exc:
            errors.append(str(exc))
            continue
        if safe_workdir not in names and not any(name.startswith(f"{safe_workdir}/") for name in names):
            errors.append(f"components[{index}].workingDirectory missing from package: {working_directory}")
    return errors


def safe_extract(package: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    destination_resolved = destination.resolve()
    with zipfile.ZipFile(package) as archive:
        for info in archive.infolist():
            validate_zip_member_name(info.filename)
            if info.is_dir():
                continue
            target = (destination / Path(*PurePosixPath(info.filename.replace("\\", "/")).parts)).resolve()
            try:
                target.relative_to(destination_resolved)
            except ValueError as exc:
                raise MicroVMError(f"archive member escapes extraction root: {info.filename}") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def create_package(workspace: Path, output: Path | None) -> Path:
    manifest = validate_workspace(workspace)
    workspace = workspace.resolve()
    package_name = f"{manifest['name']}-{manifest['version']}.microvm.zip"
    package_path = (output or workspace / package_name).resolve()

    if package_path.exists() and package_path.is_dir():
        raise MicroVMError(f"output path is a directory: {package_path}")

    package_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(package_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in sorted(workspace.rglob("*")):
            if item == package_path:
                continue
            if item.is_dir():
                continue
            relative = item.relative_to(workspace).as_posix()
            if relative.endswith(".microvm.zip"):
                continue
            validate_zip_member_name(relative)
            archive.write(item, relative)

    emit_event(
        state="packed",
        message="Workspace packed",
        workspace=manifest["name"],
        component=None,
        package=str(package_path),
    )
    return package_path


def inspect_target(target: Path) -> dict[str, Any]:
    target = target.resolve()
    manifest: dict[str, Any]
    errors: list[str]
    target_type: str

    if target.is_dir():
        target_type = "workspace"
        try:
            manifest = load_workspace_manifest(target)
            errors = validate_manifest(manifest, target)
        except MicroVMError as exc:
            manifest = {}
            errors = [str(exc)]
    elif is_zip_package(target):
        target_type = "package"
        try:
            manifest = read_manifest_from_zip(target)
            errors = validate_manifest(manifest, None)
        except MicroVMError as exc:
            manifest = {}
            errors = [str(exc)]
    else:
        raise MicroVMError(f"target is neither a workspace directory nor a package: {target}")

    components = manifest.get("components", []) if isinstance(manifest, dict) else []
    summary = {
        "target": str(target),
        "type": target_type,
        "valid": not errors,
        "errors": errors,
        "workspace": manifest.get("name") if isinstance(manifest, dict) else None,
        "version": manifest.get("version") if isinstance(manifest, dict) else None,
        "allowedRuntimes": manifest.get("allowedRuntimes", []) if isinstance(manifest, dict) else [],
        "components": [
            {
                "name": component.get("name"),
                "runtime": component.get("runtime"),
                "workingDirectory": component.get("workingDirectory"),
                "ports": component.get("ports", []),
                "stdio": component.get("stdio", False),
                "dependsOn": component.get("dependsOn", []),
            }
            for component in components
            if isinstance(component, dict)
        ],
        "permissions": manifest.get("permissions", {}) if isinstance(manifest, dict) else {},
    }
    return summary


def instance_id() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def state_dir(runtime_root: Path) -> Path:
    return runtime_root / "state"


def active_state_path(runtime_root: Path) -> Path:
    return state_dir(runtime_root) / "active.json"


def load_active_state(runtime_root: Path) -> dict[str, Any]:
    path = active_state_path(runtime_root)
    if not path.is_file():
        return {"instances": []}
    try:
        return read_json(path)
    except MicroVMError:
        return {"instances": []}


def save_active_state(runtime_root: Path, state: dict[str, Any]) -> None:
    write_json(active_state_path(runtime_root), state)


def port_is_available(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) != 0


def topological_components(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_name = {component["name"]: component for component in components}
    visited: set[str] = set()
    visiting: set[str] = set()
    ordered: list[dict[str, Any]] = []

    def visit(name: str) -> None:
        if name in visited:
            return
        if name in visiting:
            raise MicroVMError(f"dependency cycle involving component: {name}")
        visiting.add(name)
        component = by_name[name]
        for dependency in component.get("dependsOn") or []:
            visit(dependency)
        visiting.remove(name)
        visited.add(name)
        ordered.append(component)

    for component in components:
        visit(component["name"])

    return ordered


def clean_component_env(component: dict[str, Any], workspace: str, instance: str) -> dict[str, str]:
    allowed_base = {
        "PATH",
        "SystemRoot",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
        "HOME",
        "LANG",
        "SYSTEMDRIVE",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "COMMONPROGRAMFILES",
        "COMMONPROGRAMFILES(X86)",
        "DRIVERDATA",
        "CONDA_PREFIX",
        "CONDA_DEFAULT_ENV",
        "CONDA_DLL_SEARCH_MODIFICATION_ENABLE",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    }
    allowed_upper = {key.upper() for key in allowed_base}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed_upper}
    host_root = str(Path(__file__).resolve().parents[1])
    env.update(
        {
            "MICROVM_WORKSPACE": workspace,
            "MICROVM_INSTANCE_ID": instance,
            "MICROVM_HOST_ROOT": host_root,
        }
    )
    for key, value in (component.get("environment") or {}).items():
        expanded = value.replace("${MICROVM_HOST_ROOT}", host_root).replace("%MICROVM_HOST_ROOT%", host_root)
        env[key] = expanded
    return env


def resolve_runtime_command(command: list[str]) -> list[str]:
    executable = command[0]
    if shutil.which(executable) is None:
        raise MicroVMError(f"runtime executable not found on PATH: {executable}")
    return command


def wait_for_health(component: dict[str, Any]) -> tuple[bool, str]:
    health = component.get("healthCheck")
    if not health:
        return True, "No health check configured"

    timeout_seconds = float(health.get("timeoutSeconds", 2))
    interval_seconds = float(health.get("intervalSeconds", 1))
    deadline = time.time() + max(timeout_seconds, interval_seconds, 1)
    url = health["url"]

    while time.time() <= deadline:
        try:
            with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
                if 200 <= response.status < 400:
                    return True, f"Health check passed: {url}"
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(interval_seconds)

    return False, f"Health check failed: {url}"


def prepare_run_target(target: Path, runtime_root: Path) -> tuple[dict[str, Any], Path, Path | None, str]:
    target = target.resolve()
    if target.is_dir():
        manifest = validate_workspace(target)
        instance = instance_id()
        run_root = runtime_root / "runtime" / manifest["name"] / instance
        if run_root.exists():
            shutil.rmtree(run_root)
        shutil.copytree(target, run_root, ignore=shutil.ignore_patterns("*.microvm.zip", "__pycache__", "node_modules"))
        return manifest, run_root, None, instance

    manifest = validate_package(target)
    instance = instance_id()
    run_root = runtime_root / "runtime" / manifest["name"] / instance
    if run_root.exists():
        shutil.rmtree(run_root)
    safe_extract(target, run_root)
    errors = validate_manifest(manifest, run_root)
    if errors:
        raise MicroVMError("extracted package validation failed:\n- " + "\n- ".join(errors))
    return manifest, run_root, target, instance


def start_workspace(target: Path, runtime_root: Path) -> int:
    runtime_root = runtime_root.resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    manifest, run_root, package_path, instance = prepare_run_target(target, runtime_root)
    workspace = manifest["name"]
    components = topological_components(manifest["components"])
    logs_root = runtime_root / "logs" / workspace / instance
    logs_root.mkdir(parents=True, exist_ok=True)

    emit_event(state="validated", message="Workspace validated", workspace=workspace, instance_id=instance)
    emit_event(state="extracted", message="Workspace staged for execution", workspace=workspace, instance_id=instance, path=str(run_root))

    started_pids: list[int] = []

    for component in components:
        for port in component.get("ports") or []:
            if not port_is_available(port):
                emit_event(
                    state="failed",
                    message=f"Port is already in use: {port}",
                    workspace=workspace,
                    instance_id=instance,
                    component=component["name"],
                    port=port,
                    errorCode="PORT_IN_USE",
                )
                return 1

    active = load_active_state(runtime_root)
    instance_state = {
        "workspace": workspace,
        "instanceId": instance,
        "runtimeRoot": str(runtime_root),
        "runRoot": str(run_root),
        "package": str(package_path) if package_path else None,
        "components": [],
        "createdAt": utc_now(),
    }

    for component in components:
        name = component["name"]
        workdir = resolve_inside(run_root, component["workingDirectory"], f"{name}.workingDirectory")
        command = resolve_runtime_command(component["command"])
        stdout_path = logs_root / f"{name}.stdout.log"
        stderr_path = logs_root / f"{name}.stderr.log"

        emit_event(
            state="starting",
            message=f"Starting {component['runtime']} component",
            workspace=workspace,
            instance_id=instance,
            component=name,
            runtime=component["runtime"],
            command=command,
        )

        stdout_file = stdout_path.open("ab")
        stderr_file = stderr_path.open("ab")
        try:
            process = subprocess.Popen(
                command,
                cwd=str(workdir),
                env=clean_component_env(component, workspace, instance),
                stdout=stdout_file,
                stderr=stderr_file,
                stdin=subprocess.DEVNULL,
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        except OSError as exc:
            stdout_file.close()
            stderr_file.close()
            emit_event(
                state="failed",
                message=f"Failed to start component: {exc}",
                workspace=workspace,
                instance_id=instance,
                component=name,
                errorCode="START_FAILED",
            )
            for pid in started_pids:
                terminate_process(pid)
            return 1
        finally:
            stdout_file.close()
            stderr_file.close()

        time.sleep(0.25)
        if process.poll() is not None:
            emit_event(
                state="failed",
                message="Component exited during startup",
                workspace=workspace,
                instance_id=instance,
                component=name,
                pid=process.pid,
                exitCode=process.returncode,
                stdout=str(stdout_path),
                stderr=str(stderr_path),
                errorCode="EARLY_EXIT",
            )
            for pid in started_pids:
                terminate_process(pid)
            return 1

        healthy, health_message = wait_for_health(component)
        if not healthy:
            terminate_process(process.pid)
            emit_event(
                state="failed",
                message=health_message,
                workspace=workspace,
                instance_id=instance,
                component=name,
                pid=process.pid,
                errorCode="HEALTH_CHECK_FAILED",
            )
            for pid in started_pids:
                terminate_process(pid)
            return 1

        started_pids.append(process.pid)
        PROCESS_HANDLES[process.pid] = process
        emit_event(
            state="running",
            message=health_message,
            workspace=workspace,
            instance_id=instance,
            component=name,
            pid=process.pid,
            stdout=str(stdout_path),
            stderr=str(stderr_path),
        )

        instance_state["components"].append(
            {
                "name": name,
                "pid": process.pid,
                "runtime": component["runtime"],
                "workingDirectory": str(workdir),
                "command": command,
                "stdout": str(stdout_path),
                "stderr": str(stderr_path),
                "ports": component.get("ports", []),
            }
        )

    active.setdefault("instances", [])
    active["instances"] = [
        existing
        for existing in active["instances"]
        if existing.get("instanceId") != instance_state["instanceId"]
    ]
    active["instances"].append(instance_state)
    save_active_state(runtime_root, active)
    return 0


def start_component_in_instance(
    instance: dict[str, Any],
    component: dict[str, Any],
    runtime_root: Path,
) -> int:
    workspace = instance["workspace"]
    instance_id_value = instance["instanceId"]
    run_root = Path(instance["runRoot"])
    logs_root = runtime_root / "logs" / workspace / instance_id_value
    logs_root.mkdir(parents=True, exist_ok=True)

    for port in component.get("ports") or []:
        if not port_is_available(port):
            emit_event(
                state="failed",
                message=f"Port is already in use: {port}",
                workspace=workspace,
                instance_id=instance_id_value,
                component=component["name"],
                port=port,
                errorCode="PORT_IN_USE",
            )
            return 1

    name = component["name"]
    workdir = resolve_inside(run_root, component["workingDirectory"], f"{name}.workingDirectory")
    command = resolve_runtime_command(component["command"])
    stdout_path = logs_root / f"{name}.stdout.log"
    stderr_path = logs_root / f"{name}.stderr.log"

    emit_event(
        state="starting",
        message=f"Starting {component['runtime']} component",
        workspace=workspace,
        instance_id=instance_id_value,
        component=name,
        runtime=component["runtime"],
        command=command,
    )

    stdout_file = stdout_path.open("ab")
    stderr_file = stderr_path.open("ab")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(workdir),
            env=clean_component_env(component, workspace, instance_id_value),
            stdout=stdout_file,
            stderr=stderr_file,
            stdin=subprocess.DEVNULL,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except OSError as exc:
        emit_event(
            state="failed",
            message=f"Failed to start component: {exc}",
            workspace=workspace,
            instance_id=instance_id_value,
            component=name,
            errorCode="START_FAILED",
        )
        return 1
    finally:
        stdout_file.close()
        stderr_file.close()

    time.sleep(0.25)
    if process.poll() is not None:
        emit_event(
            state="failed",
            message="Component exited during startup",
            workspace=workspace,
            instance_id=instance_id_value,
            component=name,
            pid=process.pid,
            exitCode=process.returncode,
            stdout=str(stdout_path),
            stderr=str(stderr_path),
            errorCode="EARLY_EXIT",
        )
        return 1

    healthy, health_message = wait_for_health(component)
    if not healthy:
        terminate_process(process.pid)
        emit_event(
            state="failed",
            message=health_message,
            workspace=workspace,
            instance_id=instance_id_value,
            component=name,
            pid=process.pid,
            errorCode="HEALTH_CHECK_FAILED",
        )
        return 1

    PROCESS_HANDLES[process.pid] = process
    component_state = {
        "name": name,
        "pid": process.pid,
        "runtime": component["runtime"],
        "workingDirectory": str(workdir),
        "command": command,
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "ports": component.get("ports", []),
    }
    instance["components"] = [
        existing for existing in instance.get("components", []) if existing.get("name") != name
    ]
    instance["components"].append(component_state)

    active = load_active_state(runtime_root)
    active["instances"] = [
        instance if existing.get("instanceId") == instance_id_value else existing
        for existing in active.get("instances", [])
    ]
    if not any(existing.get("instanceId") == instance_id_value for existing in active.get("instances", [])):
        active.setdefault("instances", []).append(instance)
    save_active_state(runtime_root, active)

    emit_event(
        state="running",
        message=health_message,
        workspace=workspace,
        instance_id=instance_id_value,
        component=name,
        pid=process.pid,
        stdout=str(stdout_path),
        stderr=str(stderr_path),
    )
    return 0


def process_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def terminate_process(pid: int, timeout: float = 5.0) -> None:
    handle = PROCESS_HANDLES.pop(pid, None)
    if handle is not None:
        try:
            handle.terminate()
            handle.wait(timeout=timeout)
            return
        except subprocess.TimeoutExpired:
            try:
                handle.kill()
                handle.wait(timeout=timeout)
                return
            except (OSError, subprocess.TimeoutExpired):
                pass

    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not process_is_running(pid):
            return
        time.sleep(0.1)

    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def stop_component(
    component_name: str,
    runtime_root: Path,
    workspace_name: str | None = None,
    remove_from_state: bool = True,
) -> int:
    runtime_root = runtime_root.resolve()
    active = load_active_state(runtime_root)
    instances = active.get("instances", [])

    for instance in reversed(instances):
        if workspace_name and instance.get("workspace") != workspace_name:
            continue
        for component in instance.get("components", []):
            if component.get("name") != component_name:
                continue
            pid = int(component["pid"])
            terminate_process(pid)
            emit_event(
                state="stopped",
                message="Component stopped",
                workspace=instance.get("workspace"),
                instance_id=instance.get("instanceId"),
                component=component_name,
                pid=pid,
            )
            if remove_from_state:
                instance["components"] = [item for item in instance.get("components", []) if item.get("name") != component_name]
                active["instances"] = [item for item in instances if item.get("components")]
                save_active_state(runtime_root, active)
            return 0

    emit_event(
        state="failed",
        message=f"Component not found in active state: {component_name}",
        workspace=workspace_name,
        component=component_name,
        errorCode="COMPONENT_NOT_FOUND",
    )
    return 1


def find_active_instance(runtime_root: Path, component_name: str, workspace_name: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    active = load_active_state(runtime_root)
    for instance in reversed(active.get("instances", [])):
        if workspace_name and instance.get("workspace") != workspace_name:
            continue
        for component in instance.get("components", []):
            if component.get("name") == component_name:
                return instance, component
    raise MicroVMError(f"component not found in active state: {component_name}")


def extract_component_package(package: Path, destination: Path) -> dict[str, Any]:
    manifest = validate_package(package)
    components = manifest.get("components") or []
    if len(components) != 1:
        raise MicroVMError("component replacement package must contain exactly one component")
    if destination.exists():
        shutil.rmtree(destination)
    safe_extract(package, destination)
    errors = validate_manifest(manifest, destination)
    if errors:
        raise MicroVMError("replacement package validation failed after extraction:\n- " + "\n- ".join(errors))
    return manifest


def swap_component(component_name: str, package: Path, runtime_root: Path, workspace_name: str | None = None) -> int:
    runtime_root = runtime_root.resolve()
    try:
        instance, active_component = find_active_instance(runtime_root, component_name, workspace_name)
        run_root = Path(instance["runRoot"])
        current_workdir = Path(active_component["workingDirectory"])

        with tempfile.TemporaryDirectory(prefix="microvm-swap-") as temp_dir:
            temp_path = Path(temp_dir)
            replacement_manifest = extract_component_package(package, temp_path)
            replacement = replacement_manifest["components"][0]
            if replacement["name"] != component_name:
                raise MicroVMError(
                    f"replacement component name mismatch: expected {component_name!r}, got {replacement['name']!r}"
                )

            stop_result = stop_component(
                component_name,
                runtime_root,
                workspace_name=instance.get("workspace"),
                remove_from_state=False,
            )
            if stop_result != 0:
                return stop_result

            rollback_root = runtime_root / "rollback" / instance["workspace"] / instance["instanceId"]
            rollback_root.mkdir(parents=True, exist_ok=True)
            rollback_target = rollback_root / f"{component_name}-{int(time.time())}"
            if current_workdir.exists():
                shutil.move(str(current_workdir), str(rollback_target))

            replacement_workdir = resolve_inside(temp_path, replacement["workingDirectory"], "replacement.workingDirectory")
            current_workdir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(replacement_workdir, current_workdir)

            workspace_manifest_path = run_root / MANIFEST_NAME
            workspace_manifest = read_json(workspace_manifest_path)
            workspace_manifest["components"] = [
                replacement if item.get("name") == component_name else item
                for item in workspace_manifest.get("components", [])
            ]
            write_json(workspace_manifest_path, workspace_manifest)

            emit_event(
                state="swapped",
                message="Component package installed",
                workspace=instance.get("workspace"),
                instance_id=instance.get("instanceId"),
                component=component_name,
                rollback=str(rollback_target),
            )

        return start_component_in_instance(instance, replacement, runtime_root)
    except MicroVMError as exc:
        emit_event(
            state="failed",
            message=str(exc),
            workspace=workspace_name,
            component=component_name,
            errorCode="SWAP_FAILED",
        )
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="microvm", description="MicroVM portable workspace runtime prototype")
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=default_runtime_root(),
        help="Runtime state/log/package directory. Defaults to LOCALAPPDATA/MicroVM.",
    )

    subcommands = parser.add_subparsers(dest="command_name", required=True)

    pack = subcommands.add_parser("pack", help="Validate and package a workspace")
    pack.add_argument("workspace", nargs="?", type=Path, default=Path.cwd())
    pack.add_argument("-o", "--output", type=Path)

    inspect = subcommands.add_parser("inspect", help="Inspect a workspace or MicroVM package")
    inspect.add_argument("target", type=Path)

    run = subcommands.add_parser("run", help="Run a workspace or MicroVM package")
    run.add_argument("target", type=Path)

    stop = subcommands.add_parser("stop", help="Stop a running component")
    stop.add_argument("component")
    stop.add_argument("--workspace")

    swap = subcommands.add_parser("swap", help="Swap a running component with a replacement package")
    swap.add_argument("component")
    swap.add_argument("package", type=Path)
    swap.add_argument("--workspace")

    bundle = subcommands.add_parser("bundle", help="List or extract bundled docs and examples")
    bundle_commands = bundle.add_subparsers(dest="bundle_command", required=True)

    bundle_commands.add_parser("list", help="List bundled resource paths as JSON")

    bundle_extract = bundle_commands.add_parser("extract", help="Extract bundled docs and examples")
    bundle_extract.add_argument("destination", type=Path)
    bundle_extract.add_argument("--overwrite", action="store_true", help="Replace existing bundled files")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command_name == "pack":
            create_package(args.workspace, args.output)
            return 0

        if args.command_name == "inspect":
            summary = inspect_target(args.target)
            print(json.dumps(summary, indent=2, sort_keys=True))
            return 0 if summary["valid"] else 1

        if args.command_name == "run":
            return start_workspace(args.target, args.runtime_root)

        if args.command_name == "stop":
            return stop_component(args.component, args.runtime_root, workspace_name=args.workspace)

        if args.command_name == "swap":
            return swap_component(args.component, args.package, args.runtime_root, workspace_name=args.workspace)

        if args.command_name == "bundle":
            if args.bundle_command == "list":
                print(json.dumps(list_bundle_resources(), indent=2))
                return 0

            if args.bundle_command == "extract":
                files = extract_bundle(args.destination, overwrite=args.overwrite)
                print(
                    json.dumps(
                        {
                            "destination": str(args.destination.resolve()),
                            "files": files,
                        },
                        indent=2,
                    )
                )
                return 0

        parser.error("unknown command")
        return 2
    except (MicroVMError, BundleError) as exc:
        emit_event(state="failed", message=str(exc), errorCode="MICROVM_ERROR")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
