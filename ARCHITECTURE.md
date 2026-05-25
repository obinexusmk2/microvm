# MicroVM Architecture Specification

## Purpose

MicroVM is a portable, platform-agnostic isolated workspace runtime for running native services from packaged project workspaces. Its core problem statement is:

> How can a native service run in an environment that does not directly support that service, while keeping the service packaged, controlled, and replaceable?

In this specification, "MicroVM" is the project name and architecture concept. It is not a Firecracker-style hardware virtual machine, not a kernel-level hypervisor, and not a replacement for containers. The first version is a Windows-first local runtime that packages a workspace, validates it, extracts it into a controlled runtime area, and starts Python or Node components as isolated child processes.

The design preserves the original goals from the notes:

- Plug-and-play service packages.
- Hot-swappable components.
- Native runtime support for Python and Node first.
- A workspace archive as the portable unit.
- Component isolation through process boundaries and explicit host access.
- Future browser terminal and dashboard control through structured events.
- Future support for Lua, TypeScript, WASM, shared libraries, distributed service mesh patterns, and remote execution.

## Terminology

| Term | Meaning |
| --- | --- |
| MicroVM | The portable workspace runtime described by this document. |
| Workspace | The root project folder and package unit. A workspace can be packed into an archive. |
| Workspace archive | A zip package containing the workspace source, manifest, components, and local assets. |
| Manifest | The `microvm.workspace.json` file that describes runtimes, components, permissions, and lifecycle behavior. |
| Component | A runnable service inside the workspace. A component has a runtime, command, working directory, ports or stdio mode, health check, and environment. |
| Orchestrator | The local controller that validates, extracts, starts, stops, monitors, and swaps components. |
| Runtime | A host executable used to start a component, such as Python or Node. |
| Isolation | A set of controls that limit what a component can see or do. In v1 this means process boundaries, scoped directories, controlled environment variables, safe archive extraction, and explicit permission checks. |
| Hot swap | Replacing a stopped or running component package with a validated replacement, then restarting only the affected component. |
| Event stream | JSON Lines output from the orchestrator describing lifecycle events, logs, errors, and health status. |

## System Model

MicroVM uses a hierarchical workspace model:

```text
workspace-root/
  microvm.workspace.json
  components/
    api/
      app.py
      requirements.txt
    worker/
      package.json
      index.js
  data/
  README.md
```

The workspace is the portable unit. Components inside it are native services. The orchestrator treats the workspace as a controlled package, not as arbitrary host filesystem state.

The v1 runtime has four layers:

1. Package layer: creates and reads workspace archives.
2. Validation layer: checks manifest shape, paths, commands, runtimes, and permissions.
3. Isolation layer: extracts into a controlled runtime directory and starts each component with scoped process settings.
4. Control layer: exposes CLI commands and JSONL events for future browser or dashboard integration.

The orchestrator must never assume that zip packaging alone provides strong isolation. Packaging gives portability and a clean transfer boundary. Isolation comes from validation, controlled extraction, process supervision, environment constraints, and explicit access rules.

## Workspace And Package Lifecycle

### Lifecycle States

| State | Meaning |
| --- | --- |
| `packed` | A workspace has been archived into a MicroVM package. |
| `validated` | The archive and manifest passed validation. |
| `extracted` | The archive was safely extracted into a runtime directory. |
| `starting` | One or more components are being launched. |
| `running` | A component process is alive and passed its health check or readiness rule. |
| `failed` | A validation, launch, health, runtime, or shutdown step failed. |
| `stopped` | A component or workspace has been stopped cleanly. |
| `swapped` | A component was replaced with a validated package and restarted or staged for restart. |

### Package Flow

1. A developer creates a workspace folder with `microvm.workspace.json`.
2. `microvm pack` validates the workspace and creates a zip archive.
3. `microvm inspect` reads an archive and reports components, runtimes, permissions, and risks.
4. `microvm run <workspace>` accepts either a workspace folder or a package archive.
5. The orchestrator validates the manifest before extraction or process launch.
6. The archive is extracted into a controlled runtime directory, not into the original source folder.
7. Components are started according to manifest order and dependency constraints.
8. Logs, lifecycle changes, and health status are emitted as JSON Lines.
9. `microvm stop <component>` stops a component by name.
10. `microvm swap <component> <package>` validates a replacement package, stops the old component, replaces the component directory, and starts the new component if policy allows.

