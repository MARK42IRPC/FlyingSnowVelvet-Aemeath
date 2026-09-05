#define WIN32_LEAN_AND_MEAN
#include "zip_extract.h"

#include <strsafe.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "zlib.h"

#define FSV_ZIP_READ_BUFFER (1024U * 1024U)
#define FSV_ZIP_EOCD_SCAN (0x10000ULL + 22ULL)
#define FSV_ZIP_LOCAL_HEADER_SIZE 30U
#define FSV_ZIP_CENTRAL_HEADER_SIZE 46U
#define FSV_ZIP_MAX_ENTRIES 10000000ULL

typedef struct FsvZipInfo {
    ULONGLONG archive_size;
    ULONGLONG entry_count;
    ULONGLONG central_size;
    ULONGLONG central_offset;
} FsvZipInfo;

typedef struct FsvZipEntry {
    BYTE flags[2];
    WORD flags_value;
    WORD method;
    DWORD crc32;
    ULONGLONG compressed_size;
    ULONGLONG uncompressed_size;
    ULONGLONG local_offset;
    BYTE *name;
    WORD name_length;
    BOOL directory;
} FsvZipEntry;

typedef struct FsvZipProgressState {
    FsvZipProgressCallback callback;
    BOOL scanning_directory;
    ULONGLONG total_files;
    ULONGLONG total_bytes;
    ULONGLONG completed_files;
    ULONGLONG completed_bytes;
    ULONGLONG started_at;
    ULONGLONG last_post_at;
    ULONGLONG last_sample_at;
    ULONGLONG last_sample_bytes;
    double bytes_per_second;
    wchar_t current_file[FSV_ZIP_PATH_CAPACITY];
} FsvZipProgressState;

static WORD read_u16_le(const BYTE *data) {
    return (WORD)data[0] | ((WORD)data[1] << 8);
}

static DWORD read_u32_le(const BYTE *data) {
    return (DWORD)data[0] |
           ((DWORD)data[1] << 8) |
           ((DWORD)data[2] << 16) |
           ((DWORD)data[3] << 24);
}

static ULONGLONG read_u64_le(const BYTE *data) {
    return (ULONGLONG)read_u32_le(data) |
           ((ULONGLONG)read_u32_le(data + 4) << 32);
}

static BOOL add_u64(ULONGLONG *value, ULONGLONG increment) {
    if (*value > ~(ULONGLONG)0 - increment) {
        SetLastError(ERROR_FILE_TOO_LARGE);
        return FALSE;
    }
    *value += increment;
    return TRUE;
}

static voidpf fsv_zalloc(voidpf opaque, uInt items, uInt size) {
    SIZE_T total;
    (void)opaque;
    if (size != 0 && (SIZE_T)items > ~(SIZE_T)0 / (SIZE_T)size) {
        return Z_NULL;
    }
    total = (SIZE_T)items * (SIZE_T)size;
    return HeapAlloc(GetProcessHeap(), 0, total == 0 ? 1 : total);
}

static void fsv_zfree(voidpf opaque, voidpf address) {
    (void)opaque;
    if (address != Z_NULL) {
        HeapFree(GetProcessHeap(), 0, address);
    }
}

static BOOL read_at(HANDLE file, ULONGLONG offset, void *buffer, DWORD size) {
    LARGE_INTEGER position;
    DWORD received = 0;
    if (offset > 0x7fffffffffffffffULL) {
        SetLastError(ERROR_FILE_TOO_LARGE);
        return FALSE;
    }
    position.QuadPart = (LONGLONG)offset;
    if (!SetFilePointerEx(file, position, NULL, FILE_BEGIN)) {
        return FALSE;
    }
    return size == 0 || (ReadFile(file, buffer, size, &received, NULL) && received == size);
}

