cmake_minimum_required(VERSION 3.21)

project(superweb_client_backend LANGUAGES CXX)

add_library(
    superweb_client_backend
    SHARED
    src/discovery_wire.cpp
    src/runtime_wire.cpp
    src/superweb_client_backend.cpp
    src/vector_generator.cpp
)

target_compile_features(superweb_client_backend PRIVATE cxx_std_20)
target_compile_definitions(
    superweb_client_backend
    PRIVATE
    SWC_BUILD_DLL
    WIN32_LEAN_AND_MEAN
    NOMINMAX
)
target_include_directories(superweb_client_backend PUBLIC include)
target_link_libraries(superweb_client_backend PRIVATE ws2_32)

set_target_properties(
    superweb_client_backend
    PROPERTIES
    OUTPUT_NAME "superweb_client_backend"
)
