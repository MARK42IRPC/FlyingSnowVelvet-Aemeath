#include "flying_snow_dx.h"

#include <windows.h>
#include <windowsx.h>
#include <imm.h>
#include <shellapi.h>

#include <d2d1_1.h>
#include <d3d11.h>
#include <dcomp.h>
#include <dwrite.h>
#include <dwrite_3.h>
#include <dxgi1_2.h>
#include <wrl/client.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <deque>
#include <limits>
#include <memory>
#include <mutex>
#include <new>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

using Microsoft::WRL::ComPtr;

static_assert(
    sizeof(fsdx_draw_command) == FSDX_DRAW_COMMAND_V6_SIZE,
    "fsdx_draw_command ABI size changed"
);
static_assert(sizeof(fsdx_window_desc) == FSDX_WINDOW_DESC_V7_SIZE, "fsdx_window_desc ABI size changed");
static_assert(sizeof(fsdx_window_state) == FSDX_WINDOW_STATE_V7_SIZE, "fsdx_window_state ABI size changed");
static_assert(sizeof(fsdx_tray_state) == FSDX_TRAY_STATE_V7_SIZE, "fsdx_tray_state ABI size changed");
static_assert(sizeof(fsdx_event) == FSDX_EVENT_V7_SIZE, "fsdx_event ABI size changed");

namespace {

thread_local std::string g_last_error;

void clear_error() {
    g_last_error.clear();
}

fsdx_status fail(fsdx_status status, const char* message) {
    g_last_error = message != nullptr ? message : "unknown DirectX backend error";
    return status;
}

fsdx_status fail_hr(fsdx_status status, const char* operation, HRESULT hr) {
    char buffer[128]{};
    std::snprintf(buffer, sizeof(buffer), "%s failed with HRESULT 0x%08lx", operation, static_cast<unsigned long>(hr));
    g_last_error = buffer;
    return status;
}

bool is_device_loss(HRESULT hr) {
    return hr == DXGI_ERROR_DEVICE_REMOVED || hr == DXGI_ERROR_DEVICE_RESET ||
        hr == DXGI_ERROR_DEVICE_HUNG || hr == DXGI_ERROR_DRIVER_INTERNAL_ERROR ||
        hr == D2DERR_RECREATE_TARGET;
}

fsdx_status fail_render_hr(const char* operation, HRESULT hr) {
    return fail_hr(
        is_device_loss(hr) ? FSDX_STATUS_DEVICE_LOST : FSDX_STATUS_RENDER_FAILED,
        operation,
        hr
    );
}

fsdx_status fail_win32(fsdx_status status, const char* operation, DWORD error) {
    char buffer[128]{};
    std::snprintf(buffer, sizeof(buffer), "%s failed with Win32 error %lu", operation, static_cast<unsigned long>(error));
    g_last_error = buffer;
    return status;
}

bool valid_header(uint32_t abi_version, uint32_t struct_size, size_t minimum_size) {
    return abi_version == FSDX_ABI_VERSION && struct_size >= minimum_size;
}

struct Resource {
    ComPtr<ID2D1Bitmap1> bitmap;
    uint32_t width = 0;
    uint32_t height = 0;
    std::vector<uint8_t> premultiplied_pixels;
};

struct Runtime;

struct NativeWindow {
    Runtime* runtime = nullptr;
    fsdx_handle handle = 0;
    HWND hwnd = nullptr;
    DWORD owner_thread_id = 0;
    bool clickthrough = false;
    bool tracking_pointer = false;
    bool close_queued = false;
    wchar_t pending_high_surrogate = 0;
    int32_t ime_x = 16;
    int32_t ime_y = 16;
    uint32_t creation_flags = 0;
    ComPtr<IDXGISwapChain1> swap_chain;
    ComPtr<IDCompositionTarget> composition_target;
    ComPtr<IDCompositionVisual> visual;
};

struct NativeTray {
    Runtime* runtime = nullptr;
    fsdx_handle handle = 0;
    HWND hwnd = nullptr;
    DWORD owner_thread_id = 0;
    HICON icon = nullptr;
    bool owns_icon = false;
    bool desired_visible = false;
    bool icon_added = false;
    uint32_t menu_state_flags = 0;
    NOTIFYICONDATAW icon_data{};
};

struct Runtime {
    std::mutex mutex;
    std::mutex event_mutex;
    uint32_t width = 0;
    uint32_t height = 0;
    uint32_t creation_flags = 0;
    uint64_t device_generation = 1;
    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11DeviceContext> context;
    ComPtr<ID3D11Texture2D> render_texture;
    ComPtr<ID3D11Texture2D> staging_texture;
    ComPtr<ID2D1Factory1> d2d_factory;
    ComPtr<ID2D1Device> d2d_device;
    ComPtr<ID2D1DeviceContext> d2d_context;
    ComPtr<ID2D1Bitmap1> target_bitmap;
    ComPtr<ID2D1SolidColorBrush> solid_brush;
    ComPtr<IDWriteFactory> dwrite_factory;
    ComPtr<IDWriteFontCollection1> font_collection;
    std::vector<std::wstring> font_paths;
    ComPtr<IDCompositionDevice> composition_device;
    std::unordered_map<fsdx_handle, Resource> resources;
    std::unordered_set<fsdx_handle> released_resources;
    fsdx_handle next_resource = 1;
    std::unordered_map<fsdx_handle, std::shared_ptr<NativeWindow>> windows;
    std::unordered_set<fsdx_handle> destroyed_windows;
    fsdx_handle next_window = 1;
    std::unordered_map<fsdx_handle, std::shared_ptr<NativeTray>> trays;
    std::unordered_set<fsdx_handle> destroyed_trays;
    fsdx_handle next_tray = 0x8000000000000000ull;
    std::deque<fsdx_event> events;
};

fsdx_status resize_composition_surface(
    Runtime* runtime,
    NativeWindow* window,
    uint32_t width,
    uint32_t height
);

fsdx_status create_resource_bitmap(
    Runtime* runtime,
    uint32_t width,
    uint32_t height,
    const std::vector<uint8_t>& premultiplied_pixels,
    ComPtr<ID2D1Bitmap1>* bitmap_out
);

fsdx_status rebuild_font_collection(Runtime* runtime) {
    runtime->font_collection.Reset();
    if (runtime->font_paths.empty()) {
        return FSDX_STATUS_OK;
    }
    ComPtr<IDWriteFactory5> factory5;
    HRESULT hr = runtime->dwrite_factory.As(&factory5);
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_UNSUPPORTED, "IDWriteFactory5", hr);
    }
    ComPtr<IDWriteFontSetBuilder1> builder;
    hr = factory5->CreateFontSetBuilder(&builder);
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_RENDER_FAILED, "CreateFontSetBuilder", hr);
    }
    for (const auto& path : runtime->font_paths) {
        ComPtr<IDWriteFontFile> file;
        hr = runtime->dwrite_factory->CreateFontFileReference(path.c_str(), nullptr, &file);
        if (FAILED(hr)) {
            return fail_hr(FSDX_STATUS_RENDER_FAILED, "CreateFontFileReference", hr);
        }
        hr = builder->AddFontFile(file.Get());
        if (FAILED(hr)) {
            return fail_hr(FSDX_STATUS_RENDER_FAILED, "IDWriteFontSetBuilder1::AddFontFile", hr);
        }
    }
    ComPtr<IDWriteFontSet> font_set;
    hr = builder->CreateFontSet(&font_set);
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_RENDER_FAILED, "IDWriteFontSetBuilder::CreateFontSet", hr);
    }
    hr = factory5->CreateFontCollectionFromFontSet(font_set.Get(), &runtime->font_collection);
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_RENDER_FAILED, "CreateFontCollectionFromFontSet", hr);
    }
    return FSDX_STATUS_OK;
}

constexpr size_t maximum_event_queue_size = 4096;
constexpr wchar_t window_class_name[] = L"FlyingSnowDxWindowV7";
constexpr wchar_t tray_window_class_name[] = L"FlyingSnowDxTrayWindowV7";
constexpr UINT tray_callback_message = WM_APP + 0x41u;
std::once_flag g_window_class_once;
ATOM g_window_class_atom = 0;
DWORD g_window_class_error = ERROR_SUCCESS;
std::once_flag g_tray_window_class_once;
ATOM g_tray_window_class_atom = 0;
DWORD g_tray_window_class_error = ERROR_SUCCESS;
UINT g_taskbar_created_message = 0;

std::mutex g_runtimes_mutex;
std::unordered_map<fsdx_handle, std::shared_ptr<Runtime>> g_runtimes;
std::unordered_set<fsdx_handle> g_destroyed_runtimes;
fsdx_handle g_next_runtime = 1;

std::shared_ptr<Runtime> find_runtime(fsdx_handle handle) {
    std::lock_guard<std::mutex> lock(g_runtimes_mutex);
    auto it = g_runtimes.find(handle);
    return it == g_runtimes.end() ? nullptr : it->second;
}

std::shared_ptr<NativeWindow> find_window(const std::shared_ptr<Runtime>& runtime, fsdx_handle handle) {
    std::lock_guard<std::mutex> lock(runtime->mutex);
    const auto it = runtime->windows.find(handle);
    return it == runtime->windows.end() ? nullptr : it->second;
}

std::shared_ptr<NativeTray> find_tray(const std::shared_ptr<Runtime>& runtime, fsdx_handle handle) {
    std::lock_guard<std::mutex> lock(runtime->mutex);
    const auto it = runtime->trays.find(handle);
    return it == runtime->trays.end() ? nullptr : it->second;
}

bool is_coalescible_event(uint32_t type) {
    return type == FSDX_EVENT_POINTER_MOVE || type == FSDX_EVENT_WINDOW_MOVED ||
        type == FSDX_EVENT_REPAINT;
}

void queue_event(Runtime* runtime, const fsdx_event& event) {
    if (runtime == nullptr || event.window == 0) {
        return;
    }
    std::lock_guard<std::mutex> lock(runtime->event_mutex);
    if (is_coalescible_event(event.type)) {
        const auto existing = std::find_if(
            runtime->events.rbegin(),
            runtime->events.rend(),
            [&event](const fsdx_event& queued) {
                return queued.type == event.type && queued.window == event.window;
            }
        );
        if (existing != runtime->events.rend()) {
            *existing = event;
            return;
        }
    }
    if (runtime->events.size() >= maximum_event_queue_size) {
        const auto discard = std::find_if(
            runtime->events.begin(),
            runtime->events.end(),
            [](const fsdx_event& queued) { return is_coalescible_event(queued.type); }
        );
        if (discard != runtime->events.end()) {
            runtime->events.erase(discard);
        }
        else {
            return;
        }
    }
    runtime->events.push_back(event);
}

fsdx_event make_event(const NativeWindow* window, uint32_t type) {
    fsdx_event event{};
    event.abi_version = FSDX_ABI_VERSION;
    event.struct_size = sizeof(fsdx_event);
    event.type = type;
    event.window = window != nullptr ? window->handle : 0;
    event.timestamp_ms = GetTickCount64();
    return event;
}

fsdx_event make_event(const NativeTray* tray, uint32_t type) {
    fsdx_event event{};
    event.abi_version = FSDX_ABI_VERSION;
    event.struct_size = sizeof(fsdx_event);
    event.type = type;
    event.window = tray != nullptr ? tray->handle : 0;
    event.timestamp_ms = GetTickCount64();
    return event;
}

uint32_t current_mouse_buttons() {
    uint32_t buttons = 0;
    if ((GetKeyState(VK_LBUTTON) & 0x8000) != 0) buttons |= 1u;
    if ((GetKeyState(VK_RBUTTON) & 0x8000) != 0) buttons |= 2u;
    if ((GetKeyState(VK_MBUTTON) & 0x8000) != 0) buttons |= 4u;
    return buttons;
}

uint32_t current_key_modifiers() {
    uint32_t modifiers = 0;
    if ((GetKeyState(VK_SHIFT) & 0x8000) != 0) modifiers |= 0x02000000u;
    if ((GetKeyState(VK_CONTROL) & 0x8000) != 0) modifiers |= 0x04000000u;
    if ((GetKeyState(VK_MENU) & 0x8000) != 0) modifiers |= 0x08000000u;
    if ((GetKeyState(VK_LWIN) & 0x8000) != 0 || (GetKeyState(VK_RWIN) & 0x8000) != 0) {
        modifiers |= 0x10000000u;
    }
    return modifiers;
}

uint32_t window_dpi(HWND hwnd) {
    using GetDpiForWindowFn = UINT(WINAPI*)(HWND);
    static const auto get_dpi_for_window = reinterpret_cast<GetDpiForWindowFn>(
        GetProcAddress(GetModuleHandleW(L"user32.dll"), "GetDpiForWindow")
    );
    return get_dpi_for_window != nullptr && hwnd != nullptr
        ? std::max(1u, get_dpi_for_window(hwnd))
        : 96u;
}

void fill_pointer_event(fsdx_event* event, HWND hwnd, LPARAM lparam) {
    event->x = GET_X_LPARAM(lparam);
    event->y = GET_Y_LPARAM(lparam);
    POINT screen_point{event->x, event->y};
    ClientToScreen(hwnd, &screen_point);
    event->screen_x = screen_point.x;
    event->screen_y = screen_point.y;
    event->buttons = current_mouse_buttons();
    event->modifiers = current_key_modifiers();
}

void fill_pointer_message_event(fsdx_event* event, HWND hwnd, WPARAM wparam, LPARAM lparam) {
    const UINT32 pointer_id = LOWORD(wparam);
    POINT screen_point{GET_X_LPARAM(lparam), GET_Y_LPARAM(lparam)};
    POINTER_INFO pointer_info{};
    if (GetPointerInfo(pointer_id, &pointer_info)) {
        screen_point = pointer_info.ptPixelLocation;
        event->buttons = (pointer_info.pointerFlags & POINTER_FLAG_INCONTACT) != 0 ? 1u : 0u;
        if ((pointer_info.pointerFlags & POINTER_FLAG_FIRSTBUTTON) != 0) {
            event->buttons |= 1u;
        }
    }
    event->screen_x = screen_point.x;
    event->screen_y = screen_point.y;
    POINT local_point = screen_point;
    ScreenToClient(hwnd, &local_point);
    event->x = local_point.x;
    event->y = local_point.y;
    event->modifiers = current_key_modifiers();
    event->reserved = pointer_id;
}

bool is_promoted_pointer_mouse_message() {
    constexpr ULONG_PTR pointer_mouse_signature = 0xff515700u;
    return (GetMessageExtraInfo() & 0xffffff00u) == pointer_mouse_signature;
}

