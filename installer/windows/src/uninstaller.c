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
#include <tlhelp32.h>
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
#define WM_FSV_UNINSTALL_PROGRESS (WM_APP + 3)
#define IDC_UNINSTALL 2001
#define IDC_EXIT 2002
#define IDC_PROGRESS 2003
#define IDC_DELETE_VOICE 2004
#define IDC_DELETE_DATA 2005
#define IDC_PROGRESS_DELETE 2006
#define IDC_PROGRESS_STATS 2007
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
static BOOL g_voice_checked;
static BOOL g_data_checked;
static HWND g_progress_delete;
static HWND g_progress_stats;

/* Same rounded bar the installer uses on its resource page: a quiet track with
   a hairline border, a plain rectangular chunk and centred text.  A negative
   position means "amount unknown": the bar sweeps instead of filling. */
typedef struct ProgressVisualState {
    int minimum;
    int maximum;
    int position;
    BOOL show_percent;
    const wchar_t *complete_text;
    const wchar_t *pending_text;
    COLORREF fill_color;
    COLORREF track_color;
} ProgressVisualState;

static ProgressVisualState g_scan_progress_visual;
static ProgressVisualState g_delete_progress_visual;

/* Cleanup progress, filled by the worker thread and read by the UI thread
   through WM_FSV_UNINSTALL_PROGRESS. */
typedef struct CleanupProgress {
    ULONGLONG total_files;
    ULONGLONG total_bytes;
    ULONGLONG scanned_files;
    ULONGLONG scanned_bytes;
    ULONGLONG deleted_files;
    ULONGLONG deleted_bytes;
    ULONGLONG last_post_at;
    BOOL scanning;
} CleanupProgress;

static CleanupProgress g_progress_state;

static void post_cleanup_progress(BOOL force);

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

/* Owner-drawn checkboxes have no native check state, so the dialog keeps the
   toggle state and answers the button messages other code still uses. */
static BOOL *checkbox_state(UINT_PTR id) {
    if (id == (UINT_PTR)IDC_DELETE_VOICE) {
        return &g_voice_checked;
    }
    if (id == (UINT_PTR)IDC_DELETE_DATA) {
        return &g_data_checked;
    }
    return NULL;
}

static BOOL checkbox_checked(int id) {
    BOOL *state = checkbox_state((UINT_PTR)id);
    return state != NULL && *state;
}

