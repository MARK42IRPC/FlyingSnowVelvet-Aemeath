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

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

#include "resource.h"
#include "payload_info.h"
#include "zip_extract.h"

#pragma comment(lib, "bcrypt.lib")
#pragma comment(lib, "comctl32.lib")
#pragma comment(lib, "gdi32.lib")
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "shell32.lib")
#pragma comment(lib, "user32.lib")

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
#define FSV_MAX_BUTTONS 6

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
static HWND g_current_file;
static HWND g_progress_stats;
static HWND g_progress_eta;
static HWND g_status;
static HWND g_space_info;
static HWND g_done_title;
static HWND g_done_text;
static HFONT g_heading_font;
static HFONT g_body_font;
static HBRUSH g_canvas_brush;
static HBRUSH g_surface_brush;
static HBRUSH g_raised_brush;
static int g_page = 1;
static BYTE g_fade_alpha = 255;
static ButtonVisualState g_button_states[FSV_MAX_BUTTONS];
static size_t g_button_count;
static DWORD g_last_progress_percent;
static wchar_t g_requested_directory[FSV_PATH_CAPACITY];
static wchar_t g_update_state_path[FSV_PATH_CAPACITY];
static wchar_t g_update_state_source[FSV_PATH_CAPACITY];
static BOOL g_has_requested_directory;

static LRESULT CALLBACK wizard_window_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam);
static LRESULT CALLBACK button_subclass_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam, UINT_PTR subclass_id, DWORD_PTR reference_data);

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
    if (page == 1) {
        StringCchCopyW(title, ARRAYSIZE(title), L"安装飞行雪绒");
        StringCchCopyW(subtitle, ARRAYSIZE(subtitle), L"选择桌宠的安装位置，安装包内置完整运行环境。" );
    } else if (page == 2) {
        StringCchCopyW(title, ARRAYSIZE(title), L"准备安装");
        StringCchCopyW(subtitle, ARRAYSIZE(subtitle), L"确认空间与安装位置后开始写入文件。" );
    } else if (page == 3) {
        StringCchCopyW(title, ARRAYSIZE(title), L"正在安装");
        StringCchCopyW(subtitle, ARRAYSIZE(subtitle), L"正在校验并展开内置文件，请保持窗口打开。" );
    } else {
        StringCchCopyW(title, ARRAYSIZE(title), L"安装完成");
        StringCchCopyW(subtitle, ARRAYSIZE(subtitle), L"飞行雪绒已经准备就绪。" );
    }
    StringCchPrintfW(step, ARRAYSIZE(step), L"步骤 %d / 3", page >= 3 ? 3 : page);
    SetWindowTextW(g_page_title, title);
    SetWindowTextW(g_page_subtitle, subtitle);
    SetWindowTextW(g_step_label, step);
    show_control(g_path_edit, page == 1);
    show_control(g_custom_button, page == 1);
    show_control(g_next_button, page == 1);
    show_control(g_space_info, page == 2);
    show_control(g_back_button, page == 2);
    show_control(g_start_button, page == 2);
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
    if (trailer.archive_size != FSV_PAYLOAD_ARCHIVE_BYTES) {
        SetLastError(ERROR_BAD_EXE_FORMAT);
        goto cleanup;
    }
    g_context.archive_size = FSV_PAYLOAD_ARCHIVE_BYTES;
    g_context.total_files = FSV_PAYLOAD_FILE_COUNT;
    g_context.total_bytes = FSV_PAYLOAD_UNCOMPRESSED_BYTES;
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
    wchar_t free_space[64];
    wchar_t text[512];
    ULONGLONG available = 0;
    BOOL space_known;
    BOOL enough;
    format_bytes(g_context.required_bytes, required, ARRAYSIZE(required));
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
        L"安装目录\n%ls\n\n预计占用空间：%ls（含临时校验空间）\n归档文件数：%llu\n当前可用空间：%ls%s",
        g_context.install_directory,
        required,
        g_context.total_files,
        free_space,
        enough ? L"" : L"\n\n可用空间不足或目标磁盘不可用，请更换目录。"
    );
    SetWindowTextW(g_space_info, text);
    EnableWindow(g_start_button, enough);
}

static void create_button(HWND *output, const wchar_t *text, int id, int x, int y, int width, int height) {
    ButtonVisualState *state;
    *output = CreateWindowExW(0, L"BUTTON", text, WS_CHILD | WS_VISIBLE | BS_OWNERDRAW, x, y, width, height, g_window, (HMENU)(INT_PTR)id, GetModuleHandleW(NULL), NULL);
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
    int width = 760;
    int height = 470;
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
    SystemParametersInfoW(SPI_GETWORKAREA, 0, &work_area, 0);
    x = work_area.left + ((work_area.right - work_area.left) - width) / 2;
    y = work_area.top + ((work_area.bottom - work_area.top) - height) / 2;
    g_window = CreateWindowExW(WS_EX_APPWINDOW | WS_EX_LAYERED, WIZARD_CLASS_NAME, L"飞行雪绒安装器", WS_CAPTION | WS_SYSMENU, x, y, width, height, NULL, NULL, GetModuleHandleW(NULL), NULL);
    if (g_window == NULL) {
        return FALSE;
    }
    return TRUE;
}

