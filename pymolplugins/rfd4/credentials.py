"""Endpoint-bound credentials: Windows DPAPI or private POSIX user files."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile


def credential_path(url):
    key = hashlib.sha256(url.strip().rstrip("/").encode("utf-8")).hexdigest()
    return Path.home() / ".pymol" / "rfd4_credentials" / (key + ".bin")


def _dpapi(data, decrypt=False):
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    result = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise OSError("无法访问 Windows 用户凭据，请重新登录。")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel32.LocalFree(result.data)


def load_token(url):
    path = credential_path(url)
    if not path.exists():
        return ""
    if os.name != "nt":
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077):
            raise OSError("凭据文件权限不正确；请设为仅当前用户可读写。")
    data = path.read_bytes()
    if os.name == "nt":
        data = _dpapi(data, decrypt=True)
    value = json.loads(data.decode("utf-8"))
    if value.get("url") != url.strip().rstrip("/"):
        raise ValueError("凭据与服务地址不匹配，请重新登录。")
    return value["token"]


def save_token(url, token):
    path = credential_path(url)
    if not token:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        path.parent.chmod(0o700)
    data = json.dumps({"url": url.strip().rstrip("/"), "token": token.strip()}).encode("utf-8")
    if os.name == "nt":
        data = _dpapi(data)
    descriptor, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".credential-")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
