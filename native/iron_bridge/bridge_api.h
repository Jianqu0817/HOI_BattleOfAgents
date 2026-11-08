#pragma once
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif
/* Handler runs on the game's tick thread. Return UTF-8 JSON held in static or
   thread-local storage; the bridge copies it before the next handler call.
   Registered handlers/modules must stay loaded until the process exits. */
typedef const char* (__cdecl *IronOperationCallback)(uintptr_t image, const char* payload);
/* 0 = registered, 1 = invalid/reserved name or null callback, 2 = duplicate.
   Call outside DllMain. Resolve with GetProcAddress; no import library needed. */
typedef unsigned (__cdecl *IronRegisterOperationFn)(const char* operation, IronOperationCallback callback);
#ifdef IRON_BRIDGE_EXPORTS
__declspec(dllexport) unsigned __cdecl IronRegisterOperation(const char* operation, IronOperationCallback callback);
#endif
#ifdef __cplusplus
}
#endif
