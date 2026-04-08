cmake_minimum_required(VERSION 3.5.0)
project(dlopen_demo)

set(NATIVERENDER_ROOT_PATH ${CMAKE_CURRENT_SOURCE_DIR})

include_directories(${NATIVERENDER_ROOT_PATH})

add_library(dlopen SHARED napi_init.cpp)

target_link_libraries(dlopen PUBLIC libace_napi.z.so libhilog_ndk.z.so)
