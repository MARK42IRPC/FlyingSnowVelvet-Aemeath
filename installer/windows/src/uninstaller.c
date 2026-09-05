#define UNICODE
#define _UNICODE
#define WIN32_LEAN_AND_MEAN

#include <windows.h>
#include <commctrl.h>
#include <shellapi.h>
#include <strsafe.h>
#include <string.h>
#include <wchar.h>

#include "resource.h"

#pragma comment(lib, "comctl32.lib")
#pragma comment(lib, "gdi32.lib")
#pragma comment(lib, "shell32.lib")
#pragma comment(lib, "user32.lib")

#define FSV_PATH_CAPACITY 1024
#define WM_FSV_UNINSTALL_STATUS (WM_APP + 1)
#define WM_FSV_UNINSTALL_DONE (WM_APP + 2)
#define IDC_UNINSTALL 2001
#define IDC_EXIT 2002
#define IDC_PROGRESS 2003

static const wchar_t WINDOW_CLASS_NAME[] = L"FlyingSnowVelvetUninstaller";
static const wchar_t PRODUCT_NAME[] = L"飞行雪绒";
static const wchar_t CONTRACT_DIRECTORY[] = L"C:\\AemeathDeskPet";
static const BYTE INSTALL_MARKER[] = "FSV-OFFLINE-PAYLOAD-2\n";

typedef struct CleanupContext {
    wchar_t install_root[FSV_PATH_CAPACITY];
    wchar_t helper_path[FSV_PATH_CAPACITY];
    DWORD parent_pid;
    HWND window;
} CleanupContext;

static CleanupContext g_cleanup;
static HWND g_window;
static HWND g_title;
static HWND g_body;
static HWND g_path;
static HWND g_action;
static HWND g_exit;
static HWND g_progress;
static HFONT g_title_font;
static HFONT g_body_font;
static HBRUSH g_canvas_brush;
static BOOL g_cleanup_mode;
static BOOL g_cleanup_running;
static int g_hover_action;
static int g_hover_exit;

static LRESULT CALLBACK window_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam);

static BOOL join_path(const wchar_t *root, const wchar_t *relative, wchar_t *output, size_t capacity) {
    size_t length = wcslen(root);
    return SUCCEEDED(
        length > 0 && (root[length - 1] == L'\\' || root[length - 1] == L'/')
            ? StringCchPrintfW(output, capacity, L"%ls%ls", root, relative)
            : StringCchPrintfW(output, capacity, L"%ls\\%ls", root, relative)
    );
}

static BOOL parent_directory(wchar_t *path) {
    wchar_t *separator = wcsrchr(path, L'\\');
    if (separator == NULL || separator == path || (separator == path + 2 && path[1] == L':')) {
        SetLastError(ERROR_BAD_PATHNAME);
        return FALSE;
    }
    *separator = L'\0';
    return TRUE;
}

static BOOL ordinary_file(const wchar_t *path) {
    DWORD attributes = GetFileAttributesW(path);
    return attributes != INVALID_FILE_ATTRIBUTES &&
        (attributes & FILE_ATTRIBUTE_DIRECTORY) == 0 &&
        (attributes & FILE_ATTRIBUTE_REPARSE_POINT) == 0;
}

static BOOL marker_is_valid(const wchar_t *install_root) {
    wchar_t marker[FSV_PATH_CAPACITY];
    wchar_t launcher[FSV_PATH_CAPACITY];
    wchar_t runtime[FSV_PATH_CAPACITY];
    wchar_t uninstaller[FSV_PATH_CAPACITY];
    BYTE content[sizeof(INSTALL_MARKER) - 1];
    DWORD received = 0;
    HANDLE file;
    if (wcslen(install_root) <= 3 ||
        !join_path(install_root, L".fsv-install-root", marker, ARRAYSIZE(marker)) ||
        !join_path(install_root, L"app\\启动飞行雪绒.exe", launcher, ARRAYSIZE(launcher)) ||
        !join_path(install_root, L"app\\卸载飞行雪绒.exe", uninstaller, ARRAYSIZE(uninstaller)) ||
        !join_path(install_root, L"runtime\\python311\\python.exe", runtime, ARRAYSIZE(runtime)) ||
        !ordinary_file(launcher) || !ordinary_file(uninstaller) || !ordinary_file(runtime)) {
        SetLastError(ERROR_BAD_PATHNAME);
        return FALSE;
    }
    file = CreateFileW(marker, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) {
        return FALSE;
    }
    if (!ReadFile(file, content, sizeof(content), &received, NULL) ||
        received != sizeof(content) || memcmp(content, INSTALL_MARKER, sizeof(content)) != 0) {
        CloseHandle(file);
        SetLastError(ERROR_BAD_FORMAT);
        return FALSE;
    }
    CloseHandle(file);
    return TRUE;
}

