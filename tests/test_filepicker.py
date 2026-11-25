"""Native picker adaptation is tested at its real process boundary, without UI."""
from contextlib import redirect_stderr, redirect_stdout
import base64
from io import StringIO
import json
import subprocess
import unittest
from unittest.mock import patch

from good_student import filepicker


class NativeFilePickerTest(unittest.TestCase):
    def windows_result(self, kind, *, stdout="[]", stderr="", returncode=0):
        completed = subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)
        with patch.object(filepicker, "WINDOWS", True), \
             patch.object(filepicker.shutil, "which", return_value="C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"), \
             patch.object(filepicker.subprocess, "run", return_value=completed) as run, \
             patch.object(filepicker, "_tk_choose", side_effect=AssertionError("Windows must not require Tcl")):
            paths = filepicker.choose(kind)
            return paths, run.call_args

    def test_windows_paths_are_returned_by_hidden_sta_helper_without_tk(self):
        expected = ["D:/课程/第一章.pptx", "D:/课程/课堂录音.m4a"]
        actual, call = self.windows_result("files", stdout=json.dumps(expected, ensure_ascii=False))
        self.assertEqual(actual, expected)
        command = call.args[0]
        self.assertIn("-STA", command)
        self.assertEqual(command[command.index("-WindowStyle") + 1], "Hidden")
        self.assertIn("-EncodedCommand", command)
        script = base64.b64decode(command[-1]).decode("utf-16-le")
        self.assertIn("System.Windows.Forms.OpenFileDialog", script)
        self.assertIn("ShowDialog", script)
        self.assertEqual(call.kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def test_directory_uses_system_folder_dialog_and_cancel_is_empty(self):
        selected, call = self.windows_result("directory", stdout='["D:/课程"]')
        self.assertEqual(selected, ["D:/课程"])
        script = base64.b64decode(call.args[0][-1]).decode("utf-16-le")
        self.assertIn("System.Windows.Forms.FolderBrowserDialog", script)
        cancelled, _ = self.windows_result("audio")
        self.assertEqual(cancelled, [])

    def test_helper_failure_preserves_actual_backend_error(self):
        failure = json.dumps({"error": "WinForms initialization failed", "detail": "System.InvalidOperationException: actual cause"})
        with self.assertRaises(filepicker.PickerError) as caught:
            self.windows_result("files", returncode=1, stderr=failure)
        self.assertEqual(caught.exception.details["backend"], "windows-forms")
        self.assertEqual(str(caught.exception), "WinForms initialization failed")
        self.assertIn("actual cause", caught.exception.details["detail"])

    def test_invalid_helper_output_is_not_silently_treated_as_cancel(self):
        with self.assertRaises(filepicker.PickerError):
            self.windows_result("files", stdout="not JSON")
        with self.assertRaises(filepicker.PickerError):
            self.windows_result("files", stdout='"D:/single-path"')

    def test_process_launch_error_explains_backend_instead_of_guessing_permissions(self):
        with patch.object(filepicker, "WINDOWS", True), \
             patch.object(filepicker.shutil, "which", return_value="powershell.exe"), \
             patch.object(filepicker.subprocess, "run", side_effect=OSError("system helper unavailable")):
            with self.assertRaises(filepicker.PickerError) as caught:
                filepicker.choose("audio")
        self.assertEqual(caught.exception.details["detail"], "system helper unavailable")

    def test_non_windows_uses_tk_and_invalid_kind_never_starts_a_process(self):
        with patch.object(filepicker, "WINDOWS", False), \
             patch.object(filepicker, "_tk_choose", return_value=["/tmp/course"]) as tk, \
             patch.object(filepicker.subprocess, "run") as run:
            self.assertEqual(filepicker.choose("directory"), ["/tmp/course"])
            tk.assert_called_once_with("directory")
            for bad in ("execute", [], None):
                with self.assertRaises(filepicker.PickerError):
                    filepicker.choose(bad)
            run.assert_not_called()

    def test_cli_failure_writes_structured_stderr_and_nonzero_exit(self):
        stdout, stderr = StringIO(), StringIO()
        failure = filepicker.PickerError("Tcl missing", backend="tkinter", detail="Can't find a usable init.tcl")
        with patch.object(filepicker, "choose", side_effect=failure), redirect_stdout(stdout), redirect_stderr(stderr):
            code = filepicker.main(["files"])
        self.assertEqual(code, 2)
        self.assertEqual(stdout.getvalue(), "")
        body = json.loads(stderr.getvalue())
        self.assertFalse(body["ok"])
        self.assertEqual(body["error_details"]["detail"], "Can't find a usable init.tcl")


if __name__ == "__main__":
    unittest.main()
