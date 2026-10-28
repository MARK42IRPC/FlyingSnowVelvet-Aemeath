#define UNICODE
#define _UNICODE
#define WIN32_LEAN_AND_MEAN

#include <windows.h>
#include <shellapi.h>
#include <shlobj.h>
#include <bcrypt.h>
#include <tlhelp32.h>
#include <strsafe.h>
#include <stdio.h>
#include <stdlib.h>

#include "zip_extract.h"

#pragma comment(lib, "bcrypt.lib")
#pragma comment(lib, "shell32.lib")
#pragma comment(lib, "user32.lib")

#define FSV_UPDATE_PATH_CAPACITY 1024
#define FSV_UPDATE_SHUTDOWN_GRACE_MS 1800
#define FSV_UPDATE_SHUTDOWN_ROUNDS 4
#define FSV_UPDATE_MAX_PIDS 128
#define FSV_UPDATE_PARENT_WAIT_MS 45000
#define FSV_UPDATE_BUFFER_SIZE (1024U * 1024U)
#define FSV_UPDATE_BACKUP_MARKER L".fsv-updater-backup"

static const char FSV_PAYLOAD_MAGIC[] = "FSV-OFFLINE-PAYLOAD-2";

typedef struct UpdateOptions {
    wchar_t install_root[FSV_UPDATE_PATH_CAPACITY];
    wchar_t resource[FSV_UPDATE_PATH_CAPACITY];
    wchar_t restart[FSV_UPDATE_PATH_CAPACITY];
    wchar_t state_path[FSV_UPDATE_PATH_CAPACITY];
    wchar_t state_source[FSV_UPDATE_PATH_CAPACITY];
    wchar_t workspace[FSV_UPDATE_PATH_CAPACITY];
    DWORD parent_pid;
    BYTE expected_hash[32];
    BOOL has_hash;
} UpdateOptions;

static wchar_t g_log_path[FSV_UPDATE_PATH_CAPACITY];

static BOOL copy_value(const wchar_t *value, wchar_t *output, size_t capacity) {
    if (value == NULL || value[0] == L'\0' || FAILED(StringCchCopyW(output, capacity, value))) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    return TRUE;
}

static BOOL join_path(const wchar_t *directory, const wchar_t *name, wchar_t *output, size_t capacity) {
    size_t length = wcslen(directory);
    HRESULT result = length > 0 && (directory[length - 1] == L'\\' || directory[length - 1] == L'/')
        ? StringCchPrintfW(output, capacity, L"%ls%ls", directory, name)
        : StringCchPrintfW(output, capacity, L"%ls\\%ls", directory, name);
    return SUCCEEDED(result);
}