### Runtime Directories

The Windows-first implementation should use a predictable runtime root such as:

```text
%LOCALAPPDATA%/MicroVM/runtime/<workspace-name>/<instance-id>/
```

Each component runs with its working directory inside the extracted workspace:

```text
%LOCALAPPDATA%/MicroVM/runtime/demo/20260524-001/components/api/
```

The orchestrator may keep source archives and extracted runtime directories separate:

```text
%LOCALAPPDATA%/MicroVM/packages/
%LOCALAPPDATA%/MicroVM/runtime/
%LOCALAPPDATA%/MicroVM/logs/
```

## Component And Service Model

A component is the smallest runnable unit in v1. It represents one native service, such as a Python web API or a Node worker.

Each component has:

- A stable component name.
- A runtime: `python` or `node` in v1.
- A command expressed as an argument array, not a shell string.
- A working directory scoped inside the workspace.
- Optional environment variables.
- A communication mode: ports or stdio.
- Optional health checks.
- Optional dependency ordering.
- Permission requirements.

Components must be started as child processes of the orchestrator. The orchestrator records the process ID, watches exit status, streams logs, and stops the process when requested.

### Component Dependencies

Components may depend on other components by name. The orchestrator starts dependencies first and stops dependents first.

Example:

```json
{
  "name": "worker",
  "runtime": "node",
  "dependsOn": ["api"]
}
```

Dependencies are startup ordering hints, not service discovery. v1 should not invent a distributed service mesh. It should expose enough metadata for a later service discovery layer.

### Hot Swap Behavior

Hot swap replaces one component with a validated component package.

Required v1 behavior:

1. Validate the replacement package before stopping the existing process.
2. Confirm the replacement component has the same component name unless an explicit rename option is added in a later version.
3. Stop the existing component cleanly.
4. Move the old component directory to a rollback location.
5. Install the replacement component directory.
6. Start the replacement component.
7. If startup fails, emit `failed` and leave rollback metadata available.

Automatic rollback may be implemented later. For v1, rollback metadata is enough.

## Isolation Model

MicroVM v1 provides practical local process isolation, not hard VM isolation.

### Guarantees

The orchestrator must:

- Extract archives only into a runtime directory it controls.
- Reject archive entries that escape the extraction root.
- Reject absolute paths, parent traversal, and unsafe symlinks where the platform exposes them.
- Start commands without invoking a shell by default.
- Restrict component working directories to paths inside the extracted workspace.
- Pass only allowed environment variables to components.
- Track process IDs and child process state.
- Stop processes on workspace shutdown.
- Require explicit permissions for host filesystem access and network ports.

### Non-Guarantees

MicroVM v1 does not guarantee:

- Kernel-level isolation.
- Protection against malicious native code with the same user privileges.
- Full network sandboxing.
- Full filesystem sandboxing outside the process working directory.
- Memory or CPU quotas.
- Cross-platform identical behavior.
- Strong tenant isolation suitable for untrusted code from strangers.

For untrusted remote workloads, the architecture should later add a real sandbox layer such as a container, job object policy, restricted user account, WASM runtime, or true microVM backend.

### Windows-First Controls

The first implementation should use Windows-friendly controls:

- Child processes started with explicit command arrays.
- Per-component working directories.
- Clean environment blocks.
- Runtime directories under `%LOCALAPPDATA%/MicroVM`.
- Best-effort process tree termination.
- Port conflict detection before launch.
- Optional Windows Job Objects in a later version for stronger cleanup and limits.

## Orchestration Flow

### Run Flow

```text
microvm run demo.microvm.zip
  -> read archive metadata
  -> validate manifest
  -> create instance id
  -> safely extract archive
  -> resolve runtimes
  -> validate component commands and working directories
  -> check ports
  -> start components in dependency order
  -> stream JSONL events
  -> monitor process exits and health checks
```

### Stop Flow

```text
microvm stop api
  -> find running instance
  -> find component by name
  -> send graceful stop signal
  -> wait for configured timeout
  -> terminate process tree if needed
  -> emit stopped or failed event
```

### Swap Flow

```text
microvm swap api api-v2.microvm-component.zip
  -> validate replacement package
  -> verify component name and runtime compatibility
  -> stop current api component
  -> stage old component for rollback
  -> install replacement component
  -> start replacement component
  -> emit swapped and running, or failed
```

