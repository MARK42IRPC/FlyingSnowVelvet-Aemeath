#define UNICODE
#define _UNICODE
#define WIN32_LEAN_AND_MEAN

#include <windows.h>
#include <commctrl.h>
#include <knownfolders.h>
#include <shellapi.h>
#include <shlobj.h>
#include <strsafe.h>
#include <string.h>
#include <uxtheme.h>
#include <wchar.h>

#include "resource.h"
#include "installer_theme.h"

#pragma comment(lib, "comctl32.lib")
#pragma comment(lib, "gdi32.lib")
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "shell32.lib")
#pragma comment(lib, "uxtheme.lib")
#pragma comment(lib, "user32.lib")

#define FSV_PATH_CAPACITY 1024
#define WM_FSV_UNINSTALL_STATUS (WM_APP + 1)
#define WM_FSV_UNINSTALL_DONE (WM_APP + 2)
#define IDC_UNINSTALL 2001
#define IDC_EXIT 2002
#define IDC_PROGRESS 2003
#define IDC_DELETE_VOICE 2004
#define IDC_DELETE_DATA 2005
#define FSV_CLIENT_WIDTH 880
#define FSV_CLIENT_HEIGHT 568
#define FSV_CHECKBOX_SIZE 20

static const wchar_t WINDOW_CLASS_NAME[] = L"FlyingSnowVelvetUninstaller";
static const wchar_t PRODUCT_NAME[] = L"飞行雪绒";
static const wchar_t SHARED_ROOT_DIRECTORY[] = L"C:\\AemeathDeskPet";
static const wchar_t OFFICE_WORKSPACE_NAME[] = L"飞行雪绒办公区";
static const BYTE INSTALL_MARKER[] = "FSV-OFFLINE-PAYLOAD-2\n";

typedef struct CleanupContext {
    wchar_t install_root[FSV_PATH_CAPACITY];
    wchar_t helper_path[FSV_PATH_CAPACITY];
    DWORD parent_pid;
    HWND window;
    BOOL delete_voice_package;
    BOOL delete_user_data;
} CleanupContext;

static CleanupContext g_cleanup;
static HWND g_window;
static HWND g_title;
static HWND g_body;
static HWND g_path;
static HWND g_voice_check;
static HWND g_voice_hint;
static HWND g_data_check;
static HWND g_data_hint;
static HWND g_action;
static HWND g_exit;
static HWND g_progress;
static HFONT g_title_font;
static HFONT g_body_font;
static HFONT g_meta_font;
static HANDLE g_embedded_font;
static DWORD g_embedded_font_count;
static HBRUSH g_canvas_brush;
static HBRUSH g_surface_brush;
static HBRUSH g_raised_brush;
static UINT g_dpi = 96;
static BOOL g_cleanup_mode;
static BOOL g_cleanup_running;
static int g_hover_action;
static int g_hover_exit;
static int g_hover_voice;
static int g_hover_data;

static LRESULT CALLBACK window_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam);
static LRESULT CALLBACK button_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam, UINT_PTR id, DWORD_PTR data);
static LRESULT CALLBACK checkbox_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam, UINT_PTR id, DWORD_PTR data);

static int ui_px(int value) {
    return MulDiv(value, (int)g_dpi, 96);
}

static RECT ui_rect(int x, int y, int width, int height) {
    RECT result = {ui_px(x), ui_px(y), ui_px(x + width), ui_px(y + height)};
    return result;
}

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