bool utf16_codepoint(wchar_t value, wchar_t* pending_high, uint32_t* codepoint_out) {
    if (pending_high == nullptr || codepoint_out == nullptr) {
        return false;
    }
    const uint32_t code_unit = static_cast<uint32_t>(value);
    if (code_unit >= 0xd800u && code_unit <= 0xdbffu) {
        *pending_high = value;
        return false;
    }
    if (code_unit >= 0xdc00u && code_unit <= 0xdfffu) {
        if (*pending_high == 0) {
            return false;
        }
        const uint32_t high = static_cast<uint32_t>(*pending_high);
        *pending_high = 0;
        *codepoint_out = 0x10000u + ((high - 0xd800u) << 10u) + (code_unit - 0xdc00u);
        return true;
    }
    *pending_high = 0;
    if (code_unit < 0x20u || code_unit > 0x10ffffu) {
        return false;
    }
    *codepoint_out = code_unit;
    return true;
}

void queue_text_codepoint(NativeWindow* window, uint32_t codepoint, uint32_t repeat_count = 1) {
    if (window == nullptr || codepoint == 0) {
        return;
    }
    fsdx_event event = make_event(window, FSDX_EVENT_TEXT_INPUT);
    event.codepoint = codepoint;
    event.repeat_count = std::max(1u, std::min(4096u, repeat_count));
    queue_event(window->runtime, event);
}

void queue_text_string(NativeWindow* window, const std::wstring& value) {
    if (window == nullptr) {
        return;
    }
    wchar_t pending_high = 0;
    std::vector<uint32_t> codepoints;
    codepoints.reserve(value.size());
    for (const wchar_t value_unit : value) {
        uint32_t codepoint = 0;
        if (utf16_codepoint(value_unit, &pending_high, &codepoint)) {
            codepoints.push_back(codepoint);
        }
    }
    for (const uint32_t codepoint : codepoints) {
        queue_text_codepoint(window, codepoint);
    }
}

void queue_ime_composition(NativeWindow* window, const std::wstring& value) {
    if (window == nullptr) {
        return;
    }
    fsdx_event event = make_event(window, FSDX_EVENT_IME_COMPOSITION);
    event.flags = FSDX_EVENT_FLAG_TEXT_FIRST;
    wchar_t pending_high = 0;
    bool emitted = false;
    for (const wchar_t value_unit : value) {
        uint32_t codepoint = 0;
        if (!utf16_codepoint(value_unit, &pending_high, &codepoint)) {
            continue;
        }
        if (emitted) {
            event = make_event(window, FSDX_EVENT_IME_COMPOSITION);
            event.flags = 0;
        }
        event.codepoint = codepoint;
        queue_event(window->runtime, event);
        emitted = true;
    }
    if (!emitted) {
        event.codepoint = 0;
        event.flags = FSDX_EVENT_FLAG_TEXT_FIRST | FSDX_EVENT_FLAG_TEXT_LAST;
        queue_event(window->runtime, event);
    }
    if (emitted) {
        event.flags = FSDX_EVENT_FLAG_TEXT_LAST;
        event.codepoint = 0;
        queue_event(window->runtime, event);
    }
}

std::wstring ime_composition_string(HWND hwnd, DWORD index) {
    if (hwnd == nullptr) {
        return {};
    }
    HIMC context = ImmGetContext(hwnd);
    if (context == nullptr) {
        return {};
    }
    const LONG byte_count = ImmGetCompositionStringW(context, index, nullptr, 0);
    std::wstring value;
    if (byte_count > 0 && (byte_count % static_cast<LONG>(sizeof(wchar_t))) == 0) {
        value.resize(static_cast<size_t>(byte_count) / sizeof(wchar_t));
        ImmGetCompositionStringW(
            context,
            index,
            value.data(),
            byte_count
        );
    }
    ImmReleaseContext(hwnd, context);
    return value;
}

void apply_ime_position(NativeWindow* window) {
    if (window == nullptr || window->hwnd == nullptr) {
        return;
    }
    HIMC context = ImmGetContext(window->hwnd);
    if (context == nullptr) {
        return;
    }
    COMPOSITIONFORM composition{};
    composition.dwStyle = CFS_POINT;
    composition.ptCurrentPos.x = window->ime_x;
    composition.ptCurrentPos.y = window->ime_y;
    ImmSetCompositionWindow(context, &composition);
    CANDIDATEFORM candidate{};
    candidate.dwIndex = 0;
    candidate.dwStyle = CFS_CANDIDATEPOS;
    candidate.ptCurrentPos = composition.ptCurrentPos;
    ImmSetCandidateWindow(context, &candidate);
    ImmReleaseContext(window->hwnd, context);
}

bool decode_utf8(const uint8_t* bytes, uint64_t size, std::wstring* value_out) {
    if (value_out == nullptr || (size > 0 && bytes == nullptr) ||
        size > static_cast<uint64_t>(std::numeric_limits<int>::max())) {
        return false;
    }
    if (size == 0) {
        value_out->clear();
        return true;
    }
    const int source_size = static_cast<int>(size);
    const int required = MultiByteToWideChar(
        CP_UTF8,
        MB_ERR_INVALID_CHARS,
        reinterpret_cast<const char*>(bytes),
        source_size,
        nullptr,
        0
    );
    if (required <= 0) {
        return false;
    }
    std::wstring value(static_cast<size_t>(required), L'\0');
    if (MultiByteToWideChar(
            CP_UTF8,
            MB_ERR_INVALID_CHARS,
            reinterpret_cast<const char*>(bytes),
            source_size,
            value.data(),
            required
        ) != required || value.find(L'\0') != std::wstring::npos) {
        return false;
    }
    *value_out = std::move(value);
    return true;
}

bool add_tray_icon(NativeTray* tray) {
    if (tray == nullptr || tray->hwnd == nullptr) {
        return false;
    }
    if (tray->icon_added) {
        return true;
    }
    if (!Shell_NotifyIconW(NIM_ADD, &tray->icon_data)) {
        return false;
    }
    tray->icon_added = true;
    return true;
}

void remove_tray_icon(NativeTray* tray) {
    if (tray == nullptr || !tray->icon_added) {
        return;
    }
    Shell_NotifyIconW(NIM_DELETE, &tray->icon_data);
    tray->icon_added = false;
}

void queue_tray_command(NativeTray* tray, uint32_t command) {
    if (tray == nullptr) {
        return;
    }
    fsdx_event event = make_event(tray, FSDX_EVENT_TRAY_COMMAND);
    event.key = command;
    uint32_t state_flag = 0;
    if (command == FSDX_TRAY_COMMAND_TOGGLE_GAME_MODE) {
        state_flag = FSDX_TRAY_MENU_STATE_GAME_MODE;
    }
    else if (command == FSDX_TRAY_COMMAND_TOGGLE_CLICKTHROUGH) {
        state_flag = FSDX_TRAY_MENU_STATE_CLICKTHROUGH;
    }
    else if (command == FSDX_TRAY_COMMAND_TOGGLE_AUTOSTART) {
        state_flag = FSDX_TRAY_MENU_STATE_AUTOSTART;
    }
    else if (command < FSDX_TRAY_COMMAND_ANNOUNCEMENT ||
        command > FSDX_TRAY_COMMAND_OPEN_SETTINGS) {
        return;
    }
    if (state_flag != 0) {
        tray->menu_state_flags ^= state_flag;
        if ((tray->menu_state_flags & state_flag) != 0) {
            event.flags |= FSDX_EVENT_FLAG_CHECKED;
        }
    }
    queue_event(tray->runtime, event);
}

UINT tray_menu_flags(const NativeTray* tray, uint32_t state_flag) {
    const bool checked = tray != nullptr && (tray->menu_state_flags & state_flag) != 0;
    return MF_STRING | (checked ? MF_CHECKED : MF_UNCHECKED);
}

void show_tray_menu(NativeTray* tray) {
    if (tray == nullptr || tray->hwnd == nullptr || !tray->icon_added) {
        return;
    }
    HMENU menu = CreatePopupMenu();
    if (menu == nullptr) {
        return;
    }
    AppendMenuW(menu, MF_STRING, FSDX_TRAY_COMMAND_ANNOUNCEMENT, L"\u684c\u5ba0\u516c\u544a");
    AppendMenuW(menu, MF_STRING, FSDX_TRAY_COMMAND_OPEN_SETTINGS, L"\u63a7\u5236\u9762\u677f");
    AppendMenuW(menu, MF_STRING, FSDX_TRAY_COMMAND_OPEN_CMD, L"CMD\u7ec8\u7aef");
    AppendMenuW(
        menu,
        tray_menu_flags(tray, FSDX_TRAY_MENU_STATE_GAME_MODE),
        FSDX_TRAY_COMMAND_TOGGLE_GAME_MODE,
        L"\u6e38\u620f\u6a21\u5f0f"
    );
    AppendMenuW(
        menu,
        tray_menu_flags(tray, FSDX_TRAY_MENU_STATE_CLICKTHROUGH),
        FSDX_TRAY_COMMAND_TOGGLE_CLICKTHROUGH,
        L"\u9f20\u6807\u7a7f\u900f"
    );
    AppendMenuW(menu, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(
        menu,
        tray_menu_flags(tray, FSDX_TRAY_MENU_STATE_AUTOSTART),
        FSDX_TRAY_COMMAND_TOGGLE_AUTOSTART,
        L"\u5f00\u673a\u542f\u52a8"
    );
    AppendMenuW(menu, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(menu, MF_STRING, FSDX_TRAY_COMMAND_CLEANUP_DESKTOP, L"\u6e05\u7406\u684c\u9762");
    AppendMenuW(menu, MF_STRING, FSDX_TRAY_COMMAND_CLEANUP_CACHE, L"\u6e05\u7406\u7f13\u5b58");
    AppendMenuW(menu, MF_STRING, FSDX_TRAY_COMMAND_CLEANUP_HISTORY, L"\u6e05\u7406\u5386\u53f2");
    AppendMenuW(menu, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(menu, MF_STRING, FSDX_TRAY_COMMAND_OPEN_AUTHOR_PAGE, L"\u5173\u6ce8\u4f5c\u8005");
    AppendMenuW(menu, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(menu, MF_STRING, FSDX_TRAY_COMMAND_QUIT, L"\u9000\u51fa\u7a0b\u5e8f");

    POINT cursor{};
    GetCursorPos(&cursor);
    SetForegroundWindow(tray->hwnd);
    const UINT command = TrackPopupMenu(
        menu,
        TPM_RETURNCMD | TPM_NONOTIFY | TPM_RIGHTBUTTON,
        cursor.x,
        cursor.y,
        0,
        tray->hwnd,
        nullptr
    );
    DestroyMenu(menu);
    PostMessageW(tray->hwnd, WM_NULL, 0, 0);
    queue_tray_command(tray, command);
}

LRESULT CALLBACK fsdx_tray_window_proc(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam) {
    auto* tray = reinterpret_cast<NativeTray*>(GetWindowLongPtrW(hwnd, GWLP_USERDATA));
    if (message == WM_NCCREATE) {
        const auto* create = reinterpret_cast<const CREATESTRUCTW*>(lparam);
        tray = static_cast<NativeTray*>(create->lpCreateParams);
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(tray));
        if (tray != nullptr) {
            tray->hwnd = hwnd;
        }
    }
    if (tray == nullptr) {
        return DefWindowProcW(hwnd, message, wparam, lparam);
    }
    if (g_taskbar_created_message != 0 && message == g_taskbar_created_message) {
        tray->icon_added = false;
        if (tray->desired_visible) {
            add_tray_icon(tray);
        }
        return 0;
    }

    switch (message) {
    case tray_callback_message:
        if (static_cast<UINT>(lparam) == WM_RBUTTONUP ||
            static_cast<UINT>(lparam) == WM_CONTEXTMENU) {
            show_tray_menu(tray);
        }
        else if (static_cast<UINT>(lparam) == WM_LBUTTONDBLCLK) {
            queue_tray_command(tray, FSDX_TRAY_COMMAND_OPEN_SETTINGS);
        }
        return 0;
    case WM_COMMAND:
        queue_tray_command(tray, LOWORD(wparam));
        return 0;
    case WM_NCDESTROY:
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0);
        tray->hwnd = nullptr;
        break;
    default:
        break;
    }
    return DefWindowProcW(hwnd, message, wparam, lparam);
}

bool ensure_tray_window_class() {
    std::call_once(g_tray_window_class_once, []() {
        g_taskbar_created_message = RegisterWindowMessageW(L"TaskbarCreated");
        WNDCLASSEXW window_class{};
        window_class.cbSize = sizeof(window_class);
        window_class.lpfnWndProc = fsdx_tray_window_proc;
        window_class.hInstance = GetModuleHandleW(nullptr);
        window_class.lpszClassName = tray_window_class_name;
        g_tray_window_class_atom = RegisterClassExW(&window_class);
        if (g_tray_window_class_atom == 0) {
            g_tray_window_class_error = GetLastError();
        }
    });
    return g_tray_window_class_atom != 0;
}

LRESULT CALLBACK fsdx_window_proc(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam) {
    auto* window = reinterpret_cast<NativeWindow*>(GetWindowLongPtrW(hwnd, GWLP_USERDATA));
    if (message == WM_NCCREATE) {
        const auto* create = reinterpret_cast<const CREATESTRUCTW*>(lparam);
        window = static_cast<NativeWindow*>(create->lpCreateParams);
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(window));
        if (window != nullptr) {
            window->hwnd = hwnd;
        }
    }
    if (window == nullptr) {
        return DefWindowProcW(hwnd, message, wparam, lparam);
    }

    switch (message) {
    case WM_NCHITTEST:
        if (window->clickthrough) {
            return HTTRANSPARENT;
        }
        break;
    case WM_ERASEBKGND:
        return 1;
    case WM_PAINT: {
        PAINTSTRUCT paint{};
        BeginPaint(hwnd, &paint);
        EndPaint(hwnd, &paint);
        queue_event(window->runtime, make_event(window, FSDX_EVENT_REPAINT));
        return 0;
    }
    case WM_MOUSEMOVE: {
        if (is_promoted_pointer_mouse_message()) {
            return 0;
        }
        if (!window->tracking_pointer) {
            TRACKMOUSEEVENT tracking{sizeof(TRACKMOUSEEVENT), TME_LEAVE, hwnd, 0};
            if (TrackMouseEvent(&tracking)) {
                window->tracking_pointer = true;
            }
            fsdx_event enter = make_event(window, FSDX_EVENT_POINTER_ENTER);
            fill_pointer_event(&enter, hwnd, lparam);
            queue_event(window->runtime, enter);
        }
        fsdx_event event = make_event(window, FSDX_EVENT_POINTER_MOVE);
        fill_pointer_event(&event, hwnd, lparam);
        queue_event(window->runtime, event);
        return 0;
    }
    case WM_MOUSELEAVE:
        window->tracking_pointer = false;
        queue_event(window->runtime, make_event(window, FSDX_EVENT_POINTER_LEAVE));
        return 0;
    case WM_LBUTTONDOWN:
    case WM_RBUTTONDOWN:
    case WM_MBUTTONDOWN: {
        if (is_promoted_pointer_mouse_message()) {
            return 0;
        }
        SetCapture(hwnd);
        fsdx_event event = make_event(window, FSDX_EVENT_POINTER_PRESS);
        fill_pointer_event(&event, hwnd, lparam);
        event.button = message == WM_LBUTTONDOWN ? 1u : (message == WM_RBUTTONDOWN ? 2u : 4u);
        event.buttons |= event.button;
        queue_event(window->runtime, event);
        return 0;
    }
    case WM_LBUTTONUP:
    case WM_RBUTTONUP:
    case WM_MBUTTONUP: {
        if (is_promoted_pointer_mouse_message()) {
            return 0;
        }
        fsdx_event event = make_event(window, FSDX_EVENT_POINTER_RELEASE);
        fill_pointer_event(&event, hwnd, lparam);
        event.button = message == WM_LBUTTONUP ? 1u : (message == WM_RBUTTONUP ? 2u : 4u);
        event.buttons &= ~event.button;
        queue_event(window->runtime, event);
        if (event.buttons == 0 && GetCapture() == hwnd) {
            ReleaseCapture();
        }
        return 0;
    }
    case WM_POINTERENTER: {
        fsdx_event event = make_event(window, FSDX_EVENT_POINTER_ENTER);
        fill_pointer_message_event(&event, hwnd, wparam, lparam);
        queue_event(window->runtime, event);
        return 0;
    }
    case WM_POINTERLEAVE:
        queue_event(window->runtime, make_event(window, FSDX_EVENT_POINTER_LEAVE));
        return 0;
    case WM_POINTERDOWN: {
        fsdx_event event = make_event(window, FSDX_EVENT_POINTER_PRESS);
        fill_pointer_message_event(&event, hwnd, wparam, lparam);
        event.button = 1u;
        event.buttons |= event.button;
        queue_event(window->runtime, event);
        return 0;
    }
    case WM_POINTERUPDATE: {
        fsdx_event event = make_event(window, FSDX_EVENT_POINTER_MOVE);
        fill_pointer_message_event(&event, hwnd, wparam, lparam);
        queue_event(window->runtime, event);
        return 0;
    }
    case WM_POINTERUP: {
        fsdx_event event = make_event(window, FSDX_EVENT_POINTER_RELEASE);
        fill_pointer_message_event(&event, hwnd, wparam, lparam);
        event.button = 1u;
        event.buttons &= ~event.button;
        queue_event(window->runtime, event);
        return 0;
    }
    case WM_KEYDOWN:
    case WM_SYSKEYDOWN:
    case WM_KEYUP:
    case WM_SYSKEYUP: {
        const bool pressed = message == WM_KEYDOWN || message == WM_SYSKEYDOWN;
        fsdx_event event = make_event(window, pressed ? FSDX_EVENT_KEY_PRESS : FSDX_EVENT_KEY_RELEASE);
        event.key = static_cast<uint32_t>(wparam);
        event.modifiers = current_key_modifiers();
        event.repeat_count = std::max(1u, static_cast<uint32_t>(lparam & 0xffffu));
        if ((lparam & (1ll << 30)) != 0) {
            event.flags |= FSDX_EVENT_FLAG_AUTO_REPEAT;
        }
        queue_event(window->runtime, event);
        return 0;
    }
    case WM_CHAR:
    case WM_SYSCHAR: {
        uint32_t codepoint = 0;
        if (utf16_codepoint(static_cast<wchar_t>(wparam), &window->pending_high_surrogate, &codepoint)) {
            const uint32_t repeat_count = std::max(1u, static_cast<uint32_t>(lparam & 0xffffu));
            queue_text_codepoint(window, codepoint, repeat_count);
        }
        return 0;
    }
    case WM_UNICHAR:
        if (wparam == UNICODE_NOCHAR) {
            return TRUE;
        }
        if (wparam >= 0x20u && wparam <= 0x10ffffu) {
            const uint32_t repeat_count = std::max(1u, static_cast<uint32_t>(lparam & 0xffffu));
            queue_text_codepoint(window, static_cast<uint32_t>(wparam), repeat_count);
        }
        return 0;
    case WM_IME_STARTCOMPOSITION:
        apply_ime_position(window);
        queue_ime_composition(window, {});
        return 0;
    case WM_IME_COMPOSITION:
        if ((lparam & GCS_RESULTSTR) != 0) {
            queue_text_string(window, ime_composition_string(hwnd, GCS_RESULTSTR));
        }
        if ((lparam & GCS_COMPSTR) != 0) {
            queue_ime_composition(window, ime_composition_string(hwnd, GCS_COMPSTR));
        }
        return 0;
    case WM_IME_ENDCOMPOSITION:
        queue_event(window->runtime, make_event(window, FSDX_EVENT_IME_END));
        return 0;
    case WM_MOVE: {
        RECT rectangle{};
        if (GetWindowRect(hwnd, &rectangle)) {
            fsdx_event event = make_event(window, FSDX_EVENT_WINDOW_MOVED);
            event.x = rectangle.left;
            event.y = rectangle.top;
            event.width = rectangle.right - rectangle.left;
            event.height = rectangle.bottom - rectangle.top;
            queue_event(window->runtime, event);
        }
        return 0;
    }
    case WM_DPICHANGED: {
        const auto* suggested = reinterpret_cast<const RECT*>(lparam);
        if (suggested != nullptr) {
            SetWindowPos(
                hwnd,
                nullptr,
                suggested->left,
                suggested->top,
                suggested->right - suggested->left,
                suggested->bottom - suggested->top,
                SWP_NOACTIVATE | SWP_NOZORDER
            );
            const uint32_t width = static_cast<uint32_t>(
                std::max(1l, suggested->right - suggested->left)
            );
            const uint32_t height = static_cast<uint32_t>(
                std::max(1l, suggested->bottom - suggested->top)
            );
            std::lock_guard<std::mutex> lock(window->runtime->mutex);
            const fsdx_status resize_status = resize_composition_surface(
                window->runtime,
                window,
                width,
                height
            );
            if (resize_status != FSDX_STATUS_OK) {
                fsdx_event device_event = make_event(window, FSDX_EVENT_DEVICE_ERROR);
                device_event.key = static_cast<uint32_t>(
                    window->runtime->device->GetDeviceRemovedReason()
                );
                queue_event(window->runtime, device_event);
            }
        }
        fsdx_event event = make_event(window, FSDX_EVENT_DPI_CHANGED);
        event.dpi = LOWORD(wparam);
        if (suggested != nullptr) {
            event.x = suggested->left;
            event.y = suggested->top;
            event.width = suggested->right - suggested->left;
            event.height = suggested->bottom - suggested->top;
        }
        queue_event(window->runtime, event);
        return 0;
    }
    case WM_CLOSE:
        if (!window->close_queued) {
            window->close_queued = true;
            queue_event(window->runtime, make_event(window, FSDX_EVENT_CLOSE));
        }
        return 0;
    case WM_NCDESTROY:
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0);
        window->hwnd = nullptr;
        break;
    default:
        break;
    }
    return DefWindowProcW(hwnd, message, wparam, lparam);
}

bool ensure_window_class() {
    std::call_once(g_window_class_once, []() {
        WNDCLASSEXW window_class{};
        window_class.cbSize = sizeof(window_class);
        window_class.style = CS_HREDRAW | CS_VREDRAW;
        window_class.lpfnWndProc = fsdx_window_proc;
        window_class.hInstance = GetModuleHandleW(nullptr);
        window_class.hCursor = LoadCursorW(nullptr, MAKEINTRESOURCEW(32512));
        window_class.lpszClassName = window_class_name;
        g_window_class_atom = RegisterClassExW(&window_class);
        if (g_window_class_atom == 0) {
            g_window_class_error = GetLastError();
        }
    });
    return g_window_class_atom != 0;
}

bool checked_rgba_size(uint32_t width, uint32_t height, uint64_t* size_out) {
    constexpr uint64_t bytes_per_pixel = 4;
    const uint64_t pixels = static_cast<uint64_t>(width) * static_cast<uint64_t>(height);
    if (width == 0 || height == 0 || pixels > std::numeric_limits<uint64_t>::max() / bytes_per_pixel) {
        return false;
    }
    *size_out = pixels * bytes_per_pixel;
    return true;
}

bool finite_geometry(const fsdx_draw_command* command) {
    return std::isfinite(command->x0) && std::isfinite(command->y0) &&
        std::isfinite(command->x1) && std::isfinite(command->y1);
}

bool finite_transform(const fsdx_draw_command* command) {
    return std::isfinite(command->m11) && std::isfinite(command->m12) &&
        std::isfinite(command->m21) && std::isfinite(command->m22) &&
        std::isfinite(command->dx) && std::isfinite(command->dy);
}

bool valid_alpha(float alpha) {
    return std::isfinite(alpha) && alpha >= 0.0f && alpha <= 1.0f;
}

bool valid_payload_range(const fsdx_draw_command* command, uint64_t payload_size) {
    const uint64_t offset = command->payload_offset;
    const uint64_t size = command->payload_size;
    return offset <= payload_size && size <= payload_size - offset;
}

bool is_state_command(uint32_t type) {
    return type == FSDX_COMMAND_CLIP_PUSH || type == FSDX_COMMAND_CLIP_POP ||
        type == FSDX_COMMAND_TRANSFORM_PUSH || type == FSDX_COMMAND_TRANSFORM_POP;
}

bool is_push_command(uint32_t type) {
    return type == FSDX_COMMAND_CLIP_PUSH || type == FSDX_COMMAND_TRANSFORM_PUSH;
}

uint32_t matching_push_type(uint32_t pop_type) {
    return pop_type == FSDX_COMMAND_CLIP_POP
        ? FSDX_COMMAND_CLIP_PUSH
        : FSDX_COMMAND_TRANSFORM_PUSH;
}

D2D1_COLOR_F unpack_color(uint32_t rgba, float command_alpha) {
    constexpr float channel_scale = 1.0f / 255.0f;
    const float red = static_cast<float>(rgba & 0xffu) * channel_scale;
    const float green = static_cast<float>((rgba >> 8u) & 0xffu) * channel_scale;
    const float blue = static_cast<float>((rgba >> 16u) & 0xffu) * channel_scale;
    const float alpha = static_cast<float>((rgba >> 24u) & 0xffu) * channel_scale * command_alpha;
    return D2D1::ColorF(red, green, blue, alpha);
}

fsdx_status create_offscreen_targets(
    Runtime* runtime,
    uint32_t width,
    uint32_t height
) {
    D3D11_TEXTURE2D_DESC texture_desc{};
    texture_desc.Width = width;
    texture_desc.Height = height;
    texture_desc.MipLevels = 1;
    texture_desc.ArraySize = 1;
    texture_desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    texture_desc.SampleDesc.Count = 1;
    texture_desc.Usage = D3D11_USAGE_DEFAULT;
    texture_desc.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;

    ComPtr<ID3D11Texture2D> render_texture;
    HRESULT hr = runtime->device->CreateTexture2D(&texture_desc, nullptr, &render_texture);
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_DEVICE_INIT_FAILED, "CreateTexture2D(render)", hr);
    }

    D3D11_TEXTURE2D_DESC staging_desc = texture_desc;
    staging_desc.Usage = D3D11_USAGE_STAGING;
    staging_desc.BindFlags = 0;
    staging_desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    ComPtr<ID3D11Texture2D> staging_texture;
    hr = runtime->device->CreateTexture2D(&staging_desc, nullptr, &staging_texture);
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_DEVICE_INIT_FAILED, "CreateTexture2D(staging)", hr);
    }

    ComPtr<IDXGISurface> surface;
    hr = render_texture.As(&surface);
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_DEVICE_INIT_FAILED, "QueryInterface(IDXGISurface)", hr);
    }
    const auto bitmap_properties = D2D1::BitmapProperties1(
        D2D1_BITMAP_OPTIONS_TARGET | D2D1_BITMAP_OPTIONS_CANNOT_DRAW,
        D2D1::PixelFormat(DXGI_FORMAT_B8G8R8A8_UNORM, D2D1_ALPHA_MODE_PREMULTIPLIED)
    );
    ComPtr<ID2D1Bitmap1> target_bitmap;
    hr = runtime->d2d_context->CreateBitmapFromDxgiSurface(
        surface.Get(),
        bitmap_properties,
        &target_bitmap
    );
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_DEVICE_INIT_FAILED, "CreateBitmapFromDxgiSurface", hr);
    }

    runtime->d2d_context->SetTarget(nullptr);
    runtime->render_texture = std::move(render_texture);
    runtime->staging_texture = std::move(staging_texture);
    runtime->target_bitmap = std::move(target_bitmap);
    runtime->width = width;
    runtime->height = height;
    return FSDX_STATUS_OK;
}

fsdx_status validate_draw_command(const fsdx_draw_command* command, uint64_t payload_size) {
    if (command->abi_version != FSDX_ABI_VERSION || command->struct_size != sizeof(fsdx_draw_command)) {
        return fail(FSDX_STATUS_ABI_MISMATCH, "draw command ABI version or size mismatch");
    }
    if (!finite_geometry(command) || !std::isfinite(command->stroke_width) || command->stroke_width < 0.0f) {
        return fail(FSDX_STATUS_INVALID_ARGUMENT, "draw command contains invalid numeric values");
    }

    switch (command->type) {
    case FSDX_COMMAND_SPRITE:
        if (command->resource == 0 || command->x1 <= 0.0f || command->y1 <= 0.0f ||
            (command->flags & ~FSDX_DRAW_FLAG_FLIPPED) != 0 || !valid_alpha(command->alpha) ||
            command->payload_size != 0) {
            return fail(FSDX_STATUS_INVALID_ARGUMENT, "invalid sprite draw command");
        }
        return FSDX_STATUS_OK;
    case FSDX_COMMAND_LINE:
        if (command->resource != 0 || command->flags != 0 || !valid_alpha(command->alpha) ||
            command->payload_size != 0) {
            return fail(FSDX_STATUS_INVALID_ARGUMENT, "invalid line draw command");
        }
        return FSDX_STATUS_OK;
    case FSDX_COMMAND_RECT:
    case FSDX_COMMAND_ELLIPSE: {
        constexpr uint32_t shape_flags = FSDX_DRAW_FLAG_HAS_FILL | FSDX_DRAW_FLAG_HAS_STROKE;
        if (command->resource != 0 || (command->flags & ~shape_flags) != 0 || !valid_alpha(command->alpha) ||
            command->payload_size != 0 ||
            ((command->flags & FSDX_DRAW_FLAG_HAS_STROKE) != 0 && command->stroke_width <= 0.0f)) {
            return fail(FSDX_STATUS_INVALID_ARGUMENT, "invalid shape draw command");
        }
        return FSDX_STATUS_OK;
    }
    case FSDX_COMMAND_TEXT: {
        constexpr uint32_t alignment_mask = 0x000010efu;
        if (command->resource != 0 || (command->flags & ~FSDX_DRAW_FLAG_TEXT_BOLD) != 0 ||
            !valid_alpha(command->alpha) || command->stroke_width <= 0.0f ||
            command->text_length > command->payload_size ||
            (command->stroke_rgba & ~alignment_mask) != 0 || !valid_payload_range(command, payload_size)) {
            return fail(FSDX_STATUS_INVALID_ARGUMENT, "invalid text draw command");
        }
        return FSDX_STATUS_OK;
    }
    case FSDX_COMMAND_CLIP_PUSH:
        if (command->resource != 0 || command->flags != 0 || command->payload_size != 0) {
            return fail(FSDX_STATUS_INVALID_ARGUMENT, "invalid clip push command");
        }
        return FSDX_STATUS_OK;
    case FSDX_COMMAND_TRANSFORM_PUSH:
        if (command->resource != 0 || command->flags != 0 || command->payload_size != 0 ||
            !finite_transform(command)) {
            return fail(FSDX_STATUS_INVALID_ARGUMENT, "invalid transform push command");
        }
        return FSDX_STATUS_OK;
    case FSDX_COMMAND_CLIP_POP:
    case FSDX_COMMAND_TRANSFORM_POP:
        if (command->resource != 0 || command->flags != 0 || command->payload_size != 0) {
            return fail(FSDX_STATUS_INVALID_ARGUMENT, "invalid draw state pop command");
        }
        return FSDX_STATUS_OK;
    default:
        return fail(FSDX_STATUS_UNSUPPORTED, "unsupported draw command type");
    }
}

