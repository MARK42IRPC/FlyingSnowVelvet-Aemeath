cmake_minimum_required(VERSION 3.20)

project(fsv_cuda_voice_runtime LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

if(MSVC)
    # Sources carry UTF-8 Chinese diagnostics; without this MSVC decodes them as GBK.
    add_compile_options(/utf-8)
endif()

# Lower PTX target = wider hardware support; the driver JITs it at run time.
# The CUDA toolkit is a BUILD-TIME tool only, user machines need the driver alone.
#
# compute_61 is Pascal (GTX 10xx), the oldest generation still common enough to
# matter: PTX built for compute_75 cannot be JIT-compiled for it at all, so the
# newer target silently locked those cards out. Measured with fsv_kernel_bench
# on the RTX 3050 dev card, compute_61 matches compute_75 within noise
# (matmul_f32 m=1 k=2048 n=512: 28.65 vs 28.82 us; int4 m=1 k=2048 n=512:
# 39.3 vs 38.0 us), while compute_50 costs 12-22% on the same kernels, so 61 is
# the floor that buys the compatibility for free. The PTX ISA version is a
# separate matter: CUDA 12.6 emits .version 8.5 whatever the target, so the
# driver still has to be 555-series (2024) or newer.
if(NOT DEFINED FSV_PTX_ARCH)
    set(FSV_PTX_ARCH "compute_61")
endif()

find_program(NVCC_EXECUTABLE nvcc)
if(NOT NVCC_EXECUTABLE)
    message(FATAL_ERROR "nvcc not found: building the runtime needs the CUDA toolkit on this machine")
endif()

set(PTX_DIR "${CMAKE_BINARY_DIR}/ptx")
set(PTX_FILE "${PTX_DIR}/fsv_kernels.ptx")
add_custom_command(
    OUTPUT "${PTX_FILE}"
    COMMAND "${CMAKE_COMMAND}" -E make_directory "${PTX_DIR}"
    COMMAND "${NVCC_EXECUTABLE}" -ptx -O3 -arch=${FSV_PTX_ARCH}
            --use-local-env
            -o "${PTX_FILE}" "${CMAKE_CURRENT_SOURCE_DIR}/src/kernels/fsv_kernels.cu"
    DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/src/kernels/fsv_kernels.cu"
    COMMENT "Compiling self-written CUDA kernels to PTX"
    VERBATIM)

set(PTX_SOURCE "${CMAKE_BINARY_DIR}/generated/fsv_ptx_source.cpp")
add_custom_command(
    OUTPUT "${PTX_SOURCE}"
    COMMAND "${CMAKE_COMMAND}" -E make_directory "${CMAKE_BINARY_DIR}/generated"
    COMMAND "${CMAKE_COMMAND}" -DFSV_PTX_IN=${PTX_FILE} -DFSV_PTX_OUT=${PTX_SOURCE}
            -P "${CMAKE_CURRENT_SOURCE_DIR}/cmake/embed_ptx.cmake"
    DEPENDS "${PTX_FILE}" "${CMAKE_CURRENT_SOURCE_DIR}/cmake/embed_ptx.cmake"
    COMMENT "Embedding PTX into the runtime"
    VERBATIM)

# Text frontend code is host-only and has no CUDA dependency, so it lives in a
# static library that both the runtime DLL and the development CLIs link.
add_library(fsv_text_frontend STATIC
    src/host/fsv_json.cpp
    src/host/fsv_unicode.cpp
    src/host/fsv_unicode_tables.cpp
    src/host/fsv_tokenizer.cpp)
target_include_directories(fsv_text_frontend PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/src/host")

add_library(fsv_cuda_voice_runtime SHARED
    src/host/nv_runtime.cpp
    src/host/cuda_ops.cpp
    src/host/fsv_opstats.cpp
    src/host/onnx_graph_parser.cpp
    src/host/graph_runtime.cpp
    src/host/native_graph_bridge.cpp
    "${PTX_SOURCE}")
target_link_libraries(fsv_cuda_voice_runtime PRIVATE fsv_text_frontend)

target_include_directories(fsv_cuda_voice_runtime
    PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/include"
    PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/src/host")
# FSV_PTX_ARCH_TARGET is quoted by the PTX load-failure diagnostic in
# nv_runtime.cpp, so the message says which target the driver refused.
target_compile_definitions(fsv_cuda_voice_runtime PRIVATE
    FSV_CUDA_VOICE_BUILD_DLL
    FSV_PTX_ARCH_TARGET="${FSV_PTX_ARCH}")
set_target_properties(fsv_cuda_voice_runtime PROPERTIES
    OUTPUT_NAME "fsv_cuda_voice_runtime"
    WINDOWS_EXPORT_ALL_SYMBOLS OFF)
# No CUDA libraries are linked: nvcuda.dll is loaded at run time.

add_executable(fsv_engine_smoke tools/fsv_engine_smoke.cpp)
target_link_libraries(fsv_engine_smoke PRIVATE fsv_cuda_voice_runtime)
target_include_directories(fsv_engine_smoke PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/include")

# Development-only instrument: times a single kernel in isolation, because the
# graph-level benchmark cannot resolve one kernel change against another.
add_executable(fsv_kernel_bench tools/fsv_kernel_bench.cpp)
target_link_libraries(fsv_kernel_bench PRIVATE fsv_cuda_voice_runtime)
target_include_directories(fsv_kernel_bench PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/include")

add_executable(fsv_voice_cli tools/fsv_voice_cli.cpp)
target_link_libraries(fsv_voice_cli PRIVATE fsv_cuda_voice_runtime)
target_include_directories(fsv_voice_cli PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/include")

add_executable(fsv_frontend_cli tools/fsv_frontend_cli.cpp)
target_link_libraries(fsv_frontend_cli PRIVATE fsv_text_frontend)
target_include_directories(fsv_frontend_cli PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/include"
    "${CMAKE_CURRENT_SOURCE_DIR}/src/host")