/* TRUE when ``prefix`` is ``path`` itself or one of its ancestors. */
static BOOL path_is_prefix_of(const wchar_t *prefix, const wchar_t *path) {
    size_t length = wcslen(prefix);
    if (length == 0 || _wcsnicmp(prefix, path, length) != 0) {
        return FALSE;
    }
    return path[length] == L'\0' || path[length] == L'\\' || path[length] == L'/';
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

static void record_error(DWORD *error, DWORD code) {
    if (error != NULL && *error == ERROR_SUCCESS && code != ERROR_SUCCESS) {
        *error = code;
    }
}

/* Optional cleanup must never remove the installation directory itself or one
   of its ancestors: the program tree is deleted once, after this step. */
static void delete_optional_tree(const wchar_t *path, const wchar_t *install_root, DWORD *error) {
    DWORD code;
    if (path_is_prefix_of(path, install_root)) {
        return;
    }
    if (delete_tree(path)) {
        return;
    }
    code = GetLastError();
    if (code == ERROR_FILE_NOT_FOUND || code == ERROR_PATH_NOT_FOUND) {
        return;
    }
    record_error(error, code);
}

static void delete_optional_file(const wchar_t *path, DWORD *error) {
    DWORD code;
    if (!DeleteFileW(path)) {
        code = GetLastError();
        if (code != ERROR_FILE_NOT_FOUND && code != ERROR_PATH_NOT_FOUND) {
            record_error(error, code);
        }
    }
}

static BOOL desktop_office_workspace_path(wchar_t *output, size_t capacity) {
    PWSTR desktop = NULL;
    BOOL success = FALSE;
    if (SUCCEEDED(SHGetKnownFolderPath(&FOLDERID_Desktop, 0, NULL, &desktop)) && desktop != NULL) {
        success = SUCCEEDED(StringCchPrintfW(output, capacity, L"%ls\\%ls", desktop, OFFICE_WORKSPACE_NAME));
    }
    if (desktop != NULL) {
        CoTaskMemFree(desktop);
    }
    return success;
}

static void delete_voice_package(const wchar_t *install_root, DWORD *error) {
    wchar_t path[FSV_PATH_CAPACITY];
    if (join_path(SHARED_ROOT_DIRECTORY, L"voice", path, ARRAYSIZE(path))) {
        delete_optional_tree(path, install_root, error);
    }
    if (join_path(SHARED_ROOT_DIRECTORY, L"models\\vosk", path, ARRAYSIZE(path))) {
        delete_optional_tree(path, install_root, error);
    }
    if (join_path(SHARED_ROOT_DIRECTORY, L"start_gsvmove.bat", path, ARRAYSIZE(path))) {
        delete_optional_file(path, error);
    }
}

static void delete_user_data(const wchar_t *install_root, DWORD *error) {
    static const wchar_t *directories[] = {
        L"user",
        L"config",
        L"cache",
        L"logs",
        L"resc\\user",
    };
    wchar_t path[FSV_PATH_CAPACITY];
    size_t index;
    for (index = 0; index < ARRAYSIZE(directories); ++index) {
        if (join_path(SHARED_ROOT_DIRECTORY, directories[index], path, ARRAYSIZE(path))) {
            delete_optional_tree(path, install_root, error);
        }
    }
    if (desktop_office_workspace_path(path, ARRAYSIZE(path))) {
        delete_optional_tree(path, install_root, error);
    }
}

static void prune_empty_shared_root(void) {
    wchar_t path[FSV_PATH_CAPACITY];
    if (join_path(SHARED_ROOT_DIRECTORY, L"models", path, ARRAYSIZE(path))) {
        RemoveDirectoryW(path);
    }
    if (join_path(SHARED_ROOT_DIRECTORY, L"resc", path, ARRAYSIZE(path))) {
        RemoveDirectoryW(path);
    }
    RemoveDirectoryW(SHARED_ROOT_DIRECTORY);
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
    if (context->delete_voice_package) {
        post_status(L"正在删除语音包与语音推理运行时...");
        delete_voice_package(context->install_root, &result);
    }
    if (context->delete_user_data) {
        post_status(L"正在删除飞行雪绒的记忆、用户配置与 APikey...");
        delete_user_data(context->install_root, &result);
    }
    post_status(L"正在删除飞行雪绒程序文件...");
    if (!delete_tree(context->install_root)) {
        if (result == ERROR_SUCCESS) {
            result = GetLastError();
        }
    }
    prune_empty_shared_root();
    MoveFileExW(context->helper_path, NULL, MOVEFILE_DELAY_UNTIL_REBOOT);
    PostMessageW(context->window, WM_FSV_UNINSTALL_DONE, result, 0);
    return result;
}

static BOOL launch_cleanup_helper(const wchar_t *install_root, BOOL delete_voice_package, BOOL delete_user_data) {
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
            L"\"%ls\" --cleanup \"%ls\" %lu%ls%ls",
            helper,
            install_root,
            GetCurrentProcessId(),
            delete_voice_package ? L" --delete-voice-package" : L"",
            delete_user_data ? L" --delete-user-data" : L""
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

static BOOL load_embedded_harmony_font(void) {
    HINSTANCE instance = GetModuleHandleW(NULL);
    HRSRC resource = FindResourceW(instance, MAKEINTRESOURCEW(IDR_HARMONY_FONT), RT_RCDATA);
    HGLOBAL loaded;
    DWORD size;
    const void *data;
    if (resource == NULL) {
        return FALSE;
    }
    size = SizeofResource(instance, resource);
    loaded = LoadResource(instance, resource);
    if (size == 0 || loaded == NULL) {
        return FALSE;
    }
    data = LockResource(loaded);
    if (data == NULL) {
        return FALSE;
    }
    g_embedded_font = AddFontMemResourceEx((void *)data, size, NULL, &g_embedded_font_count);
    return g_embedded_font != NULL;
}

static COLORREF mix_color(COLORREF from, COLORREF to, int amount) {
    int inverse = 100 - amount;
    return RGB(
        (GetRValue(from) * inverse + GetRValue(to) * amount) / 100,
        (GetGValue(from) * inverse + GetGValue(to) * amount) / 100,
        (GetBValue(from) * inverse + GetBValue(to) * amount) / 100
    );
}

static void fill_color_rect(HDC dc, RECT rect, COLORREF color) {
    HBRUSH brush = CreateSolidBrush(color);
    FillRect(dc, &rect, brush);
    DeleteObject(brush);
}

static void draw_round_panel(HDC dc, const RECT *bounds, COLORREF fill_color, COLORREF border_color, int radius) {
    HBRUSH fill_brush = CreateSolidBrush(fill_color);
    HPEN border_pen = CreatePen(PS_SOLID, 1, border_color);
    HGDIOBJ old_brush = SelectObject(dc, fill_brush);
    HGDIOBJ old_pen = SelectObject(dc, border_pen);
    RoundRect(dc, bounds->left, bounds->top, bounds->right, bounds->bottom, radius, radius);
    SelectObject(dc, old_pen);
    SelectObject(dc, old_brush);
    DeleteObject(border_pen);
    DeleteObject(fill_brush);
}

static void draw_text_block(HDC dc, HFONT font, COLORREF text_color, const wchar_t *text, RECT bounds, UINT format) {
    HGDIOBJ old_font = SelectObject(dc, font);
    SetBkMode(dc, TRANSPARENT);
    SetTextColor(dc, text_color);
    DrawTextW(dc, text, -1, &bounds, format | DT_NOPREFIX);
    SelectObject(dc, old_font);
}
static void draw_button(const DRAWITEMSTRUCT *item) {
    BOOL primary = item->CtlID == IDC_UNINSTALL;
    int hover = primary ? g_hover_action : g_hover_exit;
    COLORREF base = primary ? FSV_COLOR_PINK : FSV_COLOR_SURFACE_RAISED;
    COLORREF hot = primary ? FSV_COLOR_PINK_HOVER : FSV_COLOR_SURFACE_HOVER;
    COLORREF fill = mix_color(base, hot, hover);
    COLORREF border = primary ? fill : (hover > 0 ? FSV_COLOR_CYAN : FSV_COLOR_BORDER);
    COLORREF text = primary ? FSV_COLOR_CANVAS : FSV_COLOR_TEXT;
    HBRUSH brush;
    HPEN pen;
    HGDIOBJ old_brush;
    HGDIOBJ old_pen;
    RECT rect = item->rcItem;
    if ((item->itemState & ODS_SELECTED) != 0) {
        fill = primary ? FSV_COLOR_CYAN : FSV_COLOR_BORDER_STRONG;
    }
    FillRect(item->hDC, &rect, g_canvas_brush);
    brush = CreateSolidBrush(fill);
    pen = CreatePen(PS_SOLID, 1, border);
    old_brush = SelectObject(item->hDC, brush);
    old_pen = SelectObject(item->hDC, pen);
    RoundRect(item->hDC, rect.left, rect.top, rect.right, rect.bottom, ui_px(8), ui_px(8));
    SelectObject(item->hDC, old_pen);
    SelectObject(item->hDC, old_brush);
    DeleteObject(pen);
    DeleteObject(brush);
    SetBkMode(item->hDC, TRANSPARENT);
    SetTextColor(item->hDC, text);
    {
        wchar_t label[128];
        HGDIOBJ old_font = SelectObject(item->hDC, g_body_font);
        GetWindowTextW(item->hwndItem, label, ARRAYSIZE(label));
        DrawTextW(item->hDC, label, -1, &rect, DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX);
        SelectObject(item->hDC, old_font);
    }
}

static LRESULT CALLBACK button_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam, UINT_PTR id, DWORD_PTR data) {
    int *hover = (int *)data;
    (void)id;
    if (message == WM_MOUSEMOVE && hover != NULL) {
        TRACKMOUSEEVENT tracking;
        ZeroMemory(&tracking, sizeof(tracking));
        tracking.cbSize = sizeof(tracking);
        tracking.dwFlags = TME_LEAVE;
        tracking.hwndTrack = window;
        TrackMouseEvent(&tracking);
        if (*hover != 100) {
            *hover = 100;
            InvalidateRect(window, NULL, TRUE);
        }
    } else if (message == WM_MOUSELEAVE && hover != NULL) {
        if (*hover != 0) {
            *hover = 0;
            InvalidateRect(window, NULL, TRUE);
        }
    } else if (message == WM_NCDESTROY) {
        RemoveWindowSubclass(window, button_proc, id);
    }
    return DefSubclassProc(window, message, wparam, lparam);
}

static void draw_check_mark(HDC dc, const RECT *box) {
    POINT points[3];
    HPEN pen = CreatePen(PS_SOLID, 2, FSV_COLOR_SURFACE);
    HGDIOBJ old_pen = SelectObject(dc, pen);
    points[0].x = box->left + 5;
    points[0].y = box->top + 10;
    points[1].x = box->left + 9;
    points[1].y = box->top + 14;
    points[2].x = box->left + 16;
    points[2].y = box->top + 5;
    Polyline(dc, points, ARRAYSIZE(points));
    SelectObject(dc, old_pen);
    DeleteObject(pen);
}

static void draw_checkbox(HWND window, HDC dc, int hover) {
    RECT bounds;
    RECT box;
    RECT text_bounds;
    int size = ui_px(FSV_CHECKBOX_SIZE);
    int checked = (int)SendMessageW(window, BM_GETCHECK, 0, 0);
    BOOL enabled = IsWindowEnabled(window);
    COLORREF border;
    GetClientRect(window, &bounds);
    FillRect(dc, &bounds, g_surface_brush);
    box.left = bounds.left + 2;
    box.top = bounds.top + (bounds.bottom - bounds.top - size) / 2;
    box.right = box.left + size;
    box.bottom = box.top + size;
    if (checked == BST_CHECKED) {
        draw_round_panel(dc, &box, FSV_COLOR_PINK, FSV_COLOR_PINK, ui_px(6));
        draw_check_mark(dc, &box);
    } else {
        border = !enabled ? FSV_COLOR_BORDER : hover > 0 ? FSV_COLOR_PINK : FSV_COLOR_BORDER_STRONG;
        draw_round_panel(dc, &box, FSV_COLOR_SURFACE, border, ui_px(6));
    }
    if (GetFocus() == window) {
        RECT ring = box;
        InflateRect(&ring, -3, -3);
        DrawFocusRect(dc, &ring);
    }
    text_bounds = bounds;
    text_bounds.left = box.right + ui_px(10);
    SetBkMode(dc, TRANSPARENT);
    SetTextColor(dc, enabled ? FSV_COLOR_TEXT : FSV_COLOR_TEXT_DIM);
    {
        wchar_t label[256];
        HGDIOBJ old_font = SelectObject(dc, g_body_font);
        GetWindowTextW(window, label, ARRAYSIZE(label));
        DrawTextW(dc, label, -1, &text_bounds, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX);
        SelectObject(dc, old_font);
    }
}

static LRESULT CALLBACK checkbox_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam, UINT_PTR id, DWORD_PTR data) {
    int *hover = (int *)data;
    if (message == WM_PAINT) {
        PAINTSTRUCT paint;
        HDC dc = BeginPaint(window, &paint);
        draw_checkbox(window, dc, hover != NULL ? *hover : 0);
        EndPaint(window, &paint);
        return 0;
    }
    if (message == WM_ERASEBKGND) {
        return 1;
    }
    if (message == WM_MOUSEMOVE && hover != NULL) {
        TRACKMOUSEEVENT tracking;
        ZeroMemory(&tracking, sizeof(tracking));
        tracking.cbSize = sizeof(tracking);
        tracking.dwFlags = TME_LEAVE;
        tracking.hwndTrack = window;
        TrackMouseEvent(&tracking);
        if (*hover != 100) {
            *hover = 100;
            InvalidateRect(window, NULL, FALSE);
        }
    } else if (message == WM_MOUSELEAVE && hover != NULL) {
        if (*hover != 0) {
            *hover = 0;
            InvalidateRect(window, NULL, FALSE);
        }
    } else if (message == WM_SETFOCUS || message == WM_KILLFOCUS || message == WM_ENABLE) {
        InvalidateRect(window, NULL, FALSE);
    } else if (message == WM_NCDESTROY) {
        RemoveWindowSubclass(window, checkbox_proc, id);
    }
    return DefSubclassProc(window, message, wparam, lparam);
}

