cmake_minimum_required(VERSION 3.20)
project(AzeoNativeClient LANGUAGES CXX)
find_package(AzeoCore 1.0.0 EXACT CONFIG REQUIRED)
add_executable(azeocore_example native_client.cpp)
target_link_libraries(azeocore_example PRIVATE Azeo::Core)
if(MSVC)
  set_property(TARGET azeocore_example PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreadedDLL")
endif()
# A Windows loader looks beside the executable, not in CMake's prefix path.
if(WIN32)
  file(GLOB _azeo_runtime_dlls "${AzeoCore_RUNTIME_DIR}/*.dll")
  add_custom_command(TARGET azeocore_example POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
      ${_azeo_runtime_dlls} "$<TARGET_FILE_DIR:azeocore_example>"
    VERBATIM)
endif()
