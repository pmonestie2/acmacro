/* Standalone Alt+left-drag for E&B. Build as a Windows GUI executable. */
#define WIN32_LEAN_AND_MEAN
#define UNICODE
#define _UNICODE
#define _WIN32_WINNT 0x0601
#define WINVER 0x0601
#include <windows.h>
#include <shellapi.h>
#include <wchar.h>

/* Declarations omitted by TinyCC's compact SDK; standard Windows APIs. */
__declspec(dllimport) BOOL WINAPI IsUserAnAdmin(void);

#ifndef PROCESS_QUERY_LIMITED_INFORMATION
#define PROCESS_QUERY_LIMITED_INFORMATION 0x1000
#endif
#ifndef LLMHF_INJECTED
#define LLMHF_INJECTED 1
#endif

#define TRAY_MESSAGE (WM_APP + 1)
#define END_DRAG (WM_APP + 2)
#define MOVE_TIMER 1
#define MENU_ENABLE 101
#define MENU_EXIT 102

static const wchar_t CLASS_NAME[] = L"AcMacro.EnbAltDrag.v1";
static HWND helper;
static HHOOK mouse_hook;
static BOOL enabled = TRUE;
static BOOL swallow_release = FALSE;
static UINT taskbar_created;
static NOTIFYICONDATAW tray;

typedef struct Drag {
    HWND window;
    DWORD pid;
    HANDLE process;
    RECT original;
    POINT start;
    POINT current;
    POINT last;
} Drag;
static Drag drag;

static LONG clamp(LONG value, LONG low, LONG high) {
    if (value < low) return low;
    return value > high ? high : value;
}

static POINT drag_position(RECT original, POINT start, POINT current, RECT work) {
    POINT result;
    LONG width = original.right - original.left;
    /* Keep part of the title bar reachable; dragging across monitors is allowed. */
    result.x = clamp(original.left + current.x - start.x, work.left - width + 64, work.right - 64);
    result.y = clamp(original.top + current.y - start.y, work.top, work.bottom - 32);
    return result;
}

static BOOL game_title(const wchar_t *title) {
    return _wcsicmp(title, L"ENB") == 0 || _wcsnicmp(title, L"ENB_", 4) == 0
        || _wcsicmp(title, L"Earth & Beyond") == 0;
}

static HANDLE game_process(HWND window, DWORD *pid) {
    typedef BOOL (WINAPI *QueryImage)(HANDLE, DWORD, LPWSTR, PDWORD);
    QueryImage query_image = (QueryImage)GetProcAddress(GetModuleHandleW(L"kernel32.dll"), "QueryFullProcessImageNameW");
    wchar_t title[256], path[32768];
    DWORD length = sizeof(path) / sizeof(path[0]);
    HANDLE process;
    const wchar_t *name;
    if (!window || !IsWindowVisible(window) || IsIconic(window)) return NULL;
    GetWindowTextW(window, title, sizeof(title) / sizeof(title[0]));
    if (!game_title(title)) return NULL;
    GetWindowThreadProcessId(window, pid);
    process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, FALSE, *pid);
    if (!process) return NULL;
    if (!query_image || !query_image(process, 0, path, &length)) {
        CloseHandle(process);
        return NULL;
    }
    name = wcsrchr(path, L'\\');
    name = name ? name + 1 : path;
    if (_wcsicmp(name, L"client.exe") != 0) {
        CloseHandle(process);
        return NULL;
    }
    return process;
}

static void finish_drag(void) {
    KillTimer(helper, MOVE_TIMER);
    if (drag.process) CloseHandle(drag.process);
    ZeroMemory(&drag, sizeof(drag));
    /* If cancelled mid-drag, still swallow the matching button-up. */
}

static void move_drag(void) {
    DWORD pid = 0;
    MONITORINFO monitor;
    POINT position;
    if (!drag.window) return;
    GetWindowThreadProcessId(drag.window, &pid);
    if (!IsWindow(drag.window) || pid != drag.pid
            || WaitForSingleObject(drag.process, 0) != WAIT_TIMEOUT) {
        finish_drag();
        return;
    }
    monitor.cbSize = sizeof(monitor);
    if (!GetMonitorInfoW(MonitorFromPoint(drag.current, MONITOR_DEFAULTTONEAREST), &monitor)) return;
    position = drag_position(drag.original, drag.start, drag.current, monitor.rcWork);
    if (position.x == drag.last.x && position.y == drag.last.y) return;
    /* Never synchronously wait for the game, including when it is hung. */
    if (!SetWindowPos(drag.window, NULL, position.x, position.y, 0, 0,
            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS)) {
        finish_drag();
        return;
    }
    drag.last = position;
}

