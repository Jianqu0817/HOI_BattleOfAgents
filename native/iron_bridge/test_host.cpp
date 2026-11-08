#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <cstdio>
#include <cstdlib>
#include <string>
#include "bridge_api.h"
const char* __cdecl registered_test(uintptr_t image, const char* payload) {
    static thread_local std::string result;
    result = "{\"payload_length\":" + std::to_string(strlen(payload)) +
        ",\"callback_thread_id\":" + std::to_string(GetCurrentThreadId()) +
        ",\"correct_image\":" + (image == reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr)) ? "true" : "false") + "}";
    return result.c_str();
}
extern "C" __declspec(dllexport) unsigned IronBridgeTestHostMarker() { return 0x49524f4e; }
extern "C" __declspec(dllexport) __declspec(noinline) void IronBridgeTestTick(void* window, char force) {
    // Keep a real body for MinHook's executable trampoline.
    if (force && window) *static_cast<volatile unsigned*>(window) += GetCurrentThreadId();
    MemoryBarrier();
}
int wmain(int argc, wchar_t** argv) {
    if (argc > 2 || (argc == 2 && !LoadLibraryW(argv[1]))) { std::fprintf(stderr, "LoadLibrary failed: %lu\n", GetLastError()); return 1; }
    if (argc == 2) {
        auto add = reinterpret_cast<IronRegisterOperationFn>(GetProcAddress(GetModuleHandleW(L"iron_bridge.dll"), "IronRegisterOperation"));
        if (!add || add("test_registered_v1", registered_test) != 0 ||
            add("test_registered_v1", registered_test) != 2 || add("native_move", registered_test) != 1) return 2;
    }
    std::printf("{\"pid\":%lu,\"main_thread_id\":%lu}\n", GetCurrentProcessId(), GetCurrentThreadId()); std::fflush(stdout);
    wchar_t delay[32]{};
    if (GetEnvironmentVariableW(L"IRON_TEST_INITIAL_DELAY_MS", delay, 32)) Sleep(wcstoul(delay, nullptr, 10));
    for (;;) { IronBridgeTestTick(nullptr, 0); Sleep(10); }
}