## Public Interfaces

### Manifest: `microvm.workspace.json`

The manifest is required at the workspace root.

Minimal example:

```json
{
  "schemaVersion": "0.1",
  "name": "demo-workspace",
  "version": "0.1.0",
  "allowedRuntimes": ["python", "node"],
  "permissions": {
    "filesystem": [
      {
        "path": "./data",
        "access": "read-write"
      }
    ],
    "network": {
      "outbound": false,
      "ports": [8000, 3000]
    }
  },
  "components": [
    {
      "name": "api",
      "runtime": "python",
      "workingDirectory": "./components/api",
      "command": ["python", "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8000"],
      "ports": [8000],
      "healthCheck": {
        "type": "http",
        "url": "http://127.0.0.1:8000/health",
        "intervalSeconds": 5,
        "timeoutSeconds": 2
      },
      "environment": {
        "PYTHONUNBUFFERED": "1"
      }
    },
    {
      "name": "worker",
      "runtime": "node",
      "workingDirectory": "./components/worker",
      "command": ["node", "index.js"],
      "stdio": true,
      "dependsOn": ["api"],
      "environment": {
        "NODE_ENV": "production"
      }
    }
  ]
}
```

### Manifest Rules

The orchestrator must enforce these rules:

- `schemaVersion`, `name`, `version`, and `components` are required.
- Workspace and component names must be stable identifiers using letters, numbers, dots, underscores, and hyphens.
- `allowedRuntimes` must include every component runtime.
- `runtime` must be `python` or `node` in v1.
- `command` must be an array of strings.
- `command[0]` must match the selected runtime or an allowed runtime alias.
- `workingDirectory` must resolve inside the workspace.
- Component names must be unique.
- Ports must be declared before launch.
- Environment values must be strings.
- Host filesystem paths outside the workspace are denied unless explicitly listed in `permissions.filesystem`.

### CLI

#### `microvm pack`

Validates the current workspace and writes a workspace archive.

Expected behavior:

- Reads `microvm.workspace.json`.
- Validates manifest and component paths.
- Rejects unsafe paths.
- Writes `<workspace-name>-<version>.microvm.zip`.
- Emits a `packed` event.

#### `microvm inspect <workspace-or-package>`

Prints manifest summary, components, runtimes, ports, permissions, and validation errors.

Expected behavior:

- Does not start any process.
- Does not extract into the runtime directory unless a temporary safe inspection directory is needed.
- Returns non-zero on invalid packages.

#### `microvm run <workspace-or-package>`

Runs a workspace folder or archive.

Expected behavior:

- Validates before running.
- Extracts archives into a controlled runtime directory.
- Starts components in dependency order.
- Emits lifecycle events as JSON Lines.
- Returns non-zero if required components fail to start.

#### `microvm stop <component>`

Stops a running component in the active workspace instance.

Expected behavior:

- Uses the orchestrator state file or process registry to locate the component.
- Attempts graceful shutdown before forced termination.
- Emits `stopped` on success.
- Emits `failed` if the component cannot be found or stopped.

#### `microvm swap <component> <package>`

Replaces one component with a validated package.

Expected behavior:

- Validates the replacement before stopping the current component.
- Rejects component name mismatches.
- Stages rollback metadata.
- Emits `swapped` after replacement.
- Emits `running` after successful restart.
- Emits `failed` if validation, replacement, or restart fails.

### JSONL Event Format

All commands that perform lifecycle work should support JSON Lines output.

Example event:

```json
{"timestamp":"2026-05-24T21:59:14Z","workspace":"demo-workspace","instanceId":"20260524-001","component":"api","state":"starting","message":"Starting Python component"}
```

Required event fields:

| Field | Meaning |
| --- | --- |
| `timestamp` | ISO 8601 UTC timestamp. |
| `workspace` | Workspace name. |
| `instanceId` | Running workspace instance identifier. |
| `component` | Component name, or `null` for workspace-level events. |
| `state` | One of the lifecycle states. |
| `message` | Human-readable summary. |

Optional fields:

- `pid`
- `runtime`
- `command`
- `port`
- `exitCode`
- `errorCode`
- `details`

The event stream is the future integration point for a browser terminal, dashboard, editor feedback loop, or remote controller.

## Security Limits And Review Checklist

MicroVM v1 is intended for trusted local development and controlled internal services. It should not run arbitrary untrusted code without an additional sandbox.