static BOOL resolve_install_root(wchar_t *output, size_t capacity) {
    DWORD length = GetModuleFileNameW(NULL, output, (DWORD)capacity);
    if (length == 0 || length >= capacity || !parent_directory(output) || !parent_directory(output)) {
        SetLastError(ERROR_BAD_PATHNAME);
        return FALSE;
    }
    return marker_is_valid(output);
}

static BOOL delete_tree(const wchar_t *directory) {
    wchar_t pattern[FSV_PATH_CAPACITY];
    WIN32_FIND_DATAW data;
    HANDLE search;
    DWORD attributes = GetFileAttributesW(directory);
    BOOL success = TRUE;
    if (attributes == INVALID_FILE_ATTRIBUTES) {
        return GetLastError() == ERROR_FILE_NOT_FOUND || GetLastError() == ERROR_PATH_NOT_FOUND;
    }
    if ((attributes & FILE_ATTRIBUTE_DIRECTORY) == 0) {
        SetLastError(ERROR_DIRECTORY);
        return FALSE;
    }
    if ((attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
        return RemoveDirectoryW(directory);
    }
    if (!join_path(directory, L"*", pattern, ARRAYSIZE(pattern))) {
        SetLastError(ERROR_BUFFER_OVERFLOW);
        return FALSE;
    }
    search = FindFirstFileW(pattern, &data);
    if (search != INVALID_HANDLE_VALUE) {
        do {
            wchar_t child[FSV_PATH_CAPACITY];
            if (wcscmp(data.cFileName, L".") == 0 || wcscmp(data.cFileName, L"..") == 0) {
                continue;
            }
            if (!join_path(directory, data.cFileName, child, ARRAYSIZE(child))) {
                SetLastError(ERROR_BUFFER_OVERFLOW);
                success = FALSE;
                break;
            }
            if ((data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0) {
                if ((data.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
                    if (!RemoveDirectoryW(child)) {
                        success = FALSE;
                        break;
                    }
                } else if (!delete_tree(child)) {
                    success = FALSE;
                    break;
                }
            } else {
                if ((data.dwFileAttributes & FILE_ATTRIBUTE_READONLY) != 0) {
                    SetFileAttributesW(child, data.dwFileAttributes & ~FILE_ATTRIBUTE_READONLY);
                }
                if (!DeleteFileW(child)) {
                    success = FALSE;
                    break;
                }
            }
        } while (FindNextFileW(search, &data));
        if (success && GetLastError() != ERROR_NO_MORE_FILES) {
            success = FALSE;
        }
        FindClose(search);
    } else if (GetLastError() != ERROR_FILE_NOT_FOUND) {
        return FALSE;
    }
    if ((attributes & FILE_ATTRIBUTE_READONLY) != 0) {
        SetFileAttributesW(directory, attributes & ~FILE_ATTRIBUTE_READONLY);
    }
    return success && (RemoveDirectoryW(directory) || GetLastError() == ERROR_PATH_NOT_FOUND);
}

static void post_status(const wchar_t *text) {
    size_t bytes = (wcslen(text) + 1) * sizeof(wchar_t);
    wchar_t *copy = (wchar_t *)HeapAlloc(GetProcessHeap(), 0, bytes);
    if (copy == NULL) {
        return;
    }
    memcpy(copy, text, bytes);
    if (!PostMessageW(g_cleanup.window, WM_FSV_UNINSTALL_STATUS, 0, (LPARAM)copy)) {
        HeapFree(GetProcessHeap(), 0, copy);
    }
}

static DWORD WINAPI cleanup_worker(void *parameter) {
    CleanupContext *context = (CleanupContext *)parameter;
    HANDLE parent = OpenProcess(SYNCHRONIZE, FALSE, context->parent_pid);
    DWORD result = ERROR_SUCCESS;
    if (parent != NULL) {
        WaitForSingleObject(parent, 30000);
        CloseHandle(parent);
    }
    post_status(L"正在删除 C:\\AemeathDeskPet 中的共享契约数据...");
    if (!delete_tree(CONTRACT_DIRECTORY)) {
        result = GetLastError();
    }
    post_status(L"正在删除飞行雪绒程序文件...");
    if (!delete_tree(context->install_root)) {
        if (result == ERROR_SUCCESS) {
            result = GetLastError();
        }
    }
    MoveFileExW(context->helper_path, NULL, MOVEFILE_DELAY_UNTIL_REBOOT);
    PostMessageW(context->window, WM_FSV_UNINSTALL_DONE, result, 0);
    return result;
}

static BOOL launch_cleanup_helper(const wchar_t *install_root) {
    wchar_t self[FSV_PATH_CAPACITY];
    wchar_t temporary_directory[FSV_PATH_CAPACITY];
    wchar_t helper[FSV_PATH_CAPACITY];
    wchar_t command[FSV_PATH_CAPACITY * 3];
    STARTUPINFOW startup;
    PROCESS_INFORMATION process;
    DWORD self_length = GetModuleFileNameW(NULL, self, ARRAYSIZE(self));
    DWORD temp_length = GetTempPathW(ARRAYSIZE(temporary_directory), temporary_directory);
    if (self_length == 0 || self_length >= ARRAYSIZE(self) ||
        temp_length == 0 || temp_length >= ARRAYSIZE(temporary_directory) ||
        FAILED(StringCchPrintfW(
            helper,
            ARRAYSIZE(helper),
            L"%lsFlyingSnowVelvet-Uninstall-%lu-%lu.exe",
            temporary_directory,
            GetCurrentProcessId(),
            GetTickCount()
        )) ||
        !CopyFileW(self, helper, TRUE) ||
        FAILED(StringCchPrintfW(
            command,
            ARRAYSIZE(command),
            L"\"%ls\" --cleanup \"%ls\" %lu",
            helper,
            install_root,
            GetCurrentProcessId()
        ))) {
        return FALSE;
    }
    ZeroMemory(&startup, sizeof(startup));
    startup.cb = sizeof(startup);
    ZeroMemory(&process, sizeof(process));
    if (!CreateProcessW(helper, command, NULL, NULL, FALSE, CREATE_UNICODE_ENVIRONMENT, NULL, temporary_directory, &startup, &process)) {
        DeleteFileW(helper);
        return FALSE;
    }
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return TRUE;
}

static COLORREF mix_color(COLORREF from, COLORREF to, int amount) {
    int inverse = 100 - amount;
    return RGB(
        (GetRValue(from) * inverse + GetRValue(to) * amount) / 100,
        (GetGValue(from) * inverse + GetGValue(to) * amount) / 100,
        (GetBValue(from) * inverse + GetBValue(to) * amount) / 100
    );
}

static void draw_button(const DRAWITEMSTRUCT *item) {
    BOOL primary = item->CtlID == IDC_UNINSTALL;
    int hover = primary ? g_hover_action : g_hover_exit;
    COLORREF base = primary ? RGB(233, 104, 157) : RGB(255, 255, 255);
    COLORREF hot = primary ? RGB(215, 83, 139) : RGB(255, 238, 246);
    COLORREF fill = mix_color(base, hot, hover);
    COLORREF border = primary ? fill : RGB(226, 142, 178);
    COLORREF text = primary ? RGB(255, 255, 255) : RGB(155, 62, 105);
    HBRUSH brush;
    HPEN pen;
    HGDIOBJ old_brush;
    HGDIOBJ old_pen;
    RECT rect = item->rcItem;
    if ((item->itemState & ODS_SELECTED) != 0) {
        fill = primary ? RGB(194, 67, 123) : RGB(250, 220, 234);
    }
    brush = CreateSolidBrush(fill);
    pen = CreatePen(PS_SOLID, 1, border);
    old_brush = SelectObject(item->hDC, brush);
    old_pen = SelectObject(item->hDC, pen);
    RoundRect(item->hDC, rect.left, rect.top, rect.right, rect.bottom, 8, 8);
    SelectObject(item->hDC, old_pen);
    SelectObject(item->hDC, old_brush);
    DeleteObject(pen);
    DeleteObject(brush);
    SetBkMode(item->hDC, TRANSPARENT);
    SetTextColor(item->hDC, text);
    {
        wchar_t label[96];
        GetWindowTextW(item->hwndItem, label, ARRAYSIZE(label));
        DrawTextW(item->hDC, label, -1, &rect, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
    }
}

static LRESULT CALLBACK button_proc(HWND button, UINT message, WPARAM wparam, LPARAM lparam, UINT_PTR id, DWORD_PTR data) {
    int *hover = (int *)data;
    (void)id;
    if (message == WM_MOUSEMOVE) {
        TRACKMOUSEEVENT tracking;
        ZeroMemory(&tracking, sizeof(tracking));
        tracking.cbSize = sizeof(tracking);
        tracking.dwFlags = TME_LEAVE;
        tracking.hwndTrack = button;
        TrackMouseEvent(&tracking);
        if (*hover != 100) {
            *hover = 100;
            InvalidateRect(button, NULL, TRUE);
        }
    } else if (message == WM_MOUSELEAVE && *hover != 0) {
        *hover = 0;
        InvalidateRect(button, NULL, TRUE);
    }
    return DefSubclassProc(button, message, wparam, lparam);
}

static void create_button(HWND *output, const wchar_t *text, int id, int x, int y, int width, int height, int *hover) {
    *output = CreateWindowExW(
        0, L"BUTTON", text, WS_CHILD | WS_VISIBLE | BS_OWNERDRAW,
        x, y, width, height, g_window, (HMENU)(INT_PTR)id, GetModuleHandleW(NULL), NULL
    );
    SendMessageW(*output, WM_SETFONT, (WPARAM)g_body_font, TRUE);
    SetWindowSubclass(*output, button_proc, (UINT_PTR)id, (DWORD_PTR)hover);
}

static BOOL create_window(void) {
    WNDCLASSW window_class;
    RECT work_area;
    int width = 680;
    int height = 390;
    int x;
    int y;
    ZeroMemory(&window_class, sizeof(window_class));
    window_class.lpfnWndProc = window_proc;
    window_class.hInstance = GetModuleHandleW(NULL);
    window_class.hCursor = LoadCursorW(NULL, IDC_ARROW);
    window_class.hbrBackground = g_canvas_brush;
    window_class.hIcon = LoadIconW(GetModuleHandleW(NULL), MAKEINTRESOURCEW(IDI_INSTALLER));
    window_class.lpszClassName = WINDOW_CLASS_NAME;
    if (RegisterClassW(&window_class) == 0 && GetLastError() != ERROR_CLASS_ALREADY_EXISTS) {
        return FALSE;
    }
    SystemParametersInfoW(SPI_GETWORKAREA, 0, &work_area, 0);
    x = work_area.left + ((work_area.right - work_area.left) - width) / 2;
    y = work_area.top + ((work_area.bottom - work_area.top) - height) / 2;
    g_window = CreateWindowExW(
        WS_EX_APPWINDOW,
        WINDOW_CLASS_NAME,
        L"卸载飞行雪绒",
        WS_CAPTION | WS_SYSMENU,
        x, y, width, height,
        NULL, NULL, GetModuleHandleW(NULL), NULL
    );
    return g_window != NULL;
}

static LRESULT CALLBACK window_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    if (message == WM_ERASEBKGND) {
        RECT rect;
        GetClientRect(window, &rect);
        FillRect((HDC)wparam, &rect, g_canvas_brush);
        return 1;
    }
    if (message == WM_CTLCOLORSTATIC) {
        SetBkMode((HDC)wparam, TRANSPARENT);
        SetTextColor((HDC)wparam, (HWND)lparam == g_title ? RGB(32, 52, 77) : RGB(58, 75, 98));
        return (LRESULT)g_canvas_brush;
    }
    if (message == WM_DRAWITEM && lparam != 0) {
        draw_button((const DRAWITEMSTRUCT *)lparam);
        return TRUE;
    }
    if (message == WM_FSV_UNINSTALL_STATUS && lparam != 0) {
        wchar_t *text = (wchar_t *)lparam;
        SetWindowTextW(g_body, text);
        HeapFree(GetProcessHeap(), 0, text);
        return 0;
    }
    if (message == WM_FSV_UNINSTALL_DONE) {
        g_cleanup_running = FALSE;
        SendMessageW(g_progress, PBM_SETMARQUEE, FALSE, 0);
        ShowWindow(g_progress, SW_HIDE);
        if (wparam == ERROR_SUCCESS) {
            SetWindowTextW(g_title, L"卸载完成");
            SetWindowTextW(g_body, L"飞行雪绒程序与 C:\\AemeathDeskPet 契约目录均已删除。");
        } else {
            wchar_t detail[320];
            SetWindowTextW(g_title, L"卸载未完成");
            StringCchPrintfW(detail, ARRAYSIZE(detail), L"部分文件无法删除（错误代码 %lu）。请关闭飞行雪绒后重试。", (DWORD)wparam);
            SetWindowTextW(g_body, detail);
        }
        ShowWindow(g_exit, SW_SHOW);
        return 0;
    }
    if (message == WM_COMMAND) {
        int id = LOWORD(wparam);
        if (id == IDC_UNINSTALL && HIWORD(wparam) == BN_CLICKED && !g_cleanup_running) {
            if (MessageBoxW(
                window,
                L"将删除安装目录以及 C:\\AemeathDeskPet 中的模型、运行时和状态数据。此操作不可撤销。",
                L"确认卸载飞行雪绒",
                MB_OKCANCEL | MB_ICONWARNING | MB_DEFBUTTON2
            ) == IDOK) {
                if (!launch_cleanup_helper(g_cleanup.install_root)) {
                    MessageBoxW(window, L"无法启动内置卸载清理程序。", L"卸载飞行雪绒", MB_OK | MB_ICONERROR);
                } else {
                    DestroyWindow(window);
                }
            }
            return 0;
        }
        if (id == IDC_EXIT && HIWORD(wparam) == BN_CLICKED) {
            DestroyWindow(window);
            return 0;
        }
    }
    if (message == WM_CLOSE) {
        if (g_cleanup_running) {
            return 0;
        }
        DestroyWindow(window);
        return 0;
    }
    if (message == WM_DESTROY) {
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(window, message, wparam, lparam);
}

static BOOL initialize_ui(void) {
    INITCOMMONCONTROLSEX controls;
    g_canvas_brush = CreateSolidBrush(RGB(255, 248, 251));
    g_title_font = CreateFontW(30, 0, 0, 0, FW_SEMIBOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, L"Microsoft YaHei UI");
    g_body_font = CreateFontW(17, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, L"Microsoft YaHei UI");
    controls.dwSize = sizeof(controls);
    controls.dwICC = ICC_PROGRESS_CLASS;
    InitCommonControlsEx(&controls);
    if (g_canvas_brush == NULL || g_title_font == NULL || g_body_font == NULL || !create_window()) {
        return FALSE;
    }
    g_cleanup.window = g_window;
    g_title = CreateWindowExW(0, L"STATIC", g_cleanup_mode ? L"正在卸载" : L"卸载飞行雪绒", WS_CHILD | WS_VISIBLE, 44, 38, 580, 48, g_window, NULL, GetModuleHandleW(NULL), NULL);
    g_body = CreateWindowExW(0, L"STATIC", g_cleanup_mode ? L"正在等待飞行雪绒退出..." : L"卸载将移除程序文件以及共享契约目录中的模型和运行时。", WS_CHILD | WS_VISIBLE | SS_LEFT, 44, 105, 580, 52, g_window, NULL, GetModuleHandleW(NULL), NULL);
    g_path = CreateWindowExW(0, L"STATIC", g_cleanup.install_root, WS_CHILD | WS_VISIBLE | SS_PATHELLIPSIS, 44, 172, 580, 32, g_window, NULL, GetModuleHandleW(NULL), NULL);
    g_progress = CreateWindowExW(0, PROGRESS_CLASSW, NULL, WS_CHILD | PBS_MARQUEE, 44, 225, 580, 18, g_window, (HMENU)(INT_PTR)IDC_PROGRESS, GetModuleHandleW(NULL), NULL);
    create_button(&g_action, L"卸载飞行雪绒", IDC_UNINSTALL, 438, 292, 186, 42, &g_hover_action);
    create_button(&g_exit, L"退出", IDC_EXIT, 504, 292, 120, 42, &g_hover_exit);
    SendMessageW(g_title, WM_SETFONT, (WPARAM)g_title_font, TRUE);
    SendMessageW(g_body, WM_SETFONT, (WPARAM)g_body_font, TRUE);
    SendMessageW(g_path, WM_SETFONT, (WPARAM)g_body_font, TRUE);
    SendMessageW(g_progress, PBM_SETBARCOLOR, 0, RGB(233, 104, 157));
    SendMessageW(g_progress, PBM_SETBKCOLOR, 0, RGB(252, 225, 237));
    if (g_cleanup_mode) {
        ShowWindow(g_action, SW_HIDE);
        ShowWindow(g_exit, SW_HIDE);
        ShowWindow(g_progress, SW_SHOW);
        SendMessageW(g_progress, PBM_SETMARQUEE, TRUE, 28);
    } else {
        ShowWindow(g_progress, SW_HIDE);
        ShowWindow(g_exit, SW_HIDE);
    }
    ShowWindow(g_window, SW_SHOWNORMAL);
    UpdateWindow(g_window);
    return TRUE;
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR command_line, int show) {
    int argc = 0;
    wchar_t **argv;
    MSG message;
    HANDLE worker = NULL;
    (void)instance;
    (void)previous;
    (void)command_line;
    (void)show;
    ZeroMemory(&g_cleanup, sizeof(g_cleanup));
    argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (argv != NULL && argc == 4 && wcscmp(argv[1], L"--cleanup") == 0) {
        wchar_t *end = NULL;
        unsigned long parsed_pid;
        g_cleanup_mode = TRUE;
        if (FAILED(StringCchCopyW(g_cleanup.install_root, ARRAYSIZE(g_cleanup.install_root), argv[2])) ||
            !marker_is_valid(g_cleanup.install_root)) {
            LocalFree(argv);
            MessageBoxW(NULL, L"拒绝清理：安装目录标记无效。", L"卸载飞行雪绒", MB_OK | MB_ICONERROR);
            return 1;
        }
        parsed_pid = wcstoul(argv[3], &end, 10);
        if (end == argv[3] || *end != L'\0' || parsed_pid == 0) {
            LocalFree(argv);
            return 1;
        }
        g_cleanup.parent_pid = (DWORD)parsed_pid;
        if (GetModuleFileNameW(NULL, g_cleanup.helper_path, ARRAYSIZE(g_cleanup.helper_path)) == 0) {
            LocalFree(argv);
            return 1;
        }
    } else if (!resolve_install_root(g_cleanup.install_root, ARRAYSIZE(g_cleanup.install_root))) {
        if (argv != NULL) {
            LocalFree(argv);
        }
        MessageBoxW(NULL, L"安装目录不完整，卸载器已拒绝执行。", L"卸载飞行雪绒", MB_OK | MB_ICONERROR);
        return 1;
    }
    if (argv != NULL) {
        LocalFree(argv);
    }
    if (!initialize_ui()) {
        MessageBoxW(NULL, L"无法创建卸载器窗口。", PRODUCT_NAME, MB_OK | MB_ICONERROR);
        return 1;
    }
    if (g_cleanup_mode) {
        g_cleanup_running = TRUE;
        worker = CreateThread(NULL, 0, cleanup_worker, &g_cleanup, 0, NULL);
        if (worker == NULL) {
            g_cleanup_running = FALSE;
            PostMessageW(g_window, WM_FSV_UNINSTALL_DONE, GetLastError(), 0);
        }
    }
    while (GetMessageW(&message, NULL, 0, 0) > 0) {
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
    if (worker != NULL) {
        WaitForSingleObject(worker, 5000);
        CloseHandle(worker);
    }
    if (g_title_font != NULL) {
        DeleteObject(g_title_font);
    }
    if (g_body_font != NULL) {
        DeleteObject(g_body_font);
    }
    if (g_canvas_brush != NULL) {
        DeleteObject(g_canvas_brush);
    }
    return 0;
}
