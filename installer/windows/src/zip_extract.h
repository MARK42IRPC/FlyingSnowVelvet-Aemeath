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

/* Worker-pool sizing and the adaptive load limit are part of the contract with
   the native harness, which pins them without running a disk-heavy
   extraction. */
unsigned fsv_zip_worker_count(unsigned logical_processors, unsigned entry_count);
unsigned fsv_zip_worker_limit(unsigned logical_processors, unsigned max_workers,
                              unsigned busy_percent, unsigned our_percent);
/* Remaining time is part of the same contract: ``finished`` entries inside
   ``elapsed_ms`` give a per-second rate which is applied to the entries still
   outstanding.  Returns 0 while no estimate is possible, so the caller keeps
   the previous value instead of flickering back to "calculating". */
ULONGLONG fsv_zip_eta_seconds(ULONGLONG total_files, ULONGLONG completed_files,
                              ULONGLONG finished, ULONGLONG elapsed_ms);

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
