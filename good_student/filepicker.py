"""Native path selection without requiring Tcl on Windows standalone Python."""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import traceback


WINDOWS = sys.platform == "win32"
KINDS = frozenset({"directory", "files", "audio"})


class PickerError(ValueError):
    def __init__(self, message, *, backend, detail=None):
        super().__init__(message)
        self.details = {"stage": "file_selection", "backend": backend,
                        "message": message, "detail": detail or message}


def _windows_script(kind):
    # Only a checked constant is embedded; selected paths never become code.
    selection = """
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = '选择课程资料文件夹'
    $dialog.ShowNewFolderButton = $false
    if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
        $paths = @($dialog.SelectedPath)
    }
""" if kind == "directory" else """
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Multiselect = $true
    $dialog.CheckFileExists = $true
    $dialog.RestoreDirectory = $true
    $dialog.Title = '选择录音文件'
    $dialog.Filter = '录音文件|*.mp3;*.m4a;*.wav;*.aac;*.flac;*.ogg;*.mp4|所有文件|*.*'
    if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
        $paths = @($dialog.FileNames)
    }
"""
    if kind == "files":
        selection = selection.replace("选择录音文件", "选择学习资料").replace(
            "录音文件|*.mp3;*.m4a;*.wav;*.aac;*.flac;*.ogg;*.mp4|所有文件|*.*",
            "学习资料|*.ppt;*.pptx;*.pdf;*.docx;*.txt;*.md;*.markdown;*.png;*.jpg;*.jpeg;*.webp;*.bmp;*.tif;*.tiff|所有文件|*.*")
    return """
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$owner = $null
$dialog = $null
try {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.Application]::EnableVisualStyles()
    $owner = New-Object System.Windows.Forms.Form
    $owner.ShowInTaskbar = $false
    $owner.TopMost = $true
    $owner.Opacity = 0
    $owner.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
    $owner.Show()
    $paths = @()
""" + selection + """
    [Console]::WriteLine((ConvertTo-Json -InputObject @($paths) -Compress))
} catch {
    $failure = @{ok=$false; error=$_.Exception.Message; detail=$_.ToString()}
    [Console]::Error.WriteLine((ConvertTo-Json -InputObject $failure -Compress))
    exit 1
} finally {
    if ($null -ne $dialog) { $dialog.Dispose() }
    if ($null -ne $owner) { $owner.Close(); $owner.Dispose() }
}
"""


def _windows_choose(kind):
    executable = shutil.which("powershell.exe")
    if executable is None:
        raise PickerError("找不到 Windows 系统文件选择器", backend="windows-forms",
                          detail="powershell.exe was not found on PATH")
    encoded = base64.b64encode(_windows_script(kind).encode("utf-16-le")).decode("ascii")
    try:
        process = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-STA", "-WindowStyle", "Hidden",
             "-EncodedCommand", encoded], capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, UnicodeError) as exc:
        raise PickerError("Windows 文件选择器未能启动", backend="windows-forms", detail=str(exc)) from exc
    def decode(value):
        if isinstance(value, str):
            return value
        if not value:
            return ""
        try:
            return value.decode("utf-8-sig")
        except UnicodeDecodeError:
            # Windows PowerShell may emit host/CLIXML errors in its legacy code
            # page before the script can set Console.OutputEncoding.
            try:
                return value.decode("mbcs")
            except (UnicodeError, LookupError):
                return value.decode("utf-8", errors="replace")
    stdout, stderr = decode(process.stdout), decode(process.stderr)
    if process.returncode:
        detail = stderr.strip() or stdout.strip() or f"PowerShell exited {process.returncode}"
        try:
            failure = json.loads(detail)
            message = failure.get("error") or "Windows 文件选择器执行失败"
            detail = failure.get("detail") or detail
        except (ValueError, AttributeError):
            message = "Windows 文件选择器执行失败"
        raise PickerError(message, backend="windows-forms", detail=detail)
    try:
        paths = json.loads(stdout)
    except ValueError as exc:
        raise PickerError("Windows 文件选择器返回格式异常", backend="windows-forms",
                          detail=stdout or "empty stdout") from exc
    if not isinstance(paths, list) or any(not isinstance(path, str) or not path for path in paths):
        raise PickerError("Windows 文件选择器返回路径异常", backend="windows-forms",
                          detail=repr(paths))
    return paths


def _tk_choose(kind):
    window = None
    try:
        import tkinter as tk
        from tkinter import filedialog
        window = tk.Tk()
        window.withdraw()
        window.attributes("-topmost", True)
        if kind == "directory":
            path = filedialog.askdirectory(parent=window, title="选择课程资料文件夹", mustexist=True)
            return [path] if path else []
        filters = ([("录音文件", "*.mp3 *.m4a *.wav *.aac *.flac *.ogg *.mp4")]
                   if kind == "audio" else [("学习资料", "*.ppt *.pptx *.pdf *.docx *.txt *.md *.markdown *.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff"),
                                            ("所有文件", "*.*")])
        return list(filedialog.askopenfilenames(parent=window,
                    title="选择录音" if kind == "audio" else "选择学习资料", filetypes=filters))
    except Exception as exc:
        raise PickerError("文件选择器初始化或选择失败", backend="tkinter", detail=traceback.format_exc()) from exc
    finally:
        if window is not None:
            window.destroy()


def choose(kind):
    """Return selected absolute paths, or [] on cancellation.

    The Windows implementation runs a hidden PowerShell STA helper. The console
    is hidden while its native selection dialog remains visible and topmost.
    Non-Windows hosts use Tk. Failures preserve the actual backend error.
    """
    if not isinstance(kind, str) or kind not in KINDS:
        raise PickerError("选择类型无效", backend="input", detail=f"unsupported selection kind: {kind!r}")
    return _windows_choose(kind) if WINDOWS else _tk_choose(kind)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if len(argv) != 1:
            raise PickerError("需要指定 directory、files 或 audio", backend="input")
        paths = choose(argv[0])
        print(json.dumps(paths, ensure_ascii=False))
        return 0
    except PickerError as exc:
        print(json.dumps({"ok": False, "error": str(exc), "error_details": exc.details}, ensure_ascii=False),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
