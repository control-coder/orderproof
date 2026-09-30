"""控制层故障注入，仅验证处理分支，不替代真实容器安全验证。"""
import base64
import io
import json
from unittest.mock import Mock, patch
from uuid import uuid4

from test_execution import RunnerFixture
from datalab.execution.runner import ExecutionError, OutputSpec


class LifecycleTests(RunnerFixture):
    def fake_process(self, payload, *, running=False):
        process = Mock()
        process.stdout = io.BytesIO(payload)
        process.poll.return_value = None if running else 0
        process.returncode = None if running else 0
        return process

    def invoke(self):
        return self.runner.execute_python(str(uuid4()), self.version.dataset_version, "", 0.05,
            OutputSpec(files=("result.json",)), project_id="demo", metric_version="trusted-v1")

    def control(self, *args):
        if args[0] == "image":
            return "sha256:" + "a" * 64
        if args[0] == "inspect":
            return json.dumps({"ExitCode": 0, "OOMKilled": False, "Running": False})
        return ""

    def test_completed_lifecycle_cleans_container(self):
        wire = json.dumps({"outputs": {"result.json": base64.b64encode(b'{}').decode()}}).encode()
        with patch.object(self.runner, "_control", side_effect=self.control) as control, \
             patch("datalab.execution.runner.subprocess.Popen", return_value=self.fake_process(wire)):
            manifest = self.invoke()
            self.assertEqual(manifest.status, "COMPLETED")
            self.assertEqual(control.call_args.args[:2], ("rm", "--force"))
            self.assertEqual((self.runner.root / manifest.run_id / "output/result.json").read_bytes(), b"{}")

    def test_timeout_always_cleans_and_writes_manifest(self):
        with patch.object(self.runner, "_control", side_effect=self.control) as control, \
             patch("datalab.execution.runner.subprocess.Popen", return_value=self.fake_process(b"", running=True)):
            manifest = self.invoke()
            self.assertEqual(manifest.status, "TIMED_OUT")
            self.assertEqual(control.call_args.args[:2], ("rm", "--force"))
            self.assertTrue((self.runner.root / manifest.run_id / "manifest.json").exists())

    def test_cleanup_failure_cannot_report_complete(self):
        def control(*args):
            if args[0] == "rm":
                raise ExecutionError("模拟清理失败")
            return self.control(*args)
        wire = b'{"outputs":{"result.json":"e30="}}'
        with patch.object(self.runner, "_control", side_effect=control), \
             patch("datalab.execution.runner.subprocess.Popen", return_value=self.fake_process(wire)):
            self.assertEqual(self.invoke().status, "CLEANUP_PENDING")

    def test_invalid_output_is_not_written(self):
        with patch.object(self.runner, "_control", side_effect=self.control), \
             patch("datalab.execution.runner.subprocess.Popen", return_value=self.fake_process(b"not json")):
            manifest = self.invoke()
            self.assertEqual(manifest.status, "FAILED")
            self.assertFalse((self.runner.root / manifest.run_id / "output").exists())