fsdx_status create_d2d_resources(const std::shared_ptr<Runtime>& runtime, uint32_t flags) {
    D3D_DRIVER_TYPE driver_type = (flags & FSDX_RUNTIME_FLAG_WARP) != 0
        ? D3D_DRIVER_TYPE_WARP
        : D3D_DRIVER_TYPE_HARDWARE;
    const D3D_FEATURE_LEVEL feature_levels[] = {
        D3D_FEATURE_LEVEL_11_1,
        D3D_FEATURE_LEVEL_11_0,
        D3D_FEATURE_LEVEL_10_1,
        D3D_FEATURE_LEVEL_10_0,
    };
    D3D_FEATURE_LEVEL selected_level{};
    HRESULT hr = D3D11CreateDevice(
        nullptr,
        driver_type,
        nullptr,
        D3D11_CREATE_DEVICE_BGRA_SUPPORT,
        feature_levels,
        static_cast<UINT>(std::size(feature_levels)),
        D3D11_SDK_VERSION,
        &runtime->device,
        &selected_level,
        &runtime->context
    );
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_DEVICE_INIT_FAILED, "D3D11CreateDevice", hr);
    }

    hr = D2D1CreateFactory(
        D2D1_FACTORY_TYPE_SINGLE_THREADED,
        IID_PPV_ARGS(&runtime->d2d_factory)
    );
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_DEVICE_INIT_FAILED, "D2D1CreateFactory", hr);
    }

    ComPtr<IDXGIDevice> dxgi_device;
    hr = runtime->device.As(&dxgi_device);
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_DEVICE_INIT_FAILED, "ID3D11Device::QueryInterface(IDXGIDevice)", hr);
    }
    hr = runtime->d2d_factory->CreateDevice(dxgi_device.Get(), &runtime->d2d_device);
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_DEVICE_INIT_FAILED, "ID2D1Factory1::CreateDevice", hr);
    }
    hr = runtime->d2d_device->CreateDeviceContext(
        D2D1_DEVICE_CONTEXT_OPTIONS_NONE,
        &runtime->d2d_context
    );
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_DEVICE_INIT_FAILED, "ID2D1Device::CreateDeviceContext", hr);
    }
    hr = runtime->d2d_context->CreateSolidColorBrush(
        D2D1::ColorF(0.0f, 0.0f, 0.0f, 0.0f),
        &runtime->solid_brush
    );
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_DEVICE_INIT_FAILED, "CreateSolidColorBrush", hr);
    }

    hr = DWriteCreateFactory(
        DWRITE_FACTORY_TYPE_SHARED,
        __uuidof(IDWriteFactory),
        reinterpret_cast<IUnknown**>(runtime->dwrite_factory.GetAddressOf())
    );
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_DEVICE_INIT_FAILED, "DWriteCreateFactory", hr);
    }

    const fsdx_status font_status = rebuild_font_collection(runtime.get());
    if (font_status != FSDX_STATUS_OK) {
        return font_status;
    }

    return create_offscreen_targets(runtime.get(), runtime->width, runtime->height);
}

fsdx_status ensure_composition_device(const std::shared_ptr<Runtime>& runtime) {
    if (runtime->composition_device != nullptr) {
        return FSDX_STATUS_OK;
    }
    ComPtr<IDXGIDevice> dxgi_device;
    HRESULT hr = runtime->device.As(&dxgi_device);
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_DEVICE_INIT_FAILED, "QueryInterface(IDXGIDevice) for DComp", hr);
    }
    hr = DCompositionCreateDevice(
        dxgi_device.Get(),
        __uuidof(IDCompositionDevice),
        reinterpret_cast<void**>(runtime->composition_device.GetAddressOf())
    );
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_DEVICE_INIT_FAILED, "DCompositionCreateDevice", hr);
    }
    return FSDX_STATUS_OK;
}

fsdx_status create_composition_surface(
    const std::shared_ptr<Runtime>& runtime,
    const std::shared_ptr<NativeWindow>& window,
    uint32_t width,
    uint32_t height
) {
    fsdx_status status = ensure_composition_device(runtime);
    if (status != FSDX_STATUS_OK) {
        return status;
    }

    ComPtr<IDXGIDevice> dxgi_device;
    HRESULT hr = runtime->device.As(&dxgi_device);
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_DEVICE_INIT_FAILED, "QueryInterface(IDXGIDevice) for swap chain", hr);
    }
    ComPtr<IDXGIAdapter> adapter;
    hr = dxgi_device->GetAdapter(&adapter);
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_DEVICE_INIT_FAILED, "IDXGIDevice::GetAdapter", hr);
    }
    ComPtr<IDXGIFactory2> factory;
    hr = adapter->GetParent(IID_PPV_ARGS(&factory));
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_DEVICE_INIT_FAILED, "IDXGIAdapter::GetParent", hr);
    }

    DXGI_SWAP_CHAIN_DESC1 swap_desc{};
    swap_desc.Width = width;
    swap_desc.Height = height;
    swap_desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    swap_desc.SampleDesc.Count = 1;
    swap_desc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    swap_desc.BufferCount = 2;
    swap_desc.Scaling = DXGI_SCALING_STRETCH;
    swap_desc.SwapEffect = DXGI_SWAP_EFFECT_FLIP_SEQUENTIAL;
    swap_desc.AlphaMode = DXGI_ALPHA_MODE_PREMULTIPLIED;
    hr = factory->CreateSwapChainForComposition(
        runtime->device.Get(),
        &swap_desc,
        nullptr,
        &window->swap_chain
    );
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_DEVICE_INIT_FAILED, "CreateSwapChainForComposition", hr);
    }
    hr = runtime->composition_device->CreateTargetForHwnd(
        window->hwnd,
        TRUE,
        &window->composition_target
    );
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_DEVICE_INIT_FAILED, "IDCompositionDevice::CreateTargetForHwnd", hr);
    }
    hr = runtime->composition_device->CreateVisual(&window->visual);
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_DEVICE_INIT_FAILED, "IDCompositionDevice::CreateVisual", hr);
    }
    hr = window->visual->SetContent(window->swap_chain.Get());
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_DEVICE_INIT_FAILED, "IDCompositionVisual::SetContent", hr);
    }
    hr = window->composition_target->SetRoot(window->visual.Get());
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_DEVICE_INIT_FAILED, "IDCompositionTarget::SetRoot", hr);
    }
    hr = runtime->composition_device->Commit();
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_DEVICE_INIT_FAILED, "IDCompositionDevice::Commit", hr);
    }
    return FSDX_STATUS_OK;
}

fsdx_status resize_composition_surface(
    Runtime* runtime,
    NativeWindow* window,
    uint32_t width,
    uint32_t height
) {
    if (width == runtime->width && height == runtime->height) {
        return FSDX_STATUS_OK;
    }
    runtime->d2d_context->SetTarget(nullptr);
    runtime->context->Flush();
    HRESULT hr = window->swap_chain->ResizeBuffers(
        0,
        width,
        height,
        DXGI_FORMAT_UNKNOWN,
        0
    );
    if (FAILED(hr)) {
        return fail_render_hr("IDXGISwapChain1::ResizeBuffers", hr);
    }
    const fsdx_status status = create_offscreen_targets(runtime, width, height);
    if (status != FSDX_STATUS_OK) {
        return status;
    }
    queue_event(runtime, make_event(window, FSDX_EVENT_REPAINT));
    return FSDX_STATUS_OK;
}

fsdx_status recover_device(const std::shared_ptr<Runtime>& runtime) {
    for (const auto& item : runtime->windows) {
        const auto& window = item.second;
        if (window->owner_thread_id != GetCurrentThreadId()) {
            return fail(FSDX_STATUS_UNSUPPORTED, "device recovery must run on the window owner thread");
        }
    }

    for (const auto& item : runtime->windows) {
        const auto& window = item.second;
        if (window->composition_target != nullptr) {
            window->composition_target->SetRoot(nullptr);
        }
        if (window->visual != nullptr) {
            window->visual->SetContent(nullptr);
        }
        window->visual.Reset();
        window->composition_target.Reset();
        window->swap_chain.Reset();
    }
    if (runtime->composition_device != nullptr) {
        runtime->composition_device->Commit();
    }

    if (runtime->d2d_context != nullptr) {
        runtime->d2d_context->SetTarget(nullptr);
    }
    for (auto& item : runtime->resources) {
        item.second.bitmap.Reset();
    }
    runtime->target_bitmap.Reset();
    runtime->solid_brush.Reset();
    runtime->dwrite_factory.Reset();
    runtime->font_collection.Reset();
    runtime->d2d_context.Reset();
    runtime->d2d_device.Reset();
    runtime->d2d_factory.Reset();
    runtime->composition_device.Reset();
    runtime->staging_texture.Reset();
    runtime->render_texture.Reset();
    runtime->context.Reset();
    runtime->device.Reset();

    fsdx_status status = create_d2d_resources(runtime, runtime->creation_flags);
    if (status != FSDX_STATUS_OK) {
        return status;
    }
    for (auto& item : runtime->resources) {
        status = create_resource_bitmap(
            runtime.get(),
            item.second.width,
            item.second.height,
            item.second.premultiplied_pixels,
            &item.second.bitmap
        );
        if (status != FSDX_STATUS_OK) {
            return status;
        }
    }
    for (const auto& item : runtime->windows) {
        const auto& window = item.second;
        status = create_composition_surface(runtime, window, runtime->width, runtime->height);
        if (status != FSDX_STATUS_OK) {
            return status;
        }
    }
    ++runtime->device_generation;
    for (const auto& item : runtime->windows) {
        fsdx_event event = make_event(item.second.get(), FSDX_EVENT_DEVICE_RECOVERED);
        event.key = static_cast<uint32_t>(runtime->device_generation & 0xffffffffu);
        queue_event(runtime.get(), event);
        queue_event(runtime.get(), make_event(item.second.get(), FSDX_EVENT_REPAINT));
    }
    return FSDX_STATUS_OK;
}

void destroy_native_window(const std::shared_ptr<NativeWindow>& window) {
    if (window == nullptr) {
        return;
    }
    if (window->hwnd != nullptr && IsWindow(window->hwnd)) {
        if (GetCapture() == window->hwnd) {
            ReleaseCapture();
        }
        DestroyWindow(window->hwnd);
    }
    window->visual.Reset();
    window->composition_target.Reset();
    window->swap_chain.Reset();
    window->hwnd = nullptr;
}

void destroy_native_tray(const std::shared_ptr<NativeTray>& tray) {
    if (tray == nullptr) {
        return;
    }
    remove_tray_icon(tray.get());
    tray->desired_visible = false;
    if (tray->hwnd != nullptr && IsWindow(tray->hwnd)) {
        DestroyWindow(tray->hwnd);
    }
    if (tray->owns_icon && tray->icon != nullptr) {
        DestroyIcon(tray->icon);
    }
    tray->icon = nullptr;
    tray->owns_icon = false;
    tray->hwnd = nullptr;
}

fsdx_status require_window_thread(const std::shared_ptr<NativeWindow>& window) {
    if (window->owner_thread_id != GetCurrentThreadId()) {
        return fail(FSDX_STATUS_UNSUPPORTED, "window operation must run on its owner thread");
    }
    return FSDX_STATUS_OK;
}

fsdx_status require_tray_thread(const std::shared_ptr<NativeTray>& tray) {
    if (tray->owner_thread_id != GetCurrentThreadId()) {
        return fail(FSDX_STATUS_UNSUPPORTED, "tray operation must run on its owner thread");
    }
    return FSDX_STATUS_OK;
}

void fill_window_state(const std::shared_ptr<NativeWindow>& window, fsdx_window_state* state) {
    *state = {};
    state->abi_version = FSDX_ABI_VERSION;
    state->struct_size = sizeof(fsdx_window_state);
    state->native_handle = reinterpret_cast<uint64_t>(window->hwnd);
    state->dpi = window_dpi(window->hwnd);
    if (window->hwnd == nullptr) {
        return;
    }
    if (IsWindowVisible(window->hwnd)) state->flags |= FSDX_WINDOW_STATE_VISIBLE;
    if (window->clickthrough) state->flags |= FSDX_WINDOW_STATE_CLICKTHROUGH;
    if (GetForegroundWindow() == window->hwnd) state->flags |= FSDX_WINDOW_STATE_ACTIVE;
    if (GetCapture() == window->hwnd) state->flags |= FSDX_WINDOW_STATE_CAPTURED;

    RECT rectangle{};
    if (GetWindowRect(window->hwnd, &rectangle)) {
        state->x = rectangle.left;
        state->y = rectangle.top;
        state->width = static_cast<uint32_t>(std::max(0l, rectangle.right - rectangle.left));
        state->height = static_cast<uint32_t>(std::max(0l, rectangle.bottom - rectangle.top));
    }
    const HMONITOR monitor = MonitorFromWindow(window->hwnd, MONITOR_DEFAULTTONEAREST);
    MONITORINFO monitor_info{sizeof(MONITORINFO)};
    if (monitor != nullptr && GetMonitorInfoW(monitor, &monitor_info)) {
        state->screen_x = monitor_info.rcMonitor.left;
        state->screen_y = monitor_info.rcMonitor.top;
        state->screen_width = static_cast<uint32_t>(
            std::max(0l, monitor_info.rcMonitor.right - monitor_info.rcMonitor.left)
        );
        state->screen_height = static_cast<uint32_t>(
            std::max(0l, monitor_info.rcMonitor.bottom - monitor_info.rcMonitor.top)
        );
    }
}

