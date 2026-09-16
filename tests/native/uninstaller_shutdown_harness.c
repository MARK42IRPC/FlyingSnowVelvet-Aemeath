/* Behavioural harness for the pre-uninstall process sweep.
 *
 * The visual harness covers layout and painting; this one covers the rule
 * that decides which processes may be closed.  It is compiled from the real
 * uninstaller.c so the sweep cannot drift away from what ships.
 *
 * Usage (each command prints one ``key=value`` line):
 *   harness.exe hold <seconds>                     -- stand-in for the pet
 *   harness.exe spawn <image> <seconds>            -- prints pid=<n>
 *   harness.exe sweep <install-root> <shared-root> -- prints terminated=<n>
 *   harness.exe alive <pid>                        -- prints alive=0|1
 */
#define wWinMain uninstaller_entry
#include "uninstaller.c"
#undef wWinMain

#include <stdio.h>

static int hold_for(int seconds) {
    Sleep((DWORD)(seconds > 0 ? seconds : 30) * 1000u);
    return 0;
}

static int spawn_holder(const wchar_t *image, int seconds) {
    wchar_t command[FSV_PATH_CAPACITY * 2];
    STARTUPINFOW startup;
    PROCESS_INFORMATION process;
    if (FAILED(StringCchPrintfW(command, ARRAYSIZE(command), L"\"%ls\" hold %d", image, seconds))) {
        return 0;
    }
    ZeroMemory(&startup, sizeof(startup));
    startup.cb = sizeof(startup);
    ZeroMemory(&process, sizeof(process));
    if (!CreateProcessW(
            image,
            command,
            NULL,
            NULL,
            FALSE,
            CREATE_UNICODE_ENVIRONMENT,
            NULL,
            NULL,
            &startup,
            &process)) {
        return 0;
    }
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return (int)process.dwProcessId;
}

int wmain(int argc, wchar_t **argv) {
    if (argc >= 2 && wcscmp(argv[1], L"hold") == 0) {
        return hold_for(argc >= 3 ? _wtoi(argv[2]) : 30);
    }
    if (argc >= 4 && wcscmp(argv[1], L"spawn") == 0) {
        int pid = spawn_holder(argv[2], _wtoi(argv[3]));
        wprintf(L"pid=%d\n", pid);
        return pid != 0 ? 0 : 3;
    }
    if (argc >= 4 && wcscmp(argv[1], L"sweep") == 0) {
        int terminated = stop_install_root_processes(argv[2], argv[3], FALSE);
        wprintf(L"terminated=%d\n", terminated);
        return 0;
    }
    if (argc >= 3 && wcscmp(argv[1], L"alive") == 0) {
        DWORD pid = (DWORD)_wtoi(argv[2]);
        wprintf(L"alive=%d\n", pid != 0 && process_still_alive(pid) ? 1 : 0);
        return 0;
    }
    return 2;
}