"""Read the Windows default audio *render* endpoint ID. Item 73.

Why this exists at all: **PortAudio cannot answer the question.**
`Pa_Initialize()` snapshots the whole device list -- including which device
is default -- and every `pyaudiowpatch` query is a microsecond-scale read of
that frozen snapshot. Measured: a second `PyAudio()` created while the first
is alive returns in 0.048 ms with an identical device index, because the
handle is refcounted and no rescan happens. So `get_default_wasapi_loopback()`
returns the *start-of-session* default for the life of the session, and
polling it would be a guaranteed no-op.

Why the endpoint ID and nothing else:

* **Index is not identity.** PortAudio's global index is a dense
  concatenation across host APIs in host-API order, so the WASAPI devices sit
  above however many MME and DirectSound entries happen to exist. Plugging in
  a headset grows MME and DirectSound, which shifts every WASAPI index --
  including devices that did not change.
* **Name is not unique.** The name is Windows' composed FriendlyName. On the
  development machine two distinct render endpoints compose to the identical
  string "Realtek HD Audio 2nd output (Realtek(R) Audio)", and three more
  share "HD Audio Driver for Display Audio".
* The MMDevice endpoint ID (e.g. `{0.0.0.00000000}.{57b9f110-...}`) survives
  index churn, replugs and reboots.

Implemented on `ctypes` against COM directly rather than adding `comtypes` or
`pycaw`: no new dependency, and the delivery machine is already the
constraint (see §11 of CLIENT_DELIVERY_SPRINT_v5.md).

**Every entry point returns "" instead of raising.** This runs on a
background watchdog thread whose only job is to notice a problem; it must
never become the problem. An unreadable endpoint is reported as unknown, and
the caller treats unknown as "no evidence of a change".
"""

from __future__ import annotations

import ctypes
from ctypes import POINTER, byref, c_void_p, c_wchar_p
from ctypes.wintypes import LPCWSTR

# MMDeviceEnumerator / IMMDeviceEnumerator, from mmdeviceapi.h.
_CLSID_MMDeviceEnumerator = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
_IID_IMMDeviceEnumerator = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"

# EDataFlow / ERole, from mmdeviceapi.h.
_E_RENDER = 0
_ROLE_MULTIMEDIA = 1

_COINIT_MULTITHREADED = 0x0
_RPC_E_CHANGED_MODE = -2147417850  # 0x80010106

# Vtable slots. IMMDeviceEnumerator and IMMDevice both start with the three
# IUnknown methods, so the interface's own methods begin at slot 3.
_SLOT_RELEASE = 2
_SLOT_GET_DEFAULT_AUDIO_ENDPOINT = 4  # IMMDeviceEnumerator
_SLOT_GET_ID = 5  # IMMDevice


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(text: str) -> _GUID:
    out = _GUID()
    hr = ctypes.oledll.ole32.CLSIDFromString(LPCWSTR(text), byref(out))
    if hr:  # oledll already raises on failure; belt and braces
        raise OSError(f"CLSIDFromString failed for {text}")
    return out


def _call(ptr: c_void_p, slot: int, argtypes: tuple, *args) -> int:
    """Invoke a COM vtable slot on `ptr` and return the HRESULT.

    `argtypes` is passed explicitly rather than derived from the values:
    `byref(x)` produces a `CArgObject`, which is not a usable ctypes argtype,
    and deriving from it silently builds a wrong prototype.
    """
    vtable = ctypes.cast(ptr, POINTER(POINTER(c_void_p)))[0]
    proto = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, *argtypes)
    return proto(vtable[slot])(ptr, *args)


def com_initialize_mta() -> bool:
    """Join the multi-threaded apartment. Call once per worker thread.

    Returns True when the thread may make COM calls. `RPC_E_CHANGED_MODE`
    means the thread is already in a different apartment, which is fine --
    the calls still work, we simply must not uninitialise it.
    """
    try:
        hr = ctypes.windll.ole32.CoInitializeEx(None, _COINIT_MULTITHREADED)
    except Exception:
        return False
    return hr >= 0 or hr == _RPC_E_CHANGED_MODE


def com_uninitialize() -> None:
    try:
        ctypes.windll.ole32.CoUninitialize()
    except Exception:
        pass


def read_default_render_endpoint_id() -> str:
    """Current default render endpoint ID, or "" if it cannot be read.

    The caller MUST treat "" as "unknown", never as "changed" -- an
    unreadable endpoint is not evidence that the device moved.
    """
    enumerator = c_void_p()
    device = c_void_p()
    try:
        ctypes.oledll.ole32.CoCreateInstance(
            byref(_guid(_CLSID_MMDeviceEnumerator)),
            None,
            1,  # CLSCTX_INPROC_SERVER
            byref(_guid(_IID_IMMDeviceEnumerator)),
            byref(enumerator),
        )
    except Exception:
        return ""
    if not enumerator:
        return ""

    try:
        hr = _call(
            enumerator,
            _SLOT_GET_DEFAULT_AUDIO_ENDPOINT,
            (ctypes.c_int, ctypes.c_int, POINTER(c_void_p)),
            ctypes.c_int(_E_RENDER),
            ctypes.c_int(_ROLE_MULTIMEDIA),
            byref(device),
        )
        # A machine with no active render endpoint returns
        # E_NOTFOUND rather than raising; that is "unknown", not an error.
        if hr < 0 or not device:
            return ""

        raw = c_wchar_p()
        hr = _call(device, _SLOT_GET_ID, (POINTER(c_wchar_p),), byref(raw))
        if hr < 0 or not raw.value:
            return ""
        endpoint_id = str(raw.value)
        try:
            ctypes.windll.ole32.CoTaskMemFree(raw)
        except Exception:
            pass
        return endpoint_id
    except Exception:
        return ""
    finally:
        for ptr in (device, enumerator):
            if ptr:
                try:
                    _call(ptr, _SLOT_RELEASE, ())
                except Exception:
                    pass


__all__ = [
    "com_initialize_mta",
    "com_uninitialize",
    "read_default_render_endpoint_id",
]