static BOOL drag_input(WPARAM message, POINT point) {
    if (message == WM_MOUSEMOVE) {
        drag.current = point;
        /* Windows must commit this movement; only the click is swallowed. */
        return FALSE;
    }
    if (message == WM_LBUTTONUP) {
        /* Release can carry stale coordinates; keep the last motion. */
        swallow_release = FALSE;
        if (helper) PostMessageW(helper, END_DRAG, 0, 0);
        return TRUE;
    }
    return FALSE;
}

static LRESULT CALLBACK mouse_callback(int code, WPARAM message, LPARAM parameter) {
    const MSLLHOOKSTRUCT *event;
    if (code != HC_ACTION) return CallNextHookEx(mouse_hook, code, message, parameter);
    event = (const MSLLHOOKSTRUCT *)parameter;
    /* Automation and injected clicks retain their ordinary behavior. */
    if (event->flags & LLMHF_INJECTED) return CallNextHookEx(mouse_hook, code, message, parameter);
    if (swallow_release && drag_input(message, event->pt)) return 1;
    if (enabled && !drag.window && message == WM_LBUTTONDOWN
            && (GetAsyncKeyState(VK_MENU) & 0x8000)
            && !(GetAsyncKeyState(VK_CONTROL) & 0x8000)) {
        HWND target = GetAncestor(WindowFromPoint(event->pt), GA_ROOT);
        DWORD pid = 0;
        HANDLE process = game_process(target, &pid);
        RECT bounds;
        if (!process) return CallNextHookEx(mouse_hook, code, message, parameter);
        if (!GetWindowRect(target, &bounds)) {
            CloseHandle(process);
            return CallNextHookEx(mouse_hook, code, message, parameter);
        }
        drag.window = target;
        drag.pid = pid;
        drag.process = process;
        drag.original = bounds;
        drag.start = drag.current = event->pt;
        drag.last.x = bounds.left;
        drag.last.y = bounds.top;
        if (!SetTimer(helper, MOVE_TIMER, 16, NULL)) {
            finish_drag();
            return CallNextHookEx(mouse_hook, code, message, parameter);
        }
        swallow_release = TRUE;
        return 1;
    }
    return CallNextHookEx(mouse_hook, code, message, parameter);
}

static BOOL update_tray(DWORD operation) {
    lstrcpynW(tray.szTip, enabled ? L"E&B Alt-drag: Alt + left-drag to move; right-click for menu"
                                : L"E&B Alt-drag paused; right-click to enable", ARRAYSIZE(tray.szTip));
    return Shell_NotifyIconW(operation, &tray);
}

static void tray_menu(void) {
    POINT point;
    HMENU menu = CreatePopupMenu();
    if (!menu) return;
    AppendMenuW(menu, MF_STRING | (enabled ? MF_CHECKED : 0), MENU_ENABLE, L"Enable Alt-drag");
    AppendMenuW(menu, MF_SEPARATOR, 0, NULL);
    AppendMenuW(menu, MF_STRING, MENU_EXIT, L"Exit");
    GetCursorPos(&point);
    SetForegroundWindow(helper);
    TrackPopupMenu(menu, TPM_RIGHTBUTTON, point.x, point.y, 0, helper, NULL);
    PostMessageW(helper, WM_NULL, 0, 0);
    DestroyMenu(menu);
}

static LRESULT CALLBACK window_callback(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    if (message == taskbar_created && taskbar_created) {
        update_tray(NIM_ADD);
        return 0;
    }
    switch (message) {
        case WM_TIMER:
            if (wparam == MOVE_TIMER) move_drag();
            return 0;
        case END_DRAG:
            move_drag();
            finish_drag();
            return 0;
        case TRAY_MESSAGE:
            if (lparam == WM_RBUTTONUP || lparam == WM_CONTEXTMENU) tray_menu();
            return 0;
        case WM_COMMAND:
            if (LOWORD(wparam) == MENU_ENABLE) {
                enabled = !enabled;
                if (!enabled) finish_drag();
                update_tray(NIM_MODIFY);
            } else if (LOWORD(wparam) == MENU_EXIT) {
                DestroyWindow(window);
            }
            return 0;
        case WM_CLOSE:
            DestroyWindow(window);
            return 0;
        case WM_DESTROY:
            if (mouse_hook) UnhookWindowsHookEx(mouse_hook);
            mouse_hook = NULL;
            finish_drag();
            Shell_NotifyIconW(NIM_DELETE, &tray);
            PostQuitMessage(0);
            return 0;
    }
    return DefWindowProcW(window, message, wparam, lparam);
}

