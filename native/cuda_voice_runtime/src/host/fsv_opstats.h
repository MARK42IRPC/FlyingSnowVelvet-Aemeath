#pragma once

#include <chrono>
#include <cstddef>

/* Per-operator profiling shared by the graph loop and the operator host layer.

   The counters used to live in graph_runtime.cpp alone, so the transfers the
   *_host CPU fallbacks perform inside cuda_ops.cpp were invisible: a synthesis
   reported 3219 uploads in [op-stats] while the driver saw 9853. Both layers
   now charge their transfers to the operator the node loop is executing, and
   the table is printed once at process exit instead of once per graph run. */

namespace fsv {
namespace opstats {

/* True when FSV_NATIVE_OPSTATS is set. When it is off every counter below
   returns after one relaxed read of a cached flag. */
bool enabled();

/* Names the operator the current thread is executing. A scope sets it, and
   the transfer helpers outside the node loop (the *_host fallbacks) read it. */
void set_current(const char* name);
const char* current();

void count_decline();
/* Both take the number of bytes moved; the report shows the count and the
   total, because a node that moves a few bytes often costs far more than one
   that moves a lot. */
void count_upload(std::size_t bytes);
void count_download(std::size_t bytes);
/* Seconds this operator spent waiting for the device queue to drain inside a
   blocking copy. The node total minus this is the host time the operator
   actually costs, which is the number a kernel rewrite has to move. */
void count_busy(double seconds);
/* One kernel launch. Charged by the driver, so the column accounts for every
   launch the graph issues rather than only the ones a device operator makes. */
void count_launch();

/* Times one node and charges its transfers to that operator name. */
class Scope {
public:
    explicit Scope(const char* name, const char* detail = nullptr);
    ~Scope();
    Scope(const Scope&) = delete;
    Scope& operator=(const Scope&) = delete;

private:
    const char* previous_;
    const char* name_;
    const char* detail_ = nullptr;
    std::chrono::steady_clock::time_point start_;
    bool active_;
};

/* Prints the accumulated table; a no-op unless FSV_NATIVE_OPSTATS is set. */
void report();

}  // namespace opstats
}  // namespace fsv
