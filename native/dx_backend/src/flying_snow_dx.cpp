#include "flying_snow_dx.h"

#include <windows.h>

#include <d2d1_1.h>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <wrl/client.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdint>
#include <cstring>
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
    sizeof(fsdx_draw_command) == FSDX_DRAW_COMMAND_V2_SIZE,
    "fsdx_draw_command ABI size changed"
);

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

bool valid_header(uint32_t abi_version, uint32_t struct_size, size_t minimum_size) {
    return abi_version == FSDX_ABI_VERSION && struct_size >= minimum_size;
}

struct Resource {
    ComPtr<ID2D1Bitmap1> bitmap;
    uint32_t width = 0;
    uint32_t height = 0;
};

struct Runtime {
    std::mutex mutex;
    uint32_t width = 0;
    uint32_t height = 0;
    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11DeviceContext> context;
    ComPtr<ID3D11Texture2D> render_texture;
    ComPtr<ID3D11Texture2D> staging_texture;
    ComPtr<ID2D1Factory1> d2d_factory;
    ComPtr<ID2D1Device> d2d_device;
    ComPtr<ID2D1DeviceContext> d2d_context;
    ComPtr<ID2D1Bitmap1> target_bitmap;
    ComPtr<ID2D1SolidColorBrush> solid_brush;
    std::unordered_map<fsdx_handle, Resource> resources;
    std::unordered_set<fsdx_handle> released_resources;
    fsdx_handle next_resource = 1;
};

std::mutex g_runtimes_mutex;
std::unordered_map<fsdx_handle, std::shared_ptr<Runtime>> g_runtimes;
std::unordered_set<fsdx_handle> g_destroyed_runtimes;
fsdx_handle g_next_runtime = 1;

std::shared_ptr<Runtime> find_runtime(fsdx_handle handle) {
    std::lock_guard<std::mutex> lock(g_runtimes_mutex);
    auto it = g_runtimes.find(handle);
    return it == g_runtimes.end() ? nullptr : it->second;
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

D2D1_COLOR_F unpack_color(uint32_t rgba, float command_alpha) {
    constexpr float channel_scale = 1.0f / 255.0f;
    const float red = static_cast<float>(rgba & 0xffu) * channel_scale;
    const float green = static_cast<float>((rgba >> 8u) & 0xffu) * channel_scale;
    const float blue = static_cast<float>((rgba >> 16u) & 0xffu) * channel_scale;
    const float alpha = static_cast<float>((rgba >> 24u) & 0xffu) * channel_scale * command_alpha;
    return D2D1::ColorF(red, green, blue, alpha);
}

fsdx_status validate_draw_command(const fsdx_draw_command* command) {
    if (command->abi_version != FSDX_ABI_VERSION || command->struct_size < sizeof(fsdx_draw_command)) {
        return fail(FSDX_STATUS_ABI_MISMATCH, "draw command ABI version or size mismatch");
    }
    if (!finite_geometry(command) || !std::isfinite(command->alpha) ||
        command->alpha < 0.0f || command->alpha > 1.0f ||
        !std::isfinite(command->stroke_width) || command->stroke_width < 0.0f) {
        return fail(FSDX_STATUS_INVALID_ARGUMENT, "draw command contains invalid numeric values");
    }

    switch (command->type) {
    case FSDX_COMMAND_SPRITE:
        if (command->resource == 0 || command->x1 <= 0.0f || command->y1 <= 0.0f ||
            (command->flags & ~FSDX_DRAW_FLAG_FLIPPED) != 0) {
            return fail(FSDX_STATUS_INVALID_ARGUMENT, "invalid sprite draw command");
        }
        return FSDX_STATUS_OK;
    case FSDX_COMMAND_LINE:
        if (command->resource != 0 || command->flags != 0) {
            return fail(FSDX_STATUS_INVALID_ARGUMENT, "invalid line draw command");
        }
        return FSDX_STATUS_OK;
    case FSDX_COMMAND_RECT:
    case FSDX_COMMAND_ELLIPSE: {
        constexpr uint32_t shape_flags = FSDX_DRAW_FLAG_HAS_FILL | FSDX_DRAW_FLAG_HAS_STROKE;
        if (command->resource != 0 || (command->flags & ~shape_flags) != 0 ||
            ((command->flags & FSDX_DRAW_FLAG_HAS_STROKE) != 0 && command->stroke_width <= 0.0f)) {
            return fail(FSDX_STATUS_INVALID_ARGUMENT, "invalid shape draw command");
        }
        return FSDX_STATUS_OK;
    }
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

    D3D11_TEXTURE2D_DESC texture_desc{};
    texture_desc.Width = runtime->width;
    texture_desc.Height = runtime->height;
    texture_desc.MipLevels = 1;
    texture_desc.ArraySize = 1;
    texture_desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    texture_desc.SampleDesc.Count = 1;
    texture_desc.Usage = D3D11_USAGE_DEFAULT;
    texture_desc.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;
    hr = runtime->device->CreateTexture2D(&texture_desc, nullptr, &runtime->render_texture);
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_DEVICE_INIT_FAILED, "CreateTexture2D(render)", hr);
    }

    D3D11_TEXTURE2D_DESC staging_desc = texture_desc;
    staging_desc.Usage = D3D11_USAGE_STAGING;
    staging_desc.BindFlags = 0;
    staging_desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    hr = runtime->device->CreateTexture2D(&staging_desc, nullptr, &runtime->staging_texture);
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_DEVICE_INIT_FAILED, "CreateTexture2D(staging)", hr);
    }

    ComPtr<IDXGISurface> surface;
    hr = runtime->render_texture.As(&surface);
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_DEVICE_INIT_FAILED, "QueryInterface(IDXGISurface)", hr);
    }
    const auto pixel_format = D2D1::PixelFormat(
        DXGI_FORMAT_B8G8R8A8_UNORM,
        D2D1_ALPHA_MODE_PREMULTIPLIED
    );
    const auto bitmap_properties = D2D1::BitmapProperties1(
        D2D1_BITMAP_OPTIONS_TARGET | D2D1_BITMAP_OPTIONS_CANNOT_DRAW,
        pixel_format
    );
    hr = runtime->d2d_context->CreateBitmapFromDxgiSurface(
        surface.Get(),
        bitmap_properties,
        &runtime->target_bitmap
    );
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_DEVICE_INIT_FAILED, "CreateBitmapFromDxgiSurface", hr);
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

    const auto properties = D2D1::BitmapProperties1(
        D2D1_BITMAP_OPTIONS_NONE,
        D2D1::PixelFormat(DXGI_FORMAT_B8G8R8A8_UNORM, D2D1_ALPHA_MODE_PREMULTIPLIED)
    );
    ComPtr<ID2D1Bitmap1> bitmap;
    HRESULT hr = runtime->d2d_context->CreateBitmap(
        D2D1::SizeU(desc->width, desc->height),
        premultiplied.data(),
        desc->width * 4,
        properties,
        &bitmap
    );
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_ALLOCATION_FAILED, "ID2D1DeviceContext::CreateBitmap", hr);
    }

    const fsdx_handle handle = runtime->next_resource++;
    runtime->resources.emplace(handle, Resource{bitmap, desc->width, desc->height});
    *resource_out = handle;
    return FSDX_STATUS_OK;
}

} // namespace