static BOOL canonicalize_path(const wchar_t *input, wchar_t *output, size_t capacity) {
    wchar_t temporary[FSV_UPDATE_PATH_CAPACITY];
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

static BOOL long_path(const wchar_t *input, wchar_t *output, size_t capacity) {
    wchar_t canonical[FSV_UPDATE_PATH_CAPACITY];
    DWORD length;
    if (!canonicalize_path(input, canonical, ARRAYSIZE(canonical))) {
        return FALSE;
    }
    length = GetLongPathNameW(canonical, output, (DWORD)capacity);
    if (length == 0 || length >= capacity) {
        return SUCCEEDED(StringCchCopyW(output, capacity, canonical));
    }
    return TRUE;
}

static BOOL is_regular_file(const wchar_t *path) {
    DWORD attributes = GetFileAttributesW(path);
    return attributes != INVALID_FILE_ATTRIBUTES &&
        (attributes & FILE_ATTRIBUTE_DIRECTORY) == 0 &&
        (attributes & FILE_ATTRIBUTE_REPARSE_POINT) == 0;
}

static BOOL is_directory(const wchar_t *path) {
    DWORD attributes = GetFileAttributesW(path);
    return attributes != INVALID_FILE_ATTRIBUTES &&
        (attributes & FILE_ATTRIBUTE_DIRECTORY) != 0 &&
        (attributes & FILE_ATTRIBUTE_REPARSE_POINT) == 0;
}

static BOOL ensure_directory(const wchar_t *directory) {
    int result;
    if (is_directory(directory)) {
        return TRUE;
    }
    result = SHCreateDirectoryExW(NULL, directory, NULL);
    if (result != ERROR_SUCCESS && result != ERROR_FILE_EXISTS && result != ERROR_ALREADY_EXISTS) {
        SetLastError((DWORD)result);
        return FALSE;
    }
    return is_directory(directory);
}

static BOOL ensure_parent_directory(const wchar_t *path) {
    wchar_t parent[FSV_UPDATE_PATH_CAPACITY];
    wchar_t *separator;
    if (FAILED(StringCchCopyW(parent, ARRAYSIZE(parent), path))) {
        return FALSE;
    }
    separator = wcsrchr(parent, L'\\');
    if (separator == NULL || separator == parent || (separator == parent + 2 && parent[1] == L':')) {
        SetLastError(ERROR_BAD_PATHNAME);
        return FALSE;
    }
    *separator = L'\0';
    return ensure_directory(parent);
}

static BOOL path_is_under(const wchar_t *root, const wchar_t *candidate) {
    wchar_t canonical_root[FSV_UPDATE_PATH_CAPACITY];
    wchar_t canonical_candidate[FSV_UPDATE_PATH_CAPACITY];
    size_t root_length;
    if (!long_path(root, canonical_root, ARRAYSIZE(canonical_root)) ||
        !long_path(candidate, canonical_candidate, ARRAYSIZE(canonical_candidate))) {
        return FALSE;
    }
    root_length = wcslen(canonical_root);
    if (_wcsnicmp(canonical_root, canonical_candidate, root_length) != 0) {
        return FALSE;
    }
    return canonical_candidate[root_length] == L'\0' || canonical_candidate[root_length] == L'\\';
}

static void updater_log(const wchar_t *message) {
    wchar_t temporary[FSV_UPDATE_PATH_CAPACITY];
    wchar_t directory[FSV_UPDATE_PATH_CAPACITY];
    FILE *file;
    DWORD length;
    if (message == NULL) {
        return;
    }
    if (g_log_path[0] == L'\0') {
        length = GetTempPathW(ARRAYSIZE(temporary), temporary);
        if (length == 0 || length >= ARRAYSIZE(temporary) ||
            FAILED(StringCchPrintfW(directory, ARRAYSIZE(directory), L"%lsFlyingSnowVelvet", temporary)) ||
            !ensure_directory(directory) ||
            FAILED(StringCchPrintfW(g_log_path, ARRAYSIZE(g_log_path), L"%ls\\updater.log", directory))) {
            return;
        }
    }
    if (_wfopen_s(&file, g_log_path, L"a, ccs=UTF-8") != 0) {
        file = NULL;
    }
    if (file == NULL) {
        return;
    }
    fwprintf(file, L"[%lu] %ls\n", GetTickCount(), message);
    fclose(file);
}

static void updater_log_error(const wchar_t *prefix, DWORD error) {
    wchar_t line[512];
    StringCchPrintfW(line, ARRAYSIZE(line), L"%ls (error=%lu)", prefix, error);
    updater_log(line);
}
static BOOL parse_sha256(const wchar_t *text, BYTE output[32]) {
    size_t index;
    if (text == NULL || wcslen(text) != 64) {
        return FALSE;
    }
    for (index = 0; index < 32; ++index) {
        int high = text[index * 2];
        int low = text[index * 2 + 1];
        if (high >= L'0' && high <= L'9') high -= L'0';
        else if (high >= L'a' && high <= L'f') high = high - L'a' + 10;
        else if (high >= L'A' && high <= L'F') high = high - L'A' + 10;
        else return FALSE;
        if (low >= L'0' && low <= L'9') low -= L'0';
        else if (low >= L'a' && low <= L'f') low = low - L'a' + 10;
        else if (low >= L'A' && low <= L'F') low = low - L'A' + 10;
        else return FALSE;
        output[index] = (BYTE)((high << 4) | low);
    }
    return TRUE;
}

static BOOL parse_options(PWSTR command_line, UpdateOptions *options) {
    int argc = 0;
    int index;
    BOOL update_mode = FALSE;
    wchar_t **argv = CommandLineToArgvW(command_line != NULL ? command_line : GetCommandLineW(), &argc);
    if (argv == NULL) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    ZeroMemory(options, sizeof(*options));
    for (index = 1; index < argc; ++index) {
        const wchar_t *argument = argv[index];
        if (wcscmp(argument, L"--update-mode") == 0) {
            if (++index >= argc || wcscmp(argv[index], L"update") != 0) {
                LocalFree(argv);
                return FALSE;
            }
            update_mode = TRUE;
        } else if (wcscmp(argument, L"--update-target") == 0) {
            if (++index >= argc || !copy_value(argv[index], options->install_root, ARRAYSIZE(options->install_root))) {
                LocalFree(argv);
                return FALSE;
            }
        } else if (wcscmp(argument, L"--update-parent") == 0) {
            wchar_t *end = NULL;
            unsigned long parsed;
            if (++index >= argc) {
                LocalFree(argv);
                return FALSE;
            }
            parsed = wcstoul(argv[index], &end, 10);
            if (end == argv[index] || *end != L'\0' || parsed == 0) {
                LocalFree(argv);
                return FALSE;
            }
            options->parent_pid = (DWORD)parsed;
        } else if (wcscmp(argument, L"--update-resource") == 0) {
            if (++index >= argc || !copy_value(argv[index], options->resource, ARRAYSIZE(options->resource))) {
                LocalFree(argv);
                return FALSE;
            }
        } else if (wcscmp(argument, L"--update-restart") == 0) {
            if (++index >= argc || !copy_value(argv[index], options->restart, ARRAYSIZE(options->restart))) {
                LocalFree(argv);
                return FALSE;
            }
        } else if (wcscmp(argument, L"--update-state") == 0) {
            if (++index >= argc || !copy_value(argv[index], options->state_path, ARRAYSIZE(options->state_path))) {
                LocalFree(argv);
                return FALSE;
            }
        } else if (wcscmp(argument, L"--update-state-source") == 0) {
            if (++index >= argc || !copy_value(argv[index], options->state_source, ARRAYSIZE(options->state_source))) {
                LocalFree(argv);
                return FALSE;
            }
        } else if (wcscmp(argument, L"--update-sha256") == 0) {
            if (++index >= argc || !parse_sha256(argv[index], options->expected_hash)) {
                LocalFree(argv);
                return FALSE;
            }
            options->has_hash = TRUE;
        } else {
            LocalFree(argv);
            SetLastError(ERROR_INVALID_PARAMETER);
            return FALSE;
        }
    }
    LocalFree(argv);
    if (!update_mode || options->install_root[0] == L'\0' || options->parent_pid == 0 ||
        options->resource[0] == L'\0' || options->restart[0] == L'\0' ||
        options->state_path[0] == L'\0' || options->state_source[0] == L'\0' ||
        !options->has_hash) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    return TRUE;
}

static BOOL sha256_file_matches(const wchar_t *path, const BYTE expected[32]) {
    BCRYPT_ALG_HANDLE algorithm = NULL;
    BCRYPT_HASH_HANDLE hash = NULL;
    PUCHAR hash_object = NULL;
    DWORD hash_object_size = 0;
    DWORD ignored = 0;
    HANDLE file = INVALID_HANDLE_VALUE;
    BYTE *buffer = NULL;
    BYTE actual[32];
    BOOL success = FALSE;
    file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) {
        return FALSE;
    }
    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, NULL, 0) != 0 ||
        BCryptGetProperty(algorithm, BCRYPT_OBJECT_LENGTH, (PUCHAR)&hash_object_size,
                          sizeof(hash_object_size), &ignored, 0) != 0) {
        SetLastError(ERROR_DLL_INIT_FAILED);
        goto cleanup;
    }
    hash_object = (PUCHAR)HeapAlloc(GetProcessHeap(), 0, hash_object_size);
    buffer = (BYTE *)HeapAlloc(GetProcessHeap(), 0, FSV_UPDATE_BUFFER_SIZE);
    if (hash_object == NULL || buffer == NULL ||
        BCryptCreateHash(algorithm, &hash, hash_object, hash_object_size, NULL, 0, 0) != 0) {
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
        goto cleanup;
    }
    for (;;) {
        DWORD read = 0;
        if (!ReadFile(file, buffer, FSV_UPDATE_BUFFER_SIZE, &read, NULL)) {
            goto cleanup;
        }
        if (read == 0) {
            break;
        }
        if (BCryptHashData(hash, buffer, read, 0) != 0) {
            goto cleanup;
        }
    }
    if (BCryptFinishHash(hash, actual, sizeof(actual), 0) != 0 ||
        memcmp(actual, expected, sizeof(actual)) != 0) {
        SetLastError(ERROR_CRC);
        goto cleanup;
    }
    success = TRUE;
