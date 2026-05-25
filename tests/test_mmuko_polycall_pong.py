import importlib.util
import json
import platform
import shutil
import socket
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from microvm.mmuko import ByteState, CalibrationTuple, summarize_stream
from microvm.polycall_adapter import detect_polycall, health, stream_events
from microvm import cli as microvm_cli


ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def import_file(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class MMUKOPolyCallPongTest(unittest.TestCase):
    def test_mmuko_classifies_core_states(self):
        calibrator = CalibrationTuple()
        self.assertEqual(calibrator.classify(bytes(16)), ByteState.NOSIGNAL)
        self.assertEqual(calibrator.classify(bytes([0xAA, 0x55]) + b"A" * 14), ByteState.SIGNAL)
        self.assertEqual(calibrator.classify(bytes(range(16))), ByteState.NONOISE)
        noise = bytes(range(128))
        self.assertEqual(calibrator.classify(noise), ByteState.NOISE)

        summary = summarize_stream(bytes([0xAA, 0x55]) + b"OBINexus")
        self.assertIn(summary["dominant"], {"SIGNAL", "NONOISE", "NOISE", "NOSIGNAL"})

    def test_polycall_detects_elf_fallback_on_windows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bin_dir = root / "examples" / "polycalld" / "bin"
            lib_dir = root / "examples" / "polycalld" / "lib"
            bin_dir.mkdir(parents=True)
            lib_dir.mkdir(parents=True)
            polycall = bin_dir / "polycall"
            polycall.write_bytes(b"\x7fELF" + b"\0" * 32)
            (lib_dir / "libpolycall.so").write_bytes(b"\x7fELF" + b"\0" * 32)

            runtime = detect_polycall(root)
            if platform.system().lower() == "windows":
                self.assertTrue(runtime.adapter_fallback)
                self.assertEqual(runtime.reason, "elf-binary-on-windows")
            else:
                self.assertFalse(runtime.adapter_fallback)

            payload = health(root)
            self.assertTrue(payload["ok"])
            self.assertIn("adapterFallback", payload)
            self.assertGreaterEqual(len(list(stream_events(root, count=2, delay=0))), 3)

    def test_ipconfig_parser_and_u_agent(self):
        common_path = ROOT / "examples" / "pong-microvm" / "components" / "common"
        sys.path.insert(0, str(common_path))
        common = import_file("pong_common_test", common_path / "pong_common.py")
        parsed = common.parse_ipconfig(
            """
            Wireless LAN adapter Wi-Fi:
               IPv4 Address. . . . . . . . . . . : 192.168.1.44
               Autoconfiguration IPv4 Address. . : 169.254.10.12
            Ethernet adapter:
               IPv4 Address. . . . . . . . . . . : 10.0.0.7
            """
        )
        self.assertEqual(parsed, ["192.168.1.44", "10.0.0.7"])

        u_agent = import_file("u_agent_test", ROOT / "examples" / "pong-microvm" / "components" / "u-agent" / "server.py")
        with tempfile.TemporaryDirectory() as temp_dir:
            u_agent.STATS_PATH = Path(temp_dir) / "u_stats.json"
            result = u_agent.predict(
                {
                    "agent": "player 2u",
                    "ball": {"y": 240, "vy": 6},
                    "canvas": {"height": 720},
                    "paddleHeight": 110,
                }
            )
            self.assertTrue(result["ok"])
            self.assertGreaterEqual(result["targetY"], 55)
            self.assertTrue(u_agent.STATS_PATH.exists())

    def test_inspect_pong_workspace(self):
        summary = microvm_cli.inspect_target(ROOT / "examples" / "pong-microvm")
        self.assertTrue(summary["valid"], summary["errors"])
        self.assertEqual({component["name"] for component in summary["components"]}, {"web", "matchmaking", "polycall", "voice", "u-agent"})

    def test_start_pong_workspace_services(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            workspace = temp_path / "pong-microvm"
            runtime_root = temp_path / "runtime"
            shutil.copytree(ROOT / "examples" / "pong-microvm", workspace)

            ports = {
                "web": free_port(),
                "matchmaking": free_port(),
                "polycall": free_port(),
                "voice": free_port(),
                "u-agent": free_port(),
            }
            manifest_path = workspace / "microvm.workspace.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["permissions"]["network"]["ports"] = list(ports.values())
            for component in manifest["components"]:
                name = component["name"]
                component["ports"] = [ports[name]]
                component["healthCheck"]["url"] = f"http://127.0.0.1:{ports[name]}/health"
                env = component.setdefault("environment", {})
                env["PYTHONPATH"] = str(ROOT)
                if name == "web":
                    env["PONG_WEB_PORT"] = str(ports["web"])
                    env["PONG_MATCHMAKING_URL"] = f"http://127.0.0.1:{ports['matchmaking']}"
                    env["PONG_POLYCALL_URL"] = f"http://127.0.0.1:{ports['polycall']}"
                    env["PONG_VOICE_URL"] = f"http://127.0.0.1:{ports['voice']}"
                    env["PONG_U_AGENT_URL"] = f"http://127.0.0.1:{ports['u-agent']}"
                if name == "matchmaking":
                    env["PONG_MATCHMAKING_PORT"] = str(ports["matchmaking"])
                if name == "polycall":
                    env["PONG_POLYCALL_PORT"] = str(ports["polycall"])
                    env["PONG_REPO_ROOT"] = str(ROOT)
                if name == "voice":
                    env["PONG_VOICE_PORT"] = str(ports["voice"])
                if name == "u-agent":
                    env["PONG_U_AGENT_PORT"] = str(ports["u-agent"])
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

            try:
                self.assertEqual(microvm_cli.start_workspace(workspace, runtime_root), 0)
                for name, port in ports.items():
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                    self.assertTrue(payload["ok"], name)

                scan_request = urllib.request.Request(
                    f"http://127.0.0.1:{ports['web']}/api/scan",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(scan_request, timeout=3) as response:
                    scan = json.loads(response.read().decode("utf-8"))
                self.assertTrue(scan["ok"])
                self.assertGreaterEqual(len(scan["players"]), 1)

                with urllib.request.urlopen(f"http://127.0.0.1:{ports['web']}/api/polycall/stream", timeout=5) as response:
                    first_line = response.readline().decode("utf-8")
                self.assertTrue(first_line.startswith("data: "))
            finally:
                for component in ("web", "u-agent", "voice", "polycall", "matchmaking"):
                    microvm_cli.stop_component(component, runtime_root, workspace_name="pong-microvm")


if __name__ == "__main__":
    unittest.main()
