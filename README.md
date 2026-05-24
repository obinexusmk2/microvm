# microvm

A lightweight microservice VM framework for distributed applications.
https://drive.google.com/drive/folders/10xjI_X6oEQlOt9C6exkAL0xe74SatQnK?usp=drive_link 

## Installation

Install the package for development:

```powershell
python -m pip install -e /workspace/microvm
microvm --help
```

## Quick Start

### Inspect a Workspace

View the structure and components of a workspace:

```powershell
microvm inspect /workspace/examples/pong-microvm
```

### Run a Workspace

Execute all MicroVM components in a workspace:

```powershell
microvm --runtime-root /workspace/.runtime run /workspace/examples/pong-microvm
```

## Example: Pong MicroVM Demo

The `examples/pong-microvm` workspace demonstrates five MicroVM components:

- `web`: full-screen HTML5 canvas Pong.
- `matchmaking`: LAN scan, invite/accept, game events, and MMUKO state.
- `polycall`: native PolyCall detection plus adapter fallback stream.
- `voice`: voice-channel negotiation events.
- `u-agent`: adaptive practice opponent named `u`.

```powershell
microvm inspect C:\Users\Public\Public\microvm\examples\pong-microvm
microvm --runtime-root C:\Users\Public\Public\microvm\.runtime run C:\Users\Public\Public\microvm\examples\pong-microvm
```

Then open:

```text
http://127.0.0.1:8890
```

## Runtime State
```powershell
python -m unittest C:\Users\Public\Public\microvm\tests\test_microvm_cli.py
python -m unittest discover C:\Users\Public\Public\microvm\tests