static void checkbox_toggle(HWND control, int id) {
    BOOL *state = checkbox_state((UINT_PTR)id);
    if (control == NULL || state == NULL) {
        return;
    }
    *state = !*state;
    InvalidateRect(control, NULL, FALSE);
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

static ULONGLONG file_size_of(const WIN32_FIND_DATAW *data) {
    return ((ULONGLONG)data->nFileSizeHigh << 32) | data->nFileSizeLow;
}

/* One traversal serves both cleanup phases: with ``count_only`` it only fills in
   the totals the progress bars need, otherwise it deletes and reports. */
static BOOL delete_tree(const wchar_t *directory, BOOL count_only) {
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
        return count_only ? TRUE : RemoveDirectoryW(directory);
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
                    if (!count_only && !RemoveDirectoryW(child)) {
                        success = FALSE;
                        break;
                    }
                } else if (!delete_tree(child, count_only)) {
                    success = FALSE;
                    break;
                }
            } else if (count_only) {
                g_progress_state.total_files += 1;
                g_progress_state.total_bytes += file_size_of(&data);
                post_cleanup_progress(FALSE);
            } else {
                if ((data.dwFileAttributes & FILE_ATTRIBUTE_READONLY) != 0) {
                    SetFileAttributesW(child, data.dwFileAttributes & ~FILE_ATTRIBUTE_READONLY);
                }
                if (!DeleteFileW(child)) {
                    success = FALSE;
                    break;
                }
                g_progress_state.deleted_files += 1;
                g_progress_state.deleted_bytes += file_size_of(&data);
                post_cleanup_progress(FALSE);
            }
        } while (FindNextFileW(search, &data));
        if (success && GetLastError() != ERROR_NO_MORE_FILES) {
            success = FALSE;
        }
        FindClose(search);
    } else if (GetLastError() != ERROR_FILE_NOT_FOUND) {
        return FALSE;
    }
    if (count_only) {
        return TRUE;
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
static void delete_optional_tree(const wchar_t *path, const wchar_t *install_root, DWORD *error, BOOL count_only) {
    DWORD code;
    if (path_is_prefix_of(path, install_root)) {
        return;
    }
    if (delete_tree(path, count_only)) {
        return;
    }
    if (count_only) {
        return;
    }
    code = GetLastError();
    if (code == ERROR_FILE_NOT_FOUND || code == ERROR_PATH_NOT_FOUND) {
        return;
    }
    record_error(error, code);
}

static void delete_optional_file(const wchar_t *path, DWORD *error, BOOL count_only) {
    DWORD code;
    if (count_only) {
        WIN32_FIND_DATAW data;
        HANDLE search = FindFirstFileW(path, &data);
        if (search != INVALID_HANDLE_VALUE) {
            g_progress_state.total_files += 1;
            g_progress_state.total_bytes += file_size_of(&data);
            FindClose(search);
        }
        return;
    }
    if (!DeleteFileW(path)) {
        code = GetLastError();
        if (code != ERROR_FILE_NOT_FOUND && code != ERROR_PATH_NOT_FOUND) {
            record_error(error, code);
        }
        return;
    }
    g_progress_state.deleted_files += 1;
    post_cleanup_progress(FALSE);
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

static void delete_voice_package(const wchar_t *install_root, DWORD *error, BOOL count_only) {
    wchar_t path[FSV_PATH_CAPACITY];
    if (join_path(SHARED_ROOT_DIRECTORY, L"voice", path, ARRAYSIZE(path))) {
        delete_optional_tree(path, install_root, error, count_only);
    }
    if (join_path(SHARED_ROOT_DIRECTORY, L"models\\vosk", path, ARRAYSIZE(path))) {
        delete_optional_tree(path, install_root, error, count_only);
    }
    if (join_path(SHARED_ROOT_DIRECTORY, L"start_gsvmove.bat", path, ARRAYSIZE(path))) {
        delete_optional_file(path, error, count_only);
    }
}

static void delete_user_data(const wchar_t *install_root, DWORD *error, BOOL count_only) {
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
            delete_optional_tree(path, install_root, error, count_only);
        }
    }
    if (desktop_office_workspace_path(path, ARRAYSIZE(path))) {
        delete_optional_tree(path, install_root, error, count_only);
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

static void format_size(ULONGLONG bytes, wchar_t *output, size_t capacity) {
    if (bytes >= 1024ULL * 1024ULL * 1024ULL) {
        StringCchPrintfW(output, capacity, L"%.2f GB", (double)bytes / (1024.0 * 1024.0 * 1024.0));
    } else if (bytes >= 1024ULL * 1024ULL) {
        StringCchPrintfW(output, capacity, L"%.1f MB", (double)bytes / (1024.0 * 1024.0));
    } else {
        StringCchPrintfW(output, capacity, L"%.0f KB", (double)bytes / 1024.0);
    }
}

static int progress_percent(ULONGLONG completed, ULONGLONG total) {
    if (total == 0) {
        return 0;
    }
    return completed >= total ? 100 : (int)(completed * 100ULL / total);
}

/* Publish both bar positions to the window thread.  Called for every deleted
   file, so the posts are coalesced to roughly twelve per second. */
static void post_cleanup_progress(BOOL force) {
    ULONGLONG now = GetTickCount64();
    int scan;
    int remove;
    if (!force && g_progress_state.last_post_at != 0 && now - g_progress_state.last_post_at < 80) {
        return;
    }
    g_progress_state.last_post_at = now;
    if (g_progress_state.scanning) {
        /* The total is still unknown while counting, so the scan bar sweeps. */
        scan = -1;
        remove = 0;
    } else {
        scan = 100;
        remove = g_progress_state.total_bytes > 0
            ? progress_percent(g_progress_state.deleted_bytes, g_progress_state.total_bytes)
            : progress_percent(g_progress_state.deleted_files, g_progress_state.total_files);
    }
    PostMessageW(g_cleanup.window, WM_FSV_UNINSTALL_PROGRESS, (WPARAM)scan, (LPARAM)remove);
}

/* Close the running Flying Snow Velvet (desktop pet plus its services).
   The desktop pet, its voice runtime and the office sidecars all run out
   of the install root, so they keep app\runtime\*.exe and *.dll mapped and
   the delete pass leaves locked leftovers behind.  The uninstaller closes
   them itself instead of asking the user to quit the pet first.

   Matching is by image path only: a process is handled when its executable
   lives inside the install root or inside the shared contract directory
   C:\AemeathDeskPet.  An unrelated system python.exe / node.exe / ollama.exe
   is never touched.  Windows first get WM_CLOSE (the pet exits through its
   own path and stops its children), and whatever is still alive after the
   grace period gets terminated. */

#define FSV_SHUTDOWN_GRACE_MS 2500
#define FSV_SHUTDOWN_ROUNDS 4
#define FSV_SHUTDOWN_MAX_PIDS 96

static BOOL path_within_root(const wchar_t *path, const wchar_t *root) {
    size_t length;
    if (path == NULL || root == NULL) {
        return FALSE;
    }
    length = wcslen(root);
    if (length == 0) {
        return FALSE;
    }
    if (_wcsnicmp(path, root, length) != 0) {
        return FALSE;
    }
    return path[length] == L'\0' || path[length] == L'\\';
}

static BOOL process_matches_root(DWORD pid, const wchar_t *install_root, const wchar_t *shared_root) {
    HANDLE handle;
    wchar_t image[FSV_PATH_CAPACITY];
    DWORD length = ARRAYSIZE(image);
    BOOL match = FALSE;
    if (pid == 0 || pid == GetCurrentProcessId()) {
        return FALSE;
    }
    handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (handle == NULL) {
        return FALSE;
    }
    if (QueryFullProcessImageNameW(handle, 0, image, &length)) {
        match = path_within_root(image, install_root) ||
                path_within_root(image, shared_root);
    }
    CloseHandle(handle);
    return match;
}

static int collect_process_targets(DWORD *pids, int capacity, const wchar_t *install_root, const wchar_t *shared_root) {
    HANDLE snapshot;
    PROCESSENTRY32W entry;
    int count = 0;
    snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snapshot == INVALID_HANDLE_VALUE) {
        return 0;
    }
    ZeroMemory(&entry, sizeof(entry));
    entry.dwSize = sizeof(entry);
    if (Process32FirstW(snapshot, &entry)) {
        do {
            if (count >= capacity) {
                break;
            }
            if (process_matches_root(entry.th32ProcessID, install_root, shared_root)) {
                pids[count++] = entry.th32ProcessID;
            }
        } while (Process32NextW(snapshot, &entry));
    }
    CloseHandle(snapshot);
    return count;
}

static BOOL CALLBACK request_close_window(HWND window, LPARAM parameter) {
    DWORD owner = 0;
    GetWindowThreadProcessId(window, &owner);
    if (owner == (DWORD)parameter) {
        PostMessageW(window, WM_CLOSE, 0, 0);
    }
    return TRUE;
}

static BOOL process_still_alive(DWORD pid) {
    HANDLE handle = OpenProcess(SYNCHRONIZE, FALSE, pid);
    DWORD wait;
    if (handle == NULL) {
        return FALSE;
    }
    wait = WaitForSingleObject(handle, 0);
    CloseHandle(handle);
    return wait == WAIT_TIMEOUT;
}

static void terminate_process(DWORD pid) {
    HANDLE handle = OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, FALSE, pid);
    if (handle == NULL) {
        return;
    }
    if (TerminateProcess(handle, 1)) {
        WaitForSingleObject(handle, 2000);
    }
    CloseHandle(handle);
}