Security review checks:

- Zip extraction rejects `../`, absolute paths, drive-letter paths, UNC paths, and unsafe symlinks.
- Commands are arrays and do not pass through `cmd.exe`, PowerShell, Bash, or another shell unless explicitly enabled later.
- Runtime executable resolution is deterministic and logged.
- Ports are checked before component launch.
- Components cannot declare undeclared ports at the manifest level.
- Environment variables are allowlisted or generated by the orchestrator.
- Host filesystem access is denied by default.
- Component working directories stay inside the extracted workspace.
- Logs do not print secrets from environment variables.
- Stop and cleanup handle orphaned child processes.
- Swap validates replacement packages before stopping the current component.

If MicroVM later supports remote browser terminal control, authentication and authorization must be designed before enabling it. A browser terminal must never expose an unauthenticated local shell.

## V1 Roadmap

### Milestone 1: Specification And Examples

- Finalize this architecture spec.
- Add one example workspace with a Python component.
- Add one example workspace with a Node component.
- Add one mixed Python plus Node example.

### Milestone 2: Local CLI Prototype

- Implement `microvm pack`.
- Implement `microvm inspect`.
- Implement `microvm run`.
- Implement `microvm stop`.
- Emit JSONL events.

### Milestone 3: Component Swap

- Define component package format.
- Implement `microvm swap`.
- Preserve rollback metadata.
- Add startup failure handling.

### Milestone 4: Browser Control Surface

- Build a read-only dashboard over the JSONL event stream.
- Add a controlled terminal view only after authentication and command policy are defined.
- Surface component logs, health, process IDs, and lifecycle states.

### Milestone 5: Stronger Isolation Backends

- Add optional Windows Job Object controls.
- Evaluate restricted users, containers, WASM, or true microVM backends for untrusted workloads.
- Add resource limits for CPU, memory, disk, and runtime duration.

## Test Plan

### Documentation Acceptance

- The spec states that MicroVM is a portable runtime concept, not a Firecracker-style hardware VM.
- The spec explains that archive packaging gives portability, not strong isolation by itself.
- The spec defines workspace, component, orchestrator, manifest, lifecycle states, CLI commands, and JSONL events.
- The spec keeps Python and Node as v1 runtimes and treats Lua, TypeScript, shared libraries, WASM, browser terminal control, and service mesh behavior as future extensions.

### Manifest Validation

- Valid workspace with one Python component passes.
- Valid workspace with one Node component passes.
- Mixed Python and Node workspace passes when both runtimes are allowed.
- Missing `microvm.workspace.json` fails.
- Duplicate component names fail.
- Runtime not listed in `allowedRuntimes` fails.
- Unsupported v1 runtime fails.
- Shell-string command fails.
- Working directory outside the workspace fails.
- Undeclared port fails.

### Package Validation

- Normal workspace archive passes.
- Archive with parent traversal path fails.
- Archive with absolute path fails.
- Archive with Windows drive-letter path fails.
- Archive with manifest outside the root fails.
- Archive with component path missing fails.

### Runtime Behavior

- Python component starts and emits `starting` then `running`.
- Node component starts and emits `starting` then `running`.
- Component process exit emits `failed` when unexpected.
- `microvm stop <component>` emits `stopped` on clean shutdown.
- Port conflict fails before launch.
- Health check timeout emits `failed`.

### Swap Behavior

- Replacement package is validated before stopping the current component.
- Component name mismatch fails.
- Successful replacement emits `swapped` then `running`.
- Failed replacement leaves rollback metadata.

## Open Design Questions For Later Versions

- Whether MicroVM should support true VM or container backends while keeping the same manifest.
- Whether TypeScript is a runtime mode or a Node build step.
- Whether shared libraries and DLLs should be loaded directly or only through component processes.
- Whether distributed peer-to-peer control belongs in the core orchestrator or a separate service mesh layer.
- Whether browser terminal access is local-only or remote-capable.

## Source Notes

This document is derived from the MicroVM transcript and sketch images in the local `microvm` folder, plus the cleaner transcript at `C:\Users\Nnamdi\Downloads\MICROVM.txt`. The original notes describe a platform-agnostic, plug-and-play workspace system for running native service artifacts from packaged project roots. This specification keeps that intent while making the v1 behavior concrete enough to implement safely.
