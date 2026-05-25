import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "microvm_cli.py"

spec = importlib.util.spec_from_file_location("microvm_cli", MODULE_PATH)
microvm_cli = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(microvm_cli)


class MicroVMCLITest(unittest.TestCase):
    def test_inspect_example_workspaces(self):
        for name in ("python-workspace", "node-workspace", "mixed-workspace"):
            summary = microvm_cli.inspect_target(ROOT / "examples" / name)
            self.assertTrue(summary["valid"], summary["errors"])
            self.assertGreaterEqual(len(summary["components"]), 1)

    def test_pack_and_validate_python_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            workspace = temp_path / "python-workspace"
            shutil.copytree(ROOT / "examples" / "python-workspace", workspace)
            output = temp_path / "python-demo.microvm.zip"

            package = microvm_cli.create_package(workspace, output)
            manifest = microvm_cli.validate_package(package)

            self.assertEqual(manifest["name"], "python-demo")
            self.assertEqual(package, output)

    def test_unsafe_zip_path_fails_validation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            package = Path(temp_dir) / "unsafe.microvm.zip"
            manifest = {
                "schemaVersion": "0.1",
                "name": "unsafe-demo",
                "version": "0.1.0",
                "allowedRuntimes": ["python"],
                "permissions": {"network": {"outbound": False, "ports": []}, "filesystem": []},
                "components": [
                    {
                        "name": "api",
                        "runtime": "python",
                        "workingDirectory": "./components/api",
                        "command": ["python", "server.py"],
                        "stdio": True,
                    }
                ],
            }
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("microvm.workspace.json", json.dumps(manifest))
                archive.writestr("../evil.txt", "escape")
                archive.writestr("components/api/server.py", "print('ok')")

            with self.assertRaises(microvm_cli.MicroVMError):
                microvm_cli.validate_package(package)

    def test_run_and_stop_python_stdio_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            workspace = temp_path / "python-workspace"
            runtime_root = temp_path / "runtime"
            component_dir = workspace / "components" / "worker"
            component_dir.mkdir(parents=True)
            (component_dir / "worker.py").write_text("import time\ntime.sleep(60)\n", encoding="utf-8")

            manifest_path = workspace / "microvm.workspace.json"
            manifest = {
                "schemaVersion": "0.1",
                "name": "python-stdio-demo",
                "version": "0.1.0",
                "allowedRuntimes": ["python"],
                "permissions": {"network": {"outbound": False, "ports": []}, "filesystem": []},
                "components": [
                    {
                        "name": "worker",
                        "runtime": "python",
                        "workingDirectory": "./components/worker",
                        "command": [sys.executable, "worker.py"],
                        "stdio": True,
                    }
                ],
            }
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

            try:
                self.assertEqual(microvm_cli.start_workspace(workspace, runtime_root), 0)
                self.assertEqual(microvm_cli.stop_component("worker", runtime_root, workspace_name="python-stdio-demo"), 0)
            finally:
                active = microvm_cli.load_active_state(runtime_root)
                for instance in active.get("instances", []):
                    for component_state in instance.get("components", []):
                        microvm_cli.terminate_process(int(component_state["pid"]))

    def test_bundle_list_includes_public_resources(self):
        resources = microvm_cli.list_bundle_resources()

        self.assertIn("README.md", resources)
        self.assertIn("LICENSE.md", resources)
        self.assertIn("examples/pong-microvm/microvm.workspace.json", resources)
        self.assertIn("docs/MICROVM.txt", resources)
        self.assertFalse(any("__pycache__" in resource for resource in resources))
        self.assertFalse(any(resource.endswith(".pyc") for resource in resources))

    def test_bundle_extract_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "bundle"

            extracted = microvm_cli.extract_bundle(destination)
            self.assertIn("examples/pong-microvm/microvm.workspace.json", extracted)
            readme = destination / "README.md"
            self.assertTrue(readme.is_file())

            readme.write_text("local edit\n", encoding="utf-8")
            with self.assertRaises(microvm_cli.BundleError):
                microvm_cli.extract_bundle(destination)
            self.assertEqual(readme.read_text(encoding="utf-8"), "local edit\n")

            overwritten = microvm_cli.extract_bundle(destination, overwrite=True)
            self.assertIn("README.md", overwritten)
            self.assertNotEqual(readme.read_text(encoding="utf-8"), "local edit\n")


if __name__ == "__main__":
    unittest.main()
