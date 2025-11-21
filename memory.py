# setmemory_active_window.py
#
# Usage example:
#   python setmemory_active_window.py
#   # or import in another script:
#   #   from setmemory_active_window import setmemory_u32_active
#   #   setmemory_u32_active(0x00BDBB80, 0)

import ctypes
from ctypes import wintypes
import struct
import sys

# ----------------- Win32 constants -----------------
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ      = 0x0010
PROCESS_VM_WRITE     = 0x0020

# Enough for our purpose (write to existing memory)
PROCESS_PERMS = PROCESS_VM_OPERATION | PROCESS_VM_WRITE | PROCESS_VM_READ

# ----------------- Load DLLs -----------------
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32   = ctypes.WinDLL("user32",   use_last_error=True)

# Function prototypes
OpenProcess = kernel32.OpenProcess
OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
OpenProcess.restype  = wintypes.HANDLE

WriteProcessMemory = kernel32.WriteProcessMemory
WriteProcessMemory.argtypes = [
    wintypes.HANDLE,        # hProcess
    wintypes.LPVOID,        # lpBaseAddress
    wintypes.LPCVOID,       # lpBuffer
    ctypes.c_size_t,        # nSize
    ctypes.POINTER(ctypes.c_size_t)  # lpNumberOfBytesWritten
]
WriteProcessMemory.restype = wintypes.BOOL

CloseHandle = kernel32.CloseHandle
CloseHandle.argtypes = [wintypes.HANDLE]
CloseHandle.restype  = wintypes.BOOL

GetForegroundWindow = user32.GetForegroundWindow
GetForegroundWindow.restype = wintypes.HWND

GetWindowThreadProcessId = user32.GetWindowThreadProcessId
GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
GetWindowThreadProcessId.restype  = wintypes.DWORD

# ----------------- Helpers -----------------

def get_active_window_pid() -> int:
    """Return PID of the currently active (foreground) window, or 0 if none."""
    hwnd = GetForegroundWindow()
    if not hwnd:
        return 0
    pid = wintypes.DWORD(0)
    GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value

def open_process(pid: int) -> int:
    """Open a process handle with VM write permissions."""
    h_proc = OpenProcess(PROCESS_PERMS, False, pid)
    if not h_proc:
        raise OSError(ctypes.get_last_error(), "OpenProcess failed")
    return h_proc

def write_process_memory_u32(h_proc, address: int, value: int) -> None:
    """
    Write an unsigned 32-bit integer to process memory at 'address'.
    Equivalent to ACTool: setmemory address, value
    """
    data = struct.pack("<I", value)  # little-endian 4 bytes
    n_written = ctypes.c_size_t(0)
    ok = WriteProcessMemory(
        h_proc,
        ctypes.c_void_p(address),
        data,
        len(data),
        ctypes.byref(n_written)
    )
    if not ok or n_written.value != len(data):
        raise OSError(ctypes.get_last_error(), "WriteProcessMemory failed")

def setmemory_u32_active(address: int, value: int) -> None:
    """
    Get PID of active window and write a 32-bit value at address in that process.
    This is the direct analog of ACTool's `setmemory` using the active window.
    """
    pid = get_active_window_pid()
    if not pid:
        raise RuntimeError("No active window found")

    h_proc = open_process(pid)
    try:
        write_process_memory_u32(h_proc, address, value)
    finally:
        CloseHandle(h_proc)