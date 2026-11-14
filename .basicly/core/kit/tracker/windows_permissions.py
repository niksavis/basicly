from __future__ import annotations

import ctypes
from ctypes import wintypes
from functools import lru_cache
from pathlib import Path
from typing import Any

POINTER = ctypes.c_void_p
POINTER_OUT = ctypes.POINTER(POINTER)
DWORD_OUT = ctypes.POINTER(wintypes.DWORD)
DACL_INFORMATION = 4
PROTECTED_DACL_INFORMATION = 0x80000000
FILE_ALL_ACCESS = 0x1F01FF


class SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.DWORD),
        ("descriptor", POINTER),
        ("inherit", wintypes.BOOL),
    ]


class FileAttributes(ctypes.Structure):
    _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]


class AclSize(ctypes.Structure):
    _fields_ = [
        ("count", wintypes.DWORD),
        ("used", wintypes.DWORD),
        ("free", wintypes.DWORD),
    ]


class AllowedAce(ctypes.Structure):
    _fields_ = [
        ("kind", ctypes.c_ubyte),
        ("flags", ctypes.c_ubyte),
        ("size", wintypes.WORD),
        ("mask", wintypes.DWORD),
        ("sid", wintypes.DWORD),
    ]


@lru_cache(maxsize=1)
def _api() -> tuple[Any, Any]:
    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        raise OSError("Private Windows state requires native Windows security APIs.")
    kernel, security = (
        loader("kernel32", use_last_error=True),
        loader("advapi32", use_last_error=True),
    )
    kernel_signatures = {
        "GetCurrentProcess": (wintypes.HANDLE, []),
        "CloseHandle": (wintypes.BOOL, [wintypes.HANDLE]),
        "LocalFree": (POINTER, [POINTER]),
        "CreateDirectoryW": (wintypes.BOOL, [wintypes.LPCWSTR, POINTER]),
        "CreateFileW": (
            wintypes.HANDLE,
            [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                POINTER,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.HANDLE,
            ],
        ),
        "GetFileInformationByHandleEx": (
            wintypes.BOOL,
            [wintypes.HANDLE, ctypes.c_int, POINTER, wintypes.DWORD],
        ),
    }
    security_signatures = {
        "OpenProcessToken": (wintypes.BOOL, [wintypes.HANDLE, wintypes.DWORD, POINTER_OUT]),
        "GetTokenInformation": (
            wintypes.BOOL,
            [wintypes.HANDLE, ctypes.c_int, POINTER, wintypes.DWORD, DWORD_OUT],
        ),
        "ConvertSidToStringSidW": (wintypes.BOOL, [POINTER, ctypes.POINTER(wintypes.LPWSTR)]),
        "ConvertStringSecurityDescriptorToSecurityDescriptorW": (
            wintypes.BOOL,
            [wintypes.LPCWSTR, wintypes.DWORD, POINTER_OUT, DWORD_OUT],
        ),
        "GetSecurityDescriptorDacl": (
            wintypes.BOOL,
            [POINTER, ctypes.POINTER(wintypes.BOOL), POINTER_OUT, ctypes.POINTER(wintypes.BOOL)],
        ),
        "SetSecurityInfo": (
            wintypes.DWORD,
            [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, POINTER, POINTER, POINTER, POINTER],
        ),
        "GetSecurityInfo": (
            wintypes.DWORD,
            [
                wintypes.HANDLE,
                ctypes.c_int,
                wintypes.DWORD,
                POINTER_OUT,
                POINTER_OUT,
                POINTER_OUT,
                POINTER_OUT,
                POINTER_OUT,
            ],
        ),
        "GetAclInformation": (wintypes.BOOL, [POINTER, POINTER, wintypes.DWORD, ctypes.c_int]),
        "GetAce": (wintypes.BOOL, [POINTER, wintypes.DWORD, POINTER_OUT]),
        "EqualSid": (wintypes.BOOL, [POINTER, POINTER]),
    }
    for library, signatures in ((kernel, kernel_signatures), (security, security_signatures)):
        for name, (result, arguments) in signatures.items():
            function = getattr(library, name)
            function.restype, function.argtypes = result, arguments
    return kernel, security