cleanup:
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    if (hash != NULL) BCryptDestroyHash(hash);
    if (algorithm != NULL) BCryptCloseAlgorithmProvider(algorithm, 0);
    if (hash_object != NULL) HeapFree(GetProcessHeap(), 0, hash_object);
    if (buffer != NULL) HeapFree(GetProcessHeap(), 0, buffer);
    return success;
}

static BOOL delete_tree(const wchar_t *directory) {
    wchar_t pattern[FSV_UPDATE_PATH_CAPACITY];
    WIN32_FIND_DATAW data;
    HANDLE handle;
    BOOL success = TRUE;
    if (!join_path(directory, L"*", pattern, ARRAYSIZE(pattern))) {
        return FALSE;
    }
    handle = FindFirstFileW(pattern, &data);
    if (handle == INVALID_HANDLE_VALUE) {
        return GetLastError() == ERROR_FILE_NOT_FOUND;
    }
    do {
        wchar_t child[FSV_UPDATE_PATH_CAPACITY];
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
    if (success && !RemoveDirectoryW(directory) && GetLastError() != ERROR_PATH_NOT_FOUND) {
        success = FALSE;
    }
    return success;
}

static BOOL copy_tree(const wchar_t *source, const wchar_t *destination) {
    wchar_t pattern[FSV_UPDATE_PATH_CAPACITY];
    WIN32_FIND_DATAW data;
    HANDLE handle;
    BOOL success = TRUE;
    if (!ensure_directory(destination) || !join_path(source, L"*", pattern, ARRAYSIZE(pattern))) {
        return FALSE;
    }
    handle = FindFirstFileW(pattern, &data);
    if (handle == INVALID_HANDLE_VALUE) {
        return GetLastError() == ERROR_FILE_NOT_FOUND;
    }
    do {
        wchar_t child_source[FSV_UPDATE_PATH_CAPACITY];
        wchar_t child_destination[FSV_UPDATE_PATH_CAPACITY];
        if (wcscmp(data.cFileName, L".") == 0 || wcscmp(data.cFileName, L"..") == 0) {
            continue;
        }
        if (!join_path(source, data.cFileName, child_source, ARRAYSIZE(child_source)) ||
            !join_path(destination, data.cFileName, child_destination, ARRAYSIZE(child_destination))) {
            success = FALSE;
            break;
        }
        if ((data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0) {
            if ((data.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0 ||
                !copy_tree(child_source, child_destination)) {
                success = FALSE;
                break;
            }
        } else if (!CopyFileW(child_source, child_destination, FALSE)) {
            success = FALSE;
            break;
        }
    } while (FindNextFileW(handle, &data));
    if (GetLastError() != ERROR_NO_MORE_FILES) {
        success = FALSE;
    }
    FindClose(handle);
    return success;
}

static BOOL write_backup_marker(const wchar_t *backup) {
    wchar_t marker[FSV_UPDATE_PATH_CAPACITY];
    HANDLE file;
    DWORD written = 0;
    if (!join_path(backup, FSV_UPDATE_BACKUP_MARKER, marker, ARRAYSIZE(marker))) {
        return FALSE;
    }
    file = CreateFileW(marker, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS,
                       FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) {
        return FALSE;
    }
    WriteFile(file, "FSV-UPDATER-BACKUP\n", 19, &written, NULL);
    CloseHandle(file);
    return written == 19;
}

static void cleanup_orphan_backups(const wchar_t *install_root) {
    wchar_t parent[FSV_UPDATE_PATH_CAPACITY];
    wchar_t pattern[FSV_UPDATE_PATH_CAPACITY];
    WIN32_FIND_DATAW data;
    HANDLE handle;
    if (!canonicalize_path(install_root, parent, ARRAYSIZE(parent))) {
        return;
    }
    {
        wchar_t *separator = wcsrchr(parent, L'\\');
        if (separator == NULL || separator == parent || (separator == parent + 2 && parent[1] == L':')) {
            return;
        }
        *separator = L'\0';
    }
    if (!join_path(parent, L"*.fsv-previous-*", pattern, ARRAYSIZE(pattern))) {
        return;
    }
    handle = FindFirstFileW(pattern, &data);
    if (handle == INVALID_HANDLE_VALUE) {
        return;
    }
    do {
        wchar_t candidate[FSV_UPDATE_PATH_CAPACITY];
        wchar_t marker[FSV_UPDATE_PATH_CAPACITY];
        if ((data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) == 0 ||
            (data.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
            continue;
        }
        if (!join_path(parent, data.cFileName, candidate, ARRAYSIZE(candidate)) ||
            !join_path(candidate, FSV_UPDATE_BACKUP_MARKER, marker, ARRAYSIZE(marker)) ||
            !is_regular_file(marker)) {
            continue;
        }
        /* Only remove backups this updater created.  The marker is written
           only after the target has been renamed into the backup path, so a
           foreign folder with a similar name is never touched. */
        if (delete_tree(candidate)) {
            updater_log(L"已清理上次更新留下的旧安装备份");
        }
    } while (FindNextFileW(handle, &data));
    FindClose(handle);
}

static BOOL process_matches_root(DWORD pid, const wchar_t *install_root) {
    HANDLE handle;
    wchar_t image[FSV_UPDATE_PATH_CAPACITY];
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
        match = path_is_under(install_root, image);
    }
    CloseHandle(handle);
    return match;
}

static int collect_targets(DWORD *pids, int capacity, const wchar_t *install_root) {
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    PROCESSENTRY32W entry;
    int count = 0;
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
            if (process_matches_root(entry.th32ProcessID, install_root)) {
                pids[count++] = entry.th32ProcessID;
            }
        } while (Process32NextW(snapshot, &entry));
    }
    CloseHandle(snapshot);
    return count;
}

static BOOL CALLBACK request_close(HWND window, LPARAM parameter) {
    DWORD owner = 0;
    GetWindowThreadProcessId(window, &owner);
    if (owner == (DWORD)parameter) {
        PostMessageW(window, WM_CLOSE, 0, 0);
    }
    return TRUE;
}

static BOOL process_alive(DWORD pid) {
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

static BOOL stop_install_root_processes(const wchar_t *install_root) {
    DWORD pids[FSV_UPDATE_MAX_PIDS];
    int round;
    for (round = 0; round < FSV_UPDATE_SHUTDOWN_ROUNDS; ++round) {
        int count = collect_targets(pids, (int)ARRAYSIZE(pids), install_root);
        int index;
        if (count == 0) {
            return TRUE;
        }
        updater_log(L"正在关闭安装目录内的残留进程");
        for (index = 0; index < count; ++index) {
            EnumWindows(request_close, (LPARAM)pids[index]);
        }
        Sleep(FSV_UPDATE_SHUTDOWN_GRACE_MS);
        for (index = 0; index < count; ++index) {
            if (process_alive(pids[index])) {
                terminate_process(pids[index]);
            }
        }
    }
    return collect_targets(pids, (int)ARRAYSIZE(pids), install_root) == 0;
}

static BOOL wait_for_parent(DWORD pid) {
    HANDLE handle = OpenProcess(SYNCHRONIZE, FALSE, pid);
    DWORD wait;
    if (handle == NULL) {
        return TRUE;
    }
    wait = WaitForSingleObject(handle, FSV_UPDATE_PARENT_WAIT_MS);
    CloseHandle(handle);
    return wait == WAIT_OBJECT_0;
}

static BOOL marker_ready(const wchar_t *root) {
    wchar_t marker[FSV_UPDATE_PATH_CAPACITY];
    wchar_t python[FSV_UPDATE_PATH_CAPACITY];
    wchar_t py_ini[FSV_UPDATE_PATH_CAPACITY];
    wchar_t launcher[FSV_UPDATE_PATH_CAPACITY];
    wchar_t uninstaller[FSV_UPDATE_PATH_CAPACITY];
    HANDLE file;
    BYTE content[sizeof(FSV_PAYLOAD_MAGIC)];
    DWORD received = 0;
    if (!join_path(root, L".fsv-install-root", marker, ARRAYSIZE(marker)) ||
        !join_path(root, L"runtime\\python311\\python.exe", python, ARRAYSIZE(python)) ||
        !join_path(root, L"app\\py.ini", py_ini, ARRAYSIZE(py_ini)) ||
        !join_path(root, L"app\\启动飞行雪绒.exe", launcher, ARRAYSIZE(launcher)) ||
        !join_path(root, L"app\\卸载飞行雪绒.exe", uninstaller, ARRAYSIZE(uninstaller))) {
        SetLastError(ERROR_BUFFER_OVERFLOW);
        return FALSE;
    }
    file = CreateFileW(marker, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) {
        return FALSE;
    }
    if (!ReadFile(file, content, sizeof(content), &received, NULL) ||
        received != sizeof(content) ||
        memcmp(content, FSV_PAYLOAD_MAGIC, sizeof(FSV_PAYLOAD_MAGIC) - 1) != 0 ||
        content[sizeof(FSV_PAYLOAD_MAGIC) - 1] != '\n') {
        CloseHandle(file);
        SetLastError(ERROR_BAD_FORMAT);
        return FALSE;
    }
    CloseHandle(file);
    return is_regular_file(python) && is_regular_file(py_ini) &&
        is_regular_file(launcher) && is_regular_file(uninstaller);
}

static BOOL make_staging_directory(const wchar_t *install_root, wchar_t *output, size_t capacity) {
    wchar_t volume[FSV_UPDATE_PATH_CAPACITY];
    DWORD attempt;
    if (!GetVolumePathNameW(install_root, volume, ARRAYSIZE(volume))) {
        return FALSE;
    }
    for (attempt = 0; attempt < 32; ++attempt) {
        if (FAILED(StringCchPrintfW(output, capacity, L"%lsFSV-%lu-%lu-%lu",
                                    volume, GetCurrentProcessId(), GetCurrentThreadId(),
                                    GetTickCount() + attempt))) {
            SetLastError(ERROR_BUFFER_OVERFLOW);
            return FALSE;
        }
        if (CreateDirectoryW(output, NULL)) {
            return TRUE;
        }
        if (GetLastError() != ERROR_ALREADY_EXISTS) {
            return FALSE;
        }
    }
    SetLastError(ERROR_TOO_MANY_NAMES);
    return FALSE;
}

static void cleanup_orphan_staging(const wchar_t *install_root) {
    wchar_t volume[FSV_UPDATE_PATH_CAPACITY];
    wchar_t pattern[FSV_UPDATE_PATH_CAPACITY];
    WIN32_FIND_DATAW data;
    HANDLE handle;
    if (!GetVolumePathNameW(install_root, volume, ARRAYSIZE(volume)) ||
        !join_path(volume, L"FSV-*", pattern, ARRAYSIZE(pattern))) {
        return;
    }
    handle = FindFirstFileW(pattern, &data);
    if (handle == INVALID_HANDLE_VALUE) {
        return;
    }
    do {
        wchar_t candidate[FSV_UPDATE_PATH_CAPACITY];
        wchar_t *end = NULL;
        unsigned long owner;
        HANDLE live;
        if ((data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) == 0 ||
            (data.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
            continue;
        }
        owner = wcstoul(data.cFileName + 4, &end, 10);
        if (end == data.cFileName + 4 || end == NULL || *end != L'-' ||
            owner == 0 || owner == GetCurrentProcessId()) {
            continue;
        }
        live = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, (DWORD)owner);
        if (live != NULL) {
            CloseHandle(live);
            continue;
        }
        if (join_path(volume, data.cFileName, candidate, ARRAYSIZE(candidate))) {
            delete_tree(candidate);
        }
    } while (FindNextFileW(handle, &data));
    FindClose(handle);
}

static BOOL preserve_user_data(const wchar_t *install_root, const wchar_t *staging, BOOL *moved) {
    wchar_t source[FSV_UPDATE_PATH_CAPACITY];
    wchar_t destination[FSV_UPDATE_PATH_CAPACITY];
    *moved = FALSE;
    if (!join_path(install_root, L"app\\resc\\user", source, ARRAYSIZE(source)) ||
        !join_path(staging, L"app\\resc\\user", destination, ARRAYSIZE(destination))) {
        return FALSE;
    }
    if (!is_directory(source)) {
        return TRUE;
    }
    if (!ensure_parent_directory(destination)) {
        return FALSE;
    }
    if (GetFileAttributesW(destination) != INVALID_FILE_ATTRIBUTES) {
        /* A package may start shipping defaults below resc/user.  Merge the old
           tree over them so user settings win.  The source stays in place, so
           a failed switch needs no rollback for this branch. */
        return copy_tree(source, destination);
    }
    if (!MoveFileExW(source, destination, MOVEFILE_WRITE_THROUGH)) {
        return FALSE;
    }
    *moved = TRUE;
    return TRUE;
}

static BOOL rollback_preserved_user(const wchar_t *install_root, const wchar_t *staging, BOOL moved) {
    wchar_t source[FSV_UPDATE_PATH_CAPACITY];
    wchar_t destination[FSV_UPDATE_PATH_CAPACITY];
    if (!moved ||
        !join_path(staging, L"app\\resc\\user", source, ARRAYSIZE(source)) ||
        !join_path(install_root, L"app\\resc\\user", destination, ARRAYSIZE(destination)) ||
        !is_directory(source) || GetFileAttributesW(destination) != INVALID_FILE_ATTRIBUTES) {
        return !moved;
    }
    if (!ensure_parent_directory(destination)) {
        return FALSE;
    }
    return MoveFileExW(source, destination, MOVEFILE_WRITE_THROUGH);
}

static BOOL move_directory_with_retry(const wchar_t *source, const wchar_t *destination) {
    DWORD attempt;
    for (attempt = 0; attempt < 12; ++attempt) {
        if (MoveFileExW(source, destination, MOVEFILE_WRITE_THROUGH)) {
            return TRUE;
        }
        if (GetLastError() != ERROR_ACCESS_DENIED &&
            GetLastError() != ERROR_SHARING_VIOLATION &&
            GetLastError() != ERROR_LOCK_VIOLATION) {
            return FALSE;
        }
        Sleep(250);
    }
    return FALSE;
}

static BOOL replace_install_directory(const wchar_t *target, const wchar_t *staging) {
    wchar_t backup[FSV_UPDATE_PATH_CAPACITY];
    DWORD attempt;
    BOOL named = FALSE;
    if (!marker_ready(staging)) {
        return FALSE;
    }
    if (!is_directory(target)) {
        SetLastError(ERROR_DIRECTORY);
        return FALSE;
    }
    for (attempt = 0; attempt < 32; ++attempt) {
        if (SUCCEEDED(StringCchPrintfW(backup, ARRAYSIZE(backup), L"%ls.fsv-previous-%lu-%lu-%lu",
                                       target, GetCurrentProcessId(), GetCurrentThreadId(),
                                       GetTickCount() + attempt)) &&
            GetFileAttributesW(backup) == INVALID_FILE_ATTRIBUTES) {
            named = TRUE;
            break;
        }
    }
    if (!named) {
        SetLastError(ERROR_TOO_MANY_NAMES);
        return FALSE;
    }
    if (!move_directory_with_retry(target, backup)) {
        return FALSE;
    }
    if (!write_backup_marker(backup)) {
        DWORD error = GetLastError();
        move_directory_with_retry(backup, target);
        SetLastError(error);
        return FALSE;
    }
    if (!move_directory_with_retry(staging, target)) {
        DWORD error = GetLastError();
        move_directory_with_retry(backup, target);
        SetLastError(error);
        return FALSE;
    }
    if (!delete_tree(backup)) {
        updater_log(L"旧安装目录已切换，但备份清理失败；下次启动会继续尝试");
    }
    return TRUE;
}

static BOOL copy_state_file(const UpdateOptions *options) {
    wchar_t target_path[FSV_UPDATE_PATH_CAPACITY];
    if (!long_path(options->state_path, target_path, ARRAYSIZE(target_path)) ||
        !path_is_under(options->install_root, target_path) ||
        !is_regular_file(options->state_source)) {
        SetLastError(ERROR_ACCESS_DENIED);
        return FALSE;
    }
    if (!ensure_parent_directory(target_path) ||
        !CopyFileW(options->state_source, target_path, FALSE)) {
        return FALSE;
    }
    DeleteFileW(options->state_source);
    return TRUE;
}

static BOOL launch_restart_helper(const UpdateOptions *options) {
    wchar_t command[FSV_UPDATE_PATH_CAPACITY * 2];
    wchar_t working_directory[FSV_UPDATE_PATH_CAPACITY];
    STARTUPINFOW startup;
    PROCESS_INFORMATION process;
    if (!is_regular_file(options->restart) ||
        !path_is_under(options->install_root, options->restart) ||
        !join_path(options->install_root, L"app", working_directory, ARRAYSIZE(working_directory)) ||
        FAILED(StringCchPrintfW(command, ARRAYSIZE(command),
                                L"\"%ls\" --fsv-update-restart %lu \"%ls\" \"%ls\"",
                                options->restart, GetCurrentProcessId(),
                                options->install_root, options->workspace))) {
        SetLastError(ERROR_FILE_NOT_FOUND);
        return FALSE;
    }
    ZeroMemory(&startup, sizeof(startup));
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESHOWWINDOW;
    startup.wShowWindow = SW_SHOWNORMAL;
    ZeroMemory(&process, sizeof(process));
    if (!CreateProcessW(options->restart, command, NULL, NULL, FALSE,
                        CREATE_UNICODE_ENVIRONMENT, NULL, working_directory,
                        &startup, &process)) {
        return FALSE;
    }
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return TRUE;
}

static void show_failure(const wchar_t *message, DWORD error) {
    wchar_t text[1024];
    StringCchPrintfW(text, ARRAYSIZE(text), L"%ls\n\n错误代码：%lu\n日志：%ls",
                     message, error, g_log_path);
    MessageBoxW(NULL, text, L"飞行雪绒更新失败", MB_OK | MB_ICONERROR);
}

static BOOL launch_installed_app(const wchar_t *install_root) {
    wchar_t launcher[FSV_UPDATE_PATH_CAPACITY];
    wchar_t working_directory[FSV_UPDATE_PATH_CAPACITY];
    wchar_t command[FSV_UPDATE_PATH_CAPACITY * 2];
    STARTUPINFOW startup;
    PROCESS_INFORMATION process;
    if (!join_path(install_root, L"app\\启动飞行雪绒.exe", launcher, ARRAYSIZE(launcher)) ||
        !join_path(install_root, L"app", working_directory, ARRAYSIZE(working_directory)) ||
        !is_regular_file(launcher) ||
        FAILED(StringCchPrintfW(command, ARRAYSIZE(command), L"\"%ls\"", launcher))) {
        return FALSE;
    }
    ZeroMemory(&startup, sizeof(startup));
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESHOWWINDOW;
    startup.wShowWindow = SW_SHOWNORMAL;
    ZeroMemory(&process, sizeof(process));
    if (!CreateProcessW(launcher, command, NULL, NULL, FALSE,
                        CREATE_UNICODE_ENVIRONMENT, NULL, working_directory,
                        &startup, &process)) {
        return FALSE;
    }
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return TRUE;
}

static int run_update(PWSTR command_line) {
    UpdateOptions options;
    wchar_t staging[FSV_UPDATE_PATH_CAPACITY];
    wchar_t module[FSV_UPDATE_PATH_CAPACITY];
    wchar_t *separator;
    BOOL staging_created = FALSE;
    BOOL user_moved = FALSE;
    BOOL switched = FALSE;
    DWORD error = ERROR_INSTALL_FAILURE;

    if (!parse_options(command_line, &options)) {
        MessageBoxW(NULL, L"更新器启动参数无效。", L"飞行雪绒更新失败", MB_OK | MB_ICONERROR);
        return 1;
    }
    if (GetModuleFileNameW(NULL, module, ARRAYSIZE(module)) == 0 ||
        FAILED(StringCchCopyW(options.workspace, ARRAYSIZE(options.workspace), module))) {
        return 1;
    }
    separator = wcsrchr(options.workspace, L'\\');
    if (separator == NULL) {
        return 1;
    }
    *separator = L'\0';
    /* The updater is launched from %TEMP%, but a shortcut or shell can still
       start it with an install-directory CWD.  That handle would keep the
       whole install root busy when rename() runs.  Move to the private
       workspace before touching anything else. */
    if (!SetCurrentDirectoryW(options.workspace)) {
        MessageBoxW(NULL, L"无法切换到更新工作目录。", L"飞行雪绒更新失败", MB_OK | MB_ICONERROR);
        return 1;
    }
    updater_log(L"独立更新器启动");

    if (!canonicalize_path(options.install_root, options.install_root, ARRAYSIZE(options.install_root)) ||
        !canonicalize_path(options.resource, options.resource, ARRAYSIZE(options.resource)) ||
        !canonicalize_path(options.restart, options.restart, ARRAYSIZE(options.restart)) ||
        !canonicalize_path(options.state_path, options.state_path, ARRAYSIZE(options.state_path)) ||
        !canonicalize_path(options.state_source, options.state_source, ARRAYSIZE(options.state_source))) {
        error = GetLastError();
        goto failure;
    }
    if (!marker_ready(options.install_root)) {
        error = ERROR_DIRECTORY;
        goto failure;
    }
    if (!wait_for_parent(options.parent_pid)) {
        updater_log(L"桌宠未在等待时间内退出，改为接管安装目录内的残留进程");
    }
    if (!stop_install_root_processes(options.install_root)) {
        error = ERROR_BUSY;
        goto failure;
    }
    cleanup_orphan_backups(options.install_root);
    if (!sha256_file_matches(options.resource, options.expected_hash)) {
        error = GetLastError();
        goto failure;
    }
    cleanup_orphan_staging(options.install_root);
    if (!make_staging_directory(options.install_root, staging, ARRAYSIZE(staging))) {
        error = GetLastError();
        goto failure;
    }
    staging_created = TRUE;
    if (!fsv_extract_zip(options.resource, staging, NULL)) {
        error = GetLastError();
        goto failure;
    }
    if (!marker_ready(staging)) {
        error = ERROR_BAD_FORMAT;
        goto failure;
    }
    if (!preserve_user_data(options.install_root, staging, &user_moved)) {
        error = GetLastError();
        goto failure;
    }
    if (!replace_install_directory(options.install_root, staging)) {
        error = GetLastError();
        goto failure;
    }
    staging_created = FALSE;
    switched = TRUE;
    if (!copy_state_file(&options)) {
        updater_log_error(L"安装目录已切换，但更新状态写入失败", GetLastError());
    }
    if (!launch_restart_helper(&options)) {
        error = GetLastError();
        goto failure;
    }
    updater_log(L"更新完成，已交接自动重启");
    return 0;

failure:
    if (error == ERROR_SUCCESS) {
        error = GetLastError();
    }
    /* Restore the only copy of user settings *before* deleting staging.
       ``preserve_user_data`` may have moved app/resc/user into staging, and
       deleting staging first would destroy it. */
    /* Only delete staging once the moved user tree is back at its old path.
       If the restore itself fails, keep staging so the settings survive for a
       manual recovery instead of silently deleting the only copy. */
    if (!switched && !rollback_preserved_user(options.install_root, staging, user_moved)) {
        updater_log_error(L"用户数据回滚失败，保留暂存目录以便恢复", GetLastError());
        staging_created = FALSE;
    }
    if (staging_created) {
        delete_tree(staging);
    }
    updater_log_error(L"更新失败", error);
    show_failure(switched ? L"新版本已经写入，但自动重启交接失败。请手动启动飞行雪绒。"
                          : L"更新未完成，当前安装仍保留在原处。", error);
    if (launch_installed_app(options.install_root)) {
        updater_log(L"已尝试重新启动现有安装");
    }
    return 1;
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR command_line, int show) {
    (void)instance;
    (void)previous;
    (void)show;
    return run_update(command_line);
}