extern "C" {

FSDX_API uint32_t fsdx_get_abi_version(void) {
    return FSDX_ABI_VERSION;
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
    std::lock_guard<std::mutex> lock(g_runtimes_mutex);
    auto it = g_runtimes.find(runtime_handle);
    if (it == g_runtimes.end()) {
        if (g_destroyed_runtimes.find(runtime_handle) != g_destroyed_runtimes.end()) {
            return FSDX_STATUS_OK;
        }
        return fail(FSDX_STATUS_INVALID_HANDLE, "runtime handle is invalid");
    }
    g_runtimes.erase(it);
    g_destroyed_runtimes.insert(runtime_handle);
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

FSDX_API fsdx_status fsdx_submit_frame(
    fsdx_handle runtime_handle,
    const fsdx_draw_command* commands,
    uint32_t command_count
) {
    clear_error();
    if (command_count > 0 && commands == nullptr) {
        return fail(FSDX_STATUS_INVALID_ARGUMENT, "command array is required");
    }
    auto runtime = find_runtime(runtime_handle);
    if (!runtime) {
        return fail(FSDX_STATUS_INVALID_HANDLE, "runtime handle is invalid");
    }
    std::vector<const fsdx_draw_command*> ordered;
    try {
        ordered.reserve(command_count);
        for (uint32_t index = 0; index < command_count; ++index) {
            const auto* command = &commands[index];
            const fsdx_status status = validate_draw_command(command);
            if (status != FSDX_STATUS_OK) {
                return status;
            }
            ordered.push_back(command);
        }
        std::stable_sort(ordered.begin(), ordered.end(), [](const auto* left, const auto* right) {
            if (left->layer != right->layer) return left->layer < right->layer;
            if (left->z != right->z) return left->z < right->z;
            return left->order < right->order;
        });
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
    runtime->d2d_context->SetTarget(runtime->target_bitmap.Get());
    runtime->d2d_context->BeginDraw();
    runtime->d2d_context->SetTransform(D2D1::Matrix3x2F::Identity());
    runtime->d2d_context->Clear(D2D1::ColorF(0, 0));
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
                runtime->d2d_context->SetTransform(D2D1::Matrix3x2F::Scale(-1.0f, 1.0f, center));
            }
            runtime->d2d_context->DrawBitmap(
                resource_it->second.bitmap.Get(),
                destination,
                command->alpha,
                D2D1_INTERPOLATION_MODE_LINEAR
            );
            runtime->d2d_context->SetTransform(D2D1::Matrix3x2F::Identity());
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
        default:
            break;
        }
    }
    const HRESULT hr = runtime->d2d_context->EndDraw();
    if (FAILED(hr)) {
        return fail_hr(FSDX_STATUS_RENDER_FAILED, "ID2D1DeviceContext::EndDraw", hr);
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
        return fail_hr(FSDX_STATUS_RENDER_FAILED, "ID3D11DeviceContext::Map", hr);
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
