#define UNICODE
#define _UNICODE
#define WIN32_LEAN_AND_MEAN

#include <windows.h>
#include <strsafe.h>
#include <wchar.h>

#include "resource.h"

#pragma comment(lib, "user32.lib")

#define FSV_PATH_CAPACITY 1024

typedef BOOL (WINAPI *SetDefaultDllDirectoriesFn)(DWORD);

static BOOL parent_directory(wchar_t *path) {
    wchar_t *separator = wcsrchr(path, L'\\');
    if (separator == NULL || separator == path || (separator == path + 2 && path[1] == L':')) {
        SetLastError(ERROR_BAD_PATHNAME);
        return FALSE;
    }
    *separator = L'\0';
    return TRUE;
}

static BOOL join_path(const wchar_t *root, const wchar_t *relative, wchar_t *output, size_t capacity) {
    return SUCCEEDED(StringCchPrintfW(output, capacity, L"%ls\\%ls", root, relative));
}

static BOOL ordinary_file(const wchar_t *path) {
    DWORD attributes = GetFileAttributesW(path);
    return attributes != INVALID_FILE_ATTRIBUTES &&
        (attributes & FILE_ATTRIBUTE_DIRECTORY) == 0 &&
        (attributes & FILE_ATTRIBUTE_REPARSE_POINT) == 0;
}

static void clear_external_runtime_overrides(void) {
    static const wchar_t *variables[] = {
        L"PYTHONHOME",
        L"PYTHONPATH",
        L"PYTHONSTARTUP",
        L"PYTHONUSERBASE",
        L"PYTHONWARNINGS",
        L"PYTHONBREAKPOINT",
        L"VIRTUAL_ENV",
        L"CONDA_PREFIX",
        L"CONDA_DEFAULT_ENV",
        L"CONDA_PYTHON_EXE",
        L"UV_PROJECT_ENVIRONMENT",
        L"UV_PYTHON",
        L"PIP_CONFIG_FILE",
        L"PIP_INDEX_URL",
        L"PIP_EXTRA_INDEX_URL",
        L"QT_PLUGIN_PATH",
        L"QT_QPA_PLATFORM_PLUGIN_PATH",
        L"QML2_IMPORT_PATH",
        L"QML_IMPORT_PATH",
        L"PLAYWRIGHT_NODEJS_PATH",
        L"PLAYWRIGHT_BROWSERS_PATH",
        L"NODE_OPTIONS",
        L"NODE_PATH",
        L"NPM_CONFIG_PREFIX",
        L"NPM_CONFIG_USERCONFIG",
        L"NPM_CONFIG_GLOBALCONFIG",
        L"DSH_HOME",
        L"DSH_BUNDLED_SKILL_DIR",
        L"DSH_TELEMETRY_DISABLED",
        L"FSV_APP_ROOT",
        L"FSV_OFFLINE_DISTRIBUTION",
        L"FSV_OFFICE_BASE_URL",
        L"FSV_OFFICE_MODEL",
        L"FSV_OFFICE_SESSION_ROOT",
        L"FSV_OFFICE_SYSTEM_PROMPT",
        L"OPENSSL_CONF",
        L"SSL_CERT_DIR",
        L"SSL_CERT_FILE"
    };
    size_t index;
    for (index = 0; index < ARRAYSIZE(variables); ++index) {
        SetEnvironmentVariableW(variables[index], NULL);
    }
}

static BOOL configure_environment(const wchar_t *install_root, const wchar_t *app_root) {
    wchar_t python_root[FSV_PATH_CAPACITY];
    wchar_t python_dlls[FSV_PATH_CAPACITY];
    wchar_t qt_bin[FSV_PATH_CAPACITY];
    wchar_t playwright_node[FSV_PATH_CAPACITY];
    wchar_t windows_directory[FSV_PATH_CAPACITY];
    wchar_t system_directory[FSV_PATH_CAPACITY];
    wchar_t path[FSV_PATH_CAPACITY * 5];
    UINT windows_length;
    UINT system_length;
    clear_external_runtime_overrides();
    if (!join_path(install_root, L"runtime\\python311", python_root, ARRAYSIZE(python_root)) ||
        !join_path(python_root, L"DLLs", python_dlls, ARRAYSIZE(python_dlls)) ||
        !join_path(python_root, L"Lib\\site-packages\\PyQt5\\Qt5\\bin", qt_bin, ARRAYSIZE(qt_bin)) ||
        !join_path(app_root, L"resc\\node-24.13.0-win-x64\\node.exe", playwright_node, ARRAYSIZE(playwright_node))) {
        SetLastError(ERROR_BUFFER_OVERFLOW);
        return FALSE;
    }
    windows_length = GetWindowsDirectoryW(windows_directory, ARRAYSIZE(windows_directory));
    system_length = GetSystemDirectoryW(system_directory, ARRAYSIZE(system_directory));
    if (windows_length == 0 || windows_length >= ARRAYSIZE(windows_directory) ||
        system_length == 0 || system_length >= ARRAYSIZE(system_directory) ||
        FAILED(StringCchPrintfW(
            path,
            ARRAYSIZE(path),
            L"%ls;%ls;%ls;%ls;%ls",
            python_root,
            python_dlls,
            qt_bin,
            system_directory,
            windows_directory
        ))) {
        SetLastError(ERROR_BUFFER_OVERFLOW);
        return FALSE;
    }
    if (!ordinary_file(playwright_node)) {
        SetLastError(ERROR_FILE_NOT_FOUND);
        return FALSE;
    }
    return SetEnvironmentVariableW(L"PATH", path) &&
        SetEnvironmentVariableW(L"FSV_APP_ROOT", app_root) &&
        SetEnvironmentVariableW(L"FSV_OFFLINE_DISTRIBUTION", L"1") &&
        SetEnvironmentVariableW(L"PYTHONNOUSERSITE", L"1") &&
        SetEnvironmentVariableW(L"PYTHONUTF8", L"1") &&
        SetEnvironmentVariableW(L"PYTHONIOENCODING", L"utf-8") &&
        SetEnvironmentVariableW(L"NODE_ENV", L"production") &&
        SetEnvironmentVariableW(L"PLAYWRIGHT_NODEJS_PATH", playwright_node) &&
        SetEnvironmentVariableW(L"AEMEATH_DESK_PET_HOME", L"C:\\AemeathDeskPet");
}