static BOOL parse_zip64_extra(
    const BYTE *extra,
    DWORD extra_length,
    DWORD compressed_32,
    DWORD uncompressed_32,
    DWORD local_offset_32,
    ULONGLONG *compressed,
    ULONGLONG *uncompressed,
    ULONGLONG *local_offset
) {
    DWORD offset = 0;
    BOOL need_compressed = compressed_32 == 0xffffffffU;
    BOOL need_uncompressed = uncompressed_32 == 0xffffffffU;
    BOOL need_offset = local_offset_32 == 0xffffffffU;
    *compressed = compressed_32;
    *uncompressed = uncompressed_32;
    *local_offset = local_offset_32;
    if (!need_compressed && !need_uncompressed && !need_offset) {
        return TRUE;
    }
    while (offset + 4 <= extra_length) {
        WORD tag = read_u16_le(extra + offset);
        WORD size = read_u16_le(extra + offset + 2);
        DWORD cursor = offset + 4;
        DWORD end = cursor + size;
        if (end > extra_length) {
            SetLastError(ERROR_BAD_FORMAT);
            return FALSE;
        }
        if (tag == 0x0001) {
            if (need_uncompressed) {
                if (cursor + 8 > end) {
                    SetLastError(ERROR_BAD_FORMAT);
                    return FALSE;
                }
                *uncompressed = read_u64_le(extra + cursor);
                cursor += 8;
                need_uncompressed = FALSE;
            }
            if (need_compressed) {
                if (cursor + 8 > end) {
                    SetLastError(ERROR_BAD_FORMAT);
                    return FALSE;
                }
                *compressed = read_u64_le(extra + cursor);
                cursor += 8;
                need_compressed = FALSE;
            }
            if (need_offset) {
                if (cursor + 8 > end) {
                    SetLastError(ERROR_BAD_FORMAT);
                    return FALSE;
                }
                *local_offset = read_u64_le(extra + cursor);
                need_offset = FALSE;
            }
            return !need_compressed && !need_uncompressed && !need_offset;
        }
        offset = end;
    }
    SetLastError(ERROR_BAD_FORMAT);
    return FALSE;
}

static BOOL locate_zip(HANDLE archive, FsvZipInfo *info) {
    LARGE_INTEGER size;
    ULONGLONG tail_length;
    BYTE *tail = NULL;
    ULONGLONG eocd_offset = 0;
    BOOL found = FALSE;
    if (!GetFileSizeEx(archive, &size) || size.QuadPart < 22) {
        SetLastError(ERROR_BAD_FORMAT);
        return FALSE;
    }
    info->archive_size = (ULONGLONG)size.QuadPart;
    tail_length = info->archive_size < FSV_ZIP_EOCD_SCAN ? info->archive_size : FSV_ZIP_EOCD_SCAN;
    tail = (BYTE *)HeapAlloc(GetProcessHeap(), 0, (SIZE_T)tail_length);
    if (tail == NULL || !read_at(archive, info->archive_size - tail_length, tail, (DWORD)tail_length)) {
        if (tail != NULL) {
            HeapFree(GetProcessHeap(), 0, tail);
        }
        SetLastError(ERROR_READ_FAULT);
        return FALSE;
    }
    {
        LONGLONG index;
        for (index = (LONGLONG)tail_length - 22; index >= 0; --index) {
            WORD entries_disk;
            WORD entries_total;
            DWORD central_size_32;
            DWORD central_offset_32;
            WORD comment_length;
            ULONGLONG candidate_end;
            if (read_u32_le(tail + index) != 0x06054b50U) {
                continue;
            }
            entries_disk = read_u16_le(tail + index + 8);
            entries_total = read_u16_le(tail + index + 10);
            central_size_32 = read_u32_le(tail + index + 12);
            central_offset_32 = read_u32_le(tail + index + 16);
            comment_length = read_u16_le(tail + index + 20);
            candidate_end = (ULONGLONG)index + 22ULL + comment_length;
            if (candidate_end != tail_length || entries_disk != entries_total) {
                continue;
            }
            eocd_offset = info->archive_size - tail_length + (ULONGLONG)index;
            if (entries_total == 0xffffU || central_size_32 == 0xffffffffU || central_offset_32 == 0xffffffffU) {
                BYTE locator[20];
                BYTE record[56];
                ULONGLONG zip64_offset;
                if (eocd_offset < sizeof(locator) ||
                    !read_at(archive, eocd_offset - sizeof(locator), locator, sizeof(locator)) ||
                    read_u32_le(locator) != 0x07064b50U || read_u32_le(locator + 4) != 0 ||
                    read_u32_le(locator + 16) != 1) {
                    continue;
                }
                zip64_offset = read_u64_le(locator + 8);
                if (!read_at(archive, zip64_offset, record, sizeof(record)) ||
                    read_u32_le(record) != 0x06064b50U || read_u32_le(record + 16) != 0 ||
                    read_u32_le(record + 20) != 0) {
                    continue;
                }
                info->entry_count = read_u64_le(record + 32);
                info->central_size = read_u64_le(record + 40);
                info->central_offset = read_u64_le(record + 48);
            } else {
                info->entry_count = entries_total;
                info->central_size = central_size_32;
                info->central_offset = central_offset_32;
            }
            if (info->central_offset > info->archive_size ||
                info->central_size > info->archive_size - info->central_offset) {
                continue;
            }
            found = TRUE;
            break;
        }
    }
    HeapFree(GetProcessHeap(), 0, tail);
    if (!found) {
        SetLastError(ERROR_BAD_FORMAT);
    }
    return found;
}

