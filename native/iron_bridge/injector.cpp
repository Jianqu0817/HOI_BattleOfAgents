#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <tlhelp32.h>
#include <cstdint>
#include <cstdio>
#include <cwchar>
#include <string>

namespace {
std::uintptr_t remote_module(DWORD pid, const wchar_t* name) {
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid);
    if (snapshot == INVALID_HANDLE_VALUE) return 0;
    MODULEENTRY32W entry{}; entry.dwSize = sizeof(entry);
    std::uintptr_t result{};
    if (Module32FirstW(snapshot, &entry)) do {
        if (!_wcsicmp(entry.szModule, name) || !_wcsicmp(entry.szExePath, name)) {
            result = reinterpret_cast<std::uintptr_t>(entry.modBaseAddr); break;
        }
    } while (Module32NextW(snapshot, &entry));
    CloseHandle(snapshot); return result;
}
}
int wmain(int argc, wchar_t** argv) {
    if (argc != 3) { std::fprintf(stderr, "usage: iron_injector PID ABSOLUTE_DLL_PATH\n"); return 2; }
    wchar_t* end{}; unsigned long number = wcstoul(argv[1], &end, 10);
    if (!number || *end || GetFileAttributesW(argv[2]) == INVALID_FILE_ATTRIBUTES) return 2;
    wchar_t path[32768]{};
    if (!GetFullPathNameW(argv[2], 32768, path, nullptr)) return 2;
    DWORD pid = static_cast<DWORD>(number);
    if (remote_module(pid, path)) { std::fprintf(stderr, "module already loaded; no reinjection\n"); return 3; }
    const auto local_load = GetProcAddress(GetModuleHandleW(L"kernel32.dll"), "LoadLibraryW");
    MEMORY_BASIC_INFORMATION info{};
    if (!local_load || !VirtualQuery(reinterpret_cast<void*>(local_load), &info, sizeof(info))) return 4;
    wchar_t owner_path[32768]{};
    if (!GetModuleFileNameW(static_cast<HMODULE>(info.AllocationBase), owner_path, 32768)) return 4;
    const wchar_t* owner_name = wcsrchr(owner_path, L'\\'); owner_name = owner_name ? owner_name + 1 : owner_path;
    const auto remote_owner = remote_module(pid, owner_name);
    if (!remote_owner) return 4;
    const auto remote_load = remote_owner + reinterpret_cast<std::uintptr_t>(local_load) - reinterpret_cast<std::uintptr_t>(info.AllocationBase);
    HANDLE process = OpenProcess(PROCESS_CREATE_THREAD | PROCESS_QUERY_INFORMATION | PROCESS_VM_OPERATION | PROCESS_VM_WRITE | PROCESS_VM_READ, FALSE, pid);
    if (!process) { std::fprintf(stderr, "OpenProcess failed: %lu\n", GetLastError()); return 5; }
    const SIZE_T bytes = (wcslen(path) + 1) * sizeof(wchar_t);
    void* remote_path = VirtualAllocEx(process, nullptr, bytes, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    SIZE_T written{};
    if (!remote_path || !WriteProcessMemory(process, remote_path, path, bytes, &written) || written != bytes) {
        if (remote_path) VirtualFreeEx(process, remote_path, 0, MEM_RELEASE);
        CloseHandle(process); return 6;
    }
    HANDLE thread = CreateRemoteThread(process, nullptr, 0, reinterpret_cast<LPTHREAD_START_ROUTINE>(remote_load), remote_path, 0, nullptr);
    if (!thread) { VirtualFreeEx(process, remote_path, 0, MEM_RELEASE); CloseHandle(process); return 7; }
    const DWORD wait = WaitForSingleObject(thread, 10000);
    if (wait != WAIT_OBJECT_0) {
        // The loader may still read this path. Leave its tiny allocation alive.
        std::fprintf(stderr, "LoadLibrary outcome unknown; do not retry blindly\n");
        CloseHandle(thread); CloseHandle(process); return 8;
    }
    CloseHandle(thread); VirtualFreeEx(process, remote_path, 0, MEM_RELEASE); CloseHandle(process);
    if (!remote_module(pid, path)) { std::fprintf(stderr, "DLL not present after loader finished\n"); return 9; }
    std::printf("{\"loaded\":true,\"pid\":%lu,\"bridge_ready\":false}\n", pid);
    return 0;
}