static HWND create_label(const wchar_t *text, int x, int y, int width, int height, HFONT font, DWORD style) {
    HWND label = CreateWindowExW(
        0, L"STATIC", text, WS_CHILD | WS_VISIBLE | style,
        ui_px(x), ui_px(y), ui_px(width), ui_px(height), g_window, NULL, GetModuleHandleW(NULL), NULL
    );
    SendMessageW(label, WM_SETFONT, (WPARAM)font, TRUE);
    return label;
}

static void create_button(HWND *output, const wchar_t *text, int id, int *hover) {
    *output = CreateWindowExW(
        0, L"BUTTON", text, WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_OWNERDRAW,
        0, 0, 1, 1, g_window, (HMENU)(INT_PTR)id, GetModuleHandleW(NULL), NULL
    );
    SendMessageW(*output, WM_SETFONT, (WPARAM)g_body_font, TRUE);
    SetWindowSubclass(*output, button_proc, (UINT_PTR)id, (DWORD_PTR)hover);
}

static void create_checkbox(HWND *output, const wchar_t *text, int id, int *hover) {
    *output = CreateWindowExW(
        0, L"BUTTON", text, WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_AUTOCHECKBOX,
        0, 0, 1, 1, g_window, (HMENU)(INT_PTR)id, GetModuleHandleW(NULL), NULL
    );
    SendMessageW(*output, WM_SETFONT, (WPARAM)g_body_font, TRUE);
    SetWindowSubclass(*output, checkbox_proc, (UINT_PTR)id, (DWORD_PTR)hover);
}