static void free_entry(FsvZipEntry *entry) {
    if (entry->name != NULL) {
        HeapFree(GetProcessHeap(), 0, entry->name);
    }
    ZeroMemory(entry, sizeof(*entry));
}

static BOOL is_reserved_windows_component(const wchar_t *component) {
    size_t base_length = wcscspn(component, L".");
    while (base_length > 0 && component[base_length - 1] == L' ') {
        --base_length;
    }
    if (base_length == 3 &&
        (_wcsnicmp(component, L"CON", 3) == 0 ||
         _wcsnicmp(component, L"PRN", 3) == 0 ||
         _wcsnicmp(component, L"AUX", 3) == 0 ||
         _wcsnicmp(component, L"NUL", 3) == 0)) {
        return TRUE;
    }
    return base_length == 4 &&
        (_wcsnicmp(component, L"COM", 3) == 0 || _wcsnicmp(component, L"LPT", 3) == 0) &&
        component[3] >= L'1' && component[3] <= L'9';
}

static BOOL read_entry(HANDLE archive, ULONGLONG position, ULONGLONG central_end, FsvZipEntry *entry, ULONGLONG *next_position) {
    BYTE header[FSV_ZIP_CENTRAL_HEADER_SIZE];
    BYTE *variable = NULL;
    WORD extra_length;
    WORD comment_length;
    DWORD variable_length;
    ULONGLONG compressed;
    ULONGLONG uncompressed;
    ULONGLONG local_offset;
    BOOL success = FALSE;
    ZeroMemory(entry, sizeof(*entry));
    if (position > central_end || central_end - position < sizeof(header) ||
        !read_at(archive, position, header, sizeof(header)) || read_u32_le(header) != 0x02014b50U) {
        SetLastError(ERROR_BAD_FORMAT);
        return FALSE;
    }
    entry->flags_value = read_u16_le(header + 8);
    entry->method = read_u16_le(header + 10);
    entry->crc32 = read_u32_le(header + 16);
    entry->name_length = read_u16_le(header + 28);
    extra_length = read_u16_le(header + 30);
    comment_length = read_u16_le(header + 32);
    variable_length = (DWORD)entry->name_length + extra_length + comment_length;
    if (variable_length > central_end - position - sizeof(header)) {
        SetLastError(ERROR_BAD_FORMAT);
        return FALSE;
    }
    if (entry->name_length == 0 || variable_length > 0) {
        variable = (BYTE *)HeapAlloc(GetProcessHeap(), 0, variable_length == 0 ? 1 : variable_length);
        if (variable == NULL || !read_at(archive, position + sizeof(header), variable, variable_length)) {
            if (variable != NULL) {
                HeapFree(GetProcessHeap(), 0, variable);
            }
            SetLastError(ERROR_READ_FAULT);
            return FALSE;
        }
    }
    if (entry->name_length == 0) {
        SetLastError(ERROR_BAD_FORMAT);
        goto cleanup;
    }
    entry->name = (BYTE *)HeapAlloc(GetProcessHeap(), 0, entry->name_length);
    if (entry->name == NULL) {
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
        goto cleanup;
    }
    memcpy(entry->name, variable, entry->name_length);
    if (!parse_zip64_extra(
        variable + entry->name_length,
        extra_length,
        read_u32_le(header + 20),
        read_u32_le(header + 24),
        read_u32_le(header + 42),
        &compressed,
        &uncompressed,
        &local_offset
    )) {
        goto cleanup;
    }
    entry->compressed_size = compressed;
    entry->uncompressed_size = uncompressed;
    entry->local_offset = local_offset;
    entry->directory = entry->name_length > 0 &&
        (entry->name[entry->name_length - 1] == '/' || entry->name[entry->name_length - 1] == '\\');
    *next_position = position + sizeof(header) + variable_length;
    success = TRUE;

cleanup:
    if (variable != NULL) {
        HeapFree(GetProcessHeap(), 0, variable);
    }
    if (!success) {
        free_entry(entry);
    }
    return success;
}

