#include "nv_runtime.h"

#include "fsv_opstats.h"

#include <windows.h>

#include <cstdio>
#include <cstring>
#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <cstdint>
#include <map>
#include <mutex>
#include <vector>
#include <unordered_map>

namespace fsv {

/* Generated at build time from src/kernels/*.cu (see cmake/embed_ptx.cmake). */
extern const char* const fsv_ptx_kernels_source;

/* PTX target the embedded kernels were built for, injected by CMakeLists from
   FSV_PTX_ARCH; quoted in load-failure diagnostics so the error names what the
   driver refused rather than only that it refused something. */
#ifndef FSV_PTX_ARCH_TARGET
#define FSV_PTX_ARCH_TARGET "unknown"
#endif

namespace {

thread_local char g_error[512] = "";

std::mutex g_mutex;
std::unordered_map<std::string, void*> g_functions;

/* A graph issues hundreds of thousands of launches, and every one of them used
   to look its entry point up by name: build a string, hash it, take a lock.
   Kernel names are string literals with static storage, so the same call site
   asks for the same pointer every time. Keying a fixed table on that address
   reduces the hot path to one load and one pointer compare; a collision or a
   miss simply falls through to the locked lookup below. */
struct FunctionSlot {
    const char* name = nullptr;
    void* function = nullptr;
};

constexpr std::size_t kFunctionSlotCount = 512;
FunctionSlot g_function_slots[kFunctionSlotCount];

std::size_t function_slot_index(const char* name) {
    return (reinterpret_cast<std::uintptr_t>(name) >> 4) % kFunctionSlotCount;
}

void* cached_function(const char* name) {
    const FunctionSlot& slot = g_function_slots[function_slot_index(name)];
    return slot.name == name ? slot.function : nullptr;
}

void store_function(const char* name, void* function) {
    FunctionSlot& slot = g_function_slots[function_slot_index(name)];
    slot.name = name;
    slot.function = function;
}

NvStats g_stats;

double now_seconds() {
    using clock = std::chrono::steady_clock;
    static const clock::time_point origin = clock::now();
    return std::chrono::duration<double>(clock::now() - origin).count();
}

/* True when the driver statistics are being collected. */
bool stats_enabled() {
    static const bool enabled = std::getenv("FSV_CUDA_STATS") != nullptr;
    return enabled;
}

bool event_timing_supported(const NvDriverApi& api) {
    return api.cuEventCreate && api.cuEventRecord && api.cuEventElapsedTime &&
           api.cuEventDestroy;
}

/* ---------------------------------------------------------------------------
   Per-kernel GPU time (FSV_NV_GPU_TIMER).

   The driver counters above say how long the host spent enqueueing and how
   long it waited for the queue to drain, but not which kernel filled that
   queue: a graph queues work asynchronously, so the read-back that pays for a
   kernel is rarely the node that launched it. Recording an event pair around a
   sample of each kernel's launches and reading the elapsed time once the whole
   run has been waited for answers that directly, and it costs nothing at all
   unless the variable is set.
   --------------------------------------------------------------------------- */

bool gpu_timer_enabled() {
    static const bool enabled = std::getenv("FSV_NV_GPU_TIMER") != nullptr;
    return enabled;
}

/* One sampled launch: the pair is created together and destroyed together. */
struct GpuSample {
    void* begin = nullptr;
    void* end = nullptr;
    unsigned long long threads = 0;
};

struct GpuKernelTiming {
    unsigned long long launches = 0;
    unsigned long long samples = 0;
    double sampled_seconds = 0.0;
    /* Threads in the widest sampled launch: with the sample count this says
       whether a kernel is expensive because its grids are big or because each
       launch costs a fixed amount whatever the grid is. */
    unsigned long long max_threads = 0;
    double sampled_threads = 0.0;
    /* Sampled seconds per grid size. A kernel that mixes a few multi-million
       element launches with thousands of tiny ones needs the split to be
       optimised for the right one. */
    static constexpr std::size_t kBucketCount = 4;
    double bucket_seconds[kBucketCount] = {0.0, 0.0, 0.0, 0.0};
    unsigned long long bucket_samples[kBucketCount] = {0, 0, 0, 0};
};

std::size_t thread_bucket(unsigned long long threads) {
    if (threads < 4096ull) return 0;
    if (threads < 65536ull) return 1;
    if (threads < 1048576ull) return 2;
    return 3;
}

/* Sampling every launch would double the number of driver calls; every 16th
   spreads the samples over the whole run instead of only its first graph. */
constexpr unsigned long long kGpuSampleStride = 16;
constexpr std::size_t kGpuSampleLimit = 512;

std::map<std::string, GpuKernelTiming>& gpu_timing_table() {
    static std::map<std::string, GpuKernelTiming>* table =
        new std::map<std::string, GpuKernelTiming>();
    return *table;
}

std::mutex& gpu_timing_mutex() {
    static std::mutex* mutex = new std::mutex();
    return *mutex;
}

/* Samples whose elapsed time has not been read yet, paired with their kernel.
   The read has to happen during the run: the driver context is no longer
   current by the time a static destructor runs at process exit, so an event
   queried there never reports anything. */
std::vector<std::pair<std::string, GpuSample>>& gpu_pending() {
    static std::vector<std::pair<std::string, GpuSample>>* pending =
        new std::vector<std::pair<std::string, GpuSample>>();
    return *pending;
}

/* The name of the entry point the launching thread looked up last. */
thread_local const char* g_kernel_name = nullptr;

/* Queues the opening marker and returns the sample to close, or a pair of
   nulls when this launch is not being timed. */
GpuSample gpu_sample_begin(const NvDriverApi& api, unsigned long long threads, bool& timed) {
    timed = false;
    if (!gpu_timer_enabled() || !g_kernel_name || !event_timing_supported(api)) return {};
    GpuKernelTiming& timing = gpu_timing_table()[g_kernel_name];
    if (timing.launches++ % kGpuSampleStride != 0) return {};
    if (timing.samples >= kGpuSampleLimit) return {};
    if (threads > timing.max_threads) timing.max_threads = threads;
    timing.sampled_threads += static_cast<double>(threads);
    GpuSample sample;
    if (api.cuEventCreate(&sample.begin, 0) != 0) return {};
    if (api.cuEventCreate(&sample.end, 0) != 0) {
        api.cuEventDestroy(sample.begin);
        return {};
    }
    if (api.cuEventRecord(sample.begin, nullptr) != 0) {
        api.cuEventDestroy(sample.begin);
        api.cuEventDestroy(sample.end);
        return {};
    }
    sample.threads = threads;
    timed = true;
    return sample;
}

void gpu_sample_end(const NvDriverApi& api, GpuSample sample) {
    if (!sample.begin || !sample.end) return;
    std::lock_guard<std::mutex> guard(gpu_timing_mutex());
    if (api.cuEventRecord(sample.end, nullptr) != 0) {
        api.cuEventDestroy(sample.begin);
        api.cuEventDestroy(sample.end);
        return;
    }
    gpu_pending().emplace_back(g_kernel_name, sample);
}

/* Reads the elapsed time of every sample recorded so far. The caller must have
   just waited for the device: an event only reports an elapsed time once both
   of its records completed, and a blocking copy is the one place in this
   runtime where that is guaranteed to have happened. */
void gpu_timing_flush(const NvDriverApi& api) {
    if (!gpu_timer_enabled() || !event_timing_supported(api)) return;
    std::lock_guard<std::mutex> guard(gpu_timing_mutex());
    for (const auto& pending : gpu_pending()) {
        float milliseconds = 0.0f;
        if (api.cuEventElapsedTime(&milliseconds, pending.second.begin, pending.second.end) == 0) {
            GpuKernelTiming& timing = gpu_timing_table()[pending.first];
            const double seconds = static_cast<double>(milliseconds) / 1000.0;
            timing.sampled_seconds += seconds;
            ++timing.samples;
            const std::size_t bucket = thread_bucket(pending.second.threads);
            timing.bucket_seconds[bucket] += seconds;
            ++timing.bucket_samples[bucket];
        }
        api.cuEventDestroy(pending.second.begin);
        api.cuEventDestroy(pending.second.end);
    }
    gpu_pending().clear();
}

/* Prints the accumulated per-kernel device time. The estimate extrapolates
   each kernel's average sample to its full launch count, which is what makes
   the table comparable with the drain totals in [nv-stats]. */
void gpu_timing_report() {
    if (!gpu_timer_enabled()) return;
    struct Row {
        double estimate = 0.0;
        double average = 0.0;
        double sampled = 0.0;
        double average_threads = 0.0;
        unsigned long long launches = 0;
        unsigned long long samples = 0;
        unsigned long long max_threads = 0;
        double bucket_seconds[GpuKernelTiming::kBucketCount] = {0.0, 0.0, 0.0, 0.0};
        unsigned long long bucket_samples[GpuKernelTiming::kBucketCount] = {0, 0, 0, 0};
        std::string name;
    };
    std::vector<Row> rows;
    double total_sampled = 0.0;
    unsigned long long total_samples = 0;
    for (auto& item : gpu_timing_table()) {
        GpuKernelTiming& timing = item.second;
        total_sampled += timing.sampled_seconds;
        total_samples += timing.samples;
        Row row;
        row.samples = timing.samples;
        row.launches = timing.launches;
        row.sampled = timing.sampled_seconds;
        row.max_threads = timing.max_threads;
        row.average_threads =
            row.samples ? timing.sampled_threads / static_cast<double>(row.samples) : 0.0;
        row.average =
            row.samples ? timing.sampled_seconds / static_cast<double>(row.samples) : 0.0;
        row.estimate = row.average * static_cast<double>(timing.launches);
        row.name = item.first;
        for (std::size_t bucket = 0; bucket < GpuKernelTiming::kBucketCount; ++bucket) {
            row.bucket_seconds[bucket] = timing.bucket_seconds[bucket];
            row.bucket_samples[bucket] = timing.bucket_samples[bucket];
        }
        rows.push_back(std::move(row));
    }
    std::sort(rows.begin(), rows.end(), [](const Row& left, const Row& right) {
        return left.estimate > right.estimate;
    });
    std::fprintf(stderr, "[gpu-stats] %-26s %9s %7s %10s %10s %10s %10s %10s\n", "kernel",
                 "launches", "samples", "sampled", "avg ms", "est total",
                 "avg thr", "max thr");
    for (const auto& row : rows) {
        std::fprintf(stderr,
                     "[gpu-stats] %-26s %9llu %7llu %9.3fs %10.3f %9.3fs %10.0f %10llu\n",
                     row.name.c_str(), row.launches,
                     static_cast<unsigned long long>(row.samples), row.sampled,
                     row.average * 1000.0, row.estimate, row.average_threads,
                     row.max_threads);
        std::size_t used = 0;
        for (std::size_t bucket = 0; bucket < GpuKernelTiming::kBucketCount; ++bucket) {
            if (row.bucket_samples[bucket]) ++used;
        }
        if (used > 1) {
            std::fprintf(stderr, "[gpu-stats]   %-24s", "grid threads");
            for (std::size_t bucket = 0; bucket < GpuKernelTiming::kBucketCount; ++bucket) {
                if (!row.bucket_samples[bucket]) continue;
                std::fprintf(stderr, " %.0f+:%llux%.3fs",
                             bucket == 0 ? 0.0 : bucket == 1 ? 4096.0
                                           : bucket == 2 ? 65536.0 : 1048576.0,
                             row.bucket_samples[bucket], row.bucket_seconds[bucket]);
            }
            std::fprintf(stderr, "\n");
        }
    }
    std::fprintf(stderr, "[gpu-stats] %-26s %9s %7llu %9.3fs\n", "TOTAL (sampled)", "",
                 static_cast<unsigned long long>(total_samples), total_sampled);
}

/* Device footprint high-water mark. One place owns the invariant so the
   acquire, recycle and release paths cannot drift apart. */
void note_device_bytes(long long delta) {
    if (delta >= 0) {
        g_stats.device_bytes_owned += static_cast<unsigned long long>(delta);
    } else {
        const unsigned long long decrease = static_cast<unsigned long long>(-delta);
        g_stats.device_bytes_owned =
            g_stats.device_bytes_owned > decrease ? g_stats.device_bytes_owned - decrease : 0;
    }
    if (g_stats.device_bytes_owned > g_stats.device_bytes_peak) {
        g_stats.device_bytes_peak = g_stats.device_bytes_owned;
    }
}

/* Times a blocking copy. The event pair measures the transfer itself; the
   caller compares that with its wall clock to obtain the queue drain, which
   is the part of a read-back that is really "the GPU catching up". */
struct BlockingCopyTimer {
    explicit BlockingCopyTimer(const NvDriverApi& api) : api_(api) {
        if (!stats_enabled() || !event_timing_supported(api_)) return;
        if (api_.cuEventCreate(&begin_, 0) != 0) {
            begin_ = nullptr;
            return;
        }
        if (api_.cuEventCreate(&end_, 0) != 0) {
            api_.cuEventDestroy(begin_);
            begin_ = nullptr;
            return;
        }
        api_.cuEventRecord(begin_, nullptr);
    }
    ~BlockingCopyTimer() { discard(); }
    BlockingCopyTimer(const BlockingCopyTimer&) = delete;
    BlockingCopyTimer& operator=(const BlockingCopyTimer&) = delete;