/* Returns how many processes had to be terminated outright.  ``report``
   writes the step into the cleanup window; the quiet mode used at startup
   leaves the instruction text alone. */
static int stop_install_root_processes(const wchar_t *install_root, const wchar_t *shared_root, BOOL report) {
    DWORD pids[FSV_SHUTDOWN_MAX_PIDS];
    int terminated = 0;
    int round;
    if (install_root == NULL || install_root[0] == L'\0') {
        return 0;
    }
    for (round = 0; round < FSV_SHUTDOWN_ROUNDS; ++round) {
        int count = collect_process_targets(pids, (int)ARRAYSIZE(pids), install_root, shared_root);
        int index;
        if (count == 0) {
            break;
        }
        if (report) {
            post_status(L"正在关闭飞行雪绒及其服务组件...");
        }
        for (index = 0; index < count; ++index) {
            EnumWindows(request_close_window, (LPARAM)pids[index]);
        }
        Sleep(FSV_SHUTDOWN_GRACE_MS);
        for (index = 0; index < count; ++index) {
            if (process_still_alive(pids[index])) {
                terminate_process(pids[index]);
                terminated += 1;
            }
        }
    }
    return terminated;
}

/* The uninstaller window shows up first, then the pet is closed in the
   background so the file delete a few clicks later is not fighting locks. */
