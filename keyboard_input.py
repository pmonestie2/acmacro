"""Scan-code input for game keys."""
import ctypes
from ctypes import wintypes

class Keyboard(ctypes.Structure):
    _fields_ = [('vk', wintypes.WORD), ('scan', wintypes.WORD),
                ('flags', wintypes.DWORD), ('time', wintypes.DWORD),
                ('extra', ctypes.c_size_t)]

class Mouse(ctypes.Structure):
    _fields_ = [('x', wintypes.LONG), ('y', wintypes.LONG),
                ('data', wintypes.DWORD), ('flags', wintypes.DWORD),
                ('time', wintypes.DWORD), ('extra', ctypes.c_size_t)]

class Payload(ctypes.Union):
    _fields_ = [('keyboard', Keyboard), ('mouse', Mouse)]

class Input(ctypes.Structure):
    _fields_ = [('type', wintypes.DWORD), ('payload', Payload)]

user32 = ctypes.WinDLL('user32', use_last_error=True)
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT

def send(scan, release=False, *, extended=True):
    event = Input(type=1, payload=Payload(keyboard=Keyboard(
        vk=0, scan=scan, flags=0x0008 | (0x0001 if extended else 0) | (0x0002 if release else 0))))
    if user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(event)) != 1:
        raise ctypes.WinError(ctypes.get_last_error())
