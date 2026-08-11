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

#define FSDX_ABI_VERSION 7u
#define FSDX_RUNTIME_FLAG_WARP 0x00000001u
#define FSDX_DRAW_FLAG_FLIPPED 0x00000001u
#define FSDX_DRAW_FLAG_HAS_FILL 0x00000002u
#define FSDX_DRAW_FLAG_HAS_STROKE 0x00000004u
#define FSDX_DRAW_FLAG_TEXT_BOLD 0x00000008u
#define FSDX_DRAW_COMMAND_V6_SIZE 104u
#define FSDX_WINDOW_FLAG_TOPMOST 0x00000001u
#define FSDX_WINDOW_FLAG_TOOL 0x00000002u
#define FSDX_WINDOW_FLAG_NO_ACTIVATE 0x00000004u
#define FSDX_WINDOW_FLAG_CLICKTHROUGH 0x00000008u
#define FSDX_WINDOW_STATE_VISIBLE 0x00000001u
#define FSDX_WINDOW_STATE_CLICKTHROUGH 0x00000002u
#define FSDX_WINDOW_STATE_ACTIVE 0x00000004u
#define FSDX_WINDOW_STATE_CAPTURED 0x00000008u
#define FSDX_TRAY_STATE_VISIBLE 0x00000001u
#define FSDX_EVENT_FLAG_AUTO_REPEAT 0x00000001u
#define FSDX_EVENT_FLAG_TEXT_FIRST 0x00000002u
#define FSDX_EVENT_FLAG_TEXT_LAST 0x00000004u
#define FSDX_EVENT_FLAG_CHECKED 0x00000100u
#define FSDX_TRAY_MENU_STATE_GAME_MODE 0x00000001u
#define FSDX_TRAY_MENU_STATE_CLICKTHROUGH 0x00000002u
#define FSDX_TRAY_MENU_STATE_AUTOSTART 0x00000004u
#define FSDX_WINDOW_DESC_V7_SIZE 32u
#define FSDX_WINDOW_STATE_V7_SIZE 56u
#define FSDX_TRAY_STATE_V7_SIZE 24u
#define FSDX_EVENT_V7_SIZE 88u

#define FSDX_TRAY_COMMAND_ANNOUNCEMENT 1u
#define FSDX_TRAY_COMMAND_QUIT 2u
#define FSDX_TRAY_COMMAND_OPEN_CMD 3u
#define FSDX_TRAY_COMMAND_TOGGLE_GAME_MODE 4u
#define FSDX_TRAY_COMMAND_TOGGLE_CLICKTHROUGH 5u
#define FSDX_TRAY_COMMAND_TOGGLE_AUTOSTART 6u
#define FSDX_TRAY_COMMAND_CLEANUP_DESKTOP 7u
#define FSDX_TRAY_COMMAND_CLEANUP_CACHE 8u
#define FSDX_TRAY_COMMAND_CLEANUP_HISTORY 9u
#define FSDX_TRAY_COMMAND_OPEN_AUTHOR_PAGE 10u
#define FSDX_TRAY_COMMAND_OPEN_SETTINGS 11u

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
    FSDX_STATUS_DEVICE_LOST = 9,
} fsdx_status;

typedef enum fsdx_command_type {
    FSDX_COMMAND_SPRITE = 1,
    FSDX_COMMAND_LINE = 2,
    FSDX_COMMAND_RECT = 3,
    FSDX_COMMAND_ELLIPSE = 4,
    FSDX_COMMAND_TEXT = 5,
    FSDX_COMMAND_CLIP_PUSH = 6,
    FSDX_COMMAND_CLIP_POP = 7,
    FSDX_COMMAND_TRANSFORM_PUSH = 8,
    FSDX_COMMAND_TRANSFORM_POP = 9,
} fsdx_command_type;

typedef enum fsdx_event_type {
    FSDX_EVENT_NONE = 0,
    FSDX_EVENT_POINTER_ENTER = 1,
    FSDX_EVENT_POINTER_LEAVE = 2,
    FSDX_EVENT_POINTER_PRESS = 3,
    FSDX_EVENT_POINTER_MOVE = 4,
    FSDX_EVENT_POINTER_RELEASE = 5,
    FSDX_EVENT_WINDOW_MOVED = 6,
    FSDX_EVENT_DPI_CHANGED = 7,
    FSDX_EVENT_CLOSE = 8,
    FSDX_EVENT_KEY_PRESS = 9,
    FSDX_EVENT_KEY_RELEASE = 10,
    FSDX_EVENT_REPAINT = 11,
    FSDX_EVENT_DEVICE_ERROR = 12,
    FSDX_EVENT_DEVICE_RECOVERED = 13,
    FSDX_EVENT_TRAY_ANNOUNCEMENT = 14,
    FSDX_EVENT_TRAY_QUIT = 15,
    FSDX_EVENT_TEXT_INPUT = 16,
    FSDX_EVENT_IME_COMPOSITION = 17,
    FSDX_EVENT_IME_END = 18,
    FSDX_EVENT_TRAY_COMMAND = 19,
} fsdx_event_type;

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

typedef struct fsdx_draw_command {
    uint32_t abi_version;
    uint32_t struct_size;
    uint32_t type;
    uint32_t flags;
    int32_t layer;
    int32_t z;
    int32_t order;
    uint32_t text_length;
    fsdx_handle resource;
    float x0;
    float y0;
    float x1;
    float y1;
    float alpha;
    float stroke_width;
    uint32_t fill_rgba;
    uint32_t stroke_rgba;
    float m11;
    float m12;
    float m21;
    float m22;
    float dx;
    float dy;
    uint32_t payload_offset;
    uint32_t payload_size;
} fsdx_draw_command;

