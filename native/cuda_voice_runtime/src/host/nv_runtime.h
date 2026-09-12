#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

namespace fsv {

typedef unsigned long long NvPtr;  /* CUdeviceptr */
typedef int NvDevice;              /* CUdevice */

/* Minimal CUDA driver API surface, resolved from nvcuda.dll at run time.
   The voice runtime must work on machines that only have the NVIDIA display
   driver installed, so nothing here links against the CUDA toolkit. */
struct NvDriverApi {
    bool loaded = false;
    std::string error;

    int (*cuInit)(unsigned) = nullptr;
    int (*cuDriverGetVersion)(int*) = nullptr;
    int (*cuDeviceGetCount)(int*) = nullptr;
    int (*cuDeviceGet)(NvDevice*, int) = nullptr;
    int (*cuDeviceGetName)(char*, int, NvDevice) = nullptr;
    int (*cuDeviceGetAttribute)(int*, int, NvDevice) = nullptr;
    int (*cuDeviceTotalMem)(std::size_t*, NvDevice) = nullptr;
    int (*cuCtxCreate)(void**, unsigned, NvDevice) = nullptr;
    int (*cuCtxDestroy)(void*) = nullptr;
    int (*cuCtxSetCurrent)(void*) = nullptr;
    int (*cuCtxGetCurrent)(void**) = nullptr;
    int (*cuCtxSynchronize)() = nullptr;
    int (*cuModuleLoadData)(void**, const void*) = nullptr;
    int (*cuModuleUnload)(void*) = nullptr;
    int (*cuModuleGetFunction)(void**, void*, const char*) = nullptr;
    int (*cuMemAlloc)(NvPtr*, std::size_t) = nullptr;
    int (*cuMemFree)(NvPtr) = nullptr;
    int (*cuMemGetInfo)(std::size_t*, std::size_t*) = nullptr;
    int (*cuMemcpyHtoD)(NvPtr, const void*, std::size_t) = nullptr;
    int (*cuMemcpyDtoH)(void*, NvPtr, std::size_t) = nullptr;
    /* Optional: page-locked staging. A copy from pageable memory synchronises
       the stream first, which lets the host interpreter run ahead of the
       device. Absent on very old drivers, in which case the synchronous entry
       points above stay in use. */
    int (*cuMemHostAlloc)(void**, std::size_t, unsigned) = nullptr;
    int (*cuMemFreeHost)(void*) = nullptr;
    int (*cuMemcpyHtoDAsync)(NvPtr, const void*, std::size_t, void*) = nullptr;
    int (*cuStreamQuery)(void*) = nullptr;
    int (*cuStreamCreate)(void**, unsigned) = nullptr;
    int (*cuStreamDestroy)(void*) = nullptr;
    int (*cuStreamSynchronize)(void*) = nullptr;
    /* Optional: event timing. A blocking copy both waits for the queue to
       drain and moves the bytes, and a wall clock alone cannot tell the two
       apart, which is exactly the question the diagnostics must answer. */
    int (*cuEventCreate)(void**, unsigned) = nullptr;
    int (*cuEventRecord)(void*, void*) = nullptr;
    int (*cuEventElapsedTime)(float*, void*, void*) = nullptr;
    int (*cuEventSynchronize)(void*) = nullptr;
    int (*cuEventDestroy)(void*) = nullptr;
    int (*cuLaunchKernel)(void*, unsigned, unsigned, unsigned,
                          unsigned, unsigned, unsigned,
                          unsigned, void*, void**, void**) = nullptr;
    int (*cuGetErrorName)(int, const char**) = nullptr;
    int (*cuGetErrorString)(int, const char**) = nullptr;
};

/* Device attributes used by the runtime (values from cuda.h). */
enum {
    kAttrComputeCapabilityMajor = 75,
    kAttrComputeCapabilityMinor = 76,
};

/* Owns the driver context, the JIT-compiled PTX module and device buffers. */
class NvRuntime {
public:
    static NvRuntime& instance();

    /* Loads nvcuda.dll, creates the context and loads the embedded PTX module. */
    bool initialize(std::string& error);
    bool ready() const { return initialized_; }

    /* Kernel lookup inside the embedded PTX module. */
    bool kernel(const char* name, void** function, std::string& error);