static BOOL create_window(void) {
    WNDCLASSW window_class;
    RECT work_area;
    RECT bounds = ui_rect(0, 0, FSV_CLIENT_WIDTH, FSV_CLIENT_HEIGHT);
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
    AdjustWindowRectExForDpi(&bounds, WS_CAPTION | WS_SYSMENU, FALSE, WS_EX_APPWINDOW | WS_EX_CONTROLPARENT, g_dpi);
    SystemParametersInfoW(SPI_GETWORKAREA, 0, &work_area, 0);
    x = work_area.left + ((work_area.right - work_area.left) - (bounds.right - bounds.left)) / 2;
    y = work_area.top + ((work_area.bottom - work_area.top) - (bounds.bottom - bounds.top)) / 2;
    g_window = CreateWindowExW(
        WS_EX_APPWINDOW | WS_EX_CONTROLPARENT,
        WINDOW_CLASS_NAME,
        L"卸载飞行雪绒",
        WS_CAPTION | WS_SYSMENU,
        x, y, bounds.right - bounds.left, bounds.bottom - bounds.top,
        NULL, NULL, GetModuleHandleW(NULL), NULL
    );
    return g_window != NULL;
}

static void place_control(HWND control, int x, int y, int width, int height, HFONT font) {
    if (control == NULL) {
        return;
    }
    SendMessageW(control, WM_SETFONT, (WPARAM)font, TRUE);
    MoveWindow(control, ui_px(x), ui_px(y), ui_px(width), ui_px(height), TRUE);
}