void fill_tray_state(const std::shared_ptr<NativeTray>& tray, fsdx_tray_state* state) {
    *state = {};
    state->abi_version = FSDX_ABI_VERSION;
    state->struct_size = sizeof(fsdx_tray_state);
    state->native_handle = reinterpret_cast<uint64_t>(tray->hwnd);
    if (tray->icon_added) {
        state->flags |= FSDX_TRAY_STATE_VISIBLE;
    }
}

fsdx_status create_resource_bitmap(
    Runtime* runtime,
    uint32_t width,
    uint32_t height,
    const std::vector<uint8_t>& premultiplied_pixels,
    ComPtr<ID2D1Bitmap1>* bitmap_out
) {
    const auto properties = D2D1::BitmapProperties1(
        D2D1_BITMAP_OPTIONS_NONE,
        D2D1::PixelFormat(DXGI_FORMAT_B8G8R8A8_UNORM, D2D1_ALPHA_MODE_PREMULTIPLIED)
    );
    HRESULT hr = runtime->d2d_context->CreateBitmap(
        D2D1::SizeU(width, height),
        premultiplied_pixels.data(),
        width * 4,
        properties,
        bitmap_out->ReleaseAndGetAddressOf()
    );
    if (FAILED(hr)) {
        return fail_render_hr("ID2D1DeviceContext::CreateBitmap", hr);
    }
    return FSDX_STATUS_OK;
}

fsdx_status create_resource(const std::shared_ptr<Runtime>& runtime, const fsdx_resource_desc* desc, fsdx_handle* resource_out) {
    uint64_t expected_size = 0;
    if (!valid_header(desc->abi_version, desc->struct_size, sizeof(fsdx_resource_desc)) ||
        !checked_rgba_size(desc->width, desc->height, &expected_size) ||
        desc->rgba_pixels == nullptr || desc->rgba_size != expected_size) {
        return fail(FSDX_STATUS_INVALID_ARGUMENT, "invalid RGBA resource descriptor");
    }

    std::vector<uint8_t> premultiplied(static_cast<size_t>(expected_size));
    for (uint64_t index = 0; index < expected_size; index += 4) {
        const uint8_t red = desc->rgba_pixels[index + 0];
        const uint8_t green = desc->rgba_pixels[index + 1];
        const uint8_t blue = desc->rgba_pixels[index + 2];
        const uint8_t alpha = desc->rgba_pixels[index + 3];
        premultiplied[index + 0] = static_cast<uint8_t>((static_cast<uint32_t>(blue) * alpha + 127u) / 255u);
        premultiplied[index + 1] = static_cast<uint8_t>((static_cast<uint32_t>(green) * alpha + 127u) / 255u);
        premultiplied[index + 2] = static_cast<uint8_t>((static_cast<uint32_t>(red) * alpha + 127u) / 255u);
        premultiplied[index + 3] = alpha;
    }

    ComPtr<ID2D1Bitmap1> bitmap;
    const fsdx_status bitmap_status = create_resource_bitmap(
        runtime.get(),
        desc->width,
        desc->height,
        premultiplied,
        &bitmap
    );
    if (bitmap_status != FSDX_STATUS_OK) {
        return bitmap_status;
    }

    const fsdx_handle handle = runtime->next_resource++;
    runtime->resources.emplace(
        handle,
        Resource{bitmap, desc->width, desc->height, std::move(premultiplied)}
    );
    *resource_out = handle;
    return FSDX_STATUS_OK;
}

fsdx_status utf8_to_wide(const uint8_t* data, uint32_t size, std::wstring* output) {
    output->clear();
    if (size == 0) {
        return FSDX_STATUS_OK;
    }
    if (data == nullptr || size > static_cast<uint32_t>(std::numeric_limits<int>::max())) {
        return fail(FSDX_STATUS_INVALID_ARGUMENT, "UTF-8 payload is invalid");
    }
    const int input_size = static_cast<int>(size);
    const int wide_size = MultiByteToWideChar(
        CP_UTF8,
        MB_ERR_INVALID_CHARS,
        reinterpret_cast<const char*>(data),
        input_size,
        nullptr,
        0
    );
    if (wide_size <= 0) {
        return fail(FSDX_STATUS_INVALID_ARGUMENT, "text payload is not valid UTF-8");
    }
    try {
        output->resize(static_cast<size_t>(wide_size));
    }
    catch (const std::bad_alloc&) {
        return fail(FSDX_STATUS_ALLOCATION_FAILED, "text conversion allocation failed");
    }
    const int converted = MultiByteToWideChar(
        CP_UTF8,
        MB_ERR_INVALID_CHARS,
        reinterpret_cast<const char*>(data),
        input_size,
        output->data(),
        wide_size
    );
    if (converted != wide_size) {
        output->clear();
        return fail(FSDX_STATUS_INVALID_ARGUMENT, "text payload UTF-8 conversion failed");
    }
    return FSDX_STATUS_OK;
}

fsdx_status draw_text_command(
    const std::shared_ptr<Runtime>& runtime,
    const fsdx_draw_command* command,
    const uint8_t* payload
) {
    const uint32_t text_size = command->text_length;
    const uint32_t family_size = command->payload_size - text_size;
    const uint8_t* command_payload = payload == nullptr
        ? nullptr
        : payload + command->payload_offset;
    std::wstring text;
    fsdx_status status = utf8_to_wide(command_payload, text_size, &text);
    if (status != FSDX_STATUS_OK) {
        return status;
    }
    std::wstring family;
    status = utf8_to_wide(
        command_payload == nullptr ? nullptr : command_payload + text_size,
        family_size,
        &family
    );
    if (status != FSDX_STATUS_OK) {
        return status;
    }
    if (text.empty()) {
        return FSDX_STATUS_OK;
    }
    if (family.empty()) {
        family = L"Segoe UI";
    }

    ComPtr<IDWriteTextFormat> format;
    HRESULT hr = runtime->dwrite_factory->CreateTextFormat(
        family.c_str(),
        runtime->font_collection.Get(),
        (command->flags & FSDX_DRAW_FLAG_TEXT_BOLD) != 0
            ? DWRITE_FONT_WEIGHT_BOLD
            : DWRITE_FONT_WEIGHT_NORMAL,
        DWRITE_FONT_STYLE_NORMAL,
        DWRITE_FONT_STRETCH_NORMAL,
        command->stroke_width,
        L"",
        &format
    );
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_RENDER_FAILED, "IDWriteFactory::CreateTextFormat", hr);
    }

    const uint32_t alignment = command->stroke_rgba;
    DWRITE_TEXT_ALIGNMENT horizontal = DWRITE_TEXT_ALIGNMENT_LEADING;
    if ((alignment & 0x0002u) != 0) {
        horizontal = DWRITE_TEXT_ALIGNMENT_TRAILING;
    }
    else if ((alignment & 0x0004u) != 0) {
        horizontal = DWRITE_TEXT_ALIGNMENT_CENTER;
    }
    else if ((alignment & 0x0008u) != 0) {
        horizontal = DWRITE_TEXT_ALIGNMENT_JUSTIFIED;
    }
    hr = format->SetTextAlignment(horizontal);
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_RENDER_FAILED, "IDWriteTextFormat::SetTextAlignment", hr);
    }

    DWRITE_PARAGRAPH_ALIGNMENT vertical = DWRITE_PARAGRAPH_ALIGNMENT_NEAR;
    if ((alignment & 0x0040u) != 0) {
        vertical = DWRITE_PARAGRAPH_ALIGNMENT_FAR;
    }
    else if ((alignment & 0x0080u) != 0) {
        vertical = DWRITE_PARAGRAPH_ALIGNMENT_CENTER;
    }
    hr = format->SetParagraphAlignment(vertical);
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_RENDER_FAILED, "IDWriteTextFormat::SetParagraphAlignment", hr);
    }
    hr = format->SetWordWrapping(
        (alignment & 0x1000u) != 0
            ? DWRITE_WORD_WRAPPING_WRAP
            : DWRITE_WORD_WRAPPING_NO_WRAP
    );
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_RENDER_FAILED, "IDWriteTextFormat::SetWordWrapping", hr);
    }

    runtime->solid_brush->SetColor(unpack_color(command->fill_rgba, command->alpha));
    const auto layout = D2D1::RectF(
        command->x0,
        command->y0,
        command->x0 + std::max(0.0f, command->x1),
        command->y0 + std::max(0.0f, command->y1)
    );
    runtime->d2d_context->DrawText(
        text.data(),
        static_cast<UINT32>(text.size()),
        format.Get(),
        layout,
        runtime->solid_brush.Get(),
        D2D1_DRAW_TEXT_OPTIONS_NONE,
        DWRITE_MEASURING_MODE_NATURAL
    );
    return FSDX_STATUS_OK;
}

} // namespace

extern "C" {

FSDX_API uint32_t fsdx_get_abi_version(void) {
    return FSDX_ABI_VERSION;
}

FSDX_API fsdx_status fsdx_measure_text(
    fsdx_handle runtime_handle,
    const uint8_t* text_utf8,
    uint64_t text_size,
    const uint8_t* family_utf8,
    uint64_t family_size,
    float font_pixel_size,
    uint32_t flags,
    float* width_out,
    float* height_out
) {
    clear_error();
    if (width_out == nullptr || height_out == nullptr ||
        text_size > static_cast<uint64_t>(std::numeric_limits<int>::max()) ||
        family_size > static_cast<uint64_t>(std::numeric_limits<int>::max()) ||
        !std::isfinite(font_pixel_size) || font_pixel_size <= 0.0f) {
        return fail(FSDX_STATUS_INVALID_ARGUMENT, "text measurement arguments are invalid");
    }
    auto runtime = find_runtime(runtime_handle);
    if (!runtime) {
        return fail(FSDX_STATUS_INVALID_HANDLE, "runtime handle is invalid");
    }
    std::wstring text;
    fsdx_status status = utf8_to_wide(text_utf8, static_cast<uint32_t>(text_size), &text);
    if (status != FSDX_STATUS_OK) {
        return status;
    }
    std::wstring family;
    status = utf8_to_wide(family_utf8, static_cast<uint32_t>(family_size), &family);
    if (status != FSDX_STATUS_OK) {
        return status;
    }
    if (family.empty()) {
        family = L"Segoe UI";
    }
    if (text.empty()) {
        *width_out = 0.0f;
        *height_out = font_pixel_size;
        return FSDX_STATUS_OK;
    }
    std::lock_guard<std::mutex> lock(runtime->mutex);
    if (!runtime->dwrite_factory) {
        return fail(FSDX_STATUS_DEVICE_INIT_FAILED, "DirectWrite factory is unavailable");
    }
    ComPtr<IDWriteTextFormat> format;
    HRESULT hr = runtime->dwrite_factory->CreateTextFormat(
        family.c_str(),
        runtime->font_collection.Get(),
        (flags & FSDX_DRAW_FLAG_TEXT_BOLD) != 0
            ? DWRITE_FONT_WEIGHT_BOLD
            : DWRITE_FONT_WEIGHT_NORMAL,
        DWRITE_FONT_STYLE_NORMAL,
        DWRITE_FONT_STRETCH_NORMAL,
        font_pixel_size,
        L"",
        &format
    );
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_RENDER_FAILED, "IDWriteFactory::CreateTextFormat(measure)", hr);
    }
    hr = format->SetWordWrapping(DWRITE_WORD_WRAPPING_NO_WRAP);
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_RENDER_FAILED, "IDWriteTextFormat::SetWordWrapping(measure)", hr);
    }
    ComPtr<IDWriteTextLayout> layout;
    hr = runtime->dwrite_factory->CreateTextLayout(
        text.data(),
        static_cast<UINT32>(text.size()),
        format.Get(),
        1000000.0f,
        1000000.0f,
        &layout
    );
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_RENDER_FAILED, "IDWriteFactory::CreateTextLayout", hr);
    }
    DWRITE_TEXT_METRICS metrics{};
    hr = layout->GetMetrics(&metrics);
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_RENDER_FAILED, "IDWriteTextLayout::GetMetrics", hr);
    }
    *width_out = std::max(0.0f, metrics.widthIncludingTrailingWhitespace);
    *height_out = std::max(0.0f, metrics.height);
    return FSDX_STATUS_OK;
}

FSDX_API fsdx_status fsdx_register_font_file(
    fsdx_handle runtime_handle,
    const uint8_t* path_utf8,
    uint64_t path_size
) {
    clear_error();
    if (path_utf8 == nullptr || path_size == 0 ||
        path_size > static_cast<uint64_t>(std::numeric_limits<int>::max())) {
        return fail(FSDX_STATUS_INVALID_ARGUMENT, "font path is invalid");
    }
    auto runtime = find_runtime(runtime_handle);
    if (!runtime) {
        return fail(FSDX_STATUS_INVALID_HANDLE, "runtime handle is invalid");
    }
    std::wstring path;
    fsdx_status status = utf8_to_wide(path_utf8, static_cast<uint32_t>(path_size), &path);
    if (status != FSDX_STATUS_OK || path.empty()) {
        return fail(FSDX_STATUS_INVALID_ARGUMENT, "font path is not valid UTF-8");
    }
    const DWORD attributes = GetFileAttributesW(path.c_str());
    if (attributes == INVALID_FILE_ATTRIBUTES || (attributes & FILE_ATTRIBUTE_DIRECTORY) != 0) {
        return fail(FSDX_STATUS_INVALID_ARGUMENT, "font file does not exist");
    }
    std::lock_guard<std::mutex> lock(runtime->mutex);
    if (std::find(runtime->font_paths.begin(), runtime->font_paths.end(), path) == runtime->font_paths.end()) {
        runtime->font_paths.push_back(path);
    }
    return rebuild_font_collection(runtime.get());
}

FSDX_API fsdx_status fsdx_create_runtime(const fsdx_runtime_desc* desc, fsdx_handle* runtime_out) {
    clear_error();
    if (desc == nullptr || runtime_out == nullptr) {
        return fail(FSDX_STATUS_INVALID_ARGUMENT, "runtime descriptor and output handle are required");
    }
    if (desc->abi_version != FSDX_ABI_VERSION) {
        return fail(FSDX_STATUS_ABI_MISMATCH, "runtime descriptor ABI version mismatch");
    }
    if (desc->struct_size < sizeof(fsdx_runtime_desc)) {
        return fail(FSDX_STATUS_ABI_MISMATCH, "runtime descriptor is smaller than the current ABI");
    }
    if (desc->width == 0 || desc->height == 0 || desc->width > 16384 || desc->height > 16384) {
        return fail(FSDX_STATUS_INVALID_ARGUMENT, "runtime dimensions are outside the supported range");
    }

    auto runtime = std::make_shared<Runtime>();
    runtime->width = desc->width;
    runtime->height = desc->height;
    runtime->creation_flags = desc->flags;
    {
        std::lock_guard<std::mutex> lock(runtime->mutex);
        const fsdx_status status = create_d2d_resources(runtime, desc->flags);
        if (status != FSDX_STATUS_OK) {
            return status;
        }
    }

    std::lock_guard<std::mutex> lock(g_runtimes_mutex);
    const fsdx_handle handle = g_next_runtime++;
    g_runtimes.emplace(handle, std::move(runtime));
    *runtime_out = handle;
    return FSDX_STATUS_OK;
}

