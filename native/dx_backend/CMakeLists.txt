cmake_minimum_required(VERSION 3.21)

project(flying_snow_dx LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

add_library(flying_snow_dx SHARED
    src/flying_snow_dx.cpp
)

target_include_directories(flying_snow_dx
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_compile_definitions(flying_snow_dx PRIVATE
    WIN32_LEAN_AND_MEAN
    NOMINMAX
    FSDX_BUILD_DLL
)

if(MSVC)
    target_compile_options(flying_snow_dx PRIVATE /W4 /EHsc /permissive-)
endif()

target_link_libraries(flying_snow_dx PRIVATE
    d3d11.lib
    d2d1.lib
    dxgi.lib
)

set_target_properties(flying_snow_dx PROPERTIES
    OUTPUT_NAME "flying_snow_dx"
    WINDOWS_EXPORT_ALL_SYMBOLS OFF
)
