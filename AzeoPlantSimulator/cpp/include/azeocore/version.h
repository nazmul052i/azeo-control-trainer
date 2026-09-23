#pragma once
#include <stdint.h>
#include "azeocore/export.h"

#ifdef __cplusplus
extern "C" {
#endif

// The numeric ABI version changes when public C++ layouts/signatures change.
// A consumer must use the matching SDK headers and compatible C++ runtime.
AZEOCORE_API uint32_t azeocore_abi_version(void);
AZEOCORE_API const char* azeocore_version(void);

#ifdef __cplusplus
}
#endif