static void restrict_dll_search(void) {
    HMODULE kernel = GetModuleHandleW(L"kernel32.dll");
    SetDefaultDllDirectoriesFn function;
    if (kernel == NULL) {
        return;
    }
    function = (SetDefaultDllDirectoriesFn)(void *)GetProcAddress(kernel, "SetDefaultDllDirectories");
    if (function != NULL) {
        function(LOAD_LIBRARY_SEARCH_APPLICATION_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32 | LOAD_LIBRARY_SEARCH_USER_DIRS);
    }
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR command_line, int show) {
    wchar_t launcher_path[FSV_PATH_CAPACITY];
    wchar_t app_root[FSV_PATH_CAPACITY];
    wchar_t install_root[FSV_PATH_CAPACITY];
    wchar_t pythonw[FSV_PATH_CAPACITY];
    wchar_t marker[FSV_PATH_CAPACITY];
    wchar_t python_command[FSV_PATH_CAPACITY * 3];
    STARTUPINFOW startup;
    PROCESS_INFORMATION process;
    DWORD length;
    (void)instance;
    (void)previous;
    (void)command_line;
    (void)show;
    restrict_dll_search();
    length = GetModuleFileNameW(NULL, launcher_path, ARRAYSIZE(launcher_path));
    if (length == 0 || length >= ARRAYSIZE(launcher_path) ||
        FAILED(StringCchCopyW(app_root, ARRAYSIZE(app_root), launcher_path)) ||
        !parent_directory(app_root) ||
        FAILED(StringCchCopyW(install_root, ARRAYSIZE(install_root), app_root)) ||
        !parent_directory(install_root) ||
        !join_path(install_root, L"runtime\\python311\\pythonw.exe", pythonw, ARRAYSIZE(pythonw)) ||
        !join_path(install_root, L".fsv-install-root", marker, ARRAYSIZE(marker)) ||
        !ordinary_file(pythonw) || !ordinary_file(marker)) {
        MessageBoxW(NULL, L"飞行雪绒安装目录不完整，请重新运行安装器。", L"飞行雪绒", MB_OK | MB_ICONERROR);
        return 1;
    }
    if (!configure_environment(install_root, app_root) ||
        FAILED(StringCchPrintfW(
            python_command,
            ARRAYSIZE(python_command),
            L"\"%ls\" -I -c \"import os,runpy,sys;p=os.environ['FSV_APP_ROOT'];sys.path.insert(0,p);runpy.run_path(os.path.join(p,'lib','core','qt_desktop_pet.py'),run_name='__main__')\"",
            pythonw
        ))) {
        MessageBoxW(NULL, L"无法准备飞行雪绒的隔离运行环境。", L"飞行雪绒", MB_OK | MB_ICONERROR);
        return 1;
    }
    ZeroMemory(&startup, sizeof(startup));
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESHOWWINDOW;
    startup.wShowWindow = SW_SHOWNORMAL;
    ZeroMemory(&process, sizeof(process));
    if (!CreateProcessW(
        pythonw,
        python_command,
        NULL,
        NULL,
        FALSE,
        CREATE_UNICODE_ENVIRONMENT | CREATE_NEW_PROCESS_GROUP,
        NULL,
        app_root,
        &startup,
        &process
    )) {
        wchar_t error[256];
        StringCchPrintfW(error, ARRAYSIZE(error), L"无法启动飞行雪绒（错误代码 %lu）。", GetLastError());
        MessageBoxW(NULL, error, L"飞行雪绒", MB_OK | MB_ICONERROR);
        return 1;
    }
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return 0;
}