FSDX_API fsdx_status fsdx_destroy_runtime(fsdx_handle runtime_handle) {
    clear_error();
    if (runtime_handle == 0) {
        return FSDX_STATUS_OK;
    }
    std::shared_ptr<Runtime> runtime;
    {
        std::lock_guard<std::mutex> lock(g_runtimes_mutex);
        auto it = g_runtimes.find(runtime_handle);
        if (it == g_runtimes.end()) {
            if (g_destroyed_runtimes.find(runtime_handle) != g_destroyed_runtimes.end()) {
                return FSDX_STATUS_OK;
            }
            return fail(FSDX_STATUS_INVALID_HANDLE, "runtime handle is invalid");
        }
        runtime = it->second;
        {
            std::lock_guard<std::mutex> runtime_lock(runtime->mutex);
            const auto wrong_thread = std::find_if(
                runtime->windows.begin(),
                runtime->windows.end(),
                [](const auto& item) {
                    return item.second->owner_thread_id != GetCurrentThreadId();
                }
            );
            if (wrong_thread != runtime->windows.end()) {
                return fail(FSDX_STATUS_UNSUPPORTED, "runtime windows must be destroyed on their owner thread");
            }
            const auto wrong_tray_thread = std::find_if(
                runtime->trays.begin(),
                runtime->trays.end(),
                [](const auto& item) {
                    return item.second->owner_thread_id != GetCurrentThreadId();
                }
            );
            if (wrong_tray_thread != runtime->trays.end()) {
                return fail(FSDX_STATUS_UNSUPPORTED, "runtime trays must be destroyed on their owner thread");
            }
        }
        g_runtimes.erase(it);
        g_destroyed_runtimes.insert(runtime_handle);
    }
    std::vector<std::shared_ptr<NativeWindow>> windows;
    std::vector<std::shared_ptr<NativeTray>> trays;
    {
        std::lock_guard<std::mutex> lock(runtime->mutex);
        windows.reserve(runtime->windows.size());
        for (auto& item : runtime->windows) {
            windows.push_back(item.second);
        }
        runtime->windows.clear();
        trays.reserve(runtime->trays.size());
        for (auto& item : runtime->trays) {
            trays.push_back(item.second);
        }
        runtime->trays.clear();
    }
    for (const auto& window : windows) {
        destroy_native_window(window);
    }
    for (const auto& tray : trays) {
        destroy_native_tray(tray);
    }
    return FSDX_STATUS_OK;
}

FSDX_API fsdx_status fsdx_recover_device(fsdx_handle runtime_handle) {
    clear_error();
    auto runtime = find_runtime(runtime_handle);
    if (!runtime) {
        return fail(FSDX_STATUS_INVALID_HANDLE, "runtime handle is invalid");
    }
    std::lock_guard<std::mutex> lock(runtime->mutex);
    return recover_device(runtime);
}

FSDX_API fsdx_status fsdx_get_device_generation(
    fsdx_handle runtime_handle,
    uint64_t* generation_out
) {
    clear_error();
    if (generation_out == nullptr) {
        return fail(FSDX_STATUS_INVALID_ARGUMENT, "device generation output is required");
    }
    auto runtime = find_runtime(runtime_handle);
    if (!runtime) {
        return fail(FSDX_STATUS_INVALID_HANDLE, "runtime handle is invalid");
    }
    std::lock_guard<std::mutex> lock(runtime->mutex);
    *generation_out = runtime->device_generation;
    return FSDX_STATUS_OK;
}

FSDX_API fsdx_status fsdx_register_resource(
    fsdx_handle runtime_handle,
    const fsdx_resource_desc* desc,
    fsdx_handle* resource_out
) {
    clear_error();
    if (desc == nullptr || resource_out == nullptr) {
        return fail(FSDX_STATUS_INVALID_ARGUMENT, "resource descriptor and output handle are required");
    }
    auto runtime = find_runtime(runtime_handle);
    if (!runtime) {
        return fail(FSDX_STATUS_INVALID_HANDLE, "runtime handle is invalid");
    }
    std::lock_guard<std::mutex> lock(runtime->mutex);
    return create_resource(runtime, desc, resource_out);
}

FSDX_API fsdx_status fsdx_release_resource(fsdx_handle runtime_handle, fsdx_handle resource_handle) {
    clear_error();
    auto runtime = find_runtime(runtime_handle);
    if (!runtime) {
        return fail(FSDX_STATUS_INVALID_HANDLE, "runtime handle is invalid");
    }
    if (resource_handle == 0) {
        return FSDX_STATUS_OK;
    }
    std::lock_guard<std::mutex> lock(runtime->mutex);
    auto it = runtime->resources.find(resource_handle);
    if (it == runtime->resources.end()) {
        if (runtime->released_resources.find(resource_handle) != runtime->released_resources.end()) {
            return FSDX_STATUS_OK;
        }
        return fail(FSDX_STATUS_INVALID_HANDLE, "resource handle is invalid");
    }
    runtime->resources.erase(it);
    runtime->released_resources.insert(resource_handle);
    return FSDX_STATUS_OK;
}

FSDX_API fsdx_status fsdx_create_window(
    fsdx_handle runtime_handle,
    const fsdx_window_desc* desc,
    fsdx_handle* window_out
) {
    clear_error();
    if (desc == nullptr || window_out == nullptr) {
        return fail(FSDX_STATUS_INVALID_ARGUMENT, "window descriptor and output handle are required");
    }
    if (!valid_header(desc->abi_version, desc->struct_size, sizeof(fsdx_window_desc))) {
        return fail(FSDX_STATUS_ABI_MISMATCH, "window descriptor ABI version or size mismatch");
    }
    constexpr uint32_t allowed_flags = FSDX_WINDOW_FLAG_TOPMOST | FSDX_WINDOW_FLAG_TOOL |
        FSDX_WINDOW_FLAG_NO_ACTIVATE | FSDX_WINDOW_FLAG_CLICKTHROUGH;
    if (desc->width == 0 || desc->height == 0 || desc->width > 16384 || desc->height > 16384 ||
        (desc->flags & ~allowed_flags) != 0 || desc->reserved != 0) {
        return fail(FSDX_STATUS_INVALID_ARGUMENT, "window descriptor is invalid");
    }
    auto runtime = find_runtime(runtime_handle);
    if (!runtime) {
        return fail(FSDX_STATUS_INVALID_HANDLE, "runtime handle is invalid");
    }
    if (desc->width != runtime->width || desc->height != runtime->height) {
        return fail(FSDX_STATUS_INVALID_ARGUMENT, "window dimensions must match the runtime render target");
    }
    if (!ensure_window_class()) {
        return fail_win32(FSDX_STATUS_DEVICE_INIT_FAILED, "RegisterClassExW", g_window_class_error);
    }
    {
        std::lock_guard<std::mutex> lock(runtime->mutex);
        if (!runtime->windows.empty()) {
            return fail(FSDX_STATUS_UNSUPPORTED, "ABI v7 supports one composition window per runtime");
        }
    }

    auto window = std::make_shared<NativeWindow>();
    window->runtime = runtime.get();
    window->owner_thread_id = GetCurrentThreadId();
    window->clickthrough = (desc->flags & FSDX_WINDOW_FLAG_CLICKTHROUGH) != 0;
    window->creation_flags = desc->flags;
    {
        std::lock_guard<std::mutex> lock(runtime->mutex);
        window->handle = runtime->next_window++;
    }

    DWORD extended_style = WS_EX_NOREDIRECTIONBITMAP;
    if ((desc->flags & FSDX_WINDOW_FLAG_TOPMOST) != 0) extended_style |= WS_EX_TOPMOST;
    if ((desc->flags & FSDX_WINDOW_FLAG_TOOL) != 0) extended_style |= WS_EX_TOOLWINDOW;
    if ((desc->flags & FSDX_WINDOW_FLAG_NO_ACTIVATE) != 0) extended_style |= WS_EX_NOACTIVATE;
    if (window->clickthrough) extended_style |= WS_EX_TRANSPARENT;

    HWND hwnd = CreateWindowExW(
        extended_style,
        window_class_name,
        L"Flying Snow",
        WS_POPUP,
        desc->x,
        desc->y,
        static_cast<int>(desc->width),
        static_cast<int>(desc->height),
        nullptr,
        nullptr,
        GetModuleHandleW(nullptr),
        window.get()
    );
    if (hwnd == nullptr) {
        return fail_win32(FSDX_STATUS_DEVICE_INIT_FAILED, "CreateWindowExW", GetLastError());
    }
    window->hwnd = hwnd;
    // Passive visual overlays must never participate in desktop hit testing.
    // WS_EX_TRANSPARENT/HTTRANSPARENT alone is not sufficient when the
    // underlying target belongs to another process (notably fullscreen apps).
    if (window->clickthrough &&
        (window->creation_flags & FSDX_WINDOW_FLAG_NO_ACTIVATE) != 0) {
        EnableWindow(hwnd, FALSE);
    }
    fsdx_status status = create_composition_surface(runtime, window, desc->width, desc->height);
    if (status != FSDX_STATUS_OK) {
        destroy_native_window(window);
        return status;
    }
    {
        std::lock_guard<std::mutex> lock(runtime->mutex);
        runtime->windows.emplace(window->handle, window);
    }
    *window_out = window->handle;
    fsdx_event moved = make_event(window.get(), FSDX_EVENT_WINDOW_MOVED);
    moved.x = desc->x;
    moved.y = desc->y;
    moved.width = static_cast<int32_t>(desc->width);
    moved.height = static_cast<int32_t>(desc->height);
    queue_event(runtime.get(), moved);
    queue_event(runtime.get(), make_event(window.get(), FSDX_EVENT_REPAINT));
    return FSDX_STATUS_OK;
}

FSDX_API fsdx_status fsdx_destroy_window(fsdx_handle runtime_handle, fsdx_handle window_handle) {
    clear_error();
    auto runtime = find_runtime(runtime_handle);
    if (!runtime) {
        return fail(FSDX_STATUS_INVALID_HANDLE, "runtime handle is invalid");
    }
    if (window_handle == 0) {
        return FSDX_STATUS_OK;
    }
    std::shared_ptr<NativeWindow> window;
    {
        std::lock_guard<std::mutex> lock(runtime->mutex);
        const auto it = runtime->windows.find(window_handle);
        if (it == runtime->windows.end()) {
            if (runtime->destroyed_windows.find(window_handle) != runtime->destroyed_windows.end()) {
                return FSDX_STATUS_OK;
            }
            return fail(FSDX_STATUS_INVALID_HANDLE, "window handle is invalid");
        }
        window = it->second;
        const fsdx_status status = require_window_thread(window);
        if (status != FSDX_STATUS_OK) {
            return status;
        }
        runtime->windows.erase(it);
        runtime->destroyed_windows.insert(window_handle);
    }
    {
        std::lock_guard<std::mutex> lock(runtime->event_mutex);
        runtime->events.erase(
            std::remove_if(
                runtime->events.begin(),
                runtime->events.end(),
                [window_handle](const fsdx_event& event) { return event.window == window_handle; }
            ),
            runtime->events.end()
        );
    }
    destroy_native_window(window);
    return FSDX_STATUS_OK;
}

FSDX_API fsdx_status fsdx_get_window_state(
    fsdx_handle runtime_handle,
    fsdx_handle window_handle,
    fsdx_window_state* state_out
) {
    clear_error();
    if (state_out == nullptr) {
        return fail(FSDX_STATUS_INVALID_ARGUMENT, "window state output is required");
    }
    auto runtime = find_runtime(runtime_handle);
    if (!runtime) {
        return fail(FSDX_STATUS_INVALID_HANDLE, "runtime handle is invalid");
    }
    auto window = find_window(runtime, window_handle);
    if (!window || window->hwnd == nullptr) {
        return fail(FSDX_STATUS_INVALID_HANDLE, "window handle is invalid");
    }
    fill_window_state(window, state_out);
    return FSDX_STATUS_OK;
}

FSDX_API fsdx_status fsdx_show_window(
    fsdx_handle runtime_handle,
    fsdx_handle window_handle,
    uint32_t visible
) {
    clear_error();
    auto runtime = find_runtime(runtime_handle);
    if (!runtime) return fail(FSDX_STATUS_INVALID_HANDLE, "runtime handle is invalid");
    auto window = find_window(runtime, window_handle);
    if (!window || window->hwnd == nullptr) return fail(FSDX_STATUS_INVALID_HANDLE, "window handle is invalid");
    fsdx_status status = require_window_thread(window);
    if (status != FSDX_STATUS_OK) return status;
    ShowWindow(window->hwnd, visible != 0 ? SW_SHOWNOACTIVATE : SW_HIDE);
    if (visible != 0) {
        InvalidateRect(window->hwnd, nullptr, FALSE);
    }
    return FSDX_STATUS_OK;
}

FSDX_API fsdx_status fsdx_set_window_geometry(
    fsdx_handle runtime_handle,
    fsdx_handle window_handle,
    int32_t x,
    int32_t y,
    uint32_t width,
    uint32_t height
) {
    clear_error();
    if (width == 0 || height == 0 || width > 16384 || height > 16384) {
        return fail(FSDX_STATUS_INVALID_ARGUMENT, "window dimensions are outside the supported range");
    }
    auto runtime = find_runtime(runtime_handle);
    if (!runtime) return fail(FSDX_STATUS_INVALID_HANDLE, "runtime handle is invalid");
    auto window = find_window(runtime, window_handle);
    if (!window || window->hwnd == nullptr) return fail(FSDX_STATUS_INVALID_HANDLE, "window handle is invalid");
    fsdx_status status = require_window_thread(window);
    if (status != FSDX_STATUS_OK) return status;
    {
        std::lock_guard<std::mutex> lock(runtime->mutex);
        status = resize_composition_surface(runtime.get(), window.get(), width, height);
        if (status != FSDX_STATUS_OK) return status;
    }
    if (!SetWindowPos(
        window->hwnd,
        nullptr,
        x,
        y,
        static_cast<int>(width),
        static_cast<int>(height),
        SWP_NOACTIVATE | SWP_NOZORDER
    )) {
        return fail_win32(FSDX_STATUS_RENDER_FAILED, "SetWindowPos(geometry)", GetLastError());
    }
    return FSDX_STATUS_OK;
}