static int self_test(void) {
    RECT original = {100, 100, 1400, 900}, work = {0, 0, 3200, 1800};
    POINT start = {200, 200}, current = {500, 600}, result;
    result = drag_position(original, start, current, work);
    if (result.x != 400 || result.y != 500) return 1;
    current.x = -10000; current.y = 10000;
    result = drag_position(original, start, current, work);
    if (result.x != -1236 || result.y != 1768) return 2;
    work.left = -1920; work.right = 0;
    current.x = -500; current.y = -10000;
    result = drag_position(original, start, current, work);
    if (result.x != -600 || result.y != 0) return 3;
    if (!game_title(L"ENB") || !game_title(L"enb_macboyje") || !game_title(L"Earth & Beyond")) return 4;
    if (game_title(L"ENB helper") || game_title(L"Notepad")) return 5;
    swallow_release = TRUE;
    current.x = 500; current.y = 600;
    if (drag_input(WM_MOUSEMOVE, current)) return 6;
    if (drag.current.x != 500 || drag.current.y != 600) return 7;
    /* A release at the original point must never undo the completed drag. */
    if (!drag_input(WM_LBUTTONUP, start) || swallow_release) return 8;
    if (drag.current.x != 500 || drag.current.y != 600) return 9;
    ZeroMemory(&drag, sizeof(drag));
    return 0;
}

int WINAPI WinMain(HINSTANCE instance, HINSTANCE previous, LPSTR command, int show) {
    HANDLE mutex;
    WNDCLASSW type;
    MSG message;
    int result;
    typedef BOOL (WINAPI *SetAwareness)(HANDLE);
    typedef BOOL (WINAPI *SetLegacyAwareness)(void);
    SetAwareness set_awareness;
    (void)previous; (void)command; (void)show;
    if (wcsstr(GetCommandLineW(), L"--self-test")) return self_test();
    if (!IsUserAnAdmin()) {
        wchar_t path[32768];
        if (!GetModuleFileNameW(NULL, path, ARRAYSIZE(path))) return 1;
        if ((INT_PTR)ShellExecuteW(NULL, L"runas", path, NULL, NULL, SW_SHOWNORMAL) <= 32) return 1;
        return 0;
    }
    mutex = CreateMutexW(NULL, FALSE, L"Local\\AcMacro.EnbAltDrag.v1");
    if (!mutex) return 1;
    if (GetLastError() == ERROR_ALREADY_EXISTS) {
        MessageBoxW(NULL, L"E&B Alt-drag is already running. Look for its tray icon.", L"E&B Alt-drag", MB_OK);
        CloseHandle(mutex);
        return 0;
    }
    set_awareness = (SetAwareness)GetProcAddress(GetModuleHandleW(L"user32.dll"), "SetProcessDpiAwarenessContext");
    if (set_awareness) set_awareness((HANDLE)(INT_PTR)-4);
    else {
        SetLegacyAwareness legacy = (SetLegacyAwareness)GetProcAddress(GetModuleHandleW(L"user32.dll"), "SetProcessDPIAware");
        if (legacy) legacy();
    }
    ZeroMemory(&type, sizeof(type));
    type.lpfnWndProc = window_callback;
    type.hInstance = instance;
    type.lpszClassName = CLASS_NAME;
    if (!RegisterClassW(&type)) { CloseHandle(mutex); return 1; }
    helper = CreateWindowExW(WS_EX_TOOLWINDOW, CLASS_NAME, L"E&B Alt-drag", WS_OVERLAPPED,
                            0, 0, 0, 0, NULL, NULL, instance, NULL);
    if (!helper) { CloseHandle(mutex); return 1; }
    ZeroMemory(&tray, sizeof(tray));
    tray.cbSize = sizeof(tray);
    tray.hWnd = helper;
    tray.uID = 1;
    tray.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP;
    tray.uCallbackMessage = TRAY_MESSAGE;
    tray.hIcon = LoadIconW(NULL, IDI_APPLICATION);
    taskbar_created = RegisterWindowMessageW(L"TaskbarCreated");
    mouse_hook = SetWindowsHookExW(WH_MOUSE_LL, mouse_callback, instance, 0);
    if (!mouse_hook || !update_tray(NIM_ADD)) {
        MessageBoxW(NULL, L"Could not install the mouse hook or tray icon.", L"E&B Alt-drag", MB_OK | MB_ICONERROR);
        DestroyWindow(helper);
        CloseHandle(mutex);
        return 1;
    }
    while ((result = GetMessageW(&message, NULL, 0, 0)) > 0) {
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
    if (result == -1 && IsWindow(helper)) DestroyWindow(helper);
    CloseHandle(mutex);
    return result == -1 ? 1 : 0;
}
