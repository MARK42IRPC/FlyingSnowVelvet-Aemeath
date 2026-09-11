#include "fsv_opstats.h"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <functional>
#include <map>
#include <mutex>
#include <string>
#include <vector>

namespace fsv {
namespace opstats {
namespace {

struct Profile {
    double seconds = 0.0;
    unsigned long long calls = 0;
};

struct Counters {
    unsigned long long declines = 0;
    unsigned long long uploads = 0;
    unsigned long long downloads = 0;
    unsigned long long upload_bytes = 0;
    unsigned long long download_bytes = 0;
    unsigned long long launches = 0;
    double busy_seconds = 0.0;
};

/* The tables and their lock are deliberately never destroyed.

   They are built on first use, which happens long after the flusher below has
   been constructed, so ordinary static destruction would run the flusher
   after the tables were gone: the report then walks freed map nodes and prints
   the same empty row over and over. Leaking three small objects for the life
   of the process keeps the exit-time report well defined. */
std::map<std::string, Profile>& profile_table() {
    static std::map<std::string, Profile>* table = new std::map<std::string, Profile>();
    return *table;
}

std::map<std::string, Counters>& counter_table() {
    static std::map<std::string, Counters>* table = new std::map<std::string, Counters>();
    return *table;
}

/* The graph runtime can run graphs from more than one thread, so the tables
   are guarded. The enabled flag keeps the hot path free of the lock when the
   diagnostic is off, which is the default. */
std::mutex& table_mutex() {
    static std::mutex* mutex = new std::mutex();
    return *mutex;
}

thread_local const char* g_current = "";

bool stats_enabled() {
    static const bool enabled = std::getenv("FSV_NATIVE_OPSTATS") != nullptr;
    return enabled;
}

Counters& current_counters() {
    std::lock_guard<std::mutex> guard(table_mutex());
    return counter_table()[g_current ? g_current : ""];
}

}  // namespace

bool enabled() { return stats_enabled(); }

void set_current(const char* name) { g_current = name ? name : ""; }

const char* current() { return g_current; }

void count_decline() {
    if (!stats_enabled()) return;
    ++current_counters().declines;
}

void count_upload(std::size_t bytes) {
    if (!stats_enabled() || !bytes) return;
    Counters& counters = current_counters();
    ++counters.uploads;
    counters.upload_bytes += bytes;
}

void count_download(std::size_t bytes) {
    if (!stats_enabled() || !bytes) return;
    Counters& counters = current_counters();
    ++counters.downloads;
    counters.download_bytes += bytes;
}

void count_busy(double seconds) {
    if (!stats_enabled() || seconds <= 0.0) return;
    current_counters().busy_seconds += seconds;
}

void count_launch() {
    if (!stats_enabled()) return;
    ++current_counters().launches;
}

Scope::Scope(const char* name, const char* detail)
    : previous_(g_current), name_(name ? name : ""), detail_(detail),
      start_(std::chrono::steady_clock::now()), active_(stats_enabled()) {
    g_current = name_;
}

Scope::~Scope() {
    g_current = previous_;
    if (!active_) return;
    const double seconds =
        std::chrono::duration<double>(std::chrono::steady_clock::now() - start_).count();
    /* Opt-in per-node outlier log: the aggregate table says which operator is
       expensive, this says which of its invocations is and how big its inputs
       were, which is what a fix needs to know. */
    static const double threshold = [] {
        const char* text = std::getenv("FSV_NATIVE_SLOW_MS");
        return text ? std::atof(text) / 1000.0 : 0.0;
    }();
    if (threshold > 0.0 && seconds >= threshold) {
        std::fprintf(stderr, "[op-slow] %9.3fms %s%s%s\n", seconds * 1000.0, name_,
                     detail_ ? " " : "", detail_ ? detail_ : "");
    }
    std::lock_guard<std::mutex> guard(table_mutex());
    Profile& profile = profile_table()[name_];
    profile.seconds += seconds;
    ++profile.calls;
}

void report() {
    if (!stats_enabled()) return;
    std::lock_guard<std::mutex> guard(table_mutex());
    std::vector<std::pair<double, std::string>> rows;
    for (const auto& item : profile_table()) {
        rows.emplace_back(item.second.seconds, item.first);
    }
    std::sort(rows.begin(), rows.end(), std::greater<std::pair<double, std::string>>());
    /* Counters can name a stage that has no timing of its own, such as the one
       charged for downloading the graph results. */
    for (const auto& item : counter_table()) {
        if (profile_table().find(item.first) == profile_table().end()) {
            rows.emplace_back(0.0, item.first);
        }
    }
    unsigned long long total_declines = 0;
    unsigned long long total_uploads = 0;
    unsigned long long total_downloads = 0;
    unsigned long long total_upload_bytes = 0;
    unsigned long long total_download_bytes = 0;
    unsigned long long total_launches = 0;
    double total_busy = 0.0;
    for (const auto& row : rows) {
        auto profile_iterator = profile_table().find(row.second);
        const Profile profile =
            profile_iterator == profile_table().end() ? Profile{} : profile_iterator->second;
        auto counter_iterator = counter_table().find(row.second);
        const Counters counters = counter_iterator == counter_table().end() ? Counters{}
                                                                           : counter_iterator->second;
        total_declines += counters.declines;
        total_uploads += counters.uploads;
        total_downloads += counters.downloads;
        total_upload_bytes += counters.upload_bytes;
        total_download_bytes += counters.download_bytes;
        total_launches += counters.launches;
        total_busy += counters.busy_seconds;
        std::fprintf(stderr,
                     "[op-stats] %-24s %8.3fs %7llu calls %7.3fs busy"
                     " %7llu decl %5llu up %7.1fMB %5llu down %7.1fMB %7llu launch\n",
                     row.second.c_str(), profile.seconds, profile.calls,
                     counters.busy_seconds, counters.declines, counters.uploads,
                     static_cast<double>(counters.upload_bytes) / 1048576.0,
                     counters.downloads,
                     static_cast<double>(counters.download_bytes) / 1048576.0,
                     counters.launches);
    }
    std::fprintf(stderr,
                 "[op-stats] %-24s %8.3fs %7llu calls %7.3fs busy"
                 " %7llu decl %5llu up %7.1fMB %5llu down %7.1fMB %7llu launch\n",
                 "TOTAL", 0.0, 0ULL, total_busy, total_declines, total_uploads,
                 static_cast<double>(total_upload_bytes) / 1048576.0, total_downloads,
                 static_cast<double>(total_download_bytes) / 1048576.0, total_launches);
}

namespace {
/* Printed once, when the runtime is unloaded, instead of at the end of every
   graph run: a synthesis runs the decoder graph 61 times and the old placement
   produced 70 copies of the same growing table. */
struct Flusher {
    ~Flusher() { report(); }
} g_flusher;
}  // namespace

}  // namespace opstats
}  // namespace fsv