FSDX_API fsdx_status fsdx_set_window_clickthrough(
    fsdx_handle runtime_handle,
    fsdx_handle window_handle,
    uint32_t enabled
) {
    clear_error();
    auto runtime = find_runtime(runtime_handle);
    if (!runtime) return fail(FSDX_STATUS_INVALID_HANDLE, "runtime handle is invalid");
    auto window = find_window(runtime, window_handle);
    if (!window || window->hwnd == nullptr) return fail(FSDX_STATUS_INVALID_HANDLE, "window handle is invalid");
    fsdx_status status = require_window_thread(window);
    if (status != FSDX_STATUS_OK) return status;
    SetLastError(ERROR_SUCCESS);
    LONG_PTR style = GetWindowLongPtrW(window->hwnd, GWL_EXSTYLE);
    if (style == 0 && GetLastError() != ERROR_SUCCESS) {
        return fail_win32(FSDX_STATUS_RENDER_FAILED, "GetWindowLongPtrW", GetLastError());
    }
    if (enabled != 0) style |= WS_EX_TRANSPARENT;
    else style &= ~static_cast<LONG_PTR>(WS_EX_TRANSPARENT);
    SetLastError(ERROR_SUCCESS);
    if (SetWindowLongPtrW(window->hwnd, GWL_EXSTYLE, style) == 0 && GetLastError() != ERROR_SUCCESS) {
        return fail_win32(FSDX_STATUS_RENDER_FAILED, "SetWindowLongPtrW", GetLastError());
    }
    window->clickthrough = enabled != 0;
    const bool passive_overlay = enabled != 0 &&
        (window->creation_flags & FSDX_WINDOW_FLAG_NO_ACTIVATE) != 0;
    EnableWindow(window->hwnd, passive_overlay ? FALSE : TRUE);
    SetWindowPos(
        window->hwnd,
        nullptr,
        0,
        0,
        0,
        0,
        SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOZORDER
    );
    return FSDX_STATUS_OK;
}

FSDX_API fsdx_status fsdx_set_window_capture(
    fsdx_handle runtime_handle,
    fsdx_handle window_handle,
    uint32_t enabled
) {
    clear_error();
    auto runtime = find_runtime(runtime_handle);
    if (!runtime) return fail(FSDX_STATUS_INVALID_HANDLE, "runtime handle is invalid");
    auto window = find_window(runtime, window_handle);
    if (!window || window->hwnd == nullptr) return fail(FSDX_STATUS_INVALID_HANDLE, "window handle is invalid");
    fsdx_status status = require_window_thread(window);
    if (status != FSDX_STATUS_OK) return status;
    if (enabled != 0) {
        SetCapture(window->hwnd);
        if (GetCapture() != window->hwnd) {
            return fail_win32(FSDX_STATUS_RENDER_FAILED, "SetCapture", GetLastError());
        }
    }
    else if (GetCapture() == window->hwnd && !ReleaseCapture()) {
        return fail_win32(FSDX_STATUS_RENDER_FAILED, "ReleaseCapture", GetLastError());
    }
    return FSDX_STATUS_OK;
}

FSDX_API fsdx_status fsdx_activate_window(fsdx_handle runtime_handle, fsdx_handle window_handle) {
    clear_error();
    auto runtime = find_runtime(runtime_handle);
    if (!runtime) return fail(FSDX_STATUS_INVALID_HANDLE, "runtime handle is invalid");
    auto window = find_window(runtime, window_handle);
    if (!window || window->hwnd == nullptr) return fail(FSDX_STATUS_INVALID_HANDLE, "window handle is invalid");
    fsdx_status status = require_window_thread(window);
    if (status != FSDX_STATUS_OK) return status;
    if (window->clickthrough || (window->creation_flags & FSDX_WINDOW_FLAG_NO_ACTIVATE) != 0) {
        return FSDX_STATUS_OK;
    }
    SetForegroundWindow(window->hwnd);
    SetActiveWindow(window->hwnd);
    apply_ime_position(window.get());
    return FSDX_STATUS_OK;
}

FSDX_API fsdx_status fsdx_set_window_ime_position(
    fsdx_handle runtime_handle,
    fsdx_handle window_handle,
    int32_t x,
    int32_t y
) {
    clear_error();
    auto runtime = find_runtime(runtime_handle);
    if (!runtime) return fail(FSDX_STATUS_INVALID_HANDLE, "runtime handle is invalid");
    auto window = find_window(runtime, window_handle);
    if (!window || window->hwnd == nullptr) return fail(FSDX_STATUS_INVALID_HANDLE, "window handle is invalid");
    fsdx_status status = require_window_thread(window);
    if (status != FSDX_STATUS_OK) return status;
    window->ime_x = x;
    window->ime_y = y;
    apply_ime_position(window.get());
    return FSDX_STATUS_OK;
}

FSDX_API fsdx_status fsdx_stack_window(
    fsdx_handle runtime_handle,
    fsdx_handle window_handle,
    int64_t insert_after
) {
    clear_error();
    auto runtime = find_runtime(runtime_handle);
    if (!runtime) return fail(FSDX_STATUS_INVALID_HANDLE, "runtime handle is invalid");
    auto window = find_window(runtime, window_handle);
    if (!window || window->hwnd == nullptr) return fail(FSDX_STATUS_INVALID_HANDLE, "window handle is invalid");
    fsdx_status status = require_window_thread(window);
    if (status != FSDX_STATUS_OK) return status;
    const HWND target = insert_after == -1
        ? HWND_TOPMOST
        : reinterpret_cast<HWND>(static_cast<intptr_t>(insert_after));
    if (!SetWindowPos(
        window->hwnd,
        target,
        0,
        0,
        0,
        0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
    )) {
        return fail_win32(FSDX_STATUS_RENDER_FAILED, "SetWindowPos(stack)", GetLastError());
    }
    return FSDX_STATUS_OK;
}

FSDX_API fsdx_status fsdx_request_window_repaint(
    fsdx_handle runtime_handle,
    fsdx_handle window_handle
) {
    clear_error();
    auto runtime = find_runtime(runtime_handle);
    if (!runtime) return fail(FSDX_STATUS_INVALID_HANDLE, "runtime handle is invalid");
    auto window = find_window(runtime, window_handle);
    if (!window || window->hwnd == nullptr) return fail(FSDX_STATUS_INVALID_HANDLE, "window handle is invalid");
    fsdx_status status = require_window_thread(window);
    if (status != FSDX_STATUS_OK) return status;
    queue_event(runtime.get(), make_event(window.get(), FSDX_EVENT_REPAINT));
    return FSDX_STATUS_OK;
}

FSDX_API fsdx_status fsdx_submit_frame(
    fsdx_handle runtime_handle,
    const fsdx_draw_command* commands,
    uint32_t command_count,
    const uint8_t* payload,
    uint64_t payload_size
) {
    clear_error();
    if (command_count > 0 && commands == nullptr) {
        return fail(FSDX_STATUS_INVALID_ARGUMENT, "command array is required");
    }
    if (payload_size > 0 && payload == nullptr) {
        return fail(FSDX_STATUS_INVALID_ARGUMENT, "frame payload is required");
    }
    auto runtime = find_runtime(runtime_handle);
    if (!runtime) {
        return fail(FSDX_STATUS_INVALID_HANDLE, "runtime handle is invalid");
    }
    std::vector<const fsdx_draw_command*> ordered;
    std::vector<const fsdx_draw_command*> pending;
    std::vector<uint32_t> state_types;
    size_t maximum_state_depth = 0;
    try {
        ordered.reserve(command_count);
        pending.reserve(command_count);
        state_types.reserve(command_count);
        const auto flush_pending = [&ordered, &pending]() {
            std::stable_sort(pending.begin(), pending.end(), [](const auto* left, const auto* right) {
                if (left->layer != right->layer) return left->layer < right->layer;
                if (left->z != right->z) return left->z < right->z;
                return left->order < right->order;
            });
            ordered.insert(ordered.end(), pending.begin(), pending.end());
            pending.clear();
        };
        for (uint32_t index = 0; index < command_count; ++index) {
            const auto* command = &commands[index];
            const fsdx_status status = validate_draw_command(command, payload_size);
            if (status != FSDX_STATUS_OK) {
                return status;
            }
            if (!is_state_command(command->type)) {
                pending.push_back(command);
                continue;
            }
            flush_pending();
            if (is_push_command(command->type)) {
                state_types.push_back(command->type);
                maximum_state_depth = std::max(maximum_state_depth, state_types.size());
            }
            else {
                if (state_types.empty() || state_types.back() != matching_push_type(command->type)) {
                    return fail(FSDX_STATUS_INVALID_ARGUMENT, "draw state pop command has no matching push");
                }
                state_types.pop_back();
            }
            ordered.push_back(command);
        }
        flush_pending();
        if (!state_types.empty()) {
            return fail(FSDX_STATUS_INVALID_ARGUMENT, "draw state push command has no matching pop");
        }
    }
    catch (const std::bad_alloc&) {
        return fail(FSDX_STATUS_ALLOCATION_FAILED, "draw command ordering allocation failed");
    }
    catch (...) {
        return fail(FSDX_STATUS_RENDER_FAILED, "unexpected draw command ordering failure");
    }

    std::lock_guard<std::mutex> lock(runtime->mutex);
    for (const auto* command : ordered) {
        if (command->type == FSDX_COMMAND_SPRITE &&
            runtime->resources.find(command->resource) == runtime->resources.end()) {
            return fail(FSDX_STATUS_INVALID_HANDLE, "sprite resource handle is invalid");
        }
    }

    struct RenderState {
        uint32_t type;
        D2D1_MATRIX_3X2_F transform;
    };
    std::vector<RenderState> render_states;
    try {
        render_states.reserve(maximum_state_depth);
    }
    catch (const std::bad_alloc&) {
        return fail(FSDX_STATUS_ALLOCATION_FAILED, "draw state allocation failed");
    }

    runtime->d2d_context->SetTarget(runtime->target_bitmap.Get());
    runtime->d2d_context->BeginDraw();
    runtime->d2d_context->SetTransform(D2D1::Matrix3x2F::Identity());
    runtime->d2d_context->SetTextAntialiasMode(D2D1_TEXT_ANTIALIAS_MODE_GRAYSCALE);
    runtime->d2d_context->Clear(D2D1::ColorF(0, 0));
    fsdx_status draw_status = FSDX_STATUS_OK;
    for (const auto* command : ordered) {
        switch (command->type) {
        case FSDX_COMMAND_SPRITE: {
            const auto resource_it = runtime->resources.find(command->resource);
            const auto destination = D2D1::RectF(
                command->x0,
                command->y0,
                command->x0 + command->x1,
                command->y0 + command->y1
            );
            if ((command->flags & FSDX_DRAW_FLAG_FLIPPED) != 0) {
                const auto center = D2D1::Point2F(
                    command->x0 + command->x1 / 2.0f,
                    command->y0 + command->y1 / 2.0f
                );
                D2D1_MATRIX_3X2_F current{};
                runtime->d2d_context->GetTransform(&current);
                runtime->d2d_context->SetTransform(
                    D2D1::Matrix3x2F::Scale(-1.0f, 1.0f, center) * current
                );
                runtime->d2d_context->DrawBitmap(
                    resource_it->second.bitmap.Get(),
                    destination,
                    command->alpha,
                    D2D1_INTERPOLATION_MODE_LINEAR
                );
                runtime->d2d_context->SetTransform(current);
                break;
            }
            runtime->d2d_context->DrawBitmap(
                resource_it->second.bitmap.Get(),
                destination,
                command->alpha,
                D2D1_INTERPOLATION_MODE_LINEAR
            );
            break;
        }
        case FSDX_COMMAND_LINE:
            runtime->solid_brush->SetColor(unpack_color(command->stroke_rgba, command->alpha));
            runtime->d2d_context->DrawLine(
                D2D1::Point2F(command->x0, command->y0),
                D2D1::Point2F(command->x1, command->y1),
                runtime->solid_brush.Get(),
                command->stroke_width > 0.0f ? command->stroke_width : 1.0f
            );
            break;
        case FSDX_COMMAND_RECT:
        case FSDX_COMMAND_ELLIPSE: {
            if (command->x1 <= 0.0f || command->y1 <= 0.0f) {
                break;
            }
            const auto rect = D2D1::RectF(
                command->x0,
                command->y0,
                command->x0 + command->x1,
                command->y0 + command->y1
            );
            const auto ellipse = D2D1::Ellipse(
                D2D1::Point2F(command->x0 + command->x1 / 2.0f, command->y0 + command->y1 / 2.0f),
                command->x1 / 2.0f,
                command->y1 / 2.0f
            );
            if ((command->flags & FSDX_DRAW_FLAG_HAS_FILL) != 0) {
                runtime->solid_brush->SetColor(unpack_color(command->fill_rgba, command->alpha));
                if (command->type == FSDX_COMMAND_ELLIPSE) {
                    runtime->d2d_context->FillEllipse(ellipse, runtime->solid_brush.Get());
                }
                else {
                    runtime->d2d_context->FillRectangle(rect, runtime->solid_brush.Get());
                }
            }
            if ((command->flags & FSDX_DRAW_FLAG_HAS_STROKE) != 0) {
                runtime->solid_brush->SetColor(unpack_color(command->stroke_rgba, command->alpha));
                if (command->type == FSDX_COMMAND_ELLIPSE) {
                    runtime->d2d_context->DrawEllipse(
                        ellipse,
                        runtime->solid_brush.Get(),
                        command->stroke_width
                    );
                }
                else {
                    runtime->d2d_context->DrawRectangle(
                        rect,
                        runtime->solid_brush.Get(),
                        command->stroke_width
                    );
                }
            }
            break;
        }
        case FSDX_COMMAND_TEXT:
            draw_status = draw_text_command(runtime, command, payload);
            break;
        case FSDX_COMMAND_CLIP_PUSH: {
            D2D1_MATRIX_3X2_F current{};
            runtime->d2d_context->GetTransform(&current);
            render_states.push_back(RenderState{command->type, current});
            runtime->d2d_context->PushAxisAlignedClip(
                D2D1::RectF(
                    command->x0,
                    command->y0,
                    command->x0 + std::max(0.0f, command->x1),
                    command->y0 + std::max(0.0f, command->y1)
                ),
                D2D1_ANTIALIAS_MODE_PER_PRIMITIVE
            );
            break;
        }
        case FSDX_COMMAND_CLIP_POP: {
            const RenderState state = render_states.back();
            runtime->d2d_context->PopAxisAlignedClip();
            runtime->d2d_context->SetTransform(state.transform);
            render_states.pop_back();
            break;
        }
        case FSDX_COMMAND_TRANSFORM_PUSH: {
            D2D1_MATRIX_3X2_F current{};
            runtime->d2d_context->GetTransform(&current);
            render_states.push_back(RenderState{command->type, current});
            const auto transform = D2D1::Matrix3x2F(
                command->m11,
                command->m12,
                command->m21,
                command->m22,
                command->dx,
                command->dy
            );
            runtime->d2d_context->SetTransform(transform * current);
            break;
        }
        case FSDX_COMMAND_TRANSFORM_POP: {
            const RenderState state = render_states.back();
            runtime->d2d_context->SetTransform(state.transform);
            render_states.pop_back();
            break;
        }
        default:
            break;
        }
        if (draw_status != FSDX_STATUS_OK) {
            break;
        }
    }
    while (!render_states.empty()) {
        const RenderState state = render_states.back();
        if (state.type == FSDX_COMMAND_CLIP_PUSH) {
            runtime->d2d_context->PopAxisAlignedClip();
        }
        runtime->d2d_context->SetTransform(state.transform);
        render_states.pop_back();
    }
    const HRESULT hr = runtime->d2d_context->EndDraw();
    if (draw_status != FSDX_STATUS_OK) {
        return draw_status;
    }
    if (FAILED(hr)) {
        return fail_render_hr("ID2D1DeviceContext::EndDraw", hr);
    }
    return FSDX_STATUS_OK;
}

