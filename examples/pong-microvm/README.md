# Pong MicroVM Demo

This workspace demonstrates MicroVM as a native-style service orchestrator.

Services:

- `web`: full-screen HTML5 canvas Pong UI.
- `matchmaking`: LAN scan, invite/accept, MMUKO status, and game events.
- `polycall`: native PolyCall health and stream adapter.
- `voice`: voice-room negotiation events without live microphone audio.
- `u-agent`: adaptive AI practice opponent named `u`.

Install MicroVM from the repo root first:

```powershell
python -m pip install -e C:\Users\Public\Public\microvm
```

Run the workspace:

```powershell
microvm --runtime-root C:\Users\Public\Public\microvm\.runtime run C:\Users\Public\Public\microvm\examples\pong-microvm
```

Open:

```text
http://127.0.0.1:8890
```

Stop the services:

```powershell
microvm --runtime-root C:\Users\Public\Public\microvm\.runtime stop web --workspace pong-microvm
microvm --runtime-root C:\Users\Public\Public\microvm\.runtime stop matchmaking --workspace pong-microvm
microvm --runtime-root C:\Users\Public\Public\microvm\.runtime stop polycall --workspace pong-microvm
microvm --runtime-root C:\Users\Public\Public\microvm\.runtime stop voice --workspace pong-microvm
microvm --runtime-root C:\Users\Public\Public\microvm\.runtime stop u-agent --workspace pong-microvm
```
