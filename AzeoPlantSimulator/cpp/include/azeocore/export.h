#pragma once

// Consumers import the same classes that the Python bridge uses. In
// particular, virtual tables and ColumnT1/T2::SPEC need data imports on MSVC.
#if defined(_WIN32)
#  if defined(AZEOCORE_BUILD)
#    define AZEOCORE_API __declspec(dllexport)
#  else
#    define AZEOCORE_API __declspec(dllimport)
#  endif
#else
#  define AZEOCORE_API __attribute__((visibility("default")))
#endif
