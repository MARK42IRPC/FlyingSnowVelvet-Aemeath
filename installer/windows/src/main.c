#define UNICODE
#define _UNICODE
#define WIN32_LEAN_AND_MEAN
#define _CRT_SECURE_NO_WARNINGS

#include <windows.h>
#include <knownfolders.h>
#include <shellapi.h>
#include <shlobj.h>
#include <shobjidl.h>
#include <commctrl.h>
#include <objbase.h>
#include <bcrypt.h>
#include <strsafe.h>
#include <uxtheme.h>
#include <urlmon.h>
#include <wininet.h>

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>
#include <float.h>

#include "resource.h"
#include "payload_info.h"
#include "installer_theme.h"
#if defined(__has_include)
#if __has_include("resource_urls.h")
#include "resource_urls.h"
#endif
#endif
#ifndef FSV_RESOURCE_URL_HF
#define FSV_RESOURCE_URL_HF L"https://huggingface.co/Mark42IRP/Aemeath_onnx_GSV_model/resolve/main/updates/FlyingSnowVelvet-LTS1.0.7pre1-Resources.zip"
#define FSV_RESOURCE_URL_MODELSCOPE L"https://www.modelscope.cn/models/Mark42IRPC/GSV_onnx_Aemeath_Pack/resolve/master/updates/FlyingSnowVelvet-LTS1.0.7pre1-Resources.zip"
#endif
#include "zip_extract.h"

#pragma comment(lib, "bcrypt.lib")
#pragma comment(lib, "comctl32.lib")
#pragma comment(lib, "gdi32.lib")
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "shell32.lib")
#pragma comment(lib, "user32.lib")
#pragma comment(lib, "uxtheme.lib")
#pragma comment(lib, "urlmon.lib")
#pragma comment(lib, "wininet.lib")

static const char PAYLOAD_MAGIC[] = "FSV-OFFLINE-PAYLOAD-2";
static const wchar_t WIZARD_CLASS_NAME[] = L"FlyingSnowVelvetOfflineInstaller";
static const DWORD ARCHIVE_BUFFER_SIZE = 1024U * 1024U;

#define WM_FSV_STATUS (WM_APP + 1)
#define WM_FSV_PROGRESS (WM_APP + 2)
#define WM_FSV_DONE (WM_APP + 3)
#define FSV_FADE_TIMER 7
#define FSV_BUTTON_TIMER 8
#define INSTALL_ERROR_CAPACITY 768
#define FSV_PATH_CAPACITY 520
static wchar_t g_log_path[FSV_PATH_CAPACITY];
#define FSV_MAX_BUTTONS 6
#define FSV_CLIENT_WIDTH 880
#define FSV_CLIENT_HEIGHT 568

/* Keep both mirrors version agnostic: the release slot's latest manifest is
 * resolved by the updater, while the online bootstrap uses the current asset
 * name injected by the build through resource URLs below. */
static const wchar_t RESOURCE_URLS[][FSV_PATH_CAPACITY] = { FSV_RESOURCE_URL_HF, FSV_RESOURCE_URL_MODELSCOPE };

#define IDC_PATH_EDIT 1001
#define IDC_CUSTOM 1002
#define IDC_BACK 1003
#define IDC_NEXT 1004
#define IDC_START 1005
#define IDC_FINISH 1006
#define IDC_PROGRESS 1007
#define IDC_CURRENT_FILE 1008
#define IDC_PROGRESS_STATS 1009
#define IDC_PROGRESS_ETA 1010
#define IDC_STATUS 1011
#define IDC_SPACE_INFO 1012
#define IDC_PAGE_TITLE 1013
#define IDC_PAGE_SUBTITLE 1014
#define IDC_PRODUCT_ICON 1015
#define IDC_DONE_TITLE 1016
#define IDC_DONE_TEXT 1017

#define FSV_PHASE_VERIFYING 1
#define FSV_PHASE_EXTRACTING 2
#define FSV_PHASE_SWITCHING 3
#define FSV_VERIFY_END_PERCENT 20
#define FSV_EXTRACT_START_PERCENT 20
#define FSV_EXTRACT_END_PERCENT 95
#define FSV_SWITCH_PERCENT 99

#pragma pack(push, 1)
typedef struct FsvPayloadTrailer {
    char magic[24];
    ULONGLONG archive_size;
    BYTE archive_sha256[32];
} FsvPayloadTrailer;
#pragma pack(pop)

typedef struct FsvProgressMessage {
    DWORD phase;
    DWORD percent;
    ULONGLONG completed_files;
    ULONGLONG total_files;
    ULONGLONG completed_bytes;
    ULONGLONG total_bytes;
    ULONGLONG eta_seconds;
    BOOL eta_known;
    wchar_t current_file[FSV_PATH_CAPACITY];
} FsvProgressMessage;

typedef struct InstallContext {
    wchar_t install_directory[FSV_PATH_CAPACITY];
    wchar_t error_message[INSTALL_ERROR_CAPACITY];
    ULONGLONG archive_size;
    ULONGLONG required_bytes;
    ULONGLONG total_files;
    ULONGLONG total_bytes;
    BOOL installed;
    BOOL installing;
    BOOL completed;
    DWORD result;
    HANDLE worker;
    HWND window;
} InstallContext;

typedef struct ButtonVisualState {
    HWND window;
    int hover_amount;
    BOOL hover_target;
} ButtonVisualState;

static InstallContext g_context;
static HWND g_window;
static HWND g_page_title;
static HWND g_page_subtitle;
static HWND g_step_label;
static HWND g_icon;
static HWND g_path_edit;
static HWND g_custom_button;
static HWND g_back_button;
static HWND g_next_button;
static HWND g_start_button;
static HWND g_finish_button;
static HWND g_progress;
static HWND g_download_progress;
static HWND g_current_file;
static HWND g_progress_stats;
static HWND g_progress_eta;
static HWND g_status;
static HWND g_space_info;
static HWND g_done_title;
static HWND g_done_text;
static HWND g_space_values[3];
static HFONT g_heading_font;
static HFONT g_body_font;
static HFONT g_meta_font;
static HANDLE g_embedded_font;
static DWORD g_embedded_font_count;
static HBRUSH g_canvas_brush;
static HBRUSH g_surface_brush;
static HBRUSH g_raised_brush;
static UINT g_dpi = 96;
static int g_page = 1;
static BYTE g_fade_alpha = 255;
static ButtonVisualState g_button_states[FSV_MAX_BUTTONS];
static size_t g_button_count;
static DWORD g_last_progress_percent;
static wchar_t g_requested_directory[FSV_PATH_CAPACITY];
static wchar_t g_update_state_path[FSV_PATH_CAPACITY];
static wchar_t g_update_state_source[FSV_PATH_CAPACITY];
static BOOL g_has_requested_directory;

static int ui_px(int value) {
    return MulDiv(value, (int)g_dpi, 96);
}

static RECT ui_rect(int x, int y, int width, int height) {
    RECT result = {ui_px(x), ui_px(y), ui_px(x + width), ui_px(y + height)};
    return result;
}

static LRESULT CALLBACK wizard_window_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam);
static LRESULT CALLBACK button_subclass_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam, UINT_PTR subclass_id, DWORD_PTR reference_data);

static BOOL load_embedded_harmony_font(void) {
    HINSTANCE instance = GetModuleHandleW(NULL);
    HRSRC resource = FindResourceW(instance, MAKEINTRESOURCEW(IDR_HARMONY_FONT), RT_RCDATA);
    HGLOBAL loaded;
    DWORD size;
    void *data;
    if (resource == NULL) {
        return FALSE;
    }
    size = SizeofResource(instance, resource);
    loaded = LoadResource(instance, resource);
    data = loaded == NULL ? NULL : LockResource(loaded);
    if (size == 0 || data == NULL) {
        return FALSE;
    }
    g_embedded_font = AddFontMemResourceEx(data, size, NULL, &g_embedded_font_count);
    return g_embedded_font != NULL;
}

static BOOL copy_command_value(const wchar_t *value, wchar_t *output, size_t capacity) {
    if (value == NULL || *value == L'\0' || FAILED(StringCchCopyW(output, capacity, value))) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    return TRUE;
}

static BOOL parse_update_arguments(PWSTR command_line) {
    int argc = 0;
    int index;
    wchar_t **argv = CommandLineToArgvW(command_line != NULL ? command_line : GetCommandLineW(), &argc);
    if (argv == NULL) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    for (index = 1; index < argc; ++index) {
        const wchar_t *argument = argv[index];
        if (wcscmp(argument, L"--update-target") == 0) {
            if (++index >= argc || !copy_command_value(argv[index], g_requested_directory, ARRAYSIZE(g_requested_directory))) {
                LocalFree(argv);
                return FALSE;
            }
            g_has_requested_directory = TRUE;
        } else if (wcscmp(argument, L"--update-state") == 0) {
            if (++index >= argc || !copy_command_value(argv[index], g_update_state_path, ARRAYSIZE(g_update_state_path))) {
                LocalFree(argv);
                return FALSE;
            }
        } else if (wcscmp(argument, L"--update-state-source") == 0) {
            if (++index >= argc || !copy_command_value(argv[index], g_update_state_source, ARRAYSIZE(g_update_state_source))) {
                LocalFree(argv);
                return FALSE;
            }
        }
    }
    LocalFree(argv);
    return TRUE;
}

static void set_install_error(InstallContext *context, const wchar_t *message) {
    if (FAILED(StringCchCopyW(context->error_message, ARRAYSIZE(context->error_message), message))) {
        StringCchCopyW(context->error_message, ARRAYSIZE(context->error_message), L"安装失败，且无法记录完整错误信息。" );
    }
}

static void set_install_error_win32(InstallContext *context, const wchar_t *prefix, DWORD error) {
    wchar_t system_message[256];
    DWORD length = FormatMessageW(
        FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS,
        NULL,
        error,
        0,
        system_message,
        ARRAYSIZE(system_message),
        NULL
    );
    if (length == 0) {
        StringCchPrintfW(context->error_message, ARRAYSIZE(context->error_message), L"%ls（错误代码 %lu）", prefix, error);
    } else {
        StringCchPrintfW(context->error_message, ARRAYSIZE(context->error_message), L"%ls：%ls", prefix, system_message);
    }
}

static void installer_log(const wchar_t *message) {
    FILE *file;
    wchar_t path[FSV_PATH_CAPACITY];
    DWORD length;
    if (message == NULL) return;
    if (g_log_path[0] == L'\0') {
        length = GetTempPathW(ARRAYSIZE(path), path);
        if (length == 0 || length >= ARRAYSIZE(path) ||
            FAILED(StringCchPrintfW(g_log_path, ARRAYSIZE(g_log_path), L"%lsFlyingSnowVelvet-installer.log", path))) return;
    }
    file = _wfopen(g_log_path, L"a, ccs=UTF-8");
    if (file == NULL) return;
    fwprintf(file, L"[%lu] %ls\n", GetTickCount(), message);
    fclose(file);
}

static void installer_log_error(const wchar_t *prefix, DWORD error) {
    wchar_t line[512];
    StringCchPrintfW(line, ARRAYSIZE(line), L"%ls (error=%lu)", prefix, error);
    installer_log(line);
}

static BOOL join_path(const wchar_t *directory, const wchar_t *name, wchar_t *output, size_t capacity) {
    size_t length = wcslen(directory);
    HRESULT result = length > 0 && (directory[length - 1] == L'\\' || directory[length - 1] == L'/')
        ? StringCchPrintfW(output, capacity, L"%ls%ls", directory, name)
        : StringCchPrintfW(output, capacity, L"%ls\\%ls", directory, name);
    return SUCCEEDED(result);
}

static BOOL is_regular_file(const wchar_t *path) {
    DWORD attributes = GetFileAttributesW(path);
    return attributes != INVALID_FILE_ATTRIBUTES &&
        (attributes & FILE_ATTRIBUTE_DIRECTORY) == 0 &&
        (attributes & FILE_ATTRIBUTE_REPARSE_POINT) == 0;
}

static BOOL canonicalize_path(const wchar_t *input, wchar_t *output, size_t capacity) {
    wchar_t temporary[FSV_PATH_CAPACITY];
    DWORD length = GetFullPathNameW(input, ARRAYSIZE(temporary), temporary, NULL);
    if (length == 0 || length >= ARRAYSIZE(temporary) ||
        FAILED(StringCchCopyW(output, capacity, temporary))) {
        SetLastError(ERROR_BUFFER_OVERFLOW);
        return FALSE;
    }
    while (wcslen(output) > 3 && (output[wcslen(output) - 1] == L'\\' || output[wcslen(output) - 1] == L'/')) {
        output[wcslen(output) - 1] = L'\0';
    }
    return TRUE;
}