static void apply_rounded_progress_region(HWND control, int radius) {
    RECT bounds;
    HRGN region;
    if (control == NULL || radius <= 0) {
        return;
    }
    GetClientRect(control, &bounds);
    if (bounds.right <= 0 || bounds.bottom <= 0) {
        return;
    }
    region = CreateRoundRectRgn(0, 0, bounds.right + 1, bounds.bottom + 1, radius, radius);
    if (region == NULL) {
        return;
    }
    if (SetWindowRgn(control, region, TRUE) == 0) {
        DeleteObject(region);
    }
}

static BOOL layout_controls(void) {
    HFONT old_title = g_title_font;
    HFONT old_body = g_body_font;
    HFONT old_meta = g_meta_font;
    g_title_font = CreateFontW(-ui_px(26), 0, 0, 0, FW_BOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, L"HarmonyOS Sans SC");
    g_body_font = CreateFontW(-ui_px(15), 0, 0, 0, FW_BOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, L"HarmonyOS Sans SC");
    g_meta_font = CreateFontW(-ui_px(12), 0, 0, 0, FW_BOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, L"HarmonyOS Sans SC");
    if (g_title_font == NULL || g_body_font == NULL || g_meta_font == NULL) {
        return FALSE;
    }
    place_control(g_title, 40, 138, 800, 40, g_title_font);
    place_control(g_body, 40, 188, 800, 64, g_body_font);
    place_control(g_path, 56, 274, 768, 28, g_body_font);
    place_control(g_voice_check, 40, 336, 800, 26, g_body_font);
    place_control(g_voice_hint, 72, 364, 768, 20, g_meta_font);
    place_control(g_data_check, 40, 400, 800, 26, g_body_font);
    place_control(g_data_hint, 72, 428, 768, 20, g_meta_font);
    place_control(g_progress, 40, 462, 800, 24, g_body_font);
    place_control(g_action, 680, 510, 160, 40, g_body_font);
    place_control(g_exit, 528, 510, 140, 40, g_body_font);
    apply_rounded_progress_region(g_progress, ui_px(24));
    if (old_title != NULL) DeleteObject(old_title);
    if (old_body != NULL) DeleteObject(old_body);
    if (old_meta != NULL) DeleteObject(old_meta);
    return TRUE;
}

static void apply_mode_visibility(void) {
    int show_options = g_cleanup_mode ? SW_HIDE : SW_SHOW;
    ShowWindow(g_title, SW_SHOW);
    ShowWindow(g_body, SW_SHOW);
    ShowWindow(g_path, show_options);
    ShowWindow(g_voice_check, show_options);
    ShowWindow(g_voice_hint, show_options);
    ShowWindow(g_data_check, show_options);
    ShowWindow(g_data_hint, show_options);
    if (g_cleanup_mode) {
        ShowWindow(g_progress, SW_SHOW);
        ShowWindow(g_action, SW_HIDE);
        ShowWindow(g_exit, SW_HIDE);
    } else {
        ShowWindow(g_progress, SW_HIDE);
        ShowWindow(g_action, SW_SHOW);
        ShowWindow(g_exit, SW_SHOW);
    }
}