def _error(code: int | None = None) -> OSError:
    factory, last_error = getattr(ctypes, "WinError", None), getattr(ctypes, "get_last_error", None)
    if factory is None or last_error is None:
        return OSError("Private Windows state requires native Windows security APIs.")
    return factory(code if code is not None else last_error())


def _check(success: Any) -> None:
    if not success:
        raise _error()


def _user(kernel: Any, security: Any) -> Any:
    token, size = POINTER(), wintypes.DWORD()
    _check(security.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)))
    try:
        security.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        if not size.value:
            raise _error()
        buffer = ctypes.create_string_buffer(size.value)
        _check(security.GetTokenInformation(token, 1, buffer, size.value, ctypes.byref(size)))
        return buffer
    finally:
        kernel.CloseHandle(token)


def _descriptor(kernel: Any, security: Any, sid: int) -> Any:
    name, descriptor = wintypes.LPWSTR(), POINTER()
    _check(security.ConvertSidToStringSidW(sid, ctypes.byref(name)))
    try:
        _check(
            security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                f"D:P(A;OICI;FA;;;{name.value})", 1, ctypes.byref(descriptor), None
            )
        )
        return descriptor
    finally:
        kernel.LocalFree(name)


def _verify(kernel: Any, security: Any, handle: Any, sid: int) -> None:
    acl, descriptor = POINTER(), POINTER()
    code = security.GetSecurityInfo(
        handle, 1, DACL_INFORMATION, None, None, ctypes.byref(acl), None, ctypes.byref(descriptor)
    )
    if code:
        raise _error(code)
    try:
        if not acl.value:
            raise OSError("The vault state has no restrictive access list.")
        size, pointer = AclSize(), POINTER()
        _check(security.GetAclInformation(acl, ctypes.byref(size), ctypes.sizeof(size), 2))
        if size.count != 1:
            raise OSError("The vault state grants access to another principal.")
        _check(security.GetAce(acl, 0, ctypes.byref(pointer)))
        if not pointer.value:
            raise OSError("The vault state has no valid access entry.")
        ace = ctypes.cast(pointer, ctypes.POINTER(AllowedAce)).contents
        if (
            ace.kind != 0
            or ace.flags != 3
            or ace.mask != FILE_ALL_ACCESS
            or not security.EqualSid(pointer.value + AllowedAce.sid.offset, sid)
        ):
            raise OSError("The vault state is not private to the current user.")
    finally:
        kernel.LocalFree(descriptor)


def private(path: Path) -> None:
    kernel, security = _api()
    user = _user(kernel, security)
    sid = ctypes.cast(user, ctypes.POINTER(POINTER))[0]
    if sid is None:
        raise OSError("The current user has no valid Windows security identity.")
    descriptor = _descriptor(kernel, security, sid)
    try:
        path = path.absolute()
        path.parent.mkdir(parents=True, exist_ok=True)
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
        if not kernel.CreateDirectoryW(str(path), ctypes.byref(attributes)):
            error = _error()
            if getattr(error, "winerror", None) != 183:
                raise error
        handle = kernel.CreateFileW(str(path), 0x60080, 3, None, 3, 0x2200000, None)
        if handle == POINTER(-1).value:
            raise _error()
        try:
            actual = FileAttributes()
            _check(
                kernel.GetFileInformationByHandleEx(
                    handle, 9, ctypes.byref(actual), ctypes.sizeof(actual)
                )
            )
            if not actual.attributes & 0x10 or actual.attributes & 0x400:
                raise OSError("The vault state must be a directory without a reparse point.")
            present, defaulted, acl = wintypes.BOOL(), wintypes.BOOL(), POINTER()
            _check(
                security.GetSecurityDescriptorDacl(
                    descriptor, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted)
                )
            )
            if not present or not acl.value:
                raise OSError("The private vault access list could not be constructed.")
            code = security.SetSecurityInfo(
                handle, 1, DACL_INFORMATION | PROTECTED_DACL_INFORMATION, None, None, acl, None
            )
            if code:
                raise _error(code)
            _verify(kernel, security, handle, sid)
        finally:
            kernel.CloseHandle(handle)
    finally:
        kernel.LocalFree(descriptor)
