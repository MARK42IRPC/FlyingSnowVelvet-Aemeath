#define UNICODE
#define _UNICODE
#define WIN32_LEAN_AND_MEAN

#include <windows.h>
#include <stdio.h>
#include <wchar.h>

#include "zip_extract.h"

static ULONGLONG g_callback_count;
static FsvZipProgressMessage g_last_progress;

static void record_progress(const FsvZipProgressMessage *message) {
    ++g_callback_count;
    g_last_progress = *message;
}

int wmain(int argc, wchar_t **argv) {
    if (argc == 3 && wcscmp(argv[1], L"stats") == 0) {
        ULONGLONG files = 0;
        ULONGLONG bytes = 0;
        if (!fsv_zip_get_statistics(argv[2], &files, &bytes)) {
            printf("ERROR %lu\n", GetLastError());
            return 2;
        }
        printf(
            "OK %llu %llu\n",
            (unsigned long long)files,
            (unsigned long long)bytes
        );
        return 0;
    }
    if (argc == 4 && wcscmp(argv[1], L"extract") == 0) {
        if (!fsv_extract_zip(argv[2], argv[3], record_progress)) {
            printf("ERROR %lu\n", GetLastError());
            return 3;
        }
        printf(
            "OK %llu %llu %lu\n",
            (unsigned long long)g_callback_count,
            (unsigned long long)g_last_progress.completed_files,
            g_last_progress.percent
        );
        return 0;
    }
    fprintf(stderr, "usage: zip_extract_harness <stats ZIP | extract ZIP DEST>\n");
    return 64;
}