static DWORD WINAPI shutdown_worker(void *parameter) {
    wchar_t *install_root = (wchar_t *)parameter;
    if (install_root != NULL) {
        stop_install_root_processes(install_root, SHARED_ROOT_DIRECTORY, FALSE);
        HeapFree(GetProcessHeap(), 0, install_root);
    }
    return 0;
}

static BOOL start_shutdown_worker(const wchar_t *install_root) {
    size_t bytes;
    wchar_t *copy;
    HANDLE worker;
    if (install_root == NULL || install_root[0] == L'\0') {
        return FALSE;
    }
    bytes = (wcslen(install_root) + 1) * sizeof(wchar_t);
    copy = (wchar_t *)HeapAlloc(GetProcessHeap(), 0, bytes);
    if (copy == NULL) {
        return FALSE;
    }
    memcpy(copy, install_root, bytes);
    worker = CreateThread(NULL, 0, shutdown_worker, copy, 0, NULL);
    if (worker == NULL) {
        HeapFree(GetProcessHeap(), 0, copy);
        return FALSE;
    }
    CloseHandle(worker);
    return TRUE;
}

static DWORD WINAPI cleanup_worker(void *parameter) {
    CleanupContext *context = (CleanupContext *)parameter;
    HANDLE parent = OpenProcess(SYNCHRONIZE, FALSE, context->parent_pid);
    DWORD result = ERROR_SUCCESS;
    DWORD counted = ERROR_SUCCESS;
    ZeroMemory(&g_progress_state, sizeof(g_progress_state));
    if (parent != NULL) {
        WaitForSingleObject(parent, 30000);
        CloseHandle(parent);
    }
    /* Nothing out of the install root may stay mapped while files are being
       deleted; the startup pass is best effort and the pet may have been
       started again while the options were on screen. */
    stop_install_root_processes(context->install_root, SHARED_ROOT_DIRECTORY, TRUE);
    /* Count first so the delete bar has a real total instead of guessing.  The
       same traversal then runs again to delete, which is cheap next to the
       seventeen thousand files an installed copy contains. */
    g_progress_state.scanning = TRUE;
    post_status(L"正在统计待删除文件...");
    post_cleanup_progress(TRUE);
    if (context->delete_voice_package) {
        delete_voice_package(context->install_root, &counted, TRUE);
    }
    if (context->delete_user_data) {
        delete_user_data(context->install_root, &counted, TRUE);
    }
    delete_tree(context->install_root, TRUE);
    g_progress_state.scanning = FALSE;
    post_cleanup_progress(TRUE);
    if (context->delete_voice_package) {
        post_status(L"正在删除语音包与语音推理运行时...");
        delete_voice_package(context->install_root, &result, FALSE);
    }
    if (context->delete_user_data) {
        post_status(L"正在删除飞行雪绒的记忆、用户配置与 APikey...");
        delete_user_data(context->install_root, &result, FALSE);
    }
    post_status(L"正在删除飞行雪绒程序文件...");
    if (!delete_tree(context->install_root, FALSE)) {
        if (result == ERROR_SUCCESS) {
            result = GetLastError();
        }
    }
    g_progress_state.deleted_files = g_progress_state.total_files;
    g_progress_state.deleted_bytes = g_progress_state.total_bytes;
    post_cleanup_progress(TRUE);
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

static void draw_checkbox(HWND window, HDC dc, int hover, BOOL checked) {
    RECT bounds;
    RECT box;
    RECT text_bounds;
    int size = ui_px(FSV_CHECKBOX_SIZE);
    BOOL enabled = IsWindowEnabled(window);
    BOOL focused = GetFocus() == window;
    COLORREF border;
    GetClientRect(window, &bounds);
    FillRect(dc, &bounds, g_surface_brush);
    box.left = bounds.left + 2;
    box.top = bounds.top + (bounds.bottom - bounds.top - size) / 2;
    box.right = box.left + size;
    box.bottom = box.top + size;
    if (checked) {
        draw_round_panel(dc, &box, FSV_COLOR_PINK, FSV_COLOR_PINK, ui_px(6));
        draw_check_mark(dc, &box);
    } else {
        border = !enabled ? FSV_COLOR_BORDER : hover > 0 ? FSV_COLOR_PINK : FSV_COLOR_BORDER_STRONG;
        draw_round_panel(dc, &box, FSV_COLOR_SURFACE, border, ui_px(6));
    }
    if (focused && enabled) {
        /* Keyboard focus uses the accent ring; the native dotted focus
           rectangle reads as a stray system artefact on this surface. */
        RECT ring = box;
        HPEN pen;
        HGDIOBJ old_pen;
        HGDIOBJ old_brush;
        InflateRect(&ring, -1, -1);
        pen = CreatePen(PS_SOLID, 1, FSV_COLOR_CYAN);
        old_brush = SelectObject(dc, GetStockObject(NULL_BRUSH));
        old_pen = SelectObject(dc, pen);
        RoundRect(dc, ring.left, ring.top, ring.right, ring.bottom, ui_px(5), ui_px(5));
        SelectObject(dc, old_pen);
        SelectObject(dc, old_brush);
        DeleteObject(pen);
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
    if (message == BM_SETCHECK || message == BM_GETCHECK) {
        /* The control is owner drawn, so the dialog answers the check state. */
        BOOL *checked = checkbox_state(id);
        if (checked == NULL) {
            return 0;
        }
        if (message == BM_SETCHECK) {
            *checked = wparam == (WPARAM)BST_CHECKED;
            InvalidateRect(window, NULL, FALSE);
            return 0;
        }
        return *checked ? BST_CHECKED : BST_UNCHECKED;
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

static void paint_rounded_progress(HDC dc, const RECT *bounds, const ProgressVisualState *state) {
    RECT panel;
    RECT filled_area;
    int span;
    int filled;
    FillRect(dc, bounds, g_surface_brush);
    if (bounds->right - bounds->left < 6 || bounds->bottom - bounds->top < 6) {
        return;
    }
    panel = *bounds;
    InflateRect(&panel, -1, -1);
    draw_round_panel(dc, &panel, state->track_color, FSV_COLOR_BORDER, ui_px(3));
    span = state->maximum - state->minimum;
    filled = 0;
    if (state->position < 0) {
        /* Unknown amount: sweep a quarter of the track instead of filling it. */
        int width = panel.right - panel.left;
        int chunk = width / 4;
        if (chunk < ui_px(24)) {
            chunk = ui_px(24);
        }
        if (chunk > width) {
            chunk = width;
        }
        if (width > chunk) {
            DWORD phase = (DWORD)(GetTickCount64() % 1600ULL);
            int travel = width - chunk;
            int offset = phase < 800
                ? travel * (int)phase / 800
                : travel * (int)(1600 - phase) / 800;
            HBRUSH sweep_brush = CreateSolidBrush(state->fill_color);
            filled_area = panel;
            filled_area.left = panel.left + offset;
            filled_area.right = filled_area.left + chunk;
            FillRect(dc, &filled_area, sweep_brush);
            DeleteObject(sweep_brush);
        }
    } else if (span > 0 && state->position > state->minimum) {
        filled = (panel.right - panel.left) * (state->position - state->minimum) / span;
        if (filled > panel.right - panel.left) {
            filled = panel.right - panel.left;
        }
        if (filled > 0) {
            HBRUSH fill_brush = CreateSolidBrush(state->fill_color);
            filled_area = panel;
            filled_area.right = panel.left + filled;
            FillRect(dc, &filled_area, fill_brush);
            DeleteObject(fill_brush);
        }
    }
    if (state->show_percent) {
        wchar_t label[64];
        HFONT label_font = g_meta_font != NULL ? g_meta_font : (HFONT)GetStockObject(DEFAULT_GUI_FONT);
        if (state->position < 0) {
            StringCchCopyW(label, ARRAYSIZE(label), state->pending_text != NULL ? state->pending_text : L"");
        } else {
            int label_percent = span > 0 ? (state->position - state->minimum) * 100 / span : 0;
            if (label_percent < 0) {
                label_percent = 0;
            } else if (label_percent > 100) {
                label_percent = 100;
            }
            if (label_percent >= 100 && state->complete_text != NULL) {
                StringCchCopyW(label, ARRAYSIZE(label), state->complete_text);
            } else {
                StringCchPrintfW(label, ARRAYSIZE(label), L"%d%%", label_percent);
            }
        }
        draw_text_block(dc, label_font, FSV_COLOR_TEXT, label, panel, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
    }
}

static void draw_rounded_progress(HWND window, const ProgressVisualState *state) {
    PAINTSTRUCT paint;
    RECT bounds;
    HDC dc = BeginPaint(window, &paint);
    GetClientRect(window, &bounds);
    paint_rounded_progress(dc, &bounds, state);
    EndPaint(window, &paint);
}

static LRESULT CALLBACK progress_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam, UINT_PTR id, DWORD_PTR data) {
    ProgressVisualState *state = (ProgressVisualState *)data;
    LRESULT result;
    switch (message) {
    case PBM_SETRANGE32:
    case PBM_SETPOS:
    case PBM_SETBARCOLOR:
    case PBM_SETBKCOLOR:
        /* Forward first so the control keeps its own value for accessibility,
           then repaint with the shared rounded style. */
        result = DefSubclassProc(window, message, wparam, lparam);
        if (state != NULL) {
            if (message == PBM_SETRANGE32) {
                state->minimum = (int)wparam;
                state->maximum = (int)lparam;
            } else if (message == PBM_SETPOS) {
                state->position = (int)wparam;
            } else if (message == PBM_SETBARCOLOR) {
                state->fill_color = (COLORREF)lparam;
            } else {
                state->track_color = (COLORREF)lparam;
            }
            InvalidateRect(window, NULL, FALSE);
        }
        return result;
    case WM_ERASEBKGND:
        return 1;
    case WM_PAINT:
        if (state != NULL) {
            draw_rounded_progress(window, state);
            return 0;
        }
        break;
    case WM_PRINT:
    case WM_PRINTCLIENT:
        /* The visual harness paints into its own DC; without this the capture
           would show the stock progress bar instead of the shipped one. */
        if (state != NULL) {
            RECT bounds;
            GetClientRect(window, &bounds);
            paint_rounded_progress((HDC)wparam, &bounds, state);
            return 0;
        }
        break;
    case WM_NCDESTROY:
        RemoveWindowSubclass(window, progress_proc, id);
        break;
    default:
        break;
    }
    return DefSubclassProc(window, message, wparam, lparam);
}

static HWND create_progress(int id, ProgressVisualState *state, COLORREF fill_color,
                            const wchar_t *complete_text, const wchar_t *pending_text) {
    HWND control = CreateWindowExW(
        0, PROGRESS_CLASSW, NULL, WS_CHILD | PBS_SMOOTH,
        0, 0, 1, 1, g_window, (HMENU)(INT_PTR)id, GetModuleHandleW(NULL), NULL
    );
    if (control == NULL) {
        return NULL;
    }
    ZeroMemory(state, sizeof(*state));
    state->minimum = 0;
    state->maximum = 100;
    state->position = 0;
    state->show_percent = TRUE;
    state->complete_text = complete_text;
    state->pending_text = pending_text;
    state->fill_color = fill_color;
    state->track_color = FSV_COLOR_SURFACE_RAISED;
    SetWindowTheme(control, L"", L"");
    SendMessageW(control, PBM_SETRANGE32, 0, 100);
    SendMessageW(control, PBM_SETPOS, 0, 0);
    SendMessageW(control, PBM_SETBARCOLOR, 0, fill_color);
    SendMessageW(control, PBM_SETBKCOLOR, 0, FSV_COLOR_SURFACE_RAISED);
    SetWindowSubclass(control, progress_proc, (UINT_PTR)id, (DWORD_PTR)state);
    return control;
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
        0, L"BUTTON", text, WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_OWNERDRAW,
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
    place_control(g_progress, 40, 320, 800, 24, g_body_font);
    place_control(g_progress_delete, 40, 364, 800, 24, g_body_font);
    place_control(g_progress_stats, 40, 400, 800, 20, g_meta_font);
    place_control(g_action, 680, 510, 160, 40, g_body_font);
    place_control(g_exit, 528, 510, 140, 40, g_body_font);
    apply_rounded_progress_region(g_progress, ui_px(24));
    apply_rounded_progress_region(g_progress_delete, ui_px(24));
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
        ShowWindow(g_progress_delete, SW_SHOW);
        ShowWindow(g_progress_stats, SW_SHOW);
        ShowWindow(g_action, SW_HIDE);
        ShowWindow(g_exit, SW_HIDE);
    } else {
        ShowWindow(g_progress, SW_HIDE);
        ShowWindow(g_progress_delete, SW_HIDE);
        ShowWindow(g_progress_stats, SW_HIDE);
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
        const DRAWITEMSTRUCT *item = (const DRAWITEMSTRUCT *)lparam;
        if (item->CtlID == IDC_DELETE_VOICE || item->CtlID == IDC_DELETE_DATA) {
            draw_checkbox(
                item->hwndItem,
                item->hDC,
                item->CtlID == IDC_DELETE_VOICE ? g_hover_voice : g_hover_data,
                checkbox_checked((int)item->CtlID)
            );
        } else {
            draw_button(item);
        }
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
    if (message == WM_FSV_UNINSTALL_PROGRESS) {
        wchar_t stats[256];
        wchar_t removed[32];
        wchar_t total[32];
        int scan = (int)wparam;
        SendMessageW(g_progress, PBM_SETPOS, scan < 0 ? -1 : scan, 0);
        SendMessageW(g_progress_delete, PBM_SETPOS, (int)lparam, 0);
        if (scan < 0) {
            StringCchPrintfW(
                stats,
                ARRAYSIZE(stats),
                L"正在统计待删除文件：%llu 个文件",
                (unsigned long long)g_progress_state.total_files
            );
        } else {
            format_size(g_progress_state.deleted_bytes, removed, ARRAYSIZE(removed));
            format_size(g_progress_state.total_bytes, total, ARRAYSIZE(total));
            StringCchPrintfW(
                stats,
                ARRAYSIZE(stats),
                L"已删除 %llu / %llu 个文件 · %ls / %ls",
                (unsigned long long)g_progress_state.deleted_files,
                (unsigned long long)g_progress_state.total_files,
                removed,
                total
            );
        }
        SetWindowTextW(g_progress_stats, stats);
        return 0;
    }
    if (message == WM_FSV_UNINSTALL_DONE) {
        g_cleanup_running = FALSE;
        ShowWindow(g_progress, SW_HIDE);
        ShowWindow(g_progress_delete, SW_HIDE);
        ShowWindow(g_progress_stats, SW_HIDE);
        show_uninstall_result((DWORD)wparam);
        InvalidateRect(window, NULL, TRUE);
        return 0;
    }
    if (message == WM_COMMAND) {
        int id = LOWORD(wparam);
        if ((id == IDC_DELETE_VOICE || id == IDC_DELETE_DATA) && HIWORD(wparam) == BN_CLICKED) {
            checkbox_toggle(id == IDC_DELETE_VOICE ? g_voice_check : g_data_check, id);
            return 0;
        }
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
        g_cleanup_mode ? L"正在关闭飞行雪绒与其服务组件，随后删除程序文件..." : L"卸载将删除程序文件（app 与 runtime）。\r\n桌宠及其服务组件已在后台关闭；用户数据与语音包默认保留，可勾选下方选项一并删除。",
        40, 188, 800, 64, g_body_font, SS_LEFT
    );
    g_path = create_label(g_cleanup.install_root, 56, 274, 768, 28, g_body_font, SS_PATHELLIPSIS);
    create_checkbox(&g_voice_check, L"删除语音包", IDC_DELETE_VOICE, &g_hover_voice);
    g_voice_hint = create_label(L"同时删除 C:\\AemeathDeskPet\\voice 下的语音包、推理运行时与语音识别模型。", 72, 364, 768, 20, g_meta_font, SS_LEFT);
    create_checkbox(&g_data_check, L"删除用户数据", IDC_DELETE_DATA, &g_hover_data);
    g_data_hint = create_label(L"包含飞行雪绒的记忆、用户配置、Apikey、桌面办公区等；删除后无法恢复。", 72, 428, 768, 20, g_meta_font, SS_LEFT);
    create_button(&g_action, L"卸载飞行雪绒", IDC_UNINSTALL, &g_hover_action);
    create_button(&g_exit, L"退出", IDC_EXIT, &g_hover_exit);
    g_progress = create_progress(IDC_PROGRESS, &g_scan_progress_visual, FSV_COLOR_CYAN, L"已完成", L"正在统计");
    g_progress_delete = create_progress(IDC_PROGRESS_DELETE, &g_delete_progress_visual, FSV_COLOR_PINK, L"已完成", L"正在准备");
    g_progress_stats = create_label(L"", 40, 400, 800, 20, g_meta_font, SS_LEFT);
    if (g_title == NULL || g_body == NULL || g_path == NULL || g_voice_check == NULL || g_voice_hint == NULL ||
        g_data_check == NULL || g_data_hint == NULL || g_action == NULL || g_exit == NULL ||
        g_progress == NULL || g_progress_delete == NULL || g_progress_stats == NULL) {
        return FALSE;
    }
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
    if (!g_cleanup_mode) {
        /* Closing the pet takes a couple of seconds, so it runs behind the
           window instead of delaying it.  Failure is not fatal: the cleanup
           worker sweeps again right before it starts deleting. */
        start_shutdown_worker(g_cleanup.install_root);
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
