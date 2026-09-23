cmake_minimum_required(VERSION 3.20)
project(azeocore VERSION 1.0.0 LANGUAGES CXX)

# The DLL is independent of Python and Qt. The optional Python bridge links
# this same library, so the UI and native SDK never contain different cores.
option(AZEO_BUILD_PYTHON "Build the trainer's Python bridge" ON)
include(GNUInstallDirs)
include(CMakePackageConfigHelpers)
include(CTest)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release)
endif()

add_library(azeocore SHARED src/version.cpp src/log.cpp src/dynamics.cpp src/devices.cpp src/tags.cpp src/bus.cpp src/iobus.cpp
                            src/unit.cpp src/packages.cpp src/units/u010.cpp src/units/u100.cpp
                            src/units/u300.cpp src/units/u400.cpp src/units/u200.cpp
                            src/units/u700.cpp src/units/u800.cpp src/units/u500.cpp
                            src/control/pid.cpp)
add_library(Azeo::Core ALIAS azeocore)
target_compile_definitions(azeocore PRIVATE AZEOCORE_BUILD)
target_compile_features(azeocore PUBLIC cxx_std_20)
target_include_directories(azeocore PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>)
set_target_properties(azeocore PROPERTIES EXPORT_NAME Core VERSION ${PROJECT_VERSION} SOVERSION 1)
# Strict floating point on both compilers: no fused multiply-add and no
# value-changing optimisation. Python rounds every operation; a contracted
# a + b * c rounds once and differs in the last bit, which the parity tests
# see on the first tag that carries it (PDT-1001 did).
if(MSVC)
  # The C++ SDK intentionally exposes STL value types. All clients must use
  # the matching headers, MSVC ABI and shared CRT; see SDK.md.
  target_compile_options(azeocore PRIVATE /W4 /permissive- /fp:strict /wd4251 /wd4275)
  set_property(TARGET azeocore PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>DLL")
else()
  target_compile_options(azeocore PRIVATE -Wall -Wextra -ffp-contract=off -fno-fast-math)
endif()

if(BUILD_TESTING)
  add_executable(azeocore_tests tests/dynamics_test.cpp)
  target_link_libraries(azeocore_tests PRIVATE Azeo::Core)
  add_test(NAME core_primitives COMMAND azeocore_tests)
endif()
add_executable(azeocore_example examples/native_client.cpp)
target_link_libraries(azeocore_example PRIVATE Azeo::Core)
if(BUILD_TESTING)
  add_test(NAME native_client COMMAND azeocore_example)
endif()

if(AZEO_BUILD_PYTHON)
  find_package(Python REQUIRED COMPONENTS Interpreter Development.Module)
  find_package(pybind11 CONFIG REQUIRED)
  pybind11_add_module(_azeocore src/py_module.cpp src/py_devices.cpp src/py_tags.cpp src/py_bus.cpp
                                src/py_units.cpp src/py_control.cpp src/py_scanner.cpp)
  target_link_libraries(_azeocore PRIVATE azeocore)
  if(MSVC)
    target_compile_options(_azeocore PRIVATE /fp:strict /wd4251 /wd4275)
  else()
    target_compile_options(_azeocore PRIVATE -ffp-contract=off -fno-fast-math)
  endif()
  set_target_properties(_azeocore PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/../azeoplant
    LIBRARY_OUTPUT_DIRECTORY_RELEASE ${CMAKE_CURRENT_SOURCE_DIR}/../azeoplant
    LIBRARY_OUTPUT_DIRECTORY_DEBUG ${CMAKE_CURRENT_SOURCE_DIR}/../azeoplant
    BUILD_RPATH "$ORIGIN")
  add_custom_command(TARGET _azeocore POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
      $<TARGET_FILE:azeocore> $<TARGET_FILE_DIR:_azeocore>
    VERBATIM)
endif()

install(TARGETS azeocore EXPORT AzeoCoreTargets
  RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
  LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
  ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR})
install(TARGETS azeocore_example RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
install(DIRECTORY include/ DESTINATION ${CMAKE_INSTALL_INCLUDEDIR})
install(DIRECTORY examples/ DESTINATION examples)
install(FILES SDK.md DESTINATION . RENAME README.md)
configure_package_config_file(cmake/AzeoCoreConfig.cmake.in
  ${CMAKE_CURRENT_BINARY_DIR}/AzeoCoreConfig.cmake
  INSTALL_DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/AzeoCore
  PATH_VARS CMAKE_INSTALL_BINDIR)
write_basic_package_version_file(${CMAKE_CURRENT_BINARY_DIR}/AzeoCoreConfigVersion.cmake
  VERSION ${PROJECT_VERSION} COMPATIBILITY ExactVersion)
install(EXPORT AzeoCoreTargets NAMESPACE Azeo:: DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/AzeoCore)
install(FILES ${CMAKE_CURRENT_BINARY_DIR}/AzeoCoreConfig.cmake
              ${CMAKE_CURRENT_BINARY_DIR}/AzeoCoreConfigVersion.cmake
  DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/AzeoCore)
# App-local release CRTs let a copied Windows SDK run without installing
# Visual Studio or its redistributable on the destination PC.
set(AZEO_MSVC_REDIST_DIRECTORY "" CACHE PATH "Directory containing the matching MSVC CRT DLLs")
if(MSVC AND NOT AZEO_MSVC_REDIST_DIRECTORY)
  string(TOLOWER "${CMAKE_CXX_COMPILER_ARCHITECTURE_ID}" _azeo_arch)
  file(GLOB _azeo_crt_dirs "$ENV{VCToolsRedistDir}/${_azeo_arch}/Microsoft.VC*.CRT")
  if(_azeo_crt_dirs)
    list(SORT _azeo_crt_dirs COMPARE NATURAL ORDER DESCENDING)
    list(GET _azeo_crt_dirs 0 AZEO_MSVC_REDIST_DIRECTORY)
  endif()
endif()
if(MSVC AND AZEO_MSVC_REDIST_DIRECTORY)
  # Older CMake versions assume VC143; VS 2026 ships VC145 instead.
  # Take the toolchain's redistributable directory, not a system-installed CRT.
  file(GLOB _azeo_runtime_dlls "${AZEO_MSVC_REDIST_DIRECTORY}/*.dll")
  if(NOT _azeo_runtime_dlls)
    message(FATAL_ERROR "AZEO_MSVC_REDIST_DIRECTORY contains no runtime DLLs")
  endif()
  install(FILES ${_azeo_runtime_dlls} DESTINATION ${CMAKE_INSTALL_BINDIR})
else()
  set(CMAKE_INSTALL_SYSTEM_RUNTIME_DESTINATION ${CMAKE_INSTALL_BINDIR})
  include(InstallRequiredSystemLibraries)
endif()