static BOOL find_existing_ancestor(const wchar_t *path, wchar_t *output, size_t capacity) {
    DWORD attributes;
    if (FAILED(StringCchCopyW(output, capacity, path))) {
        SetLastError(ERROR_BUFFER_OVERFLOW);
        return FALSE;
    }
    for (;;) {
        wchar_t *separator;
        attributes = GetFileAttributesW(output);
        if (attributes != INVALID_FILE_ATTRIBUTES) {
            if ((attributes & FILE_ATTRIBUTE_DIRECTORY) == 0) {
                SetLastError(ERROR_DIRECTORY);
                return FALSE;
            }
            return TRUE;
        }
        if (GetLastError() != ERROR_FILE_NOT_FOUND && GetLastError() != ERROR_PATH_NOT_FOUND) {
            return FALSE;
        }
        separator = wcsrchr(output, L'\\');
        if (separator == NULL) {
            SetLastError(ERROR_PATH_NOT_FOUND);
            return FALSE;
        }
        if (separator == output + 2 && output[1] == L':') {
            output[3] = L'\0';
            attributes = GetFileAttributesW(output);
            if (attributes == INVALID_FILE_ATTRIBUTES || (attributes & FILE_ATTRIBUTE_DIRECTORY) == 0) {
                SetLastError(ERROR_PATH_NOT_FOUND);
                return FALSE;
            }
            return TRUE;
        }
        *separator = L'\0';
    }
}

static BOOL query_available_space(const wchar_t *path, ULONGLONG *available_bytes) {
    wchar_t existing[FSV_PATH_CAPACITY];
    ULARGE_INTEGER available;
    if (available_bytes == NULL || !find_existing_ancestor(path, existing, ARRAYSIZE(existing)) ||
        !GetDiskFreeSpaceExW(existing, &available, NULL, NULL)) {
        return FALSE;
    }
    *available_bytes = available.QuadPart;
    return TRUE;
}

