# Turns a PTX text file into a C++ translation unit holding a byte-exact string.
# Invoked from CMake: -DFSV_PTX_IN=<file.ptx> -DFSV_PTX_OUT=<file.cpp>
if(NOT DEFINED FSV_PTX_IN OR NOT DEFINED FSV_PTX_OUT)
    message(FATAL_ERROR "FSV_PTX_IN and FSV_PTX_OUT are required")
endif()

file(READ "${FSV_PTX_IN}" PTX_HEX HEX)
string(LENGTH "${PTX_HEX}" HEX_LENGTH)
if(HEX_LENGTH EQUAL 0)
    message(FATAL_ERROR "PTX file is empty: ${FSV_PTX_IN}")
endif()

set(OUTPUT "/* Generated from ${FSV_PTX_IN}. Do not edit. */\n")
string(APPEND OUTPUT "namespace fsv {\n")
string(APPEND OUTPUT "extern const char* const fsv_ptx_kernels_source;\n")
string(APPEND OUTPUT "const char* const fsv_ptx_kernels_source =\n")

# One regex pass escapes every byte. The loop this replaces asked for a
# two-character substring of the whole hexadecimal string once per byte, so it
# expanded a half-megabyte argument a million times and grew a chunk buffer
# back to eight thousand characters a million times: a build step that should
# take a second took tens of minutes.
string(REGEX REPLACE "(..)" "\\\\x\\1" PTX_ESCAPED "${PTX_HEX}")

# The chunk size is a multiple of four so that a chunk boundary never splits
# the four characters of one \xNN escape.
set(CHUNK_SIZE 8192)
string(LENGTH "${PTX_ESCAPED}" ESCAPED_LENGTH)
math(EXPR LAST_INDEX "${ESCAPED_LENGTH} - 1")
foreach(INDEX RANGE 0 ${LAST_INDEX} ${CHUNK_SIZE})
    string(SUBSTRING "${PTX_ESCAPED}" ${INDEX} ${CHUNK_SIZE} CHUNK)
    string(APPEND OUTPUT "\"${CHUNK}\"\n")
endforeach()
string(APPEND OUTPUT ";\n}  // namespace fsv\n")

file(WRITE "${FSV_PTX_OUT}" "${OUTPUT}")
message(STATUS "Embedded PTX (${HEX_LENGTH} hex chars) into ${FSV_PTX_OUT}")