static BOOL decode_entry_path(const FsvZipEntry *entry, wchar_t *output, size_t capacity) {
    int length;
    wchar_t *cursor;
    wchar_t *component;
    WORD index;
    if (entry->name_length > INT_MAX) {
        SetLastError(ERROR_FILENAME_EXCED_RANGE);
        return FALSE;
    }
    for (index = 0; index < entry->name_length; ++index) {
        if (entry->name[index] == 0) {
            SetLastError(ERROR_BAD_PATHNAME);
            return FALSE;
        }
    }
    length = MultiByteToWideChar(
        (entry->flags_value & 0x0800U) != 0 ? CP_UTF8 : CP_ACP,
        MB_ERR_INVALID_CHARS,
        (const char *)entry->name,
        (int)entry->name_length,
        output,
        (int)capacity - 1
    );
    if (length <= 0 || (size_t)length >= capacity) {
        SetLastError(ERROR_FILENAME_EXCED_RANGE);
        return FALSE;
    }
    output[length] = L'\0';
    for (cursor = output; *cursor != L'\0'; ++cursor) {
        if (*cursor == L'/') {
            *cursor = L'\\';
        }
        if (*cursor < 32 || wcschr(L"<>:\"|?*", *cursor) != NULL) {
            SetLastError(ERROR_BAD_PATHNAME);
            return FALSE;
        }
    }
    if (output[0] == L'\\' || output[0] == L'/' ||
        (length >= 2 && output[1] == L':')) {
        SetLastError(ERROR_BAD_PATHNAME);
        return FALSE;
    }
    component = output;
    while (*component != L'\0') {
        wchar_t *end = wcschr(component, L'\\');
        if (end != NULL) {
            *end = L'\0';
        }
        if (wcscmp(component, L"..") == 0 || wcscmp(component, L".") == 0 || *component == L'\0' ||
            component[wcslen(component) - 1] == L'.' || component[wcslen(component) - 1] == L' ' ||
            is_reserved_windows_component(component)) {
            SetLastError(ERROR_BAD_PATHNAME);
            return FALSE;
        }
        if (end == NULL) {
            break;
        }
        *end = L'\\';
        component = end + 1;
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

static BOOL ensure_directory(const wchar_t *directory) {
    wchar_t parent[FSV_ZIP_PATH_CAPACITY];
    wchar_t *separator;
    DWORD attributes = GetFileAttributesW(directory);
    if (attributes != INVALID_FILE_ATTRIBUTES) {
        if ((attributes & FILE_ATTRIBUTE_DIRECTORY) == 0 || (attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
            SetLastError(ERROR_CANT_ACCESS_FILE);
            return FALSE;
        }
        return TRUE;
    }
    if (GetLastError() != ERROR_FILE_NOT_FOUND && GetLastError() != ERROR_PATH_NOT_FOUND) {
        return FALSE;
    }
    if (FAILED(StringCchCopyW(parent, ARRAYSIZE(parent), directory))) {
        SetLastError(ERROR_BUFFER_OVERFLOW);
        return FALSE;
    }
    separator = wcsrchr(parent, L'\\');
    if (separator != NULL && separator != parent) {
        *separator = L'\0';
        if (!ensure_directory(parent)) {
            return FALSE;
        }
    }
    if (!CreateDirectoryW(directory, NULL) && GetLastError() != ERROR_ALREADY_EXISTS) {
        return FALSE;
    }
    attributes = GetFileAttributesW(directory);
    return attributes != INVALID_FILE_ATTRIBUTES &&
        (attributes & FILE_ATTRIBUTE_DIRECTORY) != 0 &&
        (attributes & FILE_ATTRIBUTE_REPARSE_POINT) == 0;
}

static void post_progress(FsvZipProgressState *state, const wchar_t *path, BOOL force) {
    ULONGLONG now;
    FsvZipProgressMessage message;
    if (state->callback == NULL) {
        return;
    }
    now = GetTickCount64();
    if (path != NULL) {
        StringCchCopyW(state->current_file, ARRAYSIZE(state->current_file), path);
    }
    if (!force && state->last_post_at != 0 && now - state->last_post_at < 80) {
        return;
    }
    if (now > state->last_sample_at && state->completed_bytes >= state->last_sample_bytes) {
        ULONGLONG elapsed = now - state->last_sample_at;
        ULONGLONG delta = state->completed_bytes - state->last_sample_bytes;
        if (elapsed > 0 && delta > 0) {
            double sample = ((double)delta * 1000.0) / (double)elapsed;
            state->bytes_per_second = state->bytes_per_second <= 0.0
                ? sample
                : state->bytes_per_second * 0.75 + sample * 0.25;
        }
        state->last_sample_at = now;
        state->last_sample_bytes = state->completed_bytes;
    }
    ZeroMemory(&message, sizeof(message));
    message.completed_files = state->completed_files;
    message.scanning_directory = state->scanning_directory;
    message.total_files = state->total_files;
    message.completed_bytes = state->completed_bytes;
    message.total_bytes = state->total_bytes;
    message.percent = state->total_bytes == 0 || state->completed_bytes >= state->total_bytes
        ? 100
        : (DWORD)(((double)state->completed_bytes * 100.0) / (double)state->total_bytes);
    if (state->bytes_per_second > 1.0 && state->completed_bytes < state->total_bytes) {
        message.eta_seconds = (ULONGLONG)(((double)(state->total_bytes - state->completed_bytes) / state->bytes_per_second) + 0.5);
        message.eta_known = TRUE;
    }
    StringCchCopyW(message.current_file, ARRAYSIZE(message.current_file), state->current_file);
    state->callback(&message);
    state->last_post_at = now;
}

static BOOL copy_stored(
    HANDLE archive,
    HANDLE output,
    ULONGLONG compressed_size,
    ULONGLONG expected_size,
    DWORD expected_crc,
    FsvZipProgressState *state,
    const wchar_t *path
) {
    BYTE *buffer = (BYTE *)HeapAlloc(GetProcessHeap(), 0, FSV_ZIP_READ_BUFFER);
    ULONGLONG remaining = compressed_size;
    ULONGLONG produced = 0;
    uLong crc = crc32(0L, Z_NULL, 0);
    BOOL success = FALSE;
    if (buffer == NULL) {
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
        return FALSE;
    }
    while (remaining > 0) {
        DWORD request = remaining > FSV_ZIP_READ_BUFFER ? FSV_ZIP_READ_BUFFER : (DWORD)remaining;
        DWORD received = 0;
        if (!ReadFile(archive, buffer, request, &received, NULL) || received != request) {
            goto cleanup;
        }
        if (!WriteFile(output, buffer, received, &request, NULL) || request != received) {
            goto cleanup;
        }
        crc = crc32(crc, buffer, received);
        remaining -= received;
        produced += received;
        if (state->completed_bytes > ~(ULONGLONG)0 - received) {
            SetLastError(ERROR_FILE_TOO_LARGE);
            goto cleanup;
        }
        state->completed_bytes += received;
        post_progress(state, path, FALSE);
    }
    if (produced != expected_size || (DWORD)crc != expected_crc) {
        SetLastError(ERROR_CRC);
        goto cleanup;
    }
    success = TRUE;

cleanup:
    HeapFree(GetProcessHeap(), 0, buffer);
    return success;
}

static BOOL copy_deflated(
    HANDLE archive,
    HANDLE output,
    ULONGLONG compressed_size,
    ULONGLONG expected_size,
    DWORD expected_crc,
    FsvZipProgressState *state,
    const wchar_t *path
) {
    BYTE *input = (BYTE *)HeapAlloc(GetProcessHeap(), 0, FSV_ZIP_READ_BUFFER);
    BYTE *output_buffer = (BYTE *)HeapAlloc(GetProcessHeap(), 0, FSV_ZIP_READ_BUFFER);
    z_stream stream;
    ULONGLONG remaining = compressed_size;
    ULONGLONG produced = 0;
    uLong crc = crc32(0L, Z_NULL, 0);
    int result;
    BOOL success = FALSE;
    if (input == NULL || output_buffer == NULL) {
        HeapFree(GetProcessHeap(), 0, input);
        HeapFree(GetProcessHeap(), 0, output_buffer);
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
        return FALSE;
    }
    ZeroMemory(&stream, sizeof(stream));
    stream.zalloc = fsv_zalloc;
    stream.zfree = fsv_zfree;
    stream.opaque = Z_NULL;
    if (inflateInit2(&stream, -MAX_WBITS) != Z_OK) {
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
        goto cleanup;
    }
    stream.next_in = Z_NULL;
    stream.avail_in = 0;
    for (;;) {
        if (stream.avail_in == 0 && remaining > 0) {
            DWORD request = remaining > FSV_ZIP_READ_BUFFER ? FSV_ZIP_READ_BUFFER : (DWORD)remaining;
            DWORD received = 0;
            if (!ReadFile(archive, input, request, &received, NULL) || received != request) {
                goto inflate_cleanup;
            }
            stream.next_in = input;
            stream.avail_in = received;
            remaining -= received;
        }
        stream.next_out = output_buffer;
        stream.avail_out = FSV_ZIP_READ_BUFFER;
        result = inflate(&stream, Z_NO_FLUSH);
        {
            DWORD produced_now = FSV_ZIP_READ_BUFFER - stream.avail_out;
            if (produced_now > 0) {
                DWORD written = 0;
                if (!WriteFile(output, output_buffer, produced_now, &written, NULL) || written != produced_now) {
                    goto inflate_cleanup;
                }
                crc = crc32(crc, output_buffer, produced_now);
                produced += produced_now;
                if (state->completed_bytes > ~(ULONGLONG)0 - produced_now) {
                    SetLastError(ERROR_FILE_TOO_LARGE);
                    goto inflate_cleanup;
                }
                state->completed_bytes += produced_now;
                post_progress(state, path, FALSE);
            }
        }
        if (result == Z_STREAM_END) {
            break;
        }
        if (result != Z_OK || (stream.avail_in == 0 && remaining == 0)) {
            SetLastError(ERROR_BAD_FORMAT);
            goto inflate_cleanup;
        }
    }
    if (produced != expected_size || (DWORD)crc != expected_crc) {
        SetLastError(ERROR_CRC);
        goto inflate_cleanup;
    }
    success = TRUE;

inflate_cleanup:
    inflateEnd(&stream);
cleanup:
    HeapFree(GetProcessHeap(), 0, input);
    HeapFree(GetProcessHeap(), 0, output_buffer);
    return success;
}

static BOOL extract_entry(
    HANDLE archive,
    ULONGLONG archive_size,
    const FsvZipEntry *entry,
    const wchar_t *destination,
    FsvZipProgressState *state
) {
    BYTE local_header[FSV_ZIP_LOCAL_HEADER_SIZE];
    WORD name_length;
    WORD extra_length;
    ULONGLONG data_offset;
    wchar_t relative[FSV_ZIP_PATH_CAPACITY];
    wchar_t output_path[FSV_ZIP_PATH_CAPACITY];
    HANDLE output = INVALID_HANDLE_VALUE;
    BOOL success = FALSE;
    if (!decode_entry_path(entry, relative, ARRAYSIZE(relative))) {
        return FALSE;
    }
    if (!join_path(destination, relative, output_path, ARRAYSIZE(output_path))) {
        SetLastError(ERROR_BUFFER_OVERFLOW);
        return FALSE;
    }
    if (entry->directory) {
        return ensure_directory(output_path);
    }
    if ((entry->flags_value & 0x0001U) != 0 || (entry->method != 0 && entry->method != 8)) {
        SetLastError(ERROR_NOT_SUPPORTED);
        return FALSE;
    }
    if (!read_at(archive, entry->local_offset, local_header, sizeof(local_header)) ||
        read_u32_le(local_header) != 0x04034b50U) {
        SetLastError(ERROR_BAD_FORMAT);
        return FALSE;
    }
    name_length = read_u16_le(local_header + 26);
    extra_length = read_u16_le(local_header + 28);
    if (entry->local_offset > archive_size ||
        sizeof(local_header) > archive_size - entry->local_offset ||
        name_length > archive_size - entry->local_offset - sizeof(local_header) ||
        extra_length > archive_size - entry->local_offset - sizeof(local_header) - name_length) {
        SetLastError(ERROR_BAD_FORMAT);
        return FALSE;
    }
    data_offset = entry->local_offset + sizeof(local_header) + name_length + extra_length;
    if (data_offset < entry->local_offset || data_offset > archive_size ||
        entry->compressed_size > archive_size - data_offset) {
        SetLastError(ERROR_BAD_FORMAT);
        return FALSE;
    }
    {
        wchar_t parent[FSV_ZIP_PATH_CAPACITY];
        wchar_t *separator;
        if (FAILED(StringCchCopyW(parent, ARRAYSIZE(parent), output_path))) {
            SetLastError(ERROR_BUFFER_OVERFLOW);
            return FALSE;
        }
        separator = wcsrchr(parent, L'\\');
        if (separator == NULL) {
            SetLastError(ERROR_BAD_PATHNAME);
            return FALSE;
        }
        *separator = L'\0';
        if (!ensure_directory(parent)) {
            return FALSE;
        }
    }
    output = CreateFileW(
        output_path,
        GENERIC_WRITE,
        0,
        NULL,
        CREATE_NEW,
        FILE_ATTRIBUTE_NORMAL,
        NULL
    );
    if (output == INVALID_HANDLE_VALUE) {
        return FALSE;
    }
    {
        LARGE_INTEGER position;
        position.QuadPart = (LONGLONG)data_offset;
        if (!SetFilePointerEx(archive, position, NULL, FILE_BEGIN)) {
            CloseHandle(output);
            DeleteFileW(output_path);
            return FALSE;
        }
    }
    post_progress(state, relative, FALSE);
    if (entry->method == 0) {
        success = copy_stored(archive, output, entry->compressed_size, entry->uncompressed_size, entry->crc32, state, relative);
    } else {
        success = copy_deflated(archive, output, entry->compressed_size, entry->uncompressed_size, entry->crc32, state, relative);
    }
    if (!CloseHandle(output)) {
        output = INVALID_HANDLE_VALUE;
        success = FALSE;
    } else {
        output = INVALID_HANDLE_VALUE;
    }
    if (!success) {
        DeleteFileW(output_path);
    }
    return success;
}

BOOL fsv_extract_zip(const wchar_t *archive_path, const wchar_t *destination, FsvZipProgressCallback callback) {
    HANDLE archive = INVALID_HANDLE_VALUE;
    LARGE_INTEGER size;
    FsvZipInfo info;
    FsvZipProgressState state;
    ULONGLONG position;
    ULONGLONG central_end;
    ULONGLONG index;
    ULONGLONG file_count = 0;
    BOOL success = FALSE;
    archive = CreateFileW(
        archive_path,
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        NULL,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN,
        NULL
    );
    if (archive == INVALID_HANDLE_VALUE || !GetFileSizeEx(archive, &size) || !locate_zip(archive, &info)) {
        if (archive != INVALID_HANDLE_VALUE) {
            CloseHandle(archive);
        }
        return FALSE;
    }
    ZeroMemory(&state, sizeof(state));
    state.callback = callback;
    state.started_at = GetTickCount64();
    state.last_sample_at = state.started_at;
    state.total_files = 0;
    state.total_bytes = 0;
    position = info.central_offset;
    central_end = info.central_offset + info.central_size;
    if (central_end < info.central_offset ||
        info.entry_count > FSV_ZIP_MAX_ENTRIES ||
        (info.entry_count > 0 && info.central_size / FSV_ZIP_CENTRAL_HEADER_SIZE < info.entry_count)) {
        SetLastError(ERROR_BAD_FORMAT);
        goto cleanup;
    }
    state.scanning_directory = TRUE;
    state.total_files = info.entry_count;
    post_progress(&state, L"正在读取归档目录...", TRUE);
    for (index = 0; index < info.entry_count; ++index) {
        FsvZipEntry entry;
        ULONGLONG next_position;
        ZeroMemory(&entry, sizeof(entry));
        if (!read_entry(archive, position, central_end, &entry, &next_position)) {
            goto cleanup;
        }
        if (!entry.directory && (!add_u64(&file_count, 1) || !add_u64(&state.total_bytes, entry.uncompressed_size))) {
            free_entry(&entry);
            goto cleanup;
        }
        free_entry(&entry);
        position = next_position;
        state.completed_files = index + 1;
        post_progress(&state, NULL, FALSE);
    }
    if (position != central_end) {
        SetLastError(ERROR_BAD_FORMAT);
        goto cleanup;
    }
    state.total_files = file_count;
    if (!ensure_directory(destination)) {
        goto cleanup;
    }
    state.completed_files = 0;
    state.completed_bytes = 0;
    state.scanning_directory = FALSE;
    position = info.central_offset;
    post_progress(&state, L"正在准备解压...", TRUE);
    for (index = 0; index < info.entry_count; ++index) {
        FsvZipEntry entry;
        ULONGLONG next_position;
        ZeroMemory(&entry, sizeof(entry));
        if (!read_entry(archive, position, central_end, &entry, &next_position)) {
            goto cleanup;
        }
        if (!extract_entry(archive, info.archive_size, &entry, destination, &state)) {
            free_entry(&entry);
            goto cleanup;
        }
        if (!entry.directory) {
            ++state.completed_files;
            post_progress(&state, NULL, FALSE);
        }
        free_entry(&entry);
        position = next_position;
    }
    post_progress(&state, L"解压完成，正在校验文件...", TRUE);
    success = TRUE;

cleanup:
    {
        DWORD error = success ? ERROR_SUCCESS : GetLastError();
        CloseHandle(archive);
        if (!success) {
            SetLastError(error);
        }
    }
    return success;
}

BOOL fsv_zip_get_statistics(
    const wchar_t *archive_path,
    ULONGLONG *total_files,
    ULONGLONG *total_bytes
) {
    HANDLE archive = INVALID_HANDLE_VALUE;
    FsvZipInfo info;
    ULONGLONG position;
    ULONGLONG central_end;
    ULONGLONG index;
    ULONGLONG files = 0;
    ULONGLONG bytes = 0;
    BOOL success = FALSE;
    if (total_files == NULL || total_bytes == NULL) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    *total_files = 0;
    *total_bytes = 0;
    archive = CreateFileW(
        archive_path,
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        NULL,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN,
        NULL
    );
    if (archive == INVALID_HANDLE_VALUE || !locate_zip(archive, &info)) {
        if (archive != INVALID_HANDLE_VALUE) {
            CloseHandle(archive);
        }
        return FALSE;
    }
    position = info.central_offset;
    central_end = info.central_offset + info.central_size;
    if (central_end < info.central_offset ||
        info.entry_count > FSV_ZIP_MAX_ENTRIES ||
        (info.entry_count > 0 && info.central_size / FSV_ZIP_CENTRAL_HEADER_SIZE < info.entry_count)) {
        SetLastError(ERROR_BAD_FORMAT);
        goto cleanup;
    }
    for (index = 0; index < info.entry_count; ++index) {
        FsvZipEntry entry;
        ULONGLONG next_position;
        ZeroMemory(&entry, sizeof(entry));
        if (!read_entry(archive, position, central_end, &entry, &next_position)) {
            goto cleanup;
        }
        if (!entry.directory && (!add_u64(&files, 1) || !add_u64(&bytes, entry.uncompressed_size))) {
            free_entry(&entry);
            goto cleanup;
        }
        free_entry(&entry);
        position = next_position;
    }
    if (position != central_end) {
        SetLastError(ERROR_BAD_FORMAT);
        goto cleanup;
    }
    *total_files = files;
    *total_bytes = bytes;
    success = TRUE;

cleanup:
    {
        DWORD error = success ? ERROR_SUCCESS : GetLastError();
        CloseHandle(archive);
        if (!success) {
            SetLastError(error);
        }
    }
    return success;
}
