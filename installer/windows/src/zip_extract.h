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

/* Sharded payload contract, mirrored by ``scripts/build_offline_installer.py``.
   The offline archive keeps one placeholder entry per file, so the in-app
   updater can still walk and vet every path, but the bytes themselves live in
   a handful of independent solid LZMA2 streams ("shards").  The index entry
   lists, for every placeholder in central-directory order, which shard holds
   it and where inside the decoded stream it starts.  A placeholder advertises
   its real size with a zero compressed size, which is what tells the extractor
   to take the bytes from a shard instead of the entry itself.

   A raw LZMA2 stream does not describe its own dictionary, so the dictionary
   size is baked into both sides; 0x1C encodes 64 MiB. */
#define FSV_ZIP_SHARD_INDEX_NAME ".fsv-shard-index.bin"
#define FSV_ZIP_SHARD_NAME_PREFIX ".fsv-shard-"
#define FSV_ZIP_SHARD_NAME_SUFFIX ".fsvlzma"
#define FSV_ZIP_SHARD_NAME_DIGITS 3
#define FSV_ZIP_MAX_SHARDS 64
#define FSV_ZIP_SHARD_DICT_PROPERTY 0x1C
#define FSV_ZIP_SHARD_INDEX_ROW 20U
#define FSV_ZIP_SHARD_INDEX_MAX_BYTES (64U * 1024U * 1024U)

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