static BOOL create_controls(void) {
    HICON icon = LoadIconW(GetModuleHandleW(NULL), MAKEINTRESOURCEW(IDI_INSTALLER));
    g_heading_font = CreateFontW(28, 0, 0, 0, FW_SEMIBOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH | FF_DONTCARE, L"Microsoft YaHei UI");
    g_body_font = CreateFontW(16, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH | FF_DONTCARE, L"Microsoft YaHei UI");
    if (g_heading_font == NULL || g_body_font == NULL) {
        return FALSE;
    }
    g_icon = CreateWindowExW(0, L"STATIC", NULL, WS_CHILD | WS_VISIBLE | SS_ICON, 36, 30, 48, 48, g_window, (HMENU)(INT_PTR)IDC_PRODUCT_ICON, GetModuleHandleW(NULL), NULL);
    g_page_title = CreateWindowExW(0, L"STATIC", L"安装飞行雪绒", WS_CHILD | WS_VISIBLE, 100, 30, 500, 40, g_window, (HMENU)(INT_PTR)IDC_PAGE_TITLE, GetModuleHandleW(NULL), NULL);
    g_page_subtitle = CreateWindowExW(0, L"STATIC", L"选择桌宠的安装位置，安装包内置完整运行环境。", WS_CHILD | WS_VISIBLE, 100, 72, 610, 28, g_window, (HMENU)(INT_PTR)IDC_PAGE_SUBTITLE, GetModuleHandleW(NULL), NULL);
    g_step_label = CreateWindowExW(0, L"STATIC", L"步骤 1 / 3", WS_CHILD | WS_VISIBLE | SS_RIGHT, 610, 44, 88, 26, g_window, NULL, GetModuleHandleW(NULL), NULL);
    g_path_edit = CreateWindowExW(WS_EX_CLIENTEDGE, L"EDIT", g_context.install_directory, WS_CHILD | WS_VISIBLE | ES_AUTOHSCROLL | ES_READONLY, 48, 148, 610, 38, g_window, (HMENU)(INT_PTR)IDC_PATH_EDIT, GetModuleHandleW(NULL), NULL);
    create_button(&g_custom_button, L"自定义安装目录", IDC_CUSTOM, 48, 202, 190, 40);
    create_button(&g_next_button, L"下一步", IDC_NEXT, 574, 374, 120, 42);
    g_space_info = CreateWindowExW(0, L"STATIC", L"正在读取内置归档信息...", WS_CHILD | SS_LEFT, 48, 128, 640, 190, g_window, (HMENU)(INT_PTR)IDC_SPACE_INFO, GetModuleHandleW(NULL), NULL);
    create_button(&g_back_button, L"返回", IDC_BACK, 48, 374, 120, 42);
    create_button(&g_start_button, L"开始安装", IDC_START, 574, 374, 120, 42);
    g_status = CreateWindowExW(0, L"STATIC", L"正在准备...", WS_CHILD | SS_LEFT, 48, 128, 650, 32, g_window, (HMENU)(INT_PTR)IDC_STATUS, GetModuleHandleW(NULL), NULL);
    g_current_file = CreateWindowExW(0, L"STATIC", L"当前文件：正在准备...", WS_CHILD | SS_LEFT | SS_PATHELLIPSIS, 48, 176, 650, 32, g_window, (HMENU)(INT_PTR)IDC_CURRENT_FILE, GetModuleHandleW(NULL), NULL);
    g_progress = CreateWindowExW(0, PROGRESS_CLASSW, NULL, WS_CHILD | PBS_SMOOTH, 48, 224, 650, 28, g_window, (HMENU)(INT_PTR)IDC_PROGRESS, GetModuleHandleW(NULL), NULL);
    g_progress_stats = CreateWindowExW(0, L"STATIC", L"已解压文件：0 / 0    数据：0 B / 0 B", WS_CHILD | SS_LEFT, 48, 270, 650, 28, g_window, (HMENU)(INT_PTR)IDC_PROGRESS_STATS, GetModuleHandleW(NULL), NULL);
    g_progress_eta = CreateWindowExW(0, L"STATIC", L"预计剩余：正在计算...", WS_CHILD | SS_LEFT, 48, 304, 650, 28, g_window, (HMENU)(INT_PTR)IDC_PROGRESS_ETA, GetModuleHandleW(NULL), NULL);
    g_done_title = CreateWindowExW(0, L"STATIC", L"安装完成", WS_CHILD | SS_LEFT, 48, 148, 650, 44, g_window, (HMENU)(INT_PTR)IDC_DONE_TITLE, GetModuleHandleW(NULL), NULL);
    g_done_text = CreateWindowExW(0, L"STATIC", L"安装文件已校验完成。点击下方按钮后，将启动飞行雪绒并退出安装器。", WS_CHILD | SS_LEFT, 48, 208, 650, 72, g_window, (HMENU)(INT_PTR)IDC_DONE_TEXT, GetModuleHandleW(NULL), NULL);
    create_button(&g_finish_button, L"退出安装并启动飞行雪绒", IDC_FINISH, 430, 374, 264, 42);
    if (icon != NULL) {
        SendMessageW(g_icon, STM_SETICON, (WPARAM)icon, 0);
    }
    SendMessageW(g_progress, PBM_SETRANGE32, 0, 100);
    SendMessageW(g_progress, PBM_SETPOS, 0, 0);
    SendMessageW(g_progress, PBM_SETBARCOLOR, 0, RGB(233, 104, 157));
    SendMessageW(g_progress, PBM_SETBKCOLOR, 0, RGB(252, 225, 237));
    {
        HWND controls[] = {g_page_title, g_page_subtitle, g_step_label, g_path_edit, g_custom_button, g_next_button, g_space_info, g_back_button, g_start_button, g_status, g_current_file, g_progress, g_progress_stats, g_progress_eta, g_done_title, g_done_text, g_finish_button};
        size_t index;
        for (index = 0; index < ARRAYSIZE(controls); ++index) {
            if (controls[index] == NULL) {
                return FALSE;
            }
            SendMessageW(controls[index], WM_SETFONT, (WPARAM)(controls[index] == g_page_title || controls[index] == g_done_title ? g_heading_font : g_body_font), TRUE);
        }
    }
    set_page(1);
    ShowWindow(g_window, SW_SHOWNORMAL);
    UpdateWindow(g_window);
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
        fill_color = RGB(236, 231, 234);
        border_color = RGB(224, 216, 220);
        text_color = RGB(140, 135, 142);
    } else {
        COLORREF base = primary ? RGB(233, 104, 157) : RGB(255, 255, 255);
        COLORREF hot = primary ? RGB(216, 82, 139) : RGB(255, 235, 244);
        fill_color = mix_color(base, hot, hover);
        if ((item->itemState & ODS_SELECTED) != 0) {
            fill_color = primary ? RGB(194, 67, 123) : RGB(249, 216, 232);
        }
        border_color = primary ? fill_color : RGB(224, 132, 172);
        text_color = primary ? RGB(255, 255, 255) : RGB(151, 57, 100);
    }
    brush = CreateSolidBrush(fill_color);
    pen = CreatePen(PS_SOLID, 1, border_color);
    old_brush = SelectObject(item->hDC, brush);
    old_pen = SelectObject(item->hDC, pen);
    RoundRect(item->hDC, rect.left, rect.top, rect.right, rect.bottom, 8, 8);
    SelectObject(item->hDC, old_pen);
    SelectObject(item->hDC, old_brush);
    DeleteObject(pen);
    DeleteObject(brush);
    SetBkMode(item->hDC, TRANSPARENT);
    SetTextColor(item->hDC, text_color);
    {
        wchar_t text[128];
        GetWindowTextW(item->hwndItem, text, ARRAYSIZE(text));
        DrawTextW(item->hDC, text, -1, &rect, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
    }
    if ((item->itemState & ODS_FOCUS) != 0) {
        InflateRect(&rect, -3, -3);
        DrawFocusRect(item->hDC, &rect);
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
    if (message == WM_ERASEBKGND) {
        RECT rect;
        GetClientRect(window, &rect);
        FillRect((HDC)wparam, &rect, g_canvas_brush);
        return 1;
    }
    if (message == WM_CTLCOLORSTATIC || message == WM_CTLCOLOREDIT) {
        HDC dc = (HDC)wparam;
        HWND control = (HWND)lparam;
        SetBkMode(dc, TRANSPARENT);
        if (control == g_page_title || control == g_done_title) {
            SetTextColor(dc, RGB(32, 52, 77));
        } else if (control == g_step_label) {
            SetTextColor(dc, RGB(199, 69, 127));
        } else if (control == g_current_file || control == g_progress_eta) {
            SetTextColor(dc, RGB(52, 72, 99));
        } else {
            SetTextColor(dc, RGB(52, 72, 99));
        }
        return (LRESULT)(control == g_path_edit ? g_surface_brush : g_canvas_brush);
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
        }
        return 0;
    }
    if (message == WM_FSV_DONE) {
        close_finished_worker();
        if (wparam == ERROR_SUCCESS) {
            SetWindowTextW(g_done_title, L"文件校验通过");
            SetWindowTextW(g_done_text, L"安装文件已校验完成。点击下方按钮后，将启动飞行雪绒并退出安装器。" );
            set_page(4);
        } else {
            wchar_t text[INSTALL_ERROR_CAPACITY + 80];
            StringCchPrintfW(text, ARRAYSIZE(text), L"安装未完成。\n\n%ls", g_context.error_message);
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
        if (g_context.installing || g_page == 4) {
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
    g_canvas_brush = CreateSolidBrush(RGB(255, 248, 251));
    g_surface_brush = CreateSolidBrush(RGB(255, 255, 255));
    g_raised_brush = CreateSolidBrush(RGB(255, 245, 248));
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
    while (GetMessageW(&message, NULL, 0, 0) > 0) {
        TranslateMessage(&message);
        DispatchMessageW(&message);
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
