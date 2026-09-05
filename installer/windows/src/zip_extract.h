#pragma once

#include <windows.h>

#define FSV_ZIP_PATH_CAPACITY 520

typedef struct FsvZipProgressMessage {
    DWORD percent;
    BOOL scanning_directory;
    ULONGLONG completed_files;
    ULONGLONG total_files;
    ULONGLONG completed_bytes;
    ULONGLONG total_bytes;
    ULONGLONG eta_seconds;
    BOOL eta_known;
    wchar_t current_file[FSV_ZIP_PATH_CAPACITY];
} FsvZipProgressMessage;

typedef void (*FsvZipProgressCallback)(const FsvZipProgressMessage *message);

BOOL fsv_extract_zip(
    const wchar_t *archive_path,
    const wchar_t *destination,
    FsvZipProgressCallback callback
);

BOOL fsv_zip_get_statistics(
    const wchar_t *archive_path,
    ULONGLONG *total_files,
    ULONGLONG *total_bytes
);
