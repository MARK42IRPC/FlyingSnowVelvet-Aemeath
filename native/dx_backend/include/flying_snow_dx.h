#pragma once

#include <stddef.h>
#include <stdint.h>

#if defined(_WIN32)
#if defined(FSDX_BUILD_DLL)
#define FSDX_API __declspec(dllexport)
#else
#define FSDX_API __declspec(dllimport)
#endif
#else
#define FSDX_API
#endif

#ifdef __cplusplus
extern "C" {
#endif

#define FSDX_ABI_VERSION 1u
#define FSDX_RUNTIME_FLAG_WARP 0x00000001u
#define FSDX_SPRITE_FLAG_FLIPPED 0x00000001u

typedef uint64_t fsdx_handle;

typedef enum fsdx_status {
    FSDX_STATUS_OK = 0,
    FSDX_STATUS_INVALID_ARGUMENT = 1,
    FSDX_STATUS_ABI_MISMATCH = 2,
    FSDX_STATUS_INVALID_HANDLE = 3,
    FSDX_STATUS_ALLOCATION_FAILED = 4,
    FSDX_STATUS_DEVICE_INIT_FAILED = 5,
    FSDX_STATUS_RENDER_FAILED = 6,
    FSDX_STATUS_BUFFER_TOO_SMALL = 7,
    FSDX_STATUS_UNSUPPORTED = 8,
} fsdx_status;

typedef struct fsdx_runtime_desc {
    uint32_t abi_version;
    uint32_t struct_size;
    uint32_t width;
    uint32_t height;
    uint32_t flags;
} fsdx_runtime_desc;

typedef struct fsdx_resource_desc {
    uint32_t abi_version;
    uint32_t struct_size;
    uint32_t width;
    uint32_t height;
    const uint8_t* rgba_pixels;
    uint64_t rgba_size;
} fsdx_resource_desc;

typedef struct fsdx_sprite_command {
    uint32_t abi_version;
    uint32_t struct_size;
    fsdx_handle resource;
    int32_t x;
    int32_t y;
    int32_t width;
    int32_t height;
    float alpha;
    int32_t layer;
    int32_t z;
    int32_t order;
    uint32_t flags;
} fsdx_sprite_command;

FSDX_API uint32_t fsdx_get_abi_version(void);

FSDX_API fsdx_status fsdx_create_runtime(
    const fsdx_runtime_desc* desc,
    fsdx_handle* runtime_out
);

FSDX_API fsdx_status fsdx_destroy_runtime(fsdx_handle runtime);

FSDX_API fsdx_status fsdx_register_resource(
    fsdx_handle runtime,
    const fsdx_resource_desc* desc,
    fsdx_handle* resource_out
);

FSDX_API fsdx_status fsdx_release_resource(
    fsdx_handle runtime,
    fsdx_handle resource
);

FSDX_API fsdx_status fsdx_submit_frame(
    fsdx_handle runtime,
    const fsdx_sprite_command* commands,
    uint32_t command_count
);

FSDX_API fsdx_status fsdx_readback_rgba(
    fsdx_handle runtime,
    uint8_t* destination,
    uint64_t destination_size,
    uint64_t* written_out
);

FSDX_API const char* fsdx_get_last_error(void);

#ifdef __cplusplus
}
#endif