    bool allocate(std::size_t bytes, NvPtr& pointer, std::string& error);
    /* ``bytes`` feeds the device-footprint counter; pass the size whenever it
       is known, 0 only for a free that cannot be attributed. */
    void release(NvPtr pointer, std::size_t bytes = 0);
    /* Pooled device memory. cuMemAlloc/cuMemFree cost far more than the kernels a
       voice graph launches, so recycled buffers are kept for the next node. */
    bool acquire(std::size_t bytes, NvPtr& pointer, std::string& error);
    void recycle(NvPtr pointer, std::size_t bytes);
    void drain_pool();
    bool upload(NvPtr destination, const void* source, std::size_t bytes, std::string& error);
    bool download(void* destination, NvPtr source, std::size_t bytes, std::string& error);
    bool synchronize(std::string& error);

    bool launch(void* function, unsigned grid_x, unsigned grid_y, unsigned grid_z,
                unsigned block_x, unsigned block_y, unsigned block_z,
                unsigned shared_bytes, void** arguments, std::string& error);

    int device_count();
    bool device_info(int index, int* major, int* minor, std::size_t* memory, char* name,
                     std::size_t name_size);
    int driver_version();
    const NvDriverApi& api() const { return api_; }

private:
    NvRuntime() = default;
    NvRuntime(const NvRuntime&) = delete;
    NvRuntime& operator=(const NvRuntime&) = delete;

    bool bind(std::string& error);
    /* nvcuda.dll loaded and cuInit called, no device and no context: enough to
       enumerate cards, so a machine with no usable one can still report what it
       found. Caller holds the runtime lock. */
    bool driver_ready(std::string& error);
    bool current_context(std::string& error);

    NvDriverApi api_;
    bool initialized_ = false;
    std::string init_error_;
    void* context_ = nullptr;
    void* module_ = nullptr;
};

/* Driver-call counters, printed to stderr at exit when FSV_CUDA_STATS is set.
   launch_seconds measures the enqueueing cost only: kernels are queued on the
   default stream and waited for at the next download or explicit synchronize,
   so a high launch count with a low total means the device is the bottleneck
   rather than the driver. */
struct NvStats {
    unsigned long long allocations = 0;
    unsigned long long pool_hits = 0;
    unsigned long long uploads = 0;
    unsigned long long upload_bytes = 0;
    unsigned long long downloads = 0;
    unsigned long long download_bytes = 0;
    unsigned long long launches = 0;
    /* Refused allocations: the counter that decides when a run gives up on the
       card (see nv_device_abandoned). */
    unsigned long long allocation_failures = 0;
    unsigned long long abandonments = 0;
    double allocate_seconds = 0.0;
    double upload_seconds = 0.0;
    double download_seconds = 0.0;
    double launch_seconds = 0.0;
    /* Split of the blocking copies (read-backs and non-staged uploads) into
       the part spent waiting for the queue to drain and the part spent moving
       bytes. Only maintained when FSV_CUDA_STATS is set and the driver has
       the event API; timed_transfers counts the samples behind both totals. */
    double device_busy_seconds = 0.0;
    double transfer_seconds = 0.0;
    unsigned long long timed_transfers = 0;
    /* Device bytes the runtime owns: live tensor buffers, registered
       constants, and whatever is parked in the recycle pool. */
    unsigned long long device_bytes_owned = 0;
    unsigned long long device_bytes_peak = 0;
};

NvStats& nv_stats();

/* Index and description ("GeForce RTX 3050 Laptop GPU (8.6, 4.0 GiB)") of the
   card device selection picked for this process, or -1 when there is none.
   ``name`` may be null. Initialises the runtime, so it is also the entry point
   a host uses to report which card the "NVIDIA acceleration" switch landed on. */
int nv_active_device(char* name, std::size_t name_size);

/* Last error text for the public C API (thread local). */
const char* nv_last_error();
void nv_set_error(const std::string& text);
void nv_set_error(const char* text);

/* True once after a device allocation was refused; reading it clears the flag. */
bool nv_take_alloc_failure();

/* A card that is out of memory refuses every request, and each refusal used to
   hand one more node to the host interpreter: correct output, a hundred times
   slower, and the graph never stopped asking. Once this many allocations in a
   row have been refused the run gives up on the card entirely, and the flag
   stays set until the next run resets it. */
bool nv_device_abandoned();
void nv_reset_device_abandoned();

}  // namespace fsv
