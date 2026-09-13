#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from local_model_manager import LocalModelControlError, LocalModelManager


class FakeProcess:
    def __init__(self, pid=4242):
        self.pid = pid
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        del timeout
        self.returncode = 0
        return 0

    def terminate(self):
        self.terminated = True
        self.returncode = 0


class FakeResult:
    returncode = 0
    stdout = ""
    stderr = ""


class LocalModelManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.launcher = self.root / "start model.bat"
        self.launcher.write_text("@exit /b 0\n", encoding="utf-8")
        self.processes = []
        self.popen_calls = []
        self.run_calls = []

        def fake_popen(command, **kwargs):
            self.popen_calls.append((command, kwargs))
            process = FakeProcess()
            self.processes.append(process)
            return process

        def fake_run(command, **kwargs):
            self.run_calls.append((command, kwargs))
            if self.processes:
                self.processes[-1].returncode = 0
            return FakeResult()

        self.manager = LocalModelManager(
            log_path=self.root / "logs" / "local-model.log",
            popen_factory=fake_popen,
            run_factory=fake_run,
        )
        self.config = {"llm_launcher": str(self.launcher)}

    def tearDown(self):
        self.manager.shutdown()
        self.temp.cleanup()

    def test_status_and_configuration_never_start_a_process(self):
        status = self.manager.status(self.config, reachable=False)
        self.assertEqual("off", status["state"])
        self.assertTrue(status["can_start"])
        self.assertFalse(status["automatic_start"])
        self.assertEqual([], self.popen_calls)

    def test_start_requires_literal_one_shot_confirmation(self):
        for value in (False, None, "true", 1):
            with self.subTest(value=value), self.assertRaisesRegex(
                LocalModelControlError, "저장된 설정은 실행 동의가 아닙니다"
            ):
                self.manager.start(self.config, confirmed=value, reachable=False)
        self.assertEqual([], self.popen_calls)

    def test_explicit_start_owns_one_launcher_and_reports_readiness(self):
        status = self.manager.start(self.config, confirmed=True, reachable=False)
        self.assertEqual("starting", status["state"])
        self.assertTrue(status["owned"])
        self.assertEqual(1, len(self.popen_calls))
        command, kwargs = self.popen_calls[0]
        self.assertEqual("call", command[-2])
        self.assertEqual(str(self.launcher), command[-1])
        self.assertFalse(kwargs["shell"])
        self.assertEqual(str(self.launcher.parent), kwargs["cwd"])
        ready = self.manager.status(self.config, reachable=True)
        self.assertEqual("owned", ready["state"])
        self.assertTrue(ready["can_stop"])

    def test_external_server_is_never_owned_or_stopped(self):
        status = self.manager.start(self.config, confirmed=True, reachable=True)
        self.assertEqual("external", status["state"])
        self.assertFalse(status["owned"])
        self.assertEqual([], self.popen_calls)
        with self.assertRaisesRegex(LocalModelControlError, "앱 밖에서 실행"):
            self.manager.stop(self.config, confirmed=True, reachable=True)
        self.assertEqual([], self.run_calls)

    def test_stop_targets_only_the_owned_pid_tree(self):
        self.manager.start(self.config, confirmed=True, reachable=False)
        status = self.manager.stop(self.config, confirmed=True, reachable=True)
        self.assertEqual("off", status["state"])
        self.assertFalse(status["owned"])
        if os.name == "nt":
            self.assertEqual(1, len(self.run_calls))
            command, kwargs = self.run_calls[0]
            self.assertEqual(["taskkill", "/PID", "4242", "/T", "/F"], command)
            self.assertFalse(kwargs["check"])
        else:
            self.assertTrue(self.processes[0].terminated)

    def test_unsupported_launcher_type_is_rejected_before_spawn(self):
        unsupported = self.root / "launcher.txt"
        unsupported.write_text("not executable", encoding="utf-8")
        with self.assertRaisesRegex(LocalModelControlError, r"\.bat"):
            self.manager.start(
                {"llm_launcher": str(unsupported)}, confirmed=True, reachable=False
            )
        self.assertEqual([], self.popen_calls)


if __name__ == "__main__":
    unittest.main()