static void draw_page_background(HDC dc) {
    RECT client;
    GetClientRect(g_window, &client);
    FillRect(dc, &client, g_canvas_brush);
    fill_color_rect(dc, ui_rect(0, 96, FSV_CLIENT_WIDTH, 398), FSV_COLOR_SURFACE);
    fill_color_rect(dc, ui_rect(0, 96, FSV_CLIENT_WIDTH, 1), FSV_COLOR_BORDER);
    fill_color_rect(dc, ui_rect(0, 494, FSV_CLIENT_WIDTH, 1), FSV_COLOR_BORDER);
    fill_color_rect(dc, ui_rect(32, 29, 3, 44), FSV_COLOR_PINK);
    fill_color_rect(dc, ui_rect(35, 29, 1, 44), FSV_COLOR_CYAN);
    draw_text_block(dc, g_title_font, FSV_COLOR_TEXT, PRODUCT_NAME, ui_rect(116, 24, 360, 38), DT_LEFT | DT_VCENTER | DT_SINGLELINE);
    draw_text_block(dc, g_meta_font, FSV_COLOR_TEXT_DIM, L"卸载程序  /  UNINSTALLER", ui_rect(118, 64, 400, 18), DT_LEFT | DT_SINGLELINE);
    draw_text_block(dc, g_meta_font, FSV_COLOR_TEXT_DIM, L"WINDOWS / UNINSTALL", ui_rect(480, 46, 360, 20), DT_RIGHT | DT_VCENTER | DT_SINGLELINE);
    draw_text_block(dc, g_meta_font, FSV_COLOR_TEXT_DIM,
        g_cleanup_mode ? L"正在卸载，请保持窗口打开" : L"FLYING SNOW VELVET",
        ui_rect(40, 521, 460, 20), DT_LEFT | DT_SINGLELINE);
    if (g_cleanup_mode) {
        return;
    }
    draw_text_block(dc, g_meta_font, FSV_COLOR_TEXT_DIM, L"安装目录", ui_rect(40, 240, 400, 20), DT_LEFT | DT_SINGLELINE);
    {
        RECT field = ui_rect(40, 262, 800, 48);
        draw_round_panel(dc, &field, FSV_COLOR_SURFACE_RAISED, FSV_COLOR_BORDER, ui_px(8));
    }
}

static void show_uninstall_result(DWORD error) {
    wchar_t detail[512];
    if (error == ERROR_SUCCESS) {
        SetWindowTextW(g_title, L"卸载完成");
        StringCchPrintfW(
            detail,
            ARRAYSIZE(detail),
            L"飞行雪绒程序文件已删除。\r\n语音包：%ls   用户数据：%ls",
            g_cleanup.delete_voice_package ? L"已删除" : L"已保留",
            g_cleanup.delete_user_data ? L"已删除" : L"已保留"
        );
        SetWindowTextW(g_body, detail);
    } else {
        SetWindowTextW(g_title, L"卸载未完成");
        StringCchPrintfW(detail, ARRAYSIZE(detail), L"部分文件无法删除（错误代码 %lu）。\r\n请关闭飞行雪绒后重试。", (DWORD)error);
        SetWindowTextW(g_body, detail);
    }
    ShowWindow(g_exit, SW_SHOW);
}