static BOOL ensure_directory(const wchar_t *directory) {
    DWORD attributes = GetFileAttributesW(directory);
    DWORD error;
    if (attributes != INVALID_FILE_ATTRIBUTES) {
        if ((attributes & FILE_ATTRIBUTE_DIRECTORY) == 0 || (attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
            SetLastError(ERROR_CANT_ACCESS_FILE);
            return FALSE;
        }
        return TRUE;
    }
    error = GetLastError();
    if (error != ERROR_FILE_NOT_FOUND && error != ERROR_PATH_NOT_FOUND) {
        return FALSE;
    }
    {
        int result = SHCreateDirectoryExW(NULL, directory, NULL);
        if (result != ERROR_SUCCESS && result != ERROR_FILE_EXISTS && result != ERROR_ALREADY_EXISTS) {
            SetLastError((DWORD)result);
            return FALSE;
        }
    }
    attributes = GetFileAttributesW(directory);
    if (attributes == INVALID_FILE_ATTRIBUTES || (attributes & FILE_ATTRIBUTE_DIRECTORY) == 0 ||
        (attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
        SetLastError(ERROR_CANT_ACCESS_FILE);
        return FALSE;
    }
    return TRUE;
}

static BOOL path_is_under(const wchar_t *root, const wchar_t *candidate) {
    wchar_t canonical_root[FSV_PATH_CAPACITY];
    wchar_t canonical_candidate[FSV_PATH_CAPACITY];
    size_t root_length;
    if (root == NULL || candidate == NULL ||
        !canonicalize_path(root, canonical_root, ARRAYSIZE(canonical_root)) ||
        !canonicalize_path(candidate, canonical_candidate, ARRAYSIZE(canonical_candidate))) {
        return FALSE;
    }
    root_length = wcslen(canonical_root);
    if (_wcsnicmp(canonical_root, canonical_candidate, root_length) != 0) {
        return FALSE;
    }
    return canonical_candidate[root_length] == L'\0' || canonical_candidate[root_length] == L'\\';
}

static BOOL copy_update_state(const wchar_t *install_directory) {
    wchar_t destination[FSV_PATH_CAPACITY];
    wchar_t parent[FSV_PATH_CAPACITY];
    wchar_t *separator;
    if (g_update_state_source[0] == L'\0') {
        return TRUE;
    }
    if (g_update_state_path[0] != L'\0') {
        if (FAILED(StringCchCopyW(destination, ARRAYSIZE(destination), g_update_state_path))) {
            SetLastError(ERROR_BUFFER_OVERFLOW);
            return FALSE;
        }
    } else if (!join_path(install_directory, L"app\\resc\\user\\update_state.json", destination, ARRAYSIZE(destination))) {
        SetLastError(ERROR_BUFFER_OVERFLOW);
        return FALSE;
    }
    if (!path_is_under(install_directory, destination) ||
        !is_regular_file(g_update_state_source) ||
        FAILED(StringCchCopyW(parent, ARRAYSIZE(parent), destination))) {
        SetLastError(ERROR_ACCESS_DENIED);
        return FALSE;
    }
    separator = wcsrchr(parent, L'\\');
    if (separator == NULL) {
        SetLastError(ERROR_BAD_PATHNAME);
        return FALSE;
    }
    *separator = L'\0';
    if (!ensure_directory(parent) || !CopyFileW(g_update_state_source, destination, FALSE)) {
        return FALSE;
    }
    DeleteFileW(g_update_state_source);
    return TRUE;
}

static BOOL directory_is_empty(const wchar_t *directory) {
    wchar_t pattern[FSV_PATH_CAPACITY];
    WIN32_FIND_DATAW data;
    HANDLE handle;
    BOOL empty = TRUE;
    if (!join_path(directory, L"*", pattern, ARRAYSIZE(pattern))) {
        return FALSE;
    }
    handle = FindFirstFileW(pattern, &data);
    if (handle == INVALID_HANDLE_VALUE) {
        return GetLastError() == ERROR_FILE_NOT_FOUND;
    }
    do {
        if (wcscmp(data.cFileName, L".") != 0 && wcscmp(data.cFileName, L"..") != 0) {
            empty = FALSE;
            break;
        }
    } while (FindNextFileW(handle, &data));
    FindClose(handle);
    return empty;
}

static BOOL is_known_install(const wchar_t *directory) {
    wchar_t marker[FSV_PATH_CAPACITY];
    wchar_t python[FSV_PATH_CAPACITY];
    wchar_t launcher[FSV_PATH_CAPACITY];
    DWORD marker_attributes;
    DWORD python_attributes;
    if (!join_path(directory, L".fsv-install-root", marker, ARRAYSIZE(marker)) ||
        !join_path(directory, L"runtime\\python311\\python.exe", python, ARRAYSIZE(python)) ||
        !join_path(directory, L"app\\启动飞行雪绒.exe", launcher, ARRAYSIZE(launcher))) {
        return FALSE;
    }
    marker_attributes = GetFileAttributesW(marker);
    python_attributes = GetFileAttributesW(python);
    return marker_attributes != INVALID_FILE_ATTRIBUTES &&
        (marker_attributes & FILE_ATTRIBUTE_DIRECTORY) == 0 &&
        (marker_attributes & FILE_ATTRIBUTE_REPARSE_POINT) == 0 &&
        is_regular_file(python) && is_regular_file(launcher);
}

static BOOL choose_safe_subdirectory(const wchar_t *selected, wchar_t *output, size_t capacity) {
    DWORD attributes = GetFileAttributesW(selected);
    wchar_t candidate[FSV_PATH_CAPACITY];
    unsigned int index;
    if (attributes == INVALID_FILE_ATTRIBUTES) {
        return canonicalize_path(selected, output, capacity);
    }
    if ((attributes & FILE_ATTRIBUTE_DIRECTORY) == 0 || (attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
        SetLastError(ERROR_CANT_ACCESS_FILE);
        return FALSE;
    }
    if (is_known_install(selected) || directory_is_empty(selected)) {
        return canonicalize_path(selected, output, capacity);
    }
    for (index = 0; index < 1000; ++index) {
        if (index == 0) {
            if (FAILED(StringCchPrintfW(candidate, ARRAYSIZE(candidate), L"%ls\\飞行雪绒", selected))) {
                SetLastError(ERROR_BUFFER_OVERFLOW);
                return FALSE;
            }
        } else if (FAILED(StringCchPrintfW(candidate, ARRAYSIZE(candidate), L"%ls\\飞行雪绒 (%u)", selected, index + 1))) {
            SetLastError(ERROR_BUFFER_OVERFLOW);
            return FALSE;
        }
        attributes = GetFileAttributesW(candidate);
        if (attributes == INVALID_FILE_ATTRIBUTES) {
            if (!CreateDirectoryW(candidate, NULL)) {
                if (GetLastError() == ERROR_ALREADY_EXISTS) {
                    continue;
                }
                return FALSE;
            }
            return canonicalize_path(candidate, output, capacity);
        }
        if ((attributes & FILE_ATTRIBUTE_DIRECTORY) != 0 &&
            (attributes & FILE_ATTRIBUTE_REPARSE_POINT) == 0 && directory_is_empty(candidate)) {
            return canonicalize_path(candidate, output, capacity);
        }
    }
    SetLastError(ERROR_TOO_MANY_NAMES);
    return FALSE;
}

static BOOL get_default_directory(wchar_t *output, size_t capacity) {
    PWSTR known = NULL;
    BOOL success = FALSE;
    if (SUCCEEDED(SHGetKnownFolderPath(&FOLDERID_ProgramFiles, KF_FLAG_DEFAULT, NULL, &known))) {
        success = SUCCEEDED(StringCchPrintfW(output, capacity, L"%ls\\FlyingSnowVelvet", known));
        CoTaskMemFree(known);
    }
    if (!success) {
        success = SUCCEEDED(StringCchCopyW(output, capacity, L"C:\\Program Files\\FlyingSnowVelvet"));
    }
    return success && canonicalize_path(output, output, capacity);
}

static BOOL choose_install_directory(HWND owner, const wchar_t *initial, wchar_t *output, size_t capacity) {
    IFileDialog *dialog = NULL;
    IShellItem *folder = NULL;
    IShellItem *result = NULL;
    PWSTR path = NULL;
    DWORD options = 0;
    HRESULT hr;
    BOOL success = FALSE;
    hr = CoCreateInstance(&CLSID_FileOpenDialog, NULL, CLSCTX_INPROC_SERVER, &IID_IFileDialog, (void **)&dialog);
    if (FAILED(hr)) {
        SetLastError((DWORD)hr);
        return FALSE;
    }
    if (SUCCEEDED(dialog->lpVtbl->GetOptions(dialog, &options))) {
        dialog->lpVtbl->SetOptions(dialog, options | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_NOCHANGEDIR);
    }
    if (initial != NULL && SUCCEEDED(SHCreateItemFromParsingName(initial, NULL, &IID_IShellItem, (void **)&folder))) {
        dialog->lpVtbl->SetFolder(dialog, folder);
    }
    hr = dialog->lpVtbl->Show(dialog, owner);
    if (SUCCEEDED(hr) && SUCCEEDED(dialog->lpVtbl->GetResult(dialog, &result)) &&
        SUCCEEDED(result->lpVtbl->GetDisplayName(result, SIGDN_FILESYSPATH, &path))) {
        success = choose_safe_subdirectory(path, output, capacity);
    }
    if (path != NULL) {
        CoTaskMemFree(path);
    }
    if (result != NULL) {
        result->lpVtbl->Release(result);
    }
    if (folder != NULL) {
        folder->lpVtbl->Release(folder);
    }
    dialog->lpVtbl->Release(dialog);
    return success;
}

static BOOL read_payload_trailer(HANDLE executable, FsvPayloadTrailer *trailer, ULONGLONG *archive_offset) {
    LARGE_INTEGER file_size;
    LARGE_INTEGER position;
    DWORD received = 0;
    if (!GetFileSizeEx(executable, &file_size) || file_size.QuadPart < (LONGLONG)sizeof(*trailer)) {
        SetLastError(ERROR_BAD_EXE_FORMAT);
        return FALSE;
    }
    position.QuadPart = -(LONGLONG)sizeof(*trailer);
    if (!SetFilePointerEx(executable, position, NULL, FILE_END) ||
        !ReadFile(executable, trailer, sizeof(*trailer), &received, NULL) || received != sizeof(*trailer)) {
        SetLastError(ERROR_BAD_EXE_FORMAT);
        return FALSE;
    }
    if (memcmp(trailer->magic, PAYLOAD_MAGIC, sizeof(PAYLOAD_MAGIC) - 1) != 0 ||
        trailer->archive_size == 0 || trailer->archive_size > (ULONGLONG)file_size.QuadPart - sizeof(*trailer)) {
        SetLastError(ERROR_BAD_EXE_FORMAT);
        return FALSE;
    }
    *archive_offset = (ULONGLONG)file_size.QuadPart - sizeof(*trailer) - trailer->archive_size;
    return TRUE;
}

static DWORD overall_install_percent(DWORD phase, ULONGLONG phase_completed) {
    ULONGLONG total;
    ULONGLONG completed;
    DWORD start;
    DWORD end;
    if (phase == FSV_PHASE_VERIFYING) {
        total = g_context.archive_size;
        start = 0;
        end = FSV_VERIFY_END_PERCENT;
    } else if (phase == FSV_PHASE_EXTRACTING) {
        total = g_context.total_bytes;
        start = FSV_EXTRACT_START_PERCENT;
        end = FSV_EXTRACT_END_PERCENT;
    } else {
        return FSV_SWITCH_PERCENT;
    }
    if (total == 0 || phase_completed >= total) {
        return end;
    }
    completed = (ULONGLONG)(((long double)phase_completed * (long double)(end - start)) / (long double)total);
    if (completed > (ULONGLONG)(end - start)) {
        completed = end - start;
    }
    return start + (DWORD)completed;
}

static void post_progress(const FsvProgressMessage *source) {
    FsvProgressMessage *copy;
    if (g_context.window == NULL || source == NULL) {
        return;
    }
    copy = (FsvProgressMessage *)HeapAlloc(GetProcessHeap(), 0, sizeof(*copy));
    if (copy == NULL) {
        return;
    }
    *copy = *source;
    if (!PostMessageW(g_context.window, WM_FSV_PROGRESS, 0, (LPARAM)copy)) {
        HeapFree(GetProcessHeap(), 0, copy);
    }
}

static void post_status(const wchar_t *text) {
    size_t bytes;
    wchar_t *copy;
    if (g_context.window == NULL || text == NULL) {
        return;
    }
    installer_log(text);
    bytes = (wcslen(text) + 1) * sizeof(wchar_t);
    copy = (wchar_t *)HeapAlloc(GetProcessHeap(), 0, bytes);
    if (copy == NULL) {
        return;
    }
    memcpy(copy, text, bytes);
    if (!PostMessageW(g_context.window, WM_FSV_STATUS, 0, (LPARAM)copy)) {
        HeapFree(GetProcessHeap(), 0, copy);
    }
}

static void close_finished_worker(void) {
    if (g_context.worker != NULL &&
        WaitForSingleObject(g_context.worker, 0) == WAIT_OBJECT_0) {
        CloseHandle(g_context.worker);
        g_context.worker = NULL;
    }
}

static void zip_progress_callback(const FsvZipProgressMessage *source) {
    FsvProgressMessage update;
    ZeroMemory(&update, sizeof(update));
    update.phase = FSV_PHASE_EXTRACTING;
    update.percent = source->scanning_directory
        ? FSV_EXTRACT_START_PERCENT
        : overall_install_percent(FSV_PHASE_EXTRACTING, source->completed_bytes);
    update.completed_files = source->completed_files;
    update.total_files = source->total_files;
    update.completed_bytes = source->completed_bytes;
    update.total_bytes = source->total_bytes;
    update.eta_seconds = source->eta_seconds;
    update.eta_known = source->eta_known;
    StringCchCopyW(update.current_file, ARRAYSIZE(update.current_file), source->current_file);
    if (source->scanning_directory) {
        StringCchCopyW(update.current_file, ARRAYSIZE(update.current_file), L"正在读取归档目录...");
    }
    post_progress(&update);
}

static BOOL write_all(HANDLE file, const BYTE *buffer, DWORD size) {
    DWORD offset = 0;
    while (offset < size) {
        DWORD written = 0;
        if (!WriteFile(file, buffer + offset, size - offset, &written, NULL) || written == 0) {
            return FALSE;
        }
        offset += written;
    }
    return TRUE;
}

static BOOL copy_archive_to_temp(
    HANDLE executable,
    ULONGLONG archive_offset,
    ULONGLONG archive_size,
    const BYTE expected_hash[32],
    const wchar_t *archive_path
) {
    BCRYPT_ALG_HANDLE algorithm = NULL;
    BCRYPT_HASH_HANDLE hash = NULL;
    PUCHAR hash_object = NULL;
    DWORD hash_object_size = 0;
    DWORD ignored_size = 0;
    NTSTATUS status;
    HANDLE output = INVALID_HANDLE_VALUE;
    LARGE_INTEGER position;
    BYTE *buffer = NULL;
    BYTE actual_hash[32];
    ULONGLONG remaining = archive_size;
    ULONGLONG copied = 0;
    ULONGLONG last_post = 0;
    ULONGLONG started_at = GetTickCount64();
    BOOL success = FALSE;
    output = CreateFileW(archive_path, GENERIC_WRITE, 0, NULL, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, NULL);
    if (output == INVALID_HANDLE_VALUE) {
        return FALSE;
    }
    position.QuadPart = (LONGLONG)archive_offset;
    if (!SetFilePointerEx(executable, position, NULL, FILE_BEGIN)) {
        goto cleanup;
    }
    status = BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, NULL, 0);
    if (status != 0 || BCryptGetProperty(algorithm, BCRYPT_OBJECT_LENGTH, (PUCHAR)&hash_object_size, sizeof(hash_object_size), &ignored_size, 0) != 0) {
        SetLastError(ERROR_DLL_INIT_FAILED);
        goto cleanup;
    }
    hash_object = (PUCHAR)HeapAlloc(GetProcessHeap(), 0, hash_object_size);
    buffer = (BYTE *)HeapAlloc(GetProcessHeap(), 0, ARCHIVE_BUFFER_SIZE);
    if (hash_object == NULL || buffer == NULL || BCryptCreateHash(algorithm, &hash, hash_object, hash_object_size, NULL, 0, 0) != 0) {
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
        goto cleanup;
    }
    while (remaining > 0) {
        DWORD requested = remaining > ARCHIVE_BUFFER_SIZE ? ARCHIVE_BUFFER_SIZE : (DWORD)remaining;
        DWORD received = 0;
        if (!ReadFile(executable, buffer, requested, &received, NULL) || received != requested ||
            BCryptHashData(hash, buffer, received, 0) != 0 || !write_all(output, buffer, received)) {
            goto cleanup;
        }
        remaining -= received;
        copied += received;
        {
            ULONGLONG now = GetTickCount64();
            if (last_post == 0 || now - last_post >= 80 || remaining == 0) {
                FsvProgressMessage update;
                ULONGLONG elapsed = now - started_at;
                ZeroMemory(&update, sizeof(update));
                update.phase = FSV_PHASE_VERIFYING;
                update.percent = overall_install_percent(FSV_PHASE_VERIFYING, copied);
                update.completed_bytes = copied;
                update.total_bytes = archive_size;
                if (copied > 0 && elapsed >= 250 &&
                    g_context.archive_size <= ~(ULONGLONG)0 - g_context.total_bytes) {
                    double bytes_per_second = ((double)copied * 1000.0) / (double)elapsed;
                    double total_work = (double)(g_context.archive_size + g_context.total_bytes);
                    if (bytes_per_second > 1.0 && total_work > (double)copied) {
                        update.eta_seconds = (ULONGLONG)(((total_work - (double)copied) / bytes_per_second) + 0.5);
                        update.eta_known = TRUE;
                    }
                }
                StringCchCopyW(update.current_file, ARRAYSIZE(update.current_file), remaining == 0 ? L"正在完成内置归档 SHA-256 校验..." : L"正在复制并校验内置归档...");
                post_progress(&update);
                last_post = now;
            }
        }
    }
    {
        FsvProgressMessage update;
        ZeroMemory(&update, sizeof(update));
        update.phase = FSV_PHASE_VERIFYING;
        update.percent = overall_install_percent(FSV_PHASE_VERIFYING, archive_size);
        update.completed_bytes = archive_size;
        update.total_bytes = archive_size;
        StringCchCopyW(update.current_file, ARRAYSIZE(update.current_file), L"正在完成内置归档 SHA-256 校验...");
        post_progress(&update);
    }
    status = BCryptFinishHash(hash, actual_hash, sizeof(actual_hash), 0);
    if (status != 0 || memcmp(actual_hash, expected_hash, sizeof(actual_hash)) != 0) {
        SetLastError(ERROR_CRC);
        goto cleanup;
    }
    if (!CloseHandle(output)) {
        output = INVALID_HANDLE_VALUE;
        goto cleanup;
    }
    output = INVALID_HANDLE_VALUE;
    success = TRUE;
    {
        FsvProgressMessage update;
        ZeroMemory(&update, sizeof(update));
        update.phase = FSV_PHASE_VERIFYING;
        update.percent = overall_install_percent(FSV_PHASE_VERIFYING, archive_size);
        update.completed_bytes = archive_size;
        update.total_bytes = archive_size;
        StringCchCopyW(update.current_file, ARRAYSIZE(update.current_file), L"内置归档校验完成，正在初始化内置解压器...");
        post_progress(&update);
    }

cleanup:
    if (hash != NULL) {
        BCryptDestroyHash(hash);
    }
    if (algorithm != NULL) {
        BCryptCloseAlgorithmProvider(algorithm, 0);
    }
    HeapFree(GetProcessHeap(), 0, hash_object);
    HeapFree(GetProcessHeap(), 0, buffer);
    if (output != INVALID_HANDLE_VALUE) {
        CloseHandle(output);
    }
    if (!success) {
        DeleteFileW(archive_path);
    }
    return success;
}

static BOOL payload_marker_ready(const wchar_t *staging) {
    wchar_t marker[FSV_PATH_CAPACITY];
    wchar_t python[FSV_PATH_CAPACITY];
    wchar_t py_ini[FSV_PATH_CAPACITY];
    wchar_t launcher[FSV_PATH_CAPACITY];
    wchar_t uninstaller[FSV_PATH_CAPACITY];
    HANDLE file;
    BYTE content[sizeof(PAYLOAD_MAGIC) - 1];
    DWORD received = 0;
    if (!join_path(staging, L".fsv-install-root", marker, ARRAYSIZE(marker)) ||
        !join_path(staging, L"runtime\\python311\\python.exe", python, ARRAYSIZE(python)) ||
        !join_path(staging, L"app\\py.ini", py_ini, ARRAYSIZE(py_ini)) ||
        !join_path(staging, L"app\\启动飞行雪绒.exe", launcher, ARRAYSIZE(launcher)) ||
        !join_path(staging, L"app\\卸载飞行雪绒.exe", uninstaller, ARRAYSIZE(uninstaller))) {
        SetLastError(ERROR_BUFFER_OVERFLOW);
        return FALSE;
    }
    file = CreateFileW(marker, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) {
        return FALSE;
    }
    if (!ReadFile(file, content, sizeof(content), &received, NULL) || received != sizeof(content) ||
        memcmp(content, PAYLOAD_MAGIC, sizeof(content)) != 0) {
        CloseHandle(file);
        SetLastError(ERROR_BAD_FORMAT);
        return FALSE;
    }
    CloseHandle(file);
    return is_regular_file(python) && is_regular_file(py_ini) &&
        is_regular_file(launcher) && is_regular_file(uninstaller);
}

static BOOL download_resource_archive(const wchar_t *archive_path) {
    DWORD index;
    BOOL success = FALSE;
    DWORD last_error = ERROR_INTERNET_CANNOT_CONNECT;
    /* Interleave mirrors across four bounded timeout rounds: 2s, 4s, 8s,
       16s. A failed fast mirror immediately gives the other mirror a turn. */
    for (index = 0; index < ARRAYSIZE(RESOURCE_URLS) * 4; ++index) {
        DWORD mirror_index = index % ARRAYSIZE(RESOURCE_URLS);
        DWORD attempt = index / ARRAYSIZE(RESOURCE_URLS);
        HINTERNET internet = NULL;
        HINTERNET request = NULL;
        HANDLE output = INVALID_HANDLE_VALUE;
        wchar_t detail[FSV_PATH_CAPACITY + 128];
        DWORD timeout = 2000u << attempt;
        DWORD receive_timeout = 2000u << attempt;
        DWORD status = 0;
        DWORD status_size = sizeof(status);
        DWORD content_length = 0;
        DWORD content_size = sizeof(content_length);
        ULONGLONG received = 0;
        ULONGLONG last_reported = 0;
        DWORD started_ms = GetTickCount();
        BYTE *buffer = NULL;
        installer_log(L"资源下载开始");
        StringCchPrintfW(detail, ARRAYSIZE(detail), L"镜像 %lu，第 %lu 轮：%ls", mirror_index + 1, attempt + 1, RESOURCE_URLS[mirror_index]);
        installer_log(detail);
        DeleteFileW(archive_path);
        internet = InternetOpenW(L"FlyingSnowVelvetInstaller/1.0", INTERNET_OPEN_TYPE_PRECONFIG, NULL, NULL, 0);
        if (internet == NULL) { last_error = GetLastError(); installer_log_error(L"InternetOpenW 失败", last_error); continue; }
        InternetSetOptionW(internet, INTERNET_OPTION_CONNECT_TIMEOUT, &timeout, sizeof(timeout));
        InternetSetOptionW(internet, INTERNET_OPTION_RECEIVE_TIMEOUT, &receive_timeout, sizeof(receive_timeout));
        request = InternetOpenUrlW(internet, RESOURCE_URLS[mirror_index], NULL, 0, INTERNET_FLAG_RELOAD | INTERNET_FLAG_NO_CACHE_WRITE | INTERNET_FLAG_RESYNCHRONIZE, 0);
        if (request == NULL) { last_error = GetLastError(); installer_log_error(L"InternetOpenUrlW 失败", last_error); InternetCloseHandle(internet); continue; }
        if (!HttpQueryInfoW(request, HTTP_QUERY_STATUS_CODE | HTTP_QUERY_FLAG_NUMBER, &status, &status_size, NULL) || status < 200 || status >= 300) {
            last_error = status ? ERROR_HTTP_INVALID_SERVER_RESPONSE : GetLastError();
            installer_log_error(L"资源 HTTP 状态无效", last_error);
            InternetCloseHandle(request); InternetCloseHandle(internet); continue;
        }
        HttpQueryInfoW(request, HTTP_QUERY_CONTENT_LENGTH | HTTP_QUERY_FLAG_NUMBER, &content_length, &content_size, NULL);
        output = CreateFileW(archive_path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
        buffer = (BYTE *)HeapAlloc(GetProcessHeap(), 0, ARCHIVE_BUFFER_SIZE);
        if (output == INVALID_HANDLE_VALUE || buffer == NULL) {
            last_error = GetLastError(); installer_log_error(L"创建资源临时文件失败", last_error); goto download_cleanup;
        }
        for (;;) {
            DWORD read = 0;
            DWORD written = 0;
            if (!InternetReadFile(request, buffer, ARCHIVE_BUFFER_SIZE, &read)) { last_error = GetLastError(); installer_log_error(L"InternetReadFile 失败", last_error); goto download_cleanup; }
            if (read == 0) break;
            if (!WriteFile(output, buffer, read, &written, NULL) || written != read) { last_error = GetLastError(); installer_log_error(L"写入资源临时文件失败", last_error); goto download_cleanup; }
            received += read;
            if (content_length > 0) {
                DWORD elapsed_ms = GetTickCount() - started_ms;
                DWORD percent = (DWORD)((received * 100) / content_length);
                if (percent > 100) percent = 100;
                PostMessageW(g_context.window, WM_FSV_PROGRESS, (WPARAM)percent, 0);
                if (received == content_length || received - last_reported >= 4 * 1024 * 1024) {
                    wchar_t progress_text[256];
                    double speed = elapsed_ms > 0 ? ((double)received / 1048576.0) / ((double)elapsed_ms / 1000.0) : 0.0;
                    StringCchPrintfW(progress_text, ARRAYSIZE(progress_text), L"正在下载资源：%lu%%    已下载 %.1f / %.1f MB    速度 %.1f MB/s", percent, (double)received / 1048576.0, (double)content_length / 1048576.0, speed);
                    post_status(progress_text);
                    last_reported = received;
                }
            }
        }
        if (content_length > 0 && received != content_length) { last_error = ERROR_HANDLE_EOF; installer_log_error(L"资源下载长度不完整", last_error); goto download_cleanup; }
        success = TRUE;
        installer_log(L"资源下载完成");
download_cleanup:
        if (output != INVALID_HANDLE_VALUE) CloseHandle(output);
        if (buffer != NULL) HeapFree(GetProcessHeap(), 0, buffer);
        if (request != NULL) InternetCloseHandle(request);
        if (internet != NULL) InternetCloseHandle(internet);
        if (success && is_regular_file(archive_path)) return TRUE;
        DeleteFileW(archive_path);
        if (success) success = FALSE;
    }
    SetLastError(last_error);
    installer_log_error(L"所有资源镜像均下载失败", last_error);
    return FALSE;
}

static BOOL delete_tree(const wchar_t *directory) {
    wchar_t pattern[FSV_PATH_CAPACITY];
    WIN32_FIND_DATAW data;
    HANDLE handle;
    BOOL success = TRUE;
    if (!join_path(directory, L"*", pattern, ARRAYSIZE(pattern))) {
        return FALSE;
    }
    handle = FindFirstFileW(pattern, &data);
    if (handle != INVALID_HANDLE_VALUE) {
        do {
            wchar_t child[FSV_PATH_CAPACITY];
            if (wcscmp(data.cFileName, L".") == 0 || wcscmp(data.cFileName, L"..") == 0) {
                continue;
            }
            if (!join_path(directory, data.cFileName, child, ARRAYSIZE(child))) {
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
        } while (FindNextFileW(handle, &data));
        if (GetLastError() != ERROR_NO_MORE_FILES) {
            success = FALSE;
        }
        FindClose(handle);
    } else if (GetLastError() != ERROR_FILE_NOT_FOUND) {
        return FALSE;
    }
    if (success && !RemoveDirectoryW(directory) && GetLastError() != ERROR_PATH_NOT_FOUND) {
        success = FALSE;
    }
    return success;
}

static BOOL replace_install_directory(const wchar_t *target, const wchar_t *staging) {
    DWORD attributes = GetFileAttributesW(target);
    wchar_t backup[FSV_PATH_CAPACITY];
    DWORD attempt;
    if (attributes == INVALID_FILE_ATTRIBUTES) {
        return MoveFileExW(staging, target, MOVEFILE_WRITE_THROUGH);
    }
    if ((attributes & FILE_ATTRIBUTE_DIRECTORY) == 0 || (attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
        SetLastError(ERROR_CANT_ACCESS_FILE);
        return FALSE;
    }
    if (!is_known_install(target) && !directory_is_empty(target)) {
        SetLastError(ERROR_DIR_NOT_EMPTY);
        return FALSE;
    }
    if (is_known_install(target)) {
        BOOL backup_named = FALSE;
        BOOL target_moved = FALSE;
        DWORD move_error = ERROR_SUCCESS;
        for (attempt = 0; attempt < 32; ++attempt) {
            if (SUCCEEDED(StringCchPrintfW(
                    backup,
                    ARRAYSIZE(backup),
                    L"%ls.fsv-previous-%lu-%lu-%lu",
                    target,
                    GetCurrentProcessId(),
                    GetCurrentThreadId(),
                    GetTickCount() + attempt)) &&
                GetFileAttributesW(backup) == INVALID_FILE_ATTRIBUTES) {
                backup_named = TRUE;
                break;
            }
        }
        if (!backup_named) {
            SetLastError(ERROR_TOO_MANY_NAMES);
            return FALSE;
        }
        if (!MoveFileExW(target, backup, MOVEFILE_WRITE_THROUGH)) {
            return FALSE;
        }
        target_moved = TRUE;
        if (!MoveFileExW(staging, target, MOVEFILE_WRITE_THROUGH)) {
            move_error = GetLastError();
            if (target_moved && !MoveFileExW(backup, target, MOVEFILE_WRITE_THROUGH)) {
                SetLastError(move_error);
                return FALSE;
            }
            SetLastError(move_error);
            return FALSE;
        }
        if (!delete_tree(backup)) {
            /* A valid new install is preferable to failing after the switch. */
            SetLastError(ERROR_SUCCESS);
        }
        return TRUE;
    }
    if (!RemoveDirectoryW(target)) {
        return FALSE;
    }
    return MoveFileExW(staging, target, MOVEFILE_WRITE_THROUGH);
}

static BOOL make_staging_directory_name(const wchar_t *install_directory, wchar_t *output, size_t capacity, DWORD attempt) {
    wchar_t existing[FSV_PATH_CAPACITY];
    wchar_t volume[FSV_PATH_CAPACITY];
    if (!find_existing_ancestor(install_directory, existing, ARRAYSIZE(existing)) ||
        !GetVolumePathNameW(existing, volume, ARRAYSIZE(volume)) ||
        FAILED(StringCchPrintfW(output, capacity, L"%lsFSV-%lu-%lu-%lu", volume, GetCurrentProcessId(), GetCurrentThreadId(), GetTickCount() + attempt))) {
        SetLastError(ERROR_BUFFER_OVERFLOW);
        return FALSE;
    }
    return TRUE;
}

static DWORD WINAPI install_worker(void *parameter) {
    InstallContext *context = (InstallContext *)parameter;
    wchar_t executable_path[FSV_PATH_CAPACITY];
    wchar_t staging[FSV_PATH_CAPACITY];
    wchar_t archive_path[FSV_PATH_CAPACITY];
    FsvPayloadTrailer trailer;
    ULONGLONG archive_offset = 0;
    HANDLE executable = INVALID_HANDLE_VALUE;
    DWORD module_length;
    DWORD result = ERROR_INSTALL_FAILURE;
    ULONGLONG available_bytes = 0;
    BOOL staging_created = FALSE;
    BOOL archive_created = FALSE;
    DWORD staging_attempt;

    installer_log(L"安装生命周期开始");

    post_status(L"正在验证安装器内置 payload...");
    module_length = GetModuleFileNameW(NULL, executable_path, ARRAYSIZE(executable_path));
    if (module_length == 0 || module_length >= ARRAYSIZE(executable_path)) {
        set_install_error_win32(context, L"无法定位安装器自身", module_length == 0 ? GetLastError() : ERROR_BUFFER_OVERFLOW);
        goto cleanup;
    }
    executable = CreateFileW(executable_path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (executable == INVALID_HANDLE_VALUE || !read_payload_trailer(executable, &trailer, &archive_offset)) {
        set_install_error_win32(context, L"安装器内置 payload 无效或已损坏", GetLastError());
        goto cleanup;
    }
    if (!query_available_space(context->install_directory, &available_bytes)) {
        set_install_error_win32(context, L"无法读取目标磁盘可用空间", GetLastError());
        goto cleanup;
    }
    if (available_bytes < context->required_bytes) {
        set_install_error(context, L"目标磁盘可用空间不足，请返回后选择其他位置。" );
        goto cleanup;
    }
    for (staging_attempt = 0; staging_attempt < 32; ++staging_attempt) {
        DWORD attributes;
        if (!make_staging_directory_name(context->install_directory, staging, ARRAYSIZE(staging), staging_attempt)) {
            set_install_error_win32(context, L"无法创建临时安装目录", GetLastError());
            goto cleanup;
        }
        attributes = GetFileAttributesW(staging);
        if (attributes != INVALID_FILE_ATTRIBUTES) {
            SetLastError(ERROR_ALREADY_EXISTS);
            continue;
        }
        if (CreateDirectoryW(staging, NULL)) {
            break;
        }
        if (GetLastError() != ERROR_ALREADY_EXISTS) {
            set_install_error_win32(context, L"无法创建临时安装目录", GetLastError());
            goto cleanup;
        }
    }
    if (staging_attempt == 32) {
        set_install_error(context, L"无法创建唯一的临时安装目录，请稍后重试。" );
        goto cleanup;
    }
    staging_created = TRUE;
    if (!join_path(staging, L".fsv-payload.zip", archive_path, ARRAYSIZE(archive_path))) {
        set_install_error(context, L"无法创建临时归档路径。" );
        goto cleanup;
    }
    post_status(L"正在复制并校验内置归档...");
    if (!copy_archive_to_temp(executable, archive_offset, trailer.archive_size, trailer.archive_sha256, archive_path)) {
        set_install_error_win32(context, L"内置 payload 校验失败", GetLastError());
        goto cleanup;
    }
    archive_created = TRUE;
    post_status(L"正在使用内置解压器展开文件...");
    if (!fsv_extract_zip(archive_path, staging, zip_progress_callback)) {
        set_install_error_win32(context, L"解压离线 payload 失败", GetLastError());
        goto cleanup;
    }
    if (!DeleteFileW(archive_path)) {
        set_install_error_win32(context, L"无法清理临时内置归档", GetLastError());
        goto cleanup;
    }
    archive_created = FALSE;
    /* Online installers carry only a marker archive. Fetch the full resource
       ZIP from the fixed mirrors when that marker is present. */
    {
        wchar_t online_marker[FSV_PATH_CAPACITY];
        if (join_path(staging, L".fsv-online-resource-required", online_marker, ARRAYSIZE(online_marker)) &&
            is_regular_file(online_marker)) {
            post_status(L"正在连接资源镜像，准备下载资源包...");
            if (!download_resource_archive(archive_path)) {
                set_install_error_win32(context, L"在线资源包下载或解压失败", GetLastError());
                goto cleanup;
            }
            post_status(L"资源下载完成，正在解压资源包...");
            /* The marker archive is only a bootstrap. The full resource ZIP
               contains the real install marker with the same path; remove
               the bootstrap marker before extraction so the native extractor
               never treats the expected replacement as a collision. */
            {
                wchar_t bootstrap_root[FSV_PATH_CAPACITY];
                if (join_path(staging, L".fsv-install-root", bootstrap_root, ARRAYSIZE(bootstrap_root))) {
                    DeleteFileW(bootstrap_root);
                }
                DeleteFileW(online_marker);
            }
            if (!fsv_extract_zip(archive_path, staging, zip_progress_callback)) {
                set_install_error_win32(context, L"在线资源包下载或解压失败", GetLastError());
                goto cleanup;
            }
            archive_created = TRUE;
            if (!DeleteFileW(archive_path)) {
                set_install_error_win32(context, L"无法清理临时资源包", GetLastError());
                goto cleanup;
            }
            archive_created = FALSE;
            PostMessageW(context->window, WM_FSV_PROGRESS, 100, 0);
        }
    }
    post_status(L"正在校验安装文件...");
    if (!payload_marker_ready(staging)) {
        set_install_error_win32(context, L"安装文件校验失败", GetLastError());
        goto cleanup;
    }
    post_status(L"正在切换到新安装...");
    {
        FsvProgressMessage update;
        ZeroMemory(&update, sizeof(update));
        update.phase = FSV_PHASE_SWITCHING;
        update.percent = FSV_SWITCH_PERCENT;
        update.completed_files = context->total_files;
        update.total_files = context->total_files;
        update.completed_bytes = context->total_bytes;
        update.total_bytes = context->total_bytes;
        StringCchCopyW(update.current_file, ARRAYSIZE(update.current_file), L"安装文件校验完成，正在完成目录切换...");
        post_progress(&update);
    }
    if (!replace_install_directory(context->install_directory, staging)) {
        set_install_error_win32(context, L"无法完成安装目录切换，请确认桌宠未运行且目录可写", GetLastError());
        goto cleanup;
    }
    staging_created = FALSE;
    if (!copy_update_state(context->install_directory)) {
        /* The application is already installed; retain the successful switch
           and report a recoverable state-file warning instead of rolling back. */
        post_status(L"程序文件已安装，更新状态文件未能写入；下次启动将重新检查版本。");
    }
    context->installed = TRUE;
    {
        FsvProgressMessage update;
        ZeroMemory(&update, sizeof(update));
        update.phase = FSV_PHASE_SWITCHING;
        update.percent = 100;
        update.completed_files = context->total_files;
        update.total_files = context->total_files;
        update.completed_bytes = context->total_bytes;
        update.total_bytes = context->total_bytes;
        StringCchCopyW(update.current_file, ARRAYSIZE(update.current_file), L"全部文件已解压并校验完成。" );
        post_progress(&update);
    }
    post_status(L"安装完成，文件校验已通过。" );
    result = ERROR_SUCCESS;

cleanup:
    if (executable != INVALID_HANDLE_VALUE) {
        CloseHandle(executable);
    }
    if (archive_created) {
        DeleteFileW(archive_path);
    }
    if (staging_created) {
        delete_tree(staging);
    }
    if (g_update_state_source[0] != L'\0') {
        DeleteFileW(g_update_state_source);
    }
    context->result = result;
    installer_log_error(result == ERROR_SUCCESS ? L"安装生命周期完成" : L"安装生命周期失败", result);
    context->installing = FALSE;
    context->completed = TRUE;
    if (context->window != NULL) {
        PostMessageW(context->window, WM_FSV_DONE, result, 0);
    }
    return result;
}

static BOOL launch_installed_app(const wchar_t *install_directory) {
    wchar_t launcher[FSV_PATH_CAPACITY];
    wchar_t command_line[FSV_PATH_CAPACITY * 2];
    wchar_t working_directory[FSV_PATH_CAPACITY];
    STARTUPINFOW startup;
    PROCESS_INFORMATION process_info;
    if (!join_path(install_directory, L"app\\启动飞行雪绒.exe", launcher, ARRAYSIZE(launcher)) ||
        !join_path(install_directory, L"app", working_directory, ARRAYSIZE(working_directory)) ||
        GetFileAttributesW(launcher) == INVALID_FILE_ATTRIBUTES ||
        FAILED(StringCchPrintfW(command_line, ARRAYSIZE(command_line), L"\"%ls\"", launcher))) {
        SetLastError(ERROR_FILE_NOT_FOUND);
        return FALSE;
    }
    ZeroMemory(&startup, sizeof(startup));
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESHOWWINDOW;
    startup.wShowWindow = SW_SHOWNORMAL;
    ZeroMemory(&process_info, sizeof(process_info));
    if (!CreateProcessW(launcher, command_line, NULL, NULL, FALSE, CREATE_UNICODE_ENVIRONMENT, NULL, working_directory, &startup, &process_info)) {
        return FALSE;
    }
    CloseHandle(process_info.hThread);
    CloseHandle(process_info.hProcess);
    return TRUE;
}

static void format_bytes(ULONGLONG bytes, wchar_t *output, size_t capacity) {
    static const wchar_t *units[] = {L"B", L"KB", L"MB", L"GB", L"TB"};
    double value = (double)bytes;
    size_t unit = 0;
    while (value >= 1024.0 && unit + 1 < ARRAYSIZE(units)) {
        value /= 1024.0;
        ++unit;
    }
    if (unit == 0) {
        StringCchPrintfW(output, capacity, L"%llu %ls", bytes, units[unit]);
    } else if (value >= 100.0) {
        StringCchPrintfW(output, capacity, L"%.0f %ls", value, units[unit]);
    } else {
        StringCchPrintfW(output, capacity, L"%.1f %ls", value, units[unit]);
    }
}

static void format_duration(ULONGLONG seconds, wchar_t *output, size_t capacity) {
    ULONGLONG hours = seconds / 3600;
    ULONGLONG minutes = (seconds % 3600) / 60;
    ULONGLONG remaining = seconds % 60;
    if (hours > 0) {
        StringCchPrintfW(output, capacity, L"%lluh %02llum", hours, minutes);
    } else {
        StringCchPrintfW(output, capacity, L"%llum %02llus", minutes, remaining);
    }
}

static void show_control(HWND control, BOOL visible) {
    if (control != NULL) {
        ShowWindow(control, visible ? SW_SHOW : SW_HIDE);
    }
}

static void set_page(int page) {
    wchar_t title[256];
    wchar_t subtitle[512];
    wchar_t step[32];
    g_page = page;
    BOOL online = g_context.archive_size != FSV_PAYLOAD_ARCHIVE_BYTES;
    if (page == 1) {
        StringCchCopyW(title, ARRAYSIZE(title), L"选择安装位置");
        StringCchCopyW(subtitle, ARRAYSIZE(subtitle), online ? L"为飞行雪绒选择一个安放的位置，安装时将在线下载资源。" : L"为飞行雪绒选择一个安放的位置。" );
    } else if (page == 2) {
        StringCchCopyW(title, ARRAYSIZE(title), L"确认安装");
        StringCchCopyW(subtitle, ARRAYSIZE(subtitle), online ? L"确认磁盘空间后将下载并安装完整资源。" : L"确认空间与安装位置后开始写入内置文件。" );
    } else if (page == 3) {
        StringCchCopyW(title, ARRAYSIZE(title), L"安装资源");
        StringCchCopyW(subtitle, ARRAYSIZE(subtitle), online ? L"正在下载并解压资源，请保持窗口打开。" : L"正在校验并解压内置资源，请保持窗口打开。" );
    } else if (g_context.installed) {
        StringCchCopyW(title, ARRAYSIZE(title), L"安装完成");
        StringCchCopyW(subtitle, ARRAYSIZE(subtitle), L"飞行雪绒已经准备就绪。" );
    } else {
        StringCchCopyW(title, ARRAYSIZE(title), L"安装未完成");
        StringCchCopyW(subtitle, ARRAYSIZE(subtitle), L"请检查以下信息后重新运行安装器。" );
    }
    StringCchCopyW(step, ARRAYSIZE(step), online ? L"WINDOWS  /  ONLINE" : L"WINDOWS  /  64 BIT");
    SetWindowTextW(g_page_title, title);
    SetWindowTextW(g_page_subtitle, subtitle);
    SetWindowTextW(g_step_label, step);
    show_control(g_path_edit, page <= 2);
    show_control(g_custom_button, page == 1);
    show_control(g_next_button, page == 1);
    show_control(g_space_info, page == 2);
    {
        size_t index;
        for (index = 0; index < ARRAYSIZE(g_space_values); ++index) {
            show_control(g_space_values[index], page == 2);
        }
    }
    show_control(g_back_button, page == 2);
    show_control(g_start_button, page == 2);
    show_control(g_download_progress, page == 3);
    show_control(g_progress, page == 3);
    show_control(g_current_file, page == 3);
    show_control(g_progress_stats, page == 3);
    show_control(g_progress_eta, page == 3);
    show_control(g_status, page == 3);
    show_control(g_done_title, page == 4);
    show_control(g_done_text, page == 4);
    show_control(g_finish_button, page == 4);
    EnableWindow(g_back_button, page == 2 && !g_context.installing);
    g_fade_alpha = 218;
    SetLayeredWindowAttributes(g_window, 0, g_fade_alpha, LWA_ALPHA);
    SetTimer(g_window, FSV_FADE_TIMER, 16, NULL);
    InvalidateRect(g_window, NULL, TRUE);
    SetFocus(page == 1 ? g_next_button : page == 2 ? g_start_button : page == 4 ? g_finish_button : g_window);
}

static BOOL read_embedded_space_info(void) {
    wchar_t executable_path[FSV_PATH_CAPACITY];
    FsvPayloadTrailer trailer;
    ULONGLONG offset;
    HANDLE executable = INVALID_HANDLE_VALUE;
    DWORD module_length;
    BOOL success = FALSE;
    module_length = GetModuleFileNameW(NULL, executable_path, ARRAYSIZE(executable_path));
    if (module_length == 0 || module_length >= ARRAYSIZE(executable_path)) {
        return FALSE;
    }
    executable = CreateFileW(executable_path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (executable == INVALID_HANDLE_VALUE || !read_payload_trailer(executable, &trailer, &offset)) {
        goto cleanup;
    }
    /* Online builds intentionally embed a tiny marker archive, so their
       trailer size differs from the full offline payload metadata. */
    if (trailer.archive_size == FSV_PAYLOAD_ARCHIVE_BYTES) {
        g_context.archive_size = FSV_PAYLOAD_ARCHIVE_BYTES;
        g_context.total_files = FSV_PAYLOAD_FILE_COUNT;
        g_context.total_bytes = FSV_PAYLOAD_UNCOMPRESSED_BYTES;
    } else {
        g_context.archive_size = trailer.archive_size;
        g_context.total_files = FSV_PAYLOAD_FILE_COUNT;
        g_context.total_bytes = FSV_PAYLOAD_UNCOMPRESSED_BYTES;
    }
    g_context.required_bytes = g_context.total_bytes + trailer.archive_size;
    if (g_context.required_bytes < g_context.total_bytes || g_context.required_bytes < trailer.archive_size) {
        g_context.required_bytes = ~(ULONGLONG)0;
    }
    success = TRUE;

cleanup:
    if (executable != INVALID_HANDLE_VALUE) {
        CloseHandle(executable);
    }
    return success;
}

static BOOL normalize_selected_install_directory(void) {
    wchar_t safe_directory[FSV_PATH_CAPACITY];
    if (!choose_safe_subdirectory(
            g_context.install_directory,
            safe_directory,
            ARRAYSIZE(safe_directory))) {
        return FALSE;
    }
    if (wcscmp(safe_directory, g_context.install_directory) != 0) {
        if (FAILED(StringCchCopyW(
                g_context.install_directory,
                ARRAYSIZE(g_context.install_directory),
                safe_directory))) {
            SetLastError(ERROR_BUFFER_OVERFLOW);
            return FALSE;
        }
        SetWindowTextW(g_path_edit, g_context.install_directory);
    }
    return TRUE;
}

static void update_space_text(void) {
    wchar_t required[64];
    wchar_t installed[64];
    wchar_t free_space[64];
    wchar_t text[160];
    ULONGLONG available = 0;
    BOOL space_known;
    BOOL enough;
    format_bytes(g_context.required_bytes, required, ARRAYSIZE(required));
    format_bytes(g_context.total_bytes, installed, ARRAYSIZE(installed));
    space_known = query_available_space(g_context.install_directory, &available);
    if (space_known) {
        format_bytes(available, free_space, ARRAYSIZE(free_space));
    } else {
        StringCchCopyW(free_space, ARRAYSIZE(free_space), L"无法读取");
    }
    enough = space_known && available >= g_context.required_bytes;
    StringCchPrintfW(
        text,
        ARRAYSIZE(text),
        enough ? L"共 %llu 个文件。安装后自动释放临时空间。" : L"共 %llu 个文件。磁盘空间不足或不可用，请返回并更换位置。",
        g_context.total_files
    );
    SetWindowTextW(g_space_values[0], installed);
    SetWindowTextW(g_space_values[1], required);
    SetWindowTextW(g_space_values[2], free_space);
    SetWindowTextW(g_space_info, text);
    EnableWindow(g_start_button, enough);
}

static void create_button(HWND *output, const wchar_t *text, int id, int x, int y, int width, int height) {
    ButtonVisualState *state;
    *output = CreateWindowExW(0, L"BUTTON", text, WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_OWNERDRAW, ui_px(x), ui_px(y), ui_px(width), ui_px(height), g_window, (HMENU)(INT_PTR)id, GetModuleHandleW(NULL), NULL);
    if (*output == NULL || g_button_count >= ARRAYSIZE(g_button_states)) {
        return;
    }
    SendMessageW(*output, WM_SETFONT, (WPARAM)g_body_font, TRUE);
    state = &g_button_states[g_button_count++];
    ZeroMemory(state, sizeof(*state));
    state->window = *output;
    SetWindowSubclass(*output, button_subclass_proc, (UINT_PTR)id, (DWORD_PTR)state);
}

static LRESULT CALLBACK button_subclass_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam, UINT_PTR subclass_id, DWORD_PTR reference_data) {
    ButtonVisualState *state = (ButtonVisualState *)reference_data;
    (void)subclass_id;
    if (message == WM_MOUSEMOVE && state != NULL) {
        TRACKMOUSEEVENT tracking;
        ZeroMemory(&tracking, sizeof(tracking));
        tracking.cbSize = sizeof(tracking);
        tracking.dwFlags = TME_LEAVE;
        tracking.hwndTrack = window;
        TrackMouseEvent(&tracking);
        state->hover_target = TRUE;
        SetTimer(g_window, FSV_BUTTON_TIMER, 16, NULL);
    } else if (message == WM_MOUSELEAVE && state != NULL) {
        state->hover_target = FALSE;
        SetTimer(g_window, FSV_BUTTON_TIMER, 16, NULL);
    } else if (message == WM_NCDESTROY) {
        RemoveWindowSubclass(window, button_subclass_proc, subclass_id);
    }
    return DefSubclassProc(window, message, wparam, lparam);
}

static BOOL create_wizard_window(void) {
    WNDCLASSW window_class;
    RECT work_area;
    RECT bounds = ui_rect(0, 0, FSV_CLIENT_WIDTH, FSV_CLIENT_HEIGHT);
    int width;
    int height;
    int x;
    int y;
    ZeroMemory(&window_class, sizeof(window_class));
    window_class.lpfnWndProc = wizard_window_proc;
    window_class.hInstance = GetModuleHandleW(NULL);
    window_class.hCursor = LoadCursorW(NULL, IDC_ARROW);
    window_class.hbrBackground = g_canvas_brush;
    window_class.hIcon = LoadIconW(GetModuleHandleW(NULL), MAKEINTRESOURCEW(IDI_INSTALLER));
    window_class.hIcon = window_class.hIcon == NULL ? LoadIconW(NULL, IDI_APPLICATION) : window_class.hIcon;
    window_class.lpszClassName = WIZARD_CLASS_NAME;
    if (RegisterClassW(&window_class) == 0 && GetLastError() != ERROR_CLASS_ALREADY_EXISTS) {
        return FALSE;
    }
    AdjustWindowRectExForDpi(&bounds, WS_CAPTION | WS_SYSMENU, FALSE, WS_EX_APPWINDOW | WS_EX_LAYERED, g_dpi);
    width = bounds.right - bounds.left;
    height = bounds.bottom - bounds.top;
    SystemParametersInfoW(SPI_GETWORKAREA, 0, &work_area, 0);
    x = work_area.left + ((work_area.right - work_area.left) - width) / 2;
    y = work_area.top + ((work_area.bottom - work_area.top) - height) / 2;
    g_window = CreateWindowExW(WS_EX_APPWINDOW | WS_EX_LAYERED | WS_EX_CONTROLPARENT, WIZARD_CLASS_NAME, L"飞行雪绒安装器", WS_CAPTION | WS_SYSMENU | WS_CLIPCHILDREN, x, y, width, height, NULL, NULL, GetModuleHandleW(NULL), NULL);
    if (g_window == NULL) {
        return FALSE;
    }
    return TRUE;
}

static HWND create_label(const wchar_t *text, int id, DWORD style) {
    return CreateWindowExW(0, L"STATIC", text, WS_CHILD | WS_VISIBLE | style,
        0, 0, 1, 1, g_window, (HMENU)(INT_PTR)id, GetModuleHandleW(NULL), NULL);
}

static void place_control(HWND control, int x, int y, int width, int height, HFONT font) {
    MoveWindow(control, ui_px(x), ui_px(y), ui_px(width), ui_px(height), TRUE);
    SendMessageW(control, WM_SETFONT, (WPARAM)font, TRUE);
}

static BOOL layout_controls(void) {
    HFONT old_heading = g_heading_font;
    HFONT old_body = g_body_font;
    HFONT old_meta = g_meta_font;
    HICON icon;
    size_t index;
    g_heading_font = CreateFontW(-ui_px(26), 0, 0, 0, FW_BOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, L"HarmonyOS Sans SC");
    g_body_font = CreateFontW(-ui_px(15), 0, 0, 0, FW_BOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, L"HarmonyOS Sans SC");
    g_meta_font = CreateFontW(-ui_px(12), 0, 0, 0, FW_BOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, L"HarmonyOS Sans SC");
    if (g_heading_font == NULL || g_body_font == NULL || g_meta_font == NULL) {
        return FALSE;
    }
    place_control(g_icon, 52, 26, 48, 48, g_body_font);
    icon = (HICON)LoadImageW(GetModuleHandleW(NULL), MAKEINTRESOURCEW(IDI_INSTALLER), IMAGE_ICON, ui_px(48), ui_px(48), LR_SHARED);
    SendMessageW(g_icon, STM_SETICON, (WPARAM)icon, 0);
    place_control(g_step_label, 644, 46, 196, 20, g_meta_font);
    place_control(g_page_title, 40, 180, 800, 40, g_heading_font);
    place_control(g_page_subtitle, 40, 228, 800, 28, g_body_font);
    place_control(g_path_edit, 54, 303, 610, 24, g_body_font);
    SendMessageW(g_path_edit, EM_SETMARGINS, EC_LEFTMARGIN | EC_RIGHTMARGIN, 0);
    place_control(g_custom_button, 688, 294, 152, 42, g_body_font);
    place_control(g_next_button, 680, 510, 160, 40, g_body_font);
    place_control(g_back_button, 40, 510, 112, 40, g_body_font);
    place_control(g_start_button, 680, 510, 160, 40, g_body_font);
    place_control(g_space_info, 40, 436, 800, 42, g_meta_font);
    for (index = 0; index < ARRAYSIZE(g_space_values); ++index) {
        place_control(g_space_values[index], 40 + (int)index * 272, 382, 240, 42, g_heading_font);
    }
    place_control(g_status, 40, 280, 800, 44, g_body_font);
    place_control(g_download_progress, 40, 326, 800, 10, g_body_font);
    place_control(g_progress, 40, 350, 800, 12, g_body_font);
    place_control(g_current_file, 40, 368, 800, 28, g_meta_font);
    place_control(g_progress_stats, 40, 408, 800, 24, g_body_font);
    place_control(g_progress_eta, 40, 442, 800, 24, g_meta_font);
    place_control(g_done_title, 40, 284, 800, 36, g_body_font);
    place_control(g_done_text, 40, 336, 800, 140, g_body_font);
    place_control(g_finish_button, 548, 510, 292, 40, g_body_font);
    if (old_heading != NULL) DeleteObject(old_heading);
    if (old_body != NULL) DeleteObject(old_body);
    if (old_meta != NULL) DeleteObject(old_meta);
    return TRUE;
}

static BOOL create_controls(void) {
    size_t index;
    if (!load_embedded_harmony_font()) {
        installer_log_error(L"安装器特供字体加载失败，回退系统字体", GetLastError());
    }
    g_icon = create_label(NULL, IDC_PRODUCT_ICON, SS_ICON);
    g_page_title = create_label(L"", IDC_PAGE_TITLE, SS_LEFT);
    g_page_subtitle = create_label(L"", IDC_PAGE_SUBTITLE, SS_LEFT);
    g_step_label = create_label(L"", 0, SS_RIGHT);
    g_path_edit = CreateWindowExW(0, L"EDIT", g_context.install_directory,
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | ES_AUTOHSCROLL | ES_READONLY,
        0, 0, 1, 1, g_window, (HMENU)(INT_PTR)IDC_PATH_EDIT, GetModuleHandleW(NULL), NULL);
    create_button(&g_custom_button, L"更改位置", IDC_CUSTOM, 0, 0, 1, 1);
    create_button(&g_next_button, L"下一步", IDC_NEXT, 0, 0, 1, 1);
    create_button(&g_back_button, L"返回", IDC_BACK, 0, 0, 1, 1);
    create_button(&g_start_button, L"开始安装", IDC_START, 0, 0, 1, 1);
    create_button(&g_finish_button, L"退出安装并启动飞行雪绒", IDC_FINISH, 0, 0, 1, 1);
    g_space_info = create_label(L"", IDC_SPACE_INFO, SS_LEFT);
    for (index = 0; index < ARRAYSIZE(g_space_values); ++index) {
        g_space_values[index] = create_label(L"", 1020 + (int)index, SS_LEFT);
        if (g_space_values[index] == NULL) return FALSE;
    }
    g_status = create_label(L"正在准备...", IDC_STATUS, SS_LEFT);
    g_current_file = create_label(L"当前文件：正在准备...", IDC_CURRENT_FILE, SS_LEFT | SS_PATHELLIPSIS);
    g_progress = CreateWindowExW(0, PROGRESS_CLASSW, NULL, WS_CHILD | PBS_SMOOTH, 0, 0, 1, 1, g_window, (HMENU)(INT_PTR)IDC_PROGRESS, GetModuleHandleW(NULL), NULL);
    /* Reuse the established progress control id so accessibility/visual
       harnesses treat this as a progress indicator rather than a text field. */
    g_download_progress = CreateWindowExW(0, PROGRESS_CLASSW, NULL, WS_CHILD | PBS_SMOOTH, 0, 0, 1, 1, g_window, (HMENU)(INT_PTR)IDC_PROGRESS, GetModuleHandleW(NULL), NULL);
    g_progress_stats = create_label(L"已解压文件：0 / 0", IDC_PROGRESS_STATS, SS_LEFT);
    g_progress_eta = create_label(L"预计剩余：正在计算...", IDC_PROGRESS_ETA, SS_LEFT);
    g_done_title = create_label(L"文件校验通过", IDC_DONE_TITLE, SS_LEFT);
    g_done_text = create_label(L"安装文件已校验完成。", IDC_DONE_TEXT, SS_LEFT | SS_EDITCONTROL);
    SendMessageW(g_progress, PBM_SETRANGE32, 0, 100);
    SendMessageW(g_download_progress, PBM_SETRANGE32, 0, 100);
    SendMessageW(g_download_progress, PBM_SETPOS, 100, 0);
    SendMessageW(g_progress, PBM_SETPOS, 0, 0);
    SendMessageW(g_progress, PBM_SETBARCOLOR, 0, FSV_COLOR_PINK);
    SendMessageW(g_progress, PBM_SETBKCOLOR, 0, FSV_COLOR_SURFACE_RAISED);
    SetWindowTheme(g_progress, L"", L"");
    {
        HWND controls[] = {g_page_title, g_page_subtitle, g_step_label, g_path_edit, g_custom_button, g_next_button, g_space_info, g_back_button, g_start_button, g_status, g_current_file, g_progress, g_progress_stats, g_progress_eta, g_done_title, g_done_text, g_finish_button};
        size_t control_index;
        for (control_index = 0; control_index < ARRAYSIZE(controls); ++control_index) {
            if (controls[control_index] == NULL) {
                return FALSE;
            }
        }
    }
    if (!layout_controls()) return FALSE;
    set_page(1);
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
    HBRUSH brush;
    HPEN pen;
    HGDIOBJ old_brush;
    HGDIOBJ old_pen;
    RECT rect = item->rcItem;
    COLORREF fill_color;
    COLORREF border_color;
    COLORREF text_color;
    BOOL primary = item->CtlID != IDC_CUSTOM && item->CtlID != IDC_BACK;
    int hover = 0;
    size_t index;
    for (index = 0; index < g_button_count; ++index) {
        if (g_button_states[index].window == item->hwndItem) {
            hover = g_button_states[index].hover_amount;
            break;
        }
    }
    if ((item->itemState & ODS_DISABLED) != 0) {
        fill_color = FSV_COLOR_SURFACE_RAISED;
        border_color = FSV_COLOR_BORDER;
        text_color = FSV_COLOR_TEXT_DIM;
    } else {
        COLORREF base = primary ? FSV_COLOR_PINK : FSV_COLOR_SURFACE_RAISED;
        COLORREF hot = primary ? FSV_COLOR_PINK_HOVER : FSV_COLOR_SURFACE_HOVER;
        fill_color = mix_color(base, hot, hover);
        if ((item->itemState & ODS_SELECTED) != 0) {
            fill_color = primary ? FSV_COLOR_CYAN : FSV_COLOR_BORDER_STRONG;
        }
        border_color = primary ? fill_color : hover > 0 ? FSV_COLOR_CYAN : FSV_COLOR_BORDER;
        text_color = primary ? FSV_COLOR_CANVAS : FSV_COLOR_TEXT;
    }
    FillRect(item->hDC, &rect, item->CtlID == IDC_CUSTOM ? g_surface_brush : g_canvas_brush);
    brush = CreateSolidBrush(fill_color);
    pen = CreatePen(PS_SOLID, 1, border_color);
    old_brush = SelectObject(item->hDC, brush);
    old_pen = SelectObject(item->hDC, pen);
    RoundRect(item->hDC, rect.left, rect.top, rect.right, rect.bottom, ui_px(8), ui_px(8));
    SelectObject(item->hDC, old_pen);
    SelectObject(item->hDC, old_brush);
    DeleteObject(pen);
    DeleteObject(brush);
    SetBkMode(item->hDC, TRANSPARENT);
    SetTextColor(item->hDC, text_color);
    {
        wchar_t text[128];
        HGDIOBJ old_font = SelectObject(item->hDC, g_body_font);
        GetWindowTextW(item->hwndItem, text, ARRAYSIZE(text));
        DrawTextW(item->hDC, text, -1, &rect, DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX);
        SelectObject(item->hDC, old_font);
    }
    if ((item->itemState & ODS_FOCUS) != 0) {
        InflateRect(&rect, -3, -3);
        DrawFocusRect(item->hDC, &rect);
    }
}

static void draw_round_panel(HDC device_context, const RECT *bounds, COLORREF fill_color, COLORREF border_color, int radius) {
    HBRUSH fill_brush = CreateSolidBrush(fill_color);
    HPEN border_pen = CreatePen(PS_SOLID, 1, border_color);
    HGDIOBJ old_brush = SelectObject(device_context, fill_brush);
    HGDIOBJ old_pen = SelectObject(device_context, border_pen);
    RoundRect(device_context, bounds->left, bounds->top, bounds->right, bounds->bottom, radius, radius);
    SelectObject(device_context, old_pen);
    SelectObject(device_context, old_brush);
    DeleteObject(border_pen);
    DeleteObject(fill_brush);
}

static void draw_text_block(HDC device_context, HFONT font, COLORREF text_color, const wchar_t *text, RECT bounds, UINT format) {
    HGDIOBJ old_font = SelectObject(device_context, font);
    SetBkMode(device_context, TRANSPARENT);
    SetTextColor(device_context, text_color);
    DrawTextW(device_context, text, -1, &bounds, format | DT_NOPREFIX);
    SelectObject(device_context, old_font);
}

static void fill_color_rect(HDC dc, RECT rect, COLORREF color) {
    HBRUSH brush = CreateSolidBrush(color);
    FillRect(dc, &rect, brush);
    DeleteObject(brush);
}

static void draw_announcement_background(HDC dc) {
    RECT client;
    int index;
    const wchar_t *steps[] = {L"选择目录", L"空间确认", L"安装资源", L"安装完成"};
    GetClientRect(g_window, &client);
    FillRect(dc, &client, g_canvas_brush);
    fill_color_rect(dc, ui_rect(0, 96, FSV_CLIENT_WIDTH, 398), FSV_COLOR_SURFACE);
    fill_color_rect(dc, ui_rect(0, 96, FSV_CLIENT_WIDTH, 1), FSV_COLOR_BORDER);
    fill_color_rect(dc, ui_rect(0, 494, FSV_CLIENT_WIDTH, 1), FSV_COLOR_BORDER);
    fill_color_rect(dc, ui_rect(32, 29, 3, 44), FSV_COLOR_PINK);
    fill_color_rect(dc, ui_rect(35, 29, 1, 44), FSV_COLOR_CYAN);
    draw_text_block(dc, g_heading_font, FSV_COLOR_TEXT, L"飞行雪绒", ui_rect(116, 24, 360, 38), DT_LEFT | DT_VCENTER | DT_SINGLELINE);
    draw_text_block(dc, g_meta_font, FSV_COLOR_TEXT_DIM,
        g_context.archive_size != FSV_PAYLOAD_ARCHIVE_BYTES ? L"在线安装  /  ONLINE SETUP" : L"离线安装  /  OFFLINE SETUP",
        ui_rect(118, 64, 400, 18), DT_LEFT | DT_SINGLELINE);

    for (index = 0; index < 4; ++index) {
        BOOL active = index == (g_page > 3 ? 3 : g_page - 1);
        BOOL done = index < g_page - 1 && (g_page != 4 || g_context.installed);
        int x = 28 + index * 210;
        wchar_t number[8];
        RECT square = ui_rect(x, 120, 28, 28);
        COLORREF accent = active ? FSV_COLOR_PINK : done ? FSV_COLOR_CYAN : FSV_COLOR_BORDER;
        draw_round_panel(dc, &square, active ? FSV_COLOR_SURFACE_HOVER : FSV_COLOR_SURFACE_RAISED, accent, ui_px(6));
        StringCchPrintfW(number, ARRAYSIZE(number), L"%02d", index + 1);
        draw_text_block(dc, g_meta_font, FSV_COLOR_TEXT, number, square, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        draw_text_block(dc, g_body_font, active ? FSV_COLOR_TEXT : FSV_COLOR_TEXT_DIM, steps[index], ui_rect(x + 34, 120, 128, 28), DT_LEFT | DT_VCENTER | DT_SINGLELINE);
        if (index < 3) fill_color_rect(dc, ui_rect(x + 168, 133, 42, 1), accent);
    }
    if (g_page <= 2) {
        RECT field = ui_rect(40, 294, 638, 42);
        draw_text_block(dc, g_meta_font, FSV_COLOR_TEXT_DIM, L"安装目录", ui_rect(40, 268, 400, 20), DT_LEFT | DT_SINGLELINE);
        draw_round_panel(dc, &field, FSV_COLOR_SURFACE_RAISED, FSV_COLOR_BORDER, ui_px(8));
    }
    if (g_page == 1) {
        draw_text_block(dc, g_body_font, FSV_COLOR_TEXT,
            g_context.archive_size != FSV_PAYLOAD_ARCHIVE_BYTES ? L"安装程序较小，安装时将从资源镜像下载完整运行组件。" : L"完整运行组件已内置，安装无需联网。",
            ui_rect(40, 370, 800, 26), DT_LEFT | DT_SINGLELINE);
        draw_text_block(dc, g_meta_font, FSV_COLOR_TEXT_DIM, L"非空目录会使用独立子目录；已有飞行雪绒安装可直接更新。", ui_rect(40, 408, 800, 22), DT_LEFT | DT_SINGLELINE);
        draw_text_block(dc, g_meta_font, FSV_COLOR_TEXT_DIM, L"个人设置、语音包与使用记录保留在用户数据目录。", ui_rect(40, 438, 800, 22), DT_LEFT | DT_SINGLELINE);
    } else if (g_page == 2) {
        const wchar_t *labels[] = {L"安装后占用", L"安装所需空间（含临时文件）", L"磁盘可用空间"};
        for (index = 0; index < 3; ++index) {
            draw_text_block(dc, g_meta_font, FSV_COLOR_TEXT_DIM, labels[index], ui_rect(40 + index * 272, 356, 250, 22), DT_LEFT | DT_SINGLELINE);
        }
    }
    if (g_page != 2) {
        draw_text_block(dc, g_meta_font, FSV_COLOR_TEXT_DIM,
            g_page == 3 ? L"正在安装，请保持窗口打开" : L"FLYING SNOW VELVET",
            ui_rect(40, 521, 460, 20), DT_LEFT | DT_SINGLELINE);
    }
}

static void format_progress_message(const FsvProgressMessage *update) {
    wchar_t completed[64];
    wchar_t total[64];
    wchar_t stats[256];
    wchar_t eta[128];
    wchar_t percent[32];
    DWORD display_percent;
    if (update->current_file[0] != L'\0') {
        wchar_t current[FSV_PATH_CAPACITY + 16];
        StringCchPrintfW(current, ARRAYSIZE(current), L"当前文件：%ls", update->current_file);
        SetWindowTextW(g_current_file, current);
    }
    /* PostMessage can deliver stale worker updates behind a newer one. */
    display_percent = update->percent < g_last_progress_percent
        ? g_last_progress_percent
        : update->percent;
    g_last_progress_percent = display_percent;
    format_bytes(update->completed_bytes, completed, ARRAYSIZE(completed));
    format_bytes(update->total_bytes, total, ARRAYSIZE(total));
    if (update->phase == FSV_PHASE_VERIFYING) {
        StringCchPrintfW(stats, ARRAYSIZE(stats), L"已校验内置归档：%ls / %ls", completed, total);
    } else if (update->phase == FSV_PHASE_EXTRACTING) {
        if (update->current_file[0] != L'\0' && wcscmp(update->current_file, L"正在读取归档目录...") == 0) {
            StringCchPrintfW(stats, ARRAYSIZE(stats), L"正在读取归档目录：%llu / %llu", update->completed_files, update->total_files);
        } else {
            StringCchPrintfW(stats, ARRAYSIZE(stats), L"已解压文件：%llu / %llu    数据：%ls / %ls", update->completed_files, update->total_files, completed, total);
        }
    } else {
        StringCchPrintfW(stats, ARRAYSIZE(stats), L"已安装文件：%llu / %llu    数据：%ls / %ls", update->completed_files, update->total_files, completed, total);
    }
    StringCchPrintfW(percent, ARRAYSIZE(percent), L"%lu%%", display_percent);
    SetWindowTextW(g_progress_stats, stats);
    SendMessageW(g_progress, PBM_SETPOS, display_percent, 0);
    if (update->eta_known) {
        wchar_t duration[64];
        format_duration(update->eta_seconds, duration, ARRAYSIZE(duration));
        StringCchPrintfW(eta, ARRAYSIZE(eta), L"预计剩余：%ls    当前进度：%ls", duration, percent);
    } else {
        StringCchPrintfW(eta, ARRAYSIZE(eta), L"预计剩余：正在计算...    当前进度：%ls", percent);
    }
    SetWindowTextW(g_progress_eta, eta);
}

static LRESULT CALLBACK wizard_window_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    if (message == WM_PAINT) {
        PAINTSTRUCT paint;
        HDC device_context = BeginPaint(window, &paint);
        draw_announcement_background(device_context);
        EndPaint(window, &paint);
        return 0;
    }
    if (message == WM_PRINTCLIENT) {
        draw_announcement_background((HDC)wparam);
        return 0;
    }
    if (message == WM_DPICHANGED) {
        RECT *suggested = (RECT *)lparam;
        RECT bounds;
        g_dpi = HIWORD(wparam);
        bounds = ui_rect(0, 0, FSV_CLIENT_WIDTH, FSV_CLIENT_HEIGHT);
        AdjustWindowRectExForDpi(&bounds, WS_CAPTION | WS_SYSMENU, FALSE, WS_EX_APPWINDOW | WS_EX_LAYERED, g_dpi);
        SetWindowPos(window, NULL, suggested->left, suggested->top,
            bounds.right - bounds.left, bounds.bottom - bounds.top, SWP_NOZORDER | SWP_NOACTIVATE);
        layout_controls();
        InvalidateRect(window, NULL, TRUE);
        return 0;
    }
    if (message == WM_ERASEBKGND) {
        RECT rect;
        GetClientRect(window, &rect);
        FillRect((HDC)wparam, &rect, g_canvas_brush);
        return 1;
    }
    if (message == WM_CTLCOLORSTATIC || message == WM_CTLCOLOREDIT) {
        HDC dc = (HDC)wparam;
        HWND control = (HWND)lparam;
        BOOL header = control == g_step_label || control == g_icon;
        COLORREF background = control == g_path_edit ? FSV_COLOR_SURFACE_RAISED : header ? FSV_COLOR_CANVAS : FSV_COLOR_SURFACE;
        SetBkMode(dc, OPAQUE);
        SetBkColor(dc, background);
        SetTextColor(dc, control == g_page_title ? FSV_COLOR_TEXT : FSV_COLOR_TEXT_MUTED);
        return (LRESULT)(control == g_path_edit ? g_raised_brush : header ? g_canvas_brush : g_surface_brush);
    }
    if (message == WM_DRAWITEM && lparam != 0) {
        draw_button((const DRAWITEMSTRUCT *)lparam);
        return TRUE;
    }
    if (message == WM_TIMER && wparam == FSV_FADE_TIMER) {
        if (g_fade_alpha < 248) {
            g_fade_alpha = (BYTE)(g_fade_alpha + 24);
            SetLayeredWindowAttributes(window, 0, g_fade_alpha, LWA_ALPHA);
        } else {
            g_fade_alpha = 255;
            SetLayeredWindowAttributes(window, 0, 255, LWA_ALPHA);
            KillTimer(window, FSV_FADE_TIMER);
        }
        return 0;
    }
    if (message == WM_TIMER && wparam == FSV_BUTTON_TIMER) {
        BOOL animating = FALSE;
        size_t index;
        for (index = 0; index < g_button_count; ++index) {
            ButtonVisualState *state = &g_button_states[index];
            int target = state->hover_target ? 100 : 0;
            int previous = state->hover_amount;
            if (state->hover_amount < target) {
                state->hover_amount += 18;
                if (state->hover_amount > target) {
                    state->hover_amount = target;
                }
            } else if (state->hover_amount > target) {
                state->hover_amount -= 18;
                if (state->hover_amount < target) {
                    state->hover_amount = target;
                }
            }
            if (previous != state->hover_amount) {
                InvalidateRect(state->window, NULL, TRUE);
            }
            if (state->hover_amount != target) {
                animating = TRUE;
            }
        }
        if (!animating) {
            KillTimer(window, FSV_BUTTON_TIMER);
        }
        return 0;
    }
    if (message == WM_FSV_STATUS) {
        wchar_t *text = (wchar_t *)lparam;
        if (text != NULL) {
            SetWindowTextW(g_status, text);
            HeapFree(GetProcessHeap(), 0, text);
        }
        return 0;
    }
    if (message == WM_FSV_PROGRESS) {
        FsvProgressMessage *update = (FsvProgressMessage *)lparam;
        if (update != NULL) {
            format_progress_message(update);
            HeapFree(GetProcessHeap(), 0, update);
        } else if (wparam <= 100) {
            SendMessageW(g_download_progress, PBM_SETPOS, (WPARAM)wparam, 0);
        }
        return 0;
    }
    if (message == WM_FSV_DONE) {
        close_finished_worker();
        if (wparam == ERROR_SUCCESS) {
            SetWindowTextW(g_done_title, L"文件校验通过");
            SetWindowTextW(g_done_text, L"程序文件已安装并通过校验。\r\n\r\n启动飞行雪绒，即可开启桌面陪伴。" );
            set_page(4);
        } else {
            wchar_t text[INSTALL_ERROR_CAPACITY + 80];
            StringCchPrintfW(text, ARRAYSIZE(text), L"安装未完成。\r\n\r\n%ls", g_context.error_message);
            SetWindowTextW(g_done_text, text);
            SetWindowTextW(g_done_title, L"安装失败");
            SetWindowTextW(g_finish_button, L"退出安装器");
            set_page(4);
        }
        EnableWindow(g_finish_button, TRUE);
        return 0;
    }
    if (message == WM_COMMAND) {
        int id = LOWORD(wparam);
        if (id == IDC_CUSTOM && HIWORD(wparam) == BN_CLICKED) {
            wchar_t selected[FSV_PATH_CAPACITY];
            if (choose_install_directory(window, g_context.install_directory, selected, ARRAYSIZE(selected))) {
                StringCchCopyW(g_context.install_directory, ARRAYSIZE(g_context.install_directory), selected);
                SetWindowTextW(g_path_edit, g_context.install_directory);
            }
            return 0;
        }
        if (id == IDC_NEXT && HIWORD(wparam) == BN_CLICKED) {
            if (normalize_selected_install_directory() && read_embedded_space_info()) {
                update_space_text();
                set_page(2);
            } else {
                wchar_t error[256];
                StringCchPrintfW(
                    error,
                    ARRAYSIZE(error),
                    L"无法准备安装目录或读取内置归档信息（错误代码 %lu）。",
                    GetLastError());
                MessageBoxW(window, error, L"飞行雪绒安装器", MB_OK | MB_ICONERROR);
            }
            return 0;
        }
        if (id == IDC_BACK && HIWORD(wparam) == BN_CLICKED) {
            set_page(1);
            return 0;
        }
        if (id == IDC_START && HIWORD(wparam) == BN_CLICKED && !g_context.installing) {
            g_context.installing = TRUE;
            EnableWindow(g_start_button, FALSE);
            EnableWindow(g_back_button, FALSE);
            SendMessageW(g_progress, PBM_SETPOS, 0, 0);
            SendMessageW(g_download_progress, PBM_SETPOS,
                g_context.archive_size == FSV_PAYLOAD_ARCHIVE_BYTES ? 100 : 0, 0);
            g_last_progress_percent = 0;
            SetWindowTextW(g_status, L"正在准备安装..." );
            SetWindowTextW(g_current_file, L"当前文件：正在读取安装器..." );
            SetWindowTextW(g_progress_stats, L"已校验内置归档：0 B / 0 B" );
            SetWindowTextW(g_progress_eta, L"预计剩余：正在计算...    当前进度：0%" );
            set_page(3);
            g_context.worker = CreateThread(NULL, 0, install_worker, &g_context, 0, NULL);
            if (g_context.worker == NULL) {
                g_context.installing = FALSE;
                set_install_error_win32(&g_context, L"无法启动安装工作线程", GetLastError());
                PostMessageW(window, WM_FSV_DONE, ERROR_INSTALL_FAILURE, 0);
            }
            return 0;
        }
        if (id == IDC_FINISH && HIWORD(wparam) == BN_CLICKED) {
            if (g_context.installed && !launch_installed_app(g_context.install_directory)) {
                MessageBoxW(window, L"启动飞行雪绒失败，请手动运行安装目录中的“启动飞行雪绒.exe”。", L"飞行雪绒安装器", MB_OK | MB_ICONWARNING);
                return 0;
            }
            DestroyWindow(window);
            return 0;
        }
    }
    if (message == WM_CLOSE) {
        if (g_context.installing) {
            return 0;
        }
        DestroyWindow(window);
        return 0;
    }
    if (message == WM_DESTROY) {
        MSG pending;
        g_context.window = NULL;
        while (PeekMessageW(&pending, window, WM_FSV_STATUS, WM_FSV_STATUS, PM_REMOVE)) {
            HeapFree(GetProcessHeap(), 0, (void *)pending.lParam);
        }
        while (PeekMessageW(&pending, window, WM_FSV_PROGRESS, WM_FSV_PROGRESS, PM_REMOVE)) {
            HeapFree(GetProcessHeap(), 0, (void *)pending.lParam);
        }
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(window, message, wparam, lparam);
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR command_line, int show) {
    INITCOMMONCONTROLSEX controls;
    MSG message;
    HRESULT com_result;
    (void)instance;
    (void)previous;
    (void)show;
    ZeroMemory(&g_context, sizeof(g_context));
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    g_dpi = GetDpiForSystem();
    set_install_error(&g_context, L"离线安装未完成。" );
    if (!parse_update_arguments(command_line)) {
        MessageBoxW(NULL, L"安装器启动参数无效。", L"飞行雪绒安装器", MB_OK | MB_ICONERROR);
        return 1;
    }
    com_result = CoInitializeEx(NULL, COINIT_APARTMENTTHREADED);
    if (FAILED(com_result)) {
        MessageBoxW(NULL, L"无法初始化 Windows 安装组件。", L"飞行雪绒安装器", MB_OK | MB_ICONERROR);
        return 1;
    }
    controls.dwSize = sizeof(controls);
    controls.dwICC = ICC_PROGRESS_CLASS;
    InitCommonControlsEx(&controls);
    g_canvas_brush = CreateSolidBrush(FSV_COLOR_CANVAS);
    g_surface_brush = CreateSolidBrush(FSV_COLOR_SURFACE);
    g_raised_brush = CreateSolidBrush(FSV_COLOR_SURFACE_RAISED);
    if (g_has_requested_directory) {
        if (!canonicalize_path(g_requested_directory, g_context.install_directory, ARRAYSIZE(g_context.install_directory))) {
            CoUninitialize();
            MessageBoxW(NULL, L"更新目标目录无效。", L"飞行雪绒安装器", MB_OK | MB_ICONERROR);
            return 1;
        }
    } else if (!get_default_directory(g_context.install_directory, ARRAYSIZE(g_context.install_directory))) {
        CoUninitialize();
        MessageBoxW(NULL, L"无法确定默认安装目录。", L"飞行雪绒安装器", MB_OK | MB_ICONERROR);
        return 1;
    }
    if (!create_wizard_window() || !create_controls()) {
        CoUninitialize();
        MessageBoxW(NULL, L"无法创建安装器窗口。", L"飞行雪绒安装器", MB_OK | MB_ICONERROR);
        return 1;
    }
    g_context.window = g_window;
    ShowWindow(g_window, SW_SHOWNORMAL);
    UpdateWindow(g_window);
    while (GetMessageW(&message, NULL, 0, 0) > 0) {
        if (!IsDialogMessageW(g_window, &message)) {
            TranslateMessage(&message);
            DispatchMessageW(&message);
        }
    }
    if (g_context.worker != NULL) {
        WaitForSingleObject(g_context.worker, 5000);
        CloseHandle(g_context.worker);
    }
    if (g_heading_font != NULL) {
        DeleteObject(g_heading_font);
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