FSDX_API fsdx_status fsdx_submit_window_frame(
    fsdx_handle runtime_handle,
    fsdx_handle window_handle,
    const fsdx_draw_command* commands,
    uint32_t command_count,
    const uint8_t* payload,
    uint64_t payload_size
) {
    clear_error();
    auto runtime = find_runtime(runtime_handle);
    if (!runtime) {
        return fail(FSDX_STATUS_INVALID_HANDLE, "runtime handle is invalid");
    }
    auto window = find_window(runtime, window_handle);
    if (!window || window->hwnd == nullptr || window->swap_chain == nullptr) {
        return fail(FSDX_STATUS_INVALID_HANDLE, "window handle is invalid");
    }
    fsdx_status status = require_window_thread(window);
    if (status != FSDX_STATUS_OK) {
        return status;
    }
    status = fsdx_submit_frame(runtime_handle, commands, command_count, payload, payload_size);
    if (status != FSDX_STATUS_OK) {
        if (status == FSDX_STATUS_DEVICE_LOST) {
            fsdx_event event = make_event(window.get(), FSDX_EVENT_DEVICE_ERROR);
            event.key = runtime->device != nullptr
                ? static_cast<uint32_t>(runtime->device->GetDeviceRemovedReason())
                : static_cast<uint32_t>(DXGI_ERROR_DEVICE_REMOVED);
            queue_event(runtime.get(), event);
        }
        return status;
    }

    std::lock_guard<std::mutex> lock(runtime->mutex);
    ComPtr<ID3D11Texture2D> back_buffer;
    HRESULT hr = window->swap_chain->GetBuffer(0, IID_PPV_ARGS(&back_buffer));
    if (FAILED(hr)) {
        return fail_render_hr("IDXGISwapChain1::GetBuffer", hr);
    }
    runtime->context->CopyResource(back_buffer.Get(), runtime->render_texture.Get());
    hr = window->swap_chain->Present(1, 0);
    if (FAILED(hr)) {
        fsdx_event event = make_event(window.get(), FSDX_EVENT_DEVICE_ERROR);
        event.key = static_cast<uint32_t>(hr);
        queue_event(runtime.get(), event);
        return fail_render_hr("IDXGISwapChain1::Present", hr);
    }
    return FSDX_STATUS_OK;
}

FSDX_API fsdx_status fsdx_create_tray(
    fsdx_handle runtime_handle,
    const uint8_t* tooltip_utf8,
    uint64_t tooltip_size,
    const uint8_t* icon_path_utf8,
    uint64_t icon_path_size,
    fsdx_handle* tray_out
) {
    clear_error();
    if (tray_out == nullptr) {
        return fail(FSDX_STATUS_INVALID_ARGUMENT, "tray output handle is required");
    }
    std::wstring tooltip;
    std::wstring icon_path;
    if (!decode_utf8(tooltip_utf8, tooltip_size, &tooltip) ||
        !decode_utf8(icon_path_utf8, icon_path_size, &icon_path)) {
        return fail(FSDX_STATUS_INVALID_ARGUMENT, "tray tooltip or icon path is not valid UTF-8");
    }
    auto runtime = find_runtime(runtime_handle);
    if (!runtime) {
        return fail(FSDX_STATUS_INVALID_HANDLE, "runtime handle is invalid");
    }
    if (!ensure_tray_window_class()) {
        return fail_win32(
            FSDX_STATUS_DEVICE_INIT_FAILED,
            "RegisterClassExW(tray)",
            g_tray_window_class_error
        );
    }
    {
        std::lock_guard<std::mutex> lock(runtime->mutex);
        if (!runtime->trays.empty()) {
            return fail(FSDX_STATUS_UNSUPPORTED, "one tray icon per runtime is supported");
        }
    }

    auto tray = std::make_shared<NativeTray>();
    tray->runtime = runtime.get();
    tray->owner_thread_id = GetCurrentThreadId();
    {
        std::lock_guard<std::mutex> lock(runtime->mutex);
        tray->handle = runtime->next_tray++;
    }
    tray->hwnd = CreateWindowExW(
        WS_EX_TOOLWINDOW,
        tray_window_class_name,
        L"Flying Snow Tray",
        WS_POPUP,
        0,
        0,
        0,
        0,
        nullptr,
        nullptr,
        GetModuleHandleW(nullptr),
        tray.get()
    );
    if (tray->hwnd == nullptr) {
        return fail_win32(FSDX_STATUS_DEVICE_INIT_FAILED, "CreateWindowExW(tray)", GetLastError());
    }

    if (!icon_path.empty()) {
        tray->icon = static_cast<HICON>(LoadImageW(
            nullptr,
            icon_path.c_str(),
            IMAGE_ICON,
            0,
            0,
            LR_DEFAULTSIZE | LR_LOADFROMFILE
        ));
        tray->owns_icon = tray->icon != nullptr;
    }
    if (tray->icon == nullptr) {
        tray->icon = LoadIconW(nullptr, MAKEINTRESOURCEW(32512));
        tray->owns_icon = false;
    }
    if (tray->icon == nullptr) {
        destroy_native_tray(tray);
        return fail(FSDX_STATUS_DEVICE_INIT_FAILED, "no renderable tray icon is available");
    }

    tray->icon_data = {};
    tray->icon_data.cbSize = sizeof(NOTIFYICONDATAW);
    tray->icon_data.hWnd = tray->hwnd;
    tray->icon_data.uID = 1;
    tray->icon_data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP;
    tray->icon_data.uCallbackMessage = tray_callback_message;
    tray->icon_data.hIcon = tray->icon;
    wcsncpy_s(
        tray->icon_data.szTip,
        _countof(tray->icon_data.szTip),
        tooltip.empty() ? L"Flying Snow" : tooltip.c_str(),
        _TRUNCATE
    );
    tray->desired_visible = true;
    if (!add_tray_icon(tray.get())) {
        destroy_native_tray(tray);
        return fail(FSDX_STATUS_DEVICE_INIT_FAILED, "Shell_NotifyIconW(NIM_ADD) failed");
    }
    {
        std::lock_guard<std::mutex> lock(runtime->mutex);
        runtime->trays.emplace(tray->handle, tray);
    }
    *tray_out = tray->handle;
    return FSDX_STATUS_OK;
}

FSDX_API fsdx_status fsdx_destroy_tray(
    fsdx_handle runtime_handle,
    fsdx_handle tray_handle
) {
    clear_error();
    auto runtime = find_runtime(runtime_handle);
    if (!runtime) {
        return fail(FSDX_STATUS_INVALID_HANDLE, "runtime handle is invalid");
    }
    if (tray_handle == 0) {
        return FSDX_STATUS_OK;
    }
    std::shared_ptr<NativeTray> tray;
    {
        std::lock_guard<std::mutex> lock(runtime->mutex);
        const auto it = runtime->trays.find(tray_handle);
        if (it == runtime->trays.end()) {
            if (runtime->destroyed_trays.find(tray_handle) != runtime->destroyed_trays.end()) {
                return FSDX_STATUS_OK;
            }
            return fail(FSDX_STATUS_INVALID_HANDLE, "tray handle is invalid");
        }
        tray = it->second;
        const fsdx_status status = require_tray_thread(tray);
        if (status != FSDX_STATUS_OK) {
            return status;
        }
        runtime->trays.erase(it);
        runtime->destroyed_trays.insert(tray_handle);
    }
    {
        std::lock_guard<std::mutex> lock(runtime->event_mutex);
        runtime->events.erase(
            std::remove_if(
                runtime->events.begin(),
                runtime->events.end(),
                [tray_handle](const fsdx_event& event) { return event.window == tray_handle; }
            ),
            runtime->events.end()
        );
    }
    destroy_native_tray(tray);
    return FSDX_STATUS_OK;
}

FSDX_API fsdx_status fsdx_get_tray_state(
    fsdx_handle runtime_handle,
    fsdx_handle tray_handle,
    fsdx_tray_state* state_out
) {
    clear_error();
    if (state_out == nullptr) {
        return fail(FSDX_STATUS_INVALID_ARGUMENT, "tray state output is required");
    }
    auto runtime = find_runtime(runtime_handle);
    if (!runtime) {
        return fail(FSDX_STATUS_INVALID_HANDLE, "runtime handle is invalid");
    }
    auto tray = find_tray(runtime, tray_handle);
    if (!tray || tray->hwnd == nullptr) {
        return fail(FSDX_STATUS_INVALID_HANDLE, "tray handle is invalid");
    }
    fill_tray_state(tray, state_out);
    return FSDX_STATUS_OK;
}

FSDX_API fsdx_status fsdx_show_tray(
    fsdx_handle runtime_handle,
    fsdx_handle tray_handle,
    uint32_t visible
) {
    clear_error();
    auto runtime = find_runtime(runtime_handle);
    if (!runtime) {
        return fail(FSDX_STATUS_INVALID_HANDLE, "runtime handle is invalid");
    }
    auto tray = find_tray(runtime, tray_handle);
    if (!tray || tray->hwnd == nullptr) {
        return fail(FSDX_STATUS_INVALID_HANDLE, "tray handle is invalid");
    }
    const fsdx_status status = require_tray_thread(tray);
    if (status != FSDX_STATUS_OK) {
        return status;
    }
    tray->desired_visible = visible != 0;
    if (tray->desired_visible) {
        if (!add_tray_icon(tray.get())) {
            return fail(FSDX_STATUS_DEVICE_INIT_FAILED, "Shell_NotifyIconW(NIM_ADD) failed");
        }
    }
    else {
        remove_tray_icon(tray.get());
    }
    return FSDX_STATUS_OK;
}

FSDX_API fsdx_status fsdx_set_tray_menu_state(
    fsdx_handle runtime_handle,
    fsdx_handle tray_handle,
    uint32_t flags
) {
    clear_error();
    constexpr uint32_t supported_flags =
        FSDX_TRAY_MENU_STATE_GAME_MODE |
        FSDX_TRAY_MENU_STATE_CLICKTHROUGH |
        FSDX_TRAY_MENU_STATE_AUTOSTART;
    if ((flags & ~supported_flags) != 0) {
        return fail(FSDX_STATUS_INVALID_ARGUMENT, "tray menu state flags are invalid");
    }
    auto runtime = find_runtime(runtime_handle);
    if (!runtime) {
        return fail(FSDX_STATUS_INVALID_HANDLE, "runtime handle is invalid");
    }
    auto tray = find_tray(runtime, tray_handle);
    if (!tray || tray->hwnd == nullptr) {
        return fail(FSDX_STATUS_INVALID_HANDLE, "tray handle is invalid");
    }
    const fsdx_status status = require_tray_thread(tray);
    if (status != FSDX_STATUS_OK) {
        return status;
    }
    tray->menu_state_flags = flags;
    return FSDX_STATUS_OK;
}

FSDX_API fsdx_status fsdx_poll_events(
    fsdx_handle runtime_handle,
    fsdx_event* events,
    uint32_t capacity,
    uint32_t* written_out,
    uint32_t* pending_out
) {
    clear_error();
    if (written_out == nullptr || (capacity > 0 && events == nullptr)) {
        return fail(FSDX_STATUS_INVALID_ARGUMENT, "event output buffer and written count are required");
    }
    auto runtime = find_runtime(runtime_handle);
    if (!runtime) {
        return fail(FSDX_STATUS_INVALID_HANDLE, "runtime handle is invalid");
    }

    MSG message{};
    while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) {
        if (message.message == WM_QUIT) {
            PostQuitMessage(static_cast<int>(message.wParam));
            break;
        }
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }

    std::lock_guard<std::mutex> lock(runtime->event_mutex);
    const uint32_t written = std::min<uint32_t>(capacity, static_cast<uint32_t>(runtime->events.size()));
    for (uint32_t index = 0; index < written; ++index) {
        events[index] = runtime->events.front();
        runtime->events.pop_front();
    }
    *written_out = written;
    if (pending_out != nullptr) {
        *pending_out = static_cast<uint32_t>(runtime->events.size());
    }
    return FSDX_STATUS_OK;
}

FSDX_API fsdx_status fsdx_readback_rgba(
    fsdx_handle runtime_handle,
    uint8_t* destination,
    uint64_t destination_size,
    uint64_t* written_out
) {
    clear_error();
    auto runtime = find_runtime(runtime_handle);
    if (!runtime) {
        return fail(FSDX_STATUS_INVALID_HANDLE, "runtime handle is invalid");
    }
    uint64_t expected_size = 0;
    if (!checked_rgba_size(runtime->width, runtime->height, &expected_size)) {
        return fail(FSDX_STATUS_INVALID_ARGUMENT, "runtime dimensions are invalid");
    }
    if (written_out != nullptr) {
        *written_out = expected_size;
    }
    if (destination == nullptr || destination_size < expected_size) {
        return fail(FSDX_STATUS_BUFFER_TOO_SMALL, "readback buffer is too small");
    }

    std::lock_guard<std::mutex> lock(runtime->mutex);
    runtime->context->CopyResource(runtime->staging_texture.Get(), runtime->render_texture.Get());
    runtime->context->Flush();
    D3D11_MAPPED_SUBRESOURCE mapped{};
    const HRESULT hr = runtime->context->Map(
        runtime->staging_texture.Get(),
        0,
        D3D11_MAP_READ,
        0,
        &mapped
    );
    if (FAILED(hr)) {
        return fail_render_hr("ID3D11DeviceContext::Map", hr);
    }
    for (uint32_t row = 0; row < runtime->height; ++row) {
        const auto* source = static_cast<const uint8_t*>(mapped.pData) + mapped.RowPitch * row;
        auto* destination_row = destination + static_cast<uint64_t>(runtime->width) * row * 4;
        for (uint32_t column = 0; column < runtime->width; ++column) {
            const auto* source_pixel = source + column * 4;
            auto* destination_pixel = destination_row + column * 4;
            destination_pixel[0] = source_pixel[2];
            destination_pixel[1] = source_pixel[1];
            destination_pixel[2] = source_pixel[0];
            destination_pixel[3] = source_pixel[3];
        }
    }
    runtime->context->Unmap(runtime->staging_texture.Get(), 0);
    return FSDX_STATUS_OK;
}

FSDX_API const char* fsdx_get_last_error(void) {
    return g_last_error.c_str();
}

} // extern "C"