static LRESULT CALLBACK window_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    if (message == WM_ERASEBKGND) {
        RECT rect;
        GetClientRect(window, &rect);
        FillRect((HDC)wparam, &rect, g_canvas_brush);
        return 1;
    }
    if (message == WM_PAINT) {
        PAINTSTRUCT paint;
        HDC dc = BeginPaint(window, &paint);
        draw_page_background(dc);
        EndPaint(window, &paint);
        return 0;
    }
    if (message == WM_PRINTCLIENT) {
        draw_page_background((HDC)wparam);
        return 0;
    }
    if (message == WM_CTLCOLORSTATIC || message == WM_CTLCOLORBTN) {
        SetBkMode((HDC)wparam, TRANSPARENT);
        SetTextColor((HDC)wparam, FSV_COLOR_TEXT);
        return (LRESULT)g_surface_brush;
    }
    if (message == WM_DRAWITEM && lparam != 0) {
        draw_button((const DRAWITEMSTRUCT *)lparam);
        return TRUE;
    }
    if (message == WM_DPICHANGED && lparam != 0) {
        const RECT *suggested = (const RECT *)lparam;
        g_dpi = HIWORD(wparam);
        SetWindowPos(window, NULL, suggested->left, suggested->top,
                     suggested->right - suggested->left, suggested->bottom - suggested->top,
                     SWP_NOZORDER | SWP_NOACTIVATE);
        layout_controls();
        InvalidateRect(window, NULL, TRUE);
        return 0;
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
        show_uninstall_result((DWORD)wparam);
        InvalidateRect(window, NULL, TRUE);
        return 0;
    }
    if (message == WM_COMMAND) {
        int id = LOWORD(wparam);
        if (id == IDC_UNINSTALL && HIWORD(wparam) == BN_CLICKED && !g_cleanup_running) {
            BOOL delete_voice = SendMessageW(g_voice_check, BM_GETCHECK, 0, 0) == BST_CHECKED;
            BOOL delete_data = SendMessageW(g_data_check, BM_GETCHECK, 0, 0) == BST_CHECKED;
            wchar_t confirm[512];
            StringCchPrintfW(
                confirm,
                ARRAYSIZE(confirm),
                L"将删除飞行雪绒的程序文件。\r\n%ls%ls\r\n此操作不可撤销。",
                delete_voice ? L"同时删除语音包与语音推理运行时。\r\n" : L"语音包将保留。\r\n",
                delete_data ? L"同时删除记忆、用户配置、APikey 与桌面办公区。" : L"用户数据将保留。"
            );
            if (MessageBoxW(window, confirm, L"确认卸载飞行雪绒", MB_OKCANCEL | MB_ICONWARNING | MB_DEFBUTTON2) == IDOK) {
                if (!launch_cleanup_helper(g_cleanup.install_root, delete_voice, delete_data)) {
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
    if (!load_embedded_harmony_font()) {
        OutputDebugStringW(L"uninstaller: embedded font unavailable, falling back to system font\n");
    }
    g_canvas_brush = CreateSolidBrush(FSV_COLOR_CANVAS);
    g_surface_brush = CreateSolidBrush(FSV_COLOR_SURFACE);
    g_raised_brush = CreateSolidBrush(FSV_COLOR_SURFACE_RAISED);
    controls.dwSize = sizeof(controls);
    controls.dwICC = ICC_PROGRESS_CLASS;
    InitCommonControlsEx(&controls);
    if (g_canvas_brush == NULL || g_surface_brush == NULL || g_raised_brush == NULL || !create_window()) {
        return FALSE;
    }
    g_cleanup.window = g_window;
    g_title = create_label(g_cleanup_mode ? L"正在卸载" : L"卸载飞行雪绒", 40, 138, 800, 40, g_title_font, SS_LEFT);
    g_body = create_label(
        g_cleanup_mode ? L"正在等待飞行雪绒退出..." : L"卸载将删除程序文件（app 与 runtime）。\r\n用户数据与语音包默认保留，可勾选下方选项一并删除。",
        40, 188, 800, 64, g_body_font, SS_LEFT
    );
    g_path = create_label(g_cleanup.install_root, 56, 274, 768, 28, g_body_font, SS_PATHELLIPSIS);
    g_voice_check = CreateWindowExW(0, L"BUTTON", L"删除语音包", WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_AUTOCHECKBOX, 0, 0, 1, 1, g_window, (HMENU)(INT_PTR)IDC_DELETE_VOICE, GetModuleHandleW(NULL), NULL);
    g_voice_hint = create_label(L"同时删除 C:\\AemeathDeskPet\\voice 下的语音包、推理运行时与语音识别模型。", 72, 364, 768, 20, g_meta_font, SS_LEFT);
    g_data_check = CreateWindowExW(0, L"BUTTON", L"删除用户数据", WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_AUTOCHECKBOX, 0, 0, 1, 1, g_window, (HMENU)(INT_PTR)IDC_DELETE_DATA, GetModuleHandleW(NULL), NULL);
    g_data_hint = create_label(L"包含飞行雪绒的记忆、用户配置、Apikey、桌面办公区等；删除后无法恢复。", 72, 428, 768, 20, g_meta_font, SS_LEFT);
    create_button(&g_action, L"卸载飞行雪绒", IDC_UNINSTALL, &g_hover_action);
    create_button(&g_exit, L"退出", IDC_EXIT, &g_hover_exit);
    g_progress = CreateWindowExW(0, PROGRESS_CLASSW, NULL, WS_CHILD | PBS_MARQUEE, 0, 0, 1, 1, g_window, (HMENU)(INT_PTR)IDC_PROGRESS, GetModuleHandleW(NULL), NULL);
    if (g_title == NULL || g_body == NULL || g_path == NULL || g_voice_check == NULL || g_voice_hint == NULL ||
        g_data_check == NULL || g_data_hint == NULL || g_action == NULL || g_exit == NULL || g_progress == NULL) {
        return FALSE;
    }
    SetWindowTheme(g_progress, L"", L"");
    SendMessageW(g_progress, PBM_SETBARCOLOR, 0, FSV_COLOR_PINK);
    SendMessageW(g_progress, PBM_SETBKCOLOR, 0, FSV_COLOR_SURFACE_RAISED);
    SetWindowSubclass(g_voice_check, checkbox_proc, (UINT_PTR)IDC_DELETE_VOICE, (DWORD_PTR)&g_hover_voice);
    SetWindowSubclass(g_data_check, checkbox_proc, (UINT_PTR)IDC_DELETE_DATA, (DWORD_PTR)&g_hover_data);
    if (!layout_controls()) {
        return FALSE;
    }
    apply_mode_visibility();
    return TRUE;
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR command_line, int show) {
    INITCOMMONCONTROLSEX controls;
    int argc = 0;
    wchar_t **argv;
    MSG message;
    HANDLE worker = NULL;
    HRESULT com_result;
    BOOL delete_voice_package = FALSE;
    BOOL delete_user_data = FALSE;
    (void)instance;
    (void)previous;
    (void)command_line;
    (void)show;
    ZeroMemory(&g_cleanup, sizeof(g_cleanup));
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    g_dpi = GetDpiForSystem();
    com_result = CoInitializeEx(NULL, COINIT_APARTMENTTHREADED);
    if (FAILED(com_result)) {
        MessageBoxW(NULL, L"无法初始化 Windows 卸载组件。", L"卸载飞行雪绒", MB_OK | MB_ICONERROR);
        return 1;
    }
    controls.dwSize = sizeof(controls);
    controls.dwICC = ICC_PROGRESS_CLASS;
    InitCommonControlsEx(&controls);
    argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (argv != NULL && argc >= 4 && wcscmp(argv[1], L"--cleanup") == 0) {
        wchar_t *end = NULL;
        unsigned long parsed_pid;
        int index;
        g_cleanup_mode = TRUE;
        if (FAILED(StringCchCopyW(g_cleanup.install_root, ARRAYSIZE(g_cleanup.install_root), argv[2])) ||
            !marker_is_valid(g_cleanup.install_root)) {
            LocalFree(argv);
            CoUninitialize();
            MessageBoxW(NULL, L"拒绝清理：安装目录标记无效。", L"卸载飞行雪绒", MB_OK | MB_ICONERROR);
            return 1;
        }
        parsed_pid = wcstoul(argv[3], &end, 10);
        if (end == argv[3] || *end != L'\0' || parsed_pid == 0) {
            LocalFree(argv);
            CoUninitialize();
            return 1;
        }
        g_cleanup.parent_pid = (DWORD)parsed_pid;
        for (index = 4; index < argc; ++index) {
            if (wcscmp(argv[index], L"--delete-voice-package") == 0) {
                delete_voice_package = TRUE;
            } else if (wcscmp(argv[index], L"--delete-user-data") == 0) {
                delete_user_data = TRUE;
            } else {
                LocalFree(argv);
                CoUninitialize();
                MessageBoxW(NULL, L"拒绝清理：未知的清理参数。", L"卸载飞行雪绒", MB_OK | MB_ICONERROR);
                return 1;
            }
        }
        g_cleanup.delete_voice_package = delete_voice_package;
        g_cleanup.delete_user_data = delete_user_data;
        if (GetModuleFileNameW(NULL, g_cleanup.helper_path, ARRAYSIZE(g_cleanup.helper_path)) == 0) {
            LocalFree(argv);
            CoUninitialize();
            return 1;
        }
    } else if (argv != NULL && argc > 1) {
        LocalFree(argv);
        CoUninitialize();
        MessageBoxW(NULL, L"卸载器启动参数无效。", L"卸载飞行雪绒", MB_OK | MB_ICONERROR);
        return 1;
    } else if (!resolve_install_root(g_cleanup.install_root, ARRAYSIZE(g_cleanup.install_root))) {
        if (argv != NULL) {
            LocalFree(argv);
        }
        CoUninitialize();
        MessageBoxW(NULL, L"安装目录不完整，卸载器已拒绝执行。", L"卸载飞行雪绒", MB_OK | MB_ICONERROR);
        return 1;
    }
    if (argv != NULL) {
        LocalFree(argv);
    }
    if (!initialize_ui()) {
        CoUninitialize();
        MessageBoxW(NULL, L"无法创建卸载器窗口。", PRODUCT_NAME, MB_OK | MB_ICONERROR);
        return 1;
    }
    ShowWindow(g_window, SW_SHOWNORMAL);
    UpdateWindow(g_window);
    if (g_cleanup_mode) {
        g_cleanup_running = TRUE;
        SendMessageW(g_progress, PBM_SETMARQUEE, TRUE, 28);
        worker = CreateThread(NULL, 0, cleanup_worker, &g_cleanup, 0, NULL);
        if (worker == NULL) {
            g_cleanup_running = FALSE;
            PostMessageW(g_window, WM_FSV_UNINSTALL_DONE, GetLastError(), 0);
        }
    }
    while (GetMessageW(&message, NULL, 0, 0) > 0) {
        if (!IsDialogMessageW(g_window, &message)) {
            TranslateMessage(&message);
            DispatchMessageW(&message);
        }
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
    if (g_meta_font != NULL) {
        DeleteObject(g_meta_font);
    }
    if (g_embedded_font != NULL) {
        RemoveFontMemResourceEx(g_embedded_font);
    }
    if (g_canvas_brush != NULL) {
        DeleteObject(g_canvas_brush);
    }
    if (g_surface_brush != NULL) {
        DeleteObject(g_surface_brush);
    }
    if (g_raised_brush != NULL) {
        DeleteObject(g_raised_brush);
    }
    CoUninitialize();
    return 0;
}