    /* Seconds the transfer itself took, or 0 when events are unavailable or
       the copy failed. */
    double stop(bool succeeded) {
        if (!begin_) return 0.0;
        float milliseconds = 0.0f;
        double seconds = 0.0;
        /* The record is queued like any other stream command, so the elapsed
           query needs the end event to have completed. The context is already
           idle behind a blocking copy, so this wait costs nothing. */
        const bool recorded = succeeded && api_.cuEventRecord(end_, nullptr) == 0 &&
            (api_.cuEventSynchronize
                 ? api_.cuEventSynchronize(end_) == 0
                 : api_.cuCtxSynchronize() == 0);
        if (recorded && api_.cuEventElapsedTime(&milliseconds, begin_, end_) == 0) {
            seconds = static_cast<double>(milliseconds) / 1000.0;
        }
        discard();
        return seconds;
    }

private:
    void discard() {
        if (!begin_) return;
        api_.cuEventDestroy(begin_);
        if (end_) api_.cuEventDestroy(end_);
        begin_ = nullptr;
        end_ = nullptr;
    }

    const NvDriverApi& api_;
    void* begin_ = nullptr;
    void* end_ = nullptr;
};

/* Splits one blocking copy into the queue drain and the transfer. */
void note_blocking_copy(double wall_seconds, double copy_seconds) {
    if (copy_seconds <= 0.0) return;
    if (wall_seconds > copy_seconds) {
        g_stats.device_busy_seconds += wall_seconds - copy_seconds;
        fsv::opstats::count_busy(wall_seconds - copy_seconds);
    }
    g_stats.transfer_seconds += copy_seconds;
    ++g_stats.timed_transfers;
}

/* Idle device buffers, bucketed by the rounded-up size they can serve.

   A decode loop reads a longer sequence every step, so the size of nearly
   every intermediate differs from the step before: an exact-fit lookup missed
   and paid a cuMemAlloc/cuMemFree pair — a context stall, not a cheap call —
   for almost every tensor it produced. Handing out the smallest buffer that
   still fits turns those twelve thousand stalls a synthesis into one per shape
   class, and the oversized buffer goes back to its own bucket afterwards. */
std::map<std::size_t, std::vector<NvPtr>> g_pool;
std::size_t g_pool_bytes = 0;
const std::size_t kPoolLimitBytes = 768ull << 20;
/* Parked buffers are a cache, not a working set.  A card that cannot hold the
   weights plus that cache refuses the first allocation it cannot serve, and a
   refusal used to push every following node onto the host interpreter: correct
   output, a hundred times slower, and completely silent.  Handing the cache
   back frees memory the graph actually needs, and the smaller limit keeps it
   from filling up again. */
const std::size_t kPoolPressureLimitBytes = 96ull << 20;
std::size_t g_pool_limit = kPoolLimitBytes;
bool g_alloc_failure = false;
const int kAllocFailureLimit = 8;
int g_alloc_streak = 0;
bool g_device_abandoned = false;

std::size_t pool_bucket(std::size_t bytes) {
    const std::size_t granularity = 1024;
    return ((bytes + granularity - 1) / granularity) * granularity;
}

/* Page-locked bounce buffers for host/device copies.

   ``cuMemcpyHtoD`` from pageable memory performs a stream synchronisation
   before the copy, so the host interpreter stalls on every node that feeds a
   tensor to the device. Staging through page-locked memory and using the
   asynchronous entry point instead keeps the copy ordered on the default
   stream without blocking the host, which lets it run ahead of the device.
   A staged buffer is reusable once the stream has passed the copy that read
   from it, so the pool is only reclaimed when the stream drains or when it
   would otherwise grow past its cap. */
struct StagingBuffer {
    void* pointer = nullptr;
    std::size_t capacity = 0;
    bool pending = false;
};

std::vector<StagingBuffer> g_staging;
std::size_t g_staging_bytes = 0;
const std::size_t kStagingLimitBytes = 128ull << 20;
const unsigned kMemHostAllocPortable = 0x01;

bool staging_supported(const NvDriverApi& api) {
    return api.cuMemHostAlloc && api.cuMemFreeHost && api.cuMemcpyHtoDAsync &&
           api.cuStreamQuery && api.cuStreamSynchronize;
}

/* Every queued copy has completed, so no buffer is still being read. */
void staging_retire_all() {
    for (StagingBuffer& buffer : g_staging) buffer.pending = false;
}

bool stream_idle(const NvDriverApi& api) {
    return api.cuStreamQuery(nullptr) == 0;
}

StagingBuffer* staging_allocate(const NvDriverApi& api, std::size_t bytes) {
    const std::size_t wanted = pool_bucket(bytes);
    void* pointer = nullptr;
    if (api.cuMemHostAlloc(&pointer, wanted, kMemHostAllocPortable) != 0 || !pointer) {
        return nullptr;
    }
    StagingBuffer buffer;
    buffer.pointer = pointer;
    buffer.capacity = wanted;
    g_staging.push_back(buffer);
    g_staging_bytes += wanted;
    return &g_staging.back();
}

/* Hands out a page-locked buffer of at least ``bytes``, or 0 when the driver
   cannot provide one and the caller must fall back to a synchronous copy. */
void* staging_acquire(const NvDriverApi& api, std::size_t bytes) {
    if (!staging_supported(api)) return nullptr;
    if (stream_idle(api)) staging_retire_all();
    for (StagingBuffer& buffer : g_staging) {
        if (!buffer.pending && buffer.capacity >= bytes) {
            buffer.pending = true;
            return buffer.pointer;
        }
    }
    if (g_staging_bytes + pool_bucket(bytes) > kStagingLimitBytes) {
        /* The pool is at its cap and everything in it is still feeding a queued
           copy: wait for the device once and reuse what is already pinned. */
        if (api.cuStreamSynchronize(nullptr) == 0) staging_retire_all();
        for (StagingBuffer& buffer : g_staging) {
            if (!buffer.pending && buffer.capacity >= bytes) {
                buffer.pending = true;
                return buffer.pointer;
            }
        }
    }
    StagingBuffer* buffer = staging_allocate(api, bytes);
    if (!buffer) return nullptr;
    buffer->pending = true;
    return buffer->pointer;
}

struct StatsReporter {
    ~StatsReporter() {
        gpu_timing_report();
        if (!stats_enabled()) return;
        const NvStats& stats = g_stats;
        double accounted = stats.allocate_seconds + stats.upload_seconds +
                           stats.download_seconds + stats.launch_seconds;
        std::fprintf(stderr,
                     "[nv-stats] launch=%llu %.2fs | alloc=%llu (pool hit %llu) %.2fs |"
                     " upload=%llu %.2f GB %.2fs | download=%llu %.2f GB %.2fs |"
                     " alloc fail=%llu abandon=%llu | driver total %.2fs\n",
                     stats.launches, stats.launch_seconds,
                     stats.allocations, stats.pool_hits, stats.allocate_seconds,
                     stats.uploads,
                     static_cast<double>(stats.upload_bytes) / 1073741824.0,
                     stats.upload_seconds,
                     stats.downloads,
                     static_cast<double>(stats.download_bytes) / 1073741824.0,
                     stats.download_seconds, stats.allocation_failures,
                     stats.abandonments, accounted);
        /* The blocking copies are the only place the host waits for the
           device, so their wall clock is split: busy is the queue draining,
           transfer is the bytes moving. Peak is the runtime's own footprint,
           not what the card reports for the whole system. */
        std::fprintf(stderr,
                     "[nv-stats] blocking copies=%llu busy=%.2fs transfer=%.2fs |"
                     " device bytes owned=%.1f MB peak=%.1f MB\n",
                     stats.timed_transfers, stats.device_busy_seconds,
                     stats.transfer_seconds,
                     static_cast<double>(stats.device_bytes_owned) / 1048576.0,
                     static_cast<double>(stats.device_bytes_peak) / 1048576.0);
    }
} g_stats_reporter;

struct StagingReleaser {
    ~StagingReleaser() {
        const NvDriverApi& api = NvRuntime::instance().api();
        if (!api.cuMemFreeHost) return;
        for (StagingBuffer& buffer : g_staging) {
            if (buffer.pointer) api.cuMemFreeHost(buffer.pointer);
        }
        g_staging.clear();
        g_staging_bytes = 0;
    }
} g_staging_releaser;

HMODULE load_driver_module() {
    HMODULE module = ::LoadLibraryW(L"nvcuda.dll");
    if (!module) {
        module = ::LoadLibraryW(L"nvcuda");
    }
    return module;
}

/* First entry point a loaded nvcuda.dll did not export. A driver too old for
   this runtime used to be reported as "a driver API entry point is missing",
   which leaves the user nothing to act on; naming it turns the same failure
   into a diagnosis. Every call site passes a string literal, so the pointer
   stays valid for the process lifetime. */
const char* g_missing_symbol = nullptr;

template <typename T>
bool resolve(HMODULE module, const char* name, T& target) {
    std::string symbol(name);
    FARPROC address = ::GetProcAddress(module, symbol.c_str());
    if (!address) {
        /* Versioned entry points (cuMemAlloc_v2, cuCtxCreate_v2, ...). */
        address = ::GetProcAddress(module, (symbol + "_v2").c_str());
    }
    if (!address) {
        if (!g_missing_symbol) g_missing_symbol = name;
        return false;
    }
    target = reinterpret_cast<T>(address);
    return true;
}

std::string describe(const NvDriverApi& api, int status) {
    const char* name = nullptr;
    const char* text = nullptr;
    if (api.cuGetErrorName) api.cuGetErrorName(status, &name);
    if (api.cuGetErrorString) api.cuGetErrorString(status, &text);
    char buffer[256];
    std::snprintf(buffer, sizeof(buffer), "%s (%s, code %d)",
                  text ? text : "CUDA driver error", name ? name : "unknown", status);
    return std::string(buffer);
}

/* ---------------------------------------------------------------------------
   Device selection and the memory budget it implies.

   Picking a card used to mean "highest compute capability wins", with no look
   at memory at all. On a machine with more than one card that is the wrong
   question: a 2 GiB card outranks a 12 GiB one that can actually hold the
   working set, so the runtime chose the one card that was guaranteed to run
   out of memory on the first big allocation. The rules are now:

     * an explicitly requested index wins (requested_device_index);
     * otherwise the highest compute capability among the cards that can hold
       the working set wins, with memory and then index breaking ties;
     * a card below kMinDeviceMemoryBytes is not chosen by default, and the
       refusal says which cards were seen and how big they are, because
       "your card is too small" and "no NVIDIA card found" need different
       answers from the user.
   --------------------------------------------------------------------------- */

/* A synthesis peaks around 2.7 GiB resident on the 4 GiB laptop card this was
   tuned on, so a card that reports less than 3 GiB cannot hold it even with
   nothing else running. Such a card still works as a forced choice for
   experiments, it just must not be what the switch picks by itself. */
const std::size_t kMinDeviceMemoryBytes = 3ull << 30;

/* Result of the selection above, kept for diagnostics: which card the runtime
   settled on and the description to print for it. */
int g_active_device = -1;
std::string g_active_device_text;

/* Ceiling the recycle pool drops to after the first refused allocation. It is
   a share of the card too (see below), because a fixed 96 MiB is a twentieth
   of the 4 GiB card it was tuned on. */
std::size_t g_pool_pressure_limit = kPoolPressureLimitBytes;

/* The recycle pool is a cache, so its ceiling is a share of the card rather
   than a fixed slice: 768 MiB is a fifth of a 4 GiB card and a quarter of a
   3 GiB one. The constants stay as the ceiling for the large cards. */
std::size_t pool_limit_for_memory(std::size_t total_bytes) {
    if (!total_bytes) return kPoolLimitBytes;
    const std::size_t scaled = total_bytes / 5;
    const std::size_t floor = 96ull << 20;
    if (scaled < floor) return floor;
    return scaled > kPoolLimitBytes ? kPoolLimitBytes : scaled;
}

std::size_t pool_pressure_limit_for_memory(std::size_t total_bytes) {
    if (!total_bytes) return kPoolPressureLimitBytes;
    const std::size_t scaled = total_bytes / 40;
    const std::size_t floor = 24ull << 20;
    if (scaled < floor) return floor;
    return scaled > kPoolPressureLimitBytes ? kPoolPressureLimitBytes : scaled;
}

/* Card the user asked for: -1 automatic, -2 switched off, -3 unreadable, and
   anything else is an index. An index is obeyed exactly, including on a card
   the automatic rules would have skipped, because forcing one is what the
   switch is for.

   CUDA_VISIBLE_DEVICES is deliberately not read here. The driver already acts
   on it, so the device list this runtime enumerates is the filtered one, and
   applying its value as an index a second time would re-map a list that was
   already remapped. Measured on the development machine: with one card present,
   CUDA_VISIBLE_DEVICES=7 makes cuInit fail with CUDA_ERROR_NO_DEVICE, and
   CUDA_VISIBLE_DEVICES=0 behaves exactly like leaving it unset. */
int requested_device_index() {
    static const int requested = [] {
        const char* raw = std::getenv("AEMEATH_CUDA_VOICE_DEVICE");
        if (!raw || !*raw) return -1;
        if (std::strcmp(raw, "none") == 0 || std::strcmp(raw, "-1") == 0) return -2;
        char* end = nullptr;
        const long value = std::strtol(raw, &end, 10);
        if (end == raw || *end != '\0' || value < 0) return -3;
        return static_cast<int>(value);
    }();
    return requested;
}

/* "NVIDIA GeForce RTX 3050 Laptop GPU (8.6, 4.0 GiB)": every diagnostic that
   mentions a card uses this, so a report says which card was looked at rather
   than only that something failed. */
std::string describe_device(const NvDriverApi& api, int index) {
    char name[256] = "";
    int major = 0;
    int minor = 0;
    std::size_t total = 0;
    NvDevice device = 0;
    if (api.cuDeviceGet && api.cuDeviceGet(&device, index) == 0) {
        if (api.cuDeviceGetName) api.cuDeviceGetName(name, sizeof(name), device);
        if (api.cuDeviceGetAttribute) {
            api.cuDeviceGetAttribute(&major, kAttrComputeCapabilityMajor, device);
            api.cuDeviceGetAttribute(&minor, kAttrComputeCapabilityMinor, device);
        }
        if (api.cuDeviceTotalMem) api.cuDeviceTotalMem(&total, device);
    }
    char buffer[384];
    std::snprintf(buffer, sizeof(buffer), "%s (%d.%d, %.1f GiB)",
                  name[0] ? name : "NVIDIA GPU", major, minor,
                  static_cast<double>(total) / 1073741824.0);
    return std::string(buffer);
}

/* "12.6" from the encoded driver version (major * 1000 + minor * 10). */
std::string driver_version_text(const NvDriverApi& api) {
    int version = 0;
    if (!api.cuDriverGetVersion || api.cuDriverGetVersion(&version) != 0 || version <= 0) {
        return "未知";
    }
    char buffer[32];
    std::snprintf(buffer, sizeof(buffer), "%d.%d", version / 1000, (version % 1000) / 10);
    return std::string(buffer);
}

/* Every card the driver reports, on one line. A refusal has to list what was
   found or the user cannot tell a missing driver from a card that is too
   small. */
std::string list_devices(const NvDriverApi& api, int count) {
    std::string listing;
    for (int index = 0; index < count; ++index) {
        if (!listing.empty()) listing += "、";
        listing += std::to_string(index) + "=" + describe_device(api, index);
    }
    return listing;
}

}  // namespace

const char* nv_last_error() { return g_error; }

void nv_set_error(const std::string& text) {
    std::snprintf(g_error, sizeof(g_error), "%s", text.c_str());
}

void nv_set_error(const char* text) {
    std::snprintf(g_error, sizeof(g_error), "%s", text ? text : "");
}

bool nv_take_alloc_failure() {
    const bool failed = g_alloc_failure;
    g_alloc_failure = false;
    return failed;
}

bool nv_device_abandoned() { return g_device_abandoned; }

void nv_reset_device_abandoned() {
    g_device_abandoned = false;
    g_alloc_streak = 0;
}

NvRuntime& NvRuntime::instance() {
    /* Never destroyed. The teardown helpers below (staging release, statistics
       report) run from static destructors that are constructed before this
       object is first used, so an ordinary function-local static would already
       be gone by the time they dereference it. Nothing here owns the context's
       lifetime: the driver reclaims it when the process exits. */
    static NvRuntime* runtime = new NvRuntime();
    return *runtime;
}

bool NvRuntime::bind(std::string& error) {
    HMODULE module = load_driver_module();
    if (!module) {
        error = "没有找到 nvcuda.dll：请确认已安装 NVIDIA 显卡驱动";
        return false;
    }
    NvDriverApi& a = api_;
    bool ok = resolve(module, "cuInit", a.cuInit) &&
              resolve(module, "cuDriverGetVersion", a.cuDriverGetVersion) &&
              resolve(module, "cuDeviceGetCount", a.cuDeviceGetCount) &&
              resolve(module, "cuDeviceGet", a.cuDeviceGet) &&
              resolve(module, "cuDeviceGetName", a.cuDeviceGetName) &&
              resolve(module, "cuDeviceGetAttribute", a.cuDeviceGetAttribute) &&
              resolve(module, "cuDeviceTotalMem", a.cuDeviceTotalMem) &&
              resolve(module, "cuCtxCreate", a.cuCtxCreate) &&
              resolve(module, "cuCtxDestroy", a.cuCtxDestroy) &&
              resolve(module, "cuCtxSetCurrent", a.cuCtxSetCurrent) &&
              resolve(module, "cuCtxGetCurrent", a.cuCtxGetCurrent) &&
              resolve(module, "cuCtxSynchronize", a.cuCtxSynchronize) &&
              resolve(module, "cuModuleLoadData", a.cuModuleLoadData) &&
              resolve(module, "cuModuleUnload", a.cuModuleUnload) &&
              resolve(module, "cuModuleGetFunction", a.cuModuleGetFunction) &&
              resolve(module, "cuMemAlloc", a.cuMemAlloc) &&
              resolve(module, "cuMemFree", a.cuMemFree) &&
              resolve(module, "cuMemGetInfo", a.cuMemGetInfo) &&
              resolve(module, "cuMemcpyHtoD", a.cuMemcpyHtoD) &&
              resolve(module, "cuMemcpyDtoH", a.cuMemcpyDtoH) &&
              resolve(module, "cuStreamCreate", a.cuStreamCreate) &&
              resolve(module, "cuStreamDestroy", a.cuStreamDestroy) &&
              resolve(module, "cuStreamSynchronize", a.cuStreamSynchronize) &&
              resolve(module, "cuLaunchKernel", a.cuLaunchKernel);
    if (!ok) {
        error = std::string("nvcuda.dll 版本过旧：缺少驱动 API 入口 ") +
                (g_missing_symbol ? g_missing_symbol : "unknown") +
                "，请升级显卡驱动";
        return false;
    }
    a.cuGetErrorName = nullptr;
    a.cuGetErrorString = nullptr;
    resolve(module, "cuGetErrorName", a.cuGetErrorName);
    resolve(module, "cuGetErrorString", a.cuGetErrorString);
    resolve(module, "cuMemHostAlloc", a.cuMemHostAlloc);
    resolve(module, "cuMemFreeHost", a.cuMemFreeHost);
    resolve(module, "cuMemcpyHtoDAsync", a.cuMemcpyHtoDAsync);
    resolve(module, "cuStreamQuery", a.cuStreamQuery);
    resolve(module, "cuEventCreate", a.cuEventCreate);
    resolve(module, "cuEventRecord", a.cuEventRecord);
    resolve(module, "cuEventElapsedTime", a.cuEventElapsedTime);
    resolve(module, "cuEventSynchronize", a.cuEventSynchronize);
    resolve(module, "cuEventDestroy", a.cuEventDestroy);
    a.loaded = true;
    return true;
}

/* Driver only: no device, no context. Enumerating what a machine has must keep
   working when none of its cards is usable, otherwise "no usable device"
   cannot be explained. Caller holds g_mutex. */
bool NvRuntime::driver_ready(std::string& error) {
    if (!api_.loaded && !bind(error)) {
        init_error_ = error;
        return false;
    }
    const int status = api_.cuInit(0);
    if (status != 0) {
        error = "初始化 CUDA 驱动失败：" + describe(api_, status);
        init_error_ = error;
        return false;
    }
    return true;
}

bool NvRuntime::current_context(std::string& error) {
    if (!context_) {
        error = "CUDA 上下文尚未创建";
        return false;
    }
    void* current = nullptr;
    if (api_.cuCtxGetCurrent(&current) == 0 && current == context_) {
        return true;
    }
    int status = api_.cuCtxSetCurrent(context_);
    if (status != 0) {
        error = "切换 CUDA 上下文失败：" + describe(api_, status);
        return false;
    }
    return true;
}

bool NvRuntime::initialize(std::string& error) {
    /* Every kernel launch and every copy calls this, so the already-initialised
       case must not contend for the lock the slow path needs. */
    if (initialized_) return true;
    std::lock_guard<std::mutex> guard(g_mutex);
    if (initialized_) return true;
    if (!driver_ready(error)) return false;
    int count = 0;
    if (api_.cuDeviceGetCount(&count) != 0 || count <= 0) {
        error = "没有检测到可用的 NVIDIA 设备";
        init_error_ = error;
        return false;
    }
    const int requested = requested_device_index();
    if (requested == -2) {
        error = "自研 CUDA 推理端已被 AEMEATH_CUDA_VOICE_DEVICE 关闭";
        init_error_ = error;
        return false;
    }
    if (requested == -3) {
        error = "AEMEATH_CUDA_VOICE_DEVICE 的值无效：需要设备序号，或 none 表示关闭";
        init_error_ = error;
        return false;
    }
    int best = -1;
    int best_score = -1;
    std::size_t best_memory = 0;
    int largest = -1;
    std::size_t largest_memory = 0;
    for (int index = 0; index < count; ++index) {
        NvDevice device = 0;
        if (api_.cuDeviceGet(&device, index) != 0) continue;
        int major = 0;
        int minor = 0;
        api_.cuDeviceGetAttribute(&major, kAttrComputeCapabilityMajor, device);
        api_.cuDeviceGetAttribute(&minor, kAttrComputeCapabilityMinor, device);
        std::size_t total = 0;
        api_.cuDeviceTotalMem(&total, device);
        if (requested >= 0) {
            /* The explicit switch also overrides the memory floor: forcing a
               small card is what it is for. */
            if (index == requested) {
                best = index;
                best_memory = total;
            }
            continue;
        }
        if (total < kMinDeviceMemoryBytes) {
            if (total > largest_memory) {
                largest_memory = total;
                largest = index;
            }
            continue;
        }
        const int score = major * 100 + minor;
        if (score > best_score || (score == best_score && total > best_memory)) {
            best_score = score;
            best = index;
            best_memory = total;
        }
    }
    if (best < 0) {
        if (requested >= 0) {
            error = "指定的 CUDA 设备 " + std::to_string(requested) + " 不可用：驱动报告 " +
                    std::to_string(count) + " 个设备（" + list_devices(api_, count) + "）";
        } else {
            error = "没有显存足够的 NVIDIA 设备（合成需要 3.0 GiB 以上）：驱动报告 " +
                    std::to_string(count) + " 个设备（" + list_devices(api_, count) + "）";
            if (largest >= 0) {
                error += "；可用 AEMEATH_CUDA_VOICE_DEVICE=" + std::to_string(largest) +
                         " 强制使用显存最大的那块卡";
            }
        }
        init_error_ = error;
        return false;
    }
    /* A refused context or PTX load on a chosen card is the failure a user
       reports most often ("我的卡用不了"), so every one of these messages says
       which card, which driver and which PTX target was involved. */
    const std::string device_text = describe_device(api_, best);
    g_active_device = best;
    g_active_device_text = device_text;
    g_pool_limit = pool_limit_for_memory(best_memory);
    g_pool_pressure_limit = pool_pressure_limit_for_memory(best_memory);
    NvDevice device = 0;
    if (api_.cuDeviceGet(&device, best) != 0) {
        error = "无法打开 CUDA 设备：" + device_text;
        init_error_ = error;
        return false;
    }
    int status = api_.cuCtxCreate(&context_, 0, device);
    if (status != 0) {
        error = "在 " + device_text + " 上创建 CUDA 上下文失败：" + describe(api_, status);
        init_error_ = error;
        return false;
    }
    status = api_.cuModuleLoadData(&module_, fsv_ptx_kernels_source);
    if (status != 0) {
        error = "在 " + device_text + " 上加载自研 PTX 内核失败（驱动 CUDA " +
                driver_version_text(api_) + "，内核 PTX 目标 " + FSV_PTX_ARCH_TARGET +
                "）：" + describe(api_, status) +
                "；内核是 PTX 8.5，需要 555 系及以上驱动，请升级显卡驱动";
        api_.cuCtxDestroy(context_);
        context_ = nullptr;
        init_error_ = error;
        return false;
    }
    initialized_ = true;
    return true;
}

bool NvRuntime::kernel(const char* name, void** function, std::string& error) {
    if (!name || !function) {
        error = "无效的内核名称";
        return false;
    }
    if (void* resolved = cached_function(name)) {
        *function = resolved;
        g_kernel_name = name;
        return true;
    }
    if (!initialize(error)) return false;
    std::lock_guard<std::mutex> guard(g_mutex);
    if (void* resolved = cached_function(name)) {
        *function = resolved;
        g_kernel_name = name;
        return true;
    }
    if (!current_context(error)) return false;
    void* entry = nullptr;
    int status = api_.cuModuleGetFunction(&entry, module_, name);
    if (status != 0) {
        error = std::string("PTX 中不存在内核 ") + name + "：" + describe(api_, status);
        return false;
    }
    g_functions.emplace(name, entry);
    store_function(name, entry);
    *function = entry;
    g_kernel_name = name;
    return true;
}

bool NvRuntime::allocate(std::size_t bytes, NvPtr& pointer, std::string& error) {
    if (!initialize(error)) return false;
    if (!current_context(error)) return false;
    NvPtr address = 0;
    int status = api_.cuMemAlloc(&address, bytes ? bytes : 1);
    if (status != 0) {
        char buffer[256];
        std::snprintf(buffer, sizeof(buffer), "显存分配 %.2f MB 失败：%s",
                      static_cast<double>(bytes) / 1048576.0, describe(api_, status).c_str());
        error = buffer;
        return false;
    }
    pointer = address;
    return true;
}

void NvRuntime::release(NvPtr pointer, std::size_t bytes) {
    if (!pointer) return;
    if (bytes) note_device_bytes(-static_cast<long long>(pool_bucket(bytes)));
    api_.cuMemFree(pointer);
}

NvStats& nv_stats() { return g_stats; }

bool NvRuntime::acquire(std::size_t bytes, NvPtr& pointer, std::string& error) {
    if (!initialize(error)) return false;
    if (!current_context(error)) return false;
    /* Already given up for this run: refuse without touching the driver, so the
       remaining nodes go straight to the host implementation. */
    if (g_device_abandoned) {
        error = "显存不足：本次推理已整体切回主机执行";
        return false;
    }
    const std::size_t bucket = pool_bucket(bytes ? bytes : 1);
    auto iterator = g_pool.lower_bound(bucket);
    if (iterator != g_pool.end() && !iterator->second.empty()) {
        pointer = iterator->second.back();
        iterator->second.pop_back();
        g_pool_bytes -= iterator->first;
        if (iterator->second.empty()) g_pool.erase(iterator);
        ++g_stats.pool_hits;
        g_alloc_streak = 0;
        return true;
    }
    const double started = now_seconds();
    NvPtr address = 0;
    int status = api_.cuMemAlloc(&address, bucket);
    if (status != 0) {
        drain_pool();
        g_pool_limit = g_pool_pressure_limit;
        status = api_.cuMemAlloc(&address, bucket);
    }
    g_stats.allocate_seconds += now_seconds() - started;
    if (status != 0) {
        g_alloc_failure = true;
        ++g_stats.allocation_failures;
        if (++g_alloc_streak >= kAllocFailureLimit && !g_device_abandoned) {
            g_device_abandoned = true;
            ++g_stats.abandonments;
            std::fprintf(stderr,
                         "[fsv-cuda] 显存连续 %d 次分配失败，本次推理整体切回 CPU 执行"
                         "（不再逐节点回退）。\n",
                         kAllocFailureLimit);
        }
        char buffer[256];
        std::snprintf(buffer, sizeof(buffer), "显存分配 %.2f MB 失败：%s",
                      static_cast<double>(bucket) / 1048576.0, describe(api_, status).c_str());
        error = buffer;
        return false;
    }
    ++g_stats.allocations;
    g_alloc_streak = 0;
    note_device_bytes(static_cast<long long>(bucket));
    pointer = address;
    return true;
}

void NvRuntime::recycle(NvPtr pointer, std::size_t bytes) {
    if (!pointer) return;
    const std::size_t bucket = pool_bucket(bytes ? bytes : 1);
    if (g_pool_bytes + bucket <= g_pool_limit) {
        g_pool[bucket].push_back(pointer);
        g_pool_bytes += bucket;
        return;
    }
    release(pointer, bytes);
}

void NvRuntime::drain_pool() {
    for (auto& entry : g_pool) {
        for (NvPtr pointer : entry.second) release(pointer, entry.first);
        entry.second.clear();
    }
    g_pool_bytes = 0;
}

bool NvRuntime::upload(NvPtr destination, const void* source, std::size_t bytes,
                       std::string& error) {
    if (!initialize(error)) return false;
    if (!current_context(error)) return false;
    if (!bytes) return true;
    const double started = now_seconds();
    int status = -1;
    double copy_seconds = 0.0;
    void* staging = staging_acquire(api_, bytes);
    if (staging) {
        std::memcpy(staging, source, bytes);
        /* Queued on the default stream, in order with the kernels that read it;
           the buffer stays marked pending until the stream passes the copy. */
        status = api_.cuMemcpyHtoDAsync(destination, staging, bytes, nullptr);
    } else {
        /* Pageable memory: this copy is a stream synchronisation, so it waits
           exactly like a read-back does and is timed the same way. */
        BlockingCopyTimer copy_timer(api_);
        status = api_.cuMemcpyHtoD(destination, source, bytes);
        copy_seconds = copy_timer.stop(status == 0);
    }
    const double wall = now_seconds() - started;
    g_stats.upload_seconds += wall;
    if (status != 0) {
        error = "上传显存失败：" + describe(api_, status);
        return false;
    }
    if (!staging) note_blocking_copy(wall, copy_seconds);
    ++g_stats.uploads;
    g_stats.upload_bytes += bytes;
    /* The driver is the only place that sees every host-to-device transfer, so
       it is the only place that can charge each one to the operator that asked
       for it. Counting inside the callers missed the weight uploads of the
       prepare phase and one of the two upload paths in the graph runtime. */
    opstats::count_upload(bytes);
    /* A blocking upload waited for the queue as well, so the samples recorded
       before it can be read back here. */
    if (!staging) gpu_timing_flush(api_);
    return true;
}

bool NvRuntime::download(void* destination, NvPtr source, std::size_t bytes,
                         std::string& error) {
    if (!initialize(error)) return false;
    if (!current_context(error)) return false;
    if (!bytes) return true;
    const double started = now_seconds();
    /* A read-back is where the queue drains, so its cost is the wait rather than
       the bytes. Copying straight into the caller's buffer gets that ordering
       from one driver call; staging the bytes through a pinned buffer first
       measured four times slower, so only writes use the pool. This returns
       only once every queued write has completed, which is what makes it safe
       to hand the staging buffers those writes were reading from back out. */
    BlockingCopyTimer copy_timer(api_);
    const int status = api_.cuMemcpyDtoH(destination, source, bytes);
    const double wall = now_seconds() - started;
    const double copy_seconds = copy_timer.stop(status == 0);
    g_stats.download_seconds += wall;
    if (status != 0) {
        error = "回读显存失败：" + describe(api_, status);
        return false;
    }
    note_blocking_copy(wall, copy_seconds);
    staging_retire_all();
    ++g_stats.downloads;
    g_stats.download_bytes += bytes;
    opstats::count_download(bytes);
    gpu_timing_flush(api_);
    return true;
}

bool NvRuntime::synchronize(std::string& error) {
    if (!initialize(error)) return false;
    if (!current_context(error)) return false;
    int status = api_.cuCtxSynchronize();
    if (status != 0) {
        error = "等待核函数完成失败：" + describe(api_, status);
        return false;
    }
    staging_retire_all();
    return true;
}

bool NvRuntime::launch(void* function, unsigned grid_x, unsigned grid_y, unsigned grid_z,
                       unsigned block_x, unsigned block_y, unsigned block_z,
                       unsigned shared_bytes, void** arguments, std::string& error) {
    if (!initialize(error)) return false;
    if (!current_context(error)) return false;
    const double started = now_seconds();
    bool timed = false;
    const unsigned long long threads = static_cast<unsigned long long>(grid_x) * grid_y *
                                       grid_z * block_x * block_y * block_z;
    const GpuSample sample = gpu_sample_begin(api_, threads, timed);
    int status = api_.cuLaunchKernel(function, grid_x, grid_y, grid_z,
                                     block_x, block_y, block_z,
                                     shared_bytes, nullptr, arguments, nullptr);
    if (timed) gpu_sample_end(api_, sample);
    if (status != 0) {
        error = "启动核函数失败：" + describe(api_, status);
        return false;
    }
    /* Queued, not waited for. A graph that keeps its tensors on the device
       issues thousands of kernels, and a context-wide synchronize after every
       one of them leaves the GPU idle between launches. Everything runs on the
       default stream, so a later copy or kernel is ordered after this one and
       the device results are complete by the time anyone reads them back.
       Failures are reported by the next synchronize() or download. */
    g_stats.launch_seconds += now_seconds() - started;
    ++g_stats.launches;
    opstats::count_launch();
    return true;
}

int NvRuntime::device_count() {
    std::string error;
    if (!initialize(error)) return -1;
    int count = 0;
    if (api_.cuDeviceGetCount(&count) != 0) return -1;
    return count;
}

bool NvRuntime::device_info(int index, int* major, int* minor, std::size_t* memory,
                            char* name, std::size_t name_size) {
    std::string error;
    {
        /* Enumerating the cards has to work even when none of them is usable:
           that is exactly the case the caller needs described. */
        std::lock_guard<std::mutex> guard(g_mutex);
        if (!driver_ready(error)) {
            nv_set_error(error);
            return false;
        }
    }
    NvDevice device = 0;
    int total_devices = 0;
    if (api_.cuDeviceGetCount(&total_devices) == 0 &&
        (index < 0 || index >= total_devices)) {
        nv_set_error("设备序号 " + std::to_string(index) + " 超范围（共 " +
                     std::to_string(total_devices) + " 个设备）");
        return false;
    }
    int status = api_.cuDeviceGet(&device, index);
    if (status != 0) {
        nv_set_error(describe(api_, status));
        return false;
    }
    int capability_major = 0;
    int capability_minor = 0;
    api_.cuDeviceGetAttribute(&capability_major, kAttrComputeCapabilityMajor, device);
    api_.cuDeviceGetAttribute(&capability_minor, kAttrComputeCapabilityMinor, device);
    std::size_t total = 0;
    api_.cuDeviceTotalMem(&total, device);
    char device_name[256] = "";
    api_.cuDeviceGetName(device_name, sizeof(device_name), device);
    if (major) *major = capability_major;
    if (minor) *minor = capability_minor;
    if (memory) *memory = total;
    if (name && name_size) {
        std::snprintf(name, name_size, "%s", device_name);
    }
    return true;
}

int NvRuntime::driver_version() {
    std::string error;
    {
        /* A driver version is a property of the driver, not of a card, so it
           is still answerable when device selection refuses every card. */
        std::lock_guard<std::mutex> guard(g_mutex);
        if (!driver_ready(error)) return 0;
    }
    int version = 0;
    if (api_.cuDriverGetVersion(&version) != 0) return 0;
    return version;
}

int nv_active_device(char* name, std::size_t name_size) {
    std::string error;
    if (!NvRuntime::instance().initialize(error)) {
        nv_set_error(error);
        return -1;
    }
    if (name && name_size) std::snprintf(name, name_size, "%s", g_active_device_text.c_str());
    return g_active_device;
}

}  // namespace fsv