typedef struct fsdx_window_desc {
    uint32_t abi_version;
    uint32_t struct_size;
    int32_t x;
    int32_t y;
    uint32_t width;
    uint32_t height;
    uint32_t flags;
    uint32_t reserved;
} fsdx_window_desc;

typedef struct fsdx_window_state {
    uint32_t abi_version;
    uint32_t struct_size;
    uint32_t flags;
    uint32_t dpi;
    uint64_t native_handle;
    int32_t x;
    int32_t y;
    uint32_t width;
    uint32_t height;
    int32_t screen_x;
    int32_t screen_y;
    uint32_t screen_width;
    uint32_t screen_height;
} fsdx_window_state;

typedef struct fsdx_tray_state {
    uint32_t abi_version;
    uint32_t struct_size;
    uint32_t flags;
    uint32_t reserved;
    uint64_t native_handle;
} fsdx_tray_state;

typedef struct fsdx_event {
    uint32_t abi_version;
    uint32_t struct_size;
    uint32_t type;
    uint32_t flags;
    fsdx_handle window;
    uint64_t timestamp_ms;
    int32_t x;
    int32_t y;
    int32_t screen_x;
    int32_t screen_y;
    int32_t width;
    int32_t height;
    uint32_t dpi;
    uint32_t key;
    uint32_t button;
    uint32_t buttons;
    uint32_t modifiers;
    uint32_t repeat_count;
    uint32_t codepoint;
    uint32_t reserved;
} fsdx_event;

/*
 * Text commands use x0/y0/x1/y1 as the layout rectangle, stroke_width as
 * the font pixel size, fill_rgba as text color, stroke_rgba as alignment,
 * and text_length to split UTF-8 text from the following font family.
 * Transform commands use m11/m12/m21/m22/dx/dy. Other command types leave
 * the payload and transform fields zeroed.
 */

FSDX_API uint32_t fsdx_get_abi_version(void);

FSDX_API fsdx_status fsdx_create_runtime(
    const fsdx_runtime_desc* desc,
    fsdx_handle* runtime_out
);

FSDX_API fsdx_status fsdx_destroy_runtime(fsdx_handle runtime);

FSDX_API fsdx_status fsdx_recover_device(fsdx_handle runtime);

FSDX_API fsdx_status fsdx_get_device_generation(
    fsdx_handle runtime,
    uint64_t* generation_out
);

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
    const fsdx_draw_command* commands,
    uint32_t command_count,
    const uint8_t* payload,
    uint64_t payload_size
);

FSDX_API fsdx_status fsdx_create_window(
    fsdx_handle runtime,
    const fsdx_window_desc* desc,
    fsdx_handle* window_out
);

FSDX_API fsdx_status fsdx_destroy_window(
    fsdx_handle runtime,
    fsdx_handle window
);

FSDX_API fsdx_status fsdx_get_window_state(
    fsdx_handle runtime,
    fsdx_handle window,
    fsdx_window_state* state_out
);

FSDX_API fsdx_status fsdx_show_window(
    fsdx_handle runtime,
    fsdx_handle window,
    uint32_t visible
);

FSDX_API fsdx_status fsdx_set_window_geometry(
    fsdx_handle runtime,
    fsdx_handle window,
    int32_t x,
    int32_t y,
    uint32_t width,
    uint32_t height
);

FSDX_API fsdx_status fsdx_set_window_clickthrough(
    fsdx_handle runtime,
    fsdx_handle window,
    uint32_t enabled
);

FSDX_API fsdx_status fsdx_set_window_capture(
    fsdx_handle runtime,
    fsdx_handle window,
    uint32_t enabled
);

FSDX_API fsdx_status fsdx_activate_window(
    fsdx_handle runtime,
    fsdx_handle window
);

FSDX_API fsdx_status fsdx_set_window_ime_position(
    fsdx_handle runtime,
    fsdx_handle window,
    int32_t x,
    int32_t y
);

FSDX_API fsdx_status fsdx_stack_window(
    fsdx_handle runtime,
    fsdx_handle window,
    int64_t insert_after
);

FSDX_API fsdx_status fsdx_request_window_repaint(
    fsdx_handle runtime,
    fsdx_handle window
);

FSDX_API fsdx_status fsdx_submit_window_frame(
    fsdx_handle runtime,
    fsdx_handle window,
    const fsdx_draw_command* commands,
    uint32_t command_count,
    const uint8_t* payload,
    uint64_t payload_size
);

FSDX_API fsdx_status fsdx_create_tray(
    fsdx_handle runtime,
    const uint8_t* tooltip_utf8,
    uint64_t tooltip_size,
    const uint8_t* icon_path_utf8,
    uint64_t icon_path_size,
    fsdx_handle* tray_out
);

FSDX_API fsdx_status fsdx_destroy_tray(
    fsdx_handle runtime,
    fsdx_handle tray
);

FSDX_API fsdx_status fsdx_get_tray_state(
    fsdx_handle runtime,
    fsdx_handle tray,
    fsdx_tray_state* state_out
);

FSDX_API fsdx_status fsdx_show_tray(
    fsdx_handle runtime,
    fsdx_handle tray,
    uint32_t visible
);

FSDX_API fsdx_status fsdx_set_tray_menu_state(
    fsdx_handle runtime,
    fsdx_handle tray,
    uint32_t flags
);

FSDX_API fsdx_status fsdx_poll_events(
    fsdx_handle runtime,
    fsdx_event* events,
    uint32_t capacity,
    uint32_t* written_out,
    uint32_t* pending_out
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
