"""Run a Windows Host acceptance script on an independent, hidden desktop."""

import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import os
import subprocess
import sys

import win32event
import win32process


def input_desktop(user32):
    handle = user32.OpenInputDesktop(0, False, 1)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        name = ctypes.create_unicode_buffer(256)
        size = wintypes.DWORD()
        if not user32.GetUserObjectInformationW(handle, 2, name, ctypes.sizeof(name), ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        return name.value
    finally:
        user32.CloseDesktop(handle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("script", type=Path)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.CreateDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p,
                                     wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    user32.CreateDesktopW.restype = wintypes.HANDLE
    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]

    desktop_name = f"PyMOLAcceptance_{os.getpid()}"
    result = {"desktop": desktop_name, "input_before": input_desktop(user32)}
    desktop = user32.CreateDesktopW(desktop_name, None, None, 0, 0x10000000, None)
    if not desktop:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        startup = win32process.STARTUPINFO()
        startup.lpDesktop = "winsta0\\" + desktop_name
        command = [sys.executable, "-I", "-u", str(args.script.resolve()), *args.arguments]
        process, thread, result["pid"], _ = win32process.CreateProcess(
            sys.executable, subprocess.list2cmdline(command),
            None, None, False, 0x08000000, None, None, startup,
        )
        try:
            if win32event.WaitForSingleObject(process, args.timeout * 1000) == 258:
                win32process.TerminateProcess(process, 124)
                win32event.WaitForSingleObject(process, 5000)
                result["timed_out"] = True
            result["exit_code"] = win32process.GetExitCodeProcess(process)
        finally:
            thread.Close()
            process.Close()
    finally:
        result["input_after"] = input_desktop(user32)
        user32.CloseDesktop(desktop)
        args.record.parent.mkdir(parents=True, exist_ok=True)
        args.record.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result), flush=True)
    assert result["input_before"] == result["input_after"], result
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
