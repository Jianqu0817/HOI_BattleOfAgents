#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <bcrypt.h>
#include <array>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <sstream>
#include "bridge_api.h"
#include "clock.hpp"
namespace {
constexpr char hash_pin[] = "bccc62909b6a1ec2c1d5a46ef18608e4d0c7cdc89ad341b7402a23c40f7a8037";
struct NativeContext { std::uintptr_t image{}, state{}, idler{}, queue{}, name{}; } context;
template<class T> bool read(std::uintptr_t address, T& value) {
    SIZE_T received{};
    return ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(address), &value, sizeof(value), &received) && received == sizeof(value);
}
bool string_value(std::uintptr_t address, std::string& out) {
    std::array<unsigned char, 32> data{};
    if (!read(address, data)) return false;
    std::uintptr_t pointer{}; std::uint64_t size{}, capacity{};
    memcpy(&pointer, data.data(), 8); memcpy(&size, data.data() + 16, 8); memcpy(&capacity, data.data() + 24, 8);
    if (size > 512 || size > capacity || capacity > 1048576) return false;
    out.resize(static_cast<std::size_t>(size));
    if (capacity <= 15) memcpy(out.data(), data.data(), out.size());
    else if (size) {
        SIZE_T count{};
        if (!ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(pointer), out.data(), out.size(), &count) || count != out.size()) return false;
    }
    return !out.empty() && out.find('\0') == out.npos;
}
bool native_read(void* opaque, iron::ClockSnapshot& out) {
    auto& c = *static_cast<NativeContext*>(opaque);
    std::uintptr_t vtable{};
    unsigned char paused{};
    if (!read(c.image + 0x33DD6B0, c.state) || !c.state || !read(c.image + 0x33DDB10, c.idler) || !c.idler ||
        !read(c.idler, vtable) || vtable != c.image + 0x29E58D8 || !read(c.idler + 0x6C1, paused) || paused > 1 ||
        !read(c.state + 0x928, out.remaining_hours)) return false;
    using GetQueue = void* (__fastcall*)(void*);
    using GetPlayer = void* (__fastcall*)(void*);
    c.queue = reinterpret_cast<std::uintptr_t>(reinterpret_cast<GetQueue>(c.image + 0xB8E400)(reinterpret_cast<void*>(c.idler)));
    const auto profile = reinterpret_cast<std::uintptr_t>(reinterpret_cast<GetPlayer>(c.image + 0x1F1A40)(reinterpret_cast<void*>(c.state)));
    if (!c.queue || !profile || !string_value(profile + 0x40, out.player)) return false;
    c.name = profile + 0x40; out.paused = paused != 0; return true;
}
bool native_arm(void* opaque, int hours) {
    auto& c = *static_cast<NativeContext*>(opaque);
    using Arm = void (__fastcall*)(void*, int);
    reinterpret_cast<Arm>(c.image + 0x204770)(reinterpret_cast<void*>(c.state), hours);
    int remaining{}; return read(c.state + 0x928, remaining) && remaining == hours;
}
bool native_pause(void* opaque, bool post) {
    auto& c = *static_cast<NativeContext*>(opaque);
    iron::ClockSnapshot before;
    if (!native_read(opaque, before)) return false;
    using Allocate = void* (__fastcall*)(std::size_t);
    using Construct = void* (__fastcall*)(void*, const void*, unsigned char, unsigned char);
    using Destroy = void* (__fastcall*)(void*, unsigned int);
    using Post = bool (__fastcall*)(void*, void*, unsigned char);
    auto command = reinterpret_cast<Allocate>(c.image + 0x25A01DC)(0x50);
    if (!command) return false;
    reinterpret_cast<Construct>(c.image + 0xE52520)(command, reinterpret_cast<void*>(c.name), 0, 0);
    const auto address = reinterpret_cast<std::uintptr_t>(command);
    std::uintptr_t vtable{}; unsigned char a{}, b{}; std::string player;
    const bool verified = read(address, vtable) && vtable == c.image + 0x29E6E10 &&
        read(address + 0x48, a) && !a && read(address + 0x49, b) && !b &&
        string_value(address + 0x28, player) && player == before.player;
    if (!verified || !post) {
        reinterpret_cast<Destroy>(c.image + 0xE52A30)(command, 1); return verified;
    }
    // The queue consumes the command on success and rejection.
    return reinterpret_cast<Post>(c.image + 0x22C4250)(reinterpret_cast<void*>(c.queue), command, 0);
}
struct Synthetic { bool paused = true; int remaining = -1; unsigned arms{}, posts{}, builds{}; bool last_arm_before_post{}; } synthetic;
bool fake_read(void*, iron::ClockSnapshot& out) { out = {synthetic.paused, synthetic.remaining, "Synthetic Player"}; return true; }
bool fake_arm(void*, int hours) { synthetic.remaining = hours; ++synthetic.arms; return true; }
bool fake_pause(void*, bool post) {
    if (post) { synthetic.last_arm_before_post = synthetic.arms > 0; ++synthetic.posts; synthetic.paused = !synthetic.paused; }
    else ++synthetic.builds;
    return true;
}
iron::ClockApi api;
const char* __cdecl callback(std::uintptr_t image, const char* payload) {
    static thread_local std::string result;
    if (image != context.image) { result = "{\"ok\":false,\"error\":\"image base mismatch\"}"; return result.c_str(); }
    result = iron::clock_command(api, payload);
    return result.c_str();
}
std::string hash_file(const wchar_t* path) {
    BCRYPT_ALG_HANDLE algorithm{}; BCRYPT_HASH_HANDLE hash{};
    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) < 0) return {};
    DWORD size{}, returned{};
    BCryptGetProperty(algorithm, BCRYPT_OBJECT_LENGTH, reinterpret_cast<PUCHAR>(&size), sizeof(size), &returned, 0);
    std::string object(size, '\0');
    if (BCryptCreateHash(algorithm, &hash, reinterpret_cast<PUCHAR>(object.data()), size, nullptr, 0, 0) < 0) { BCryptCloseAlgorithmProvider(algorithm, 0); return {}; }
    std::ifstream file(path, std::ios::binary); std::array<char, 65536> buffer{}; bool valid = file.is_open();
    while (file) { file.read(buffer.data(), buffer.size()); if (file.gcount() && BCryptHashData(hash, reinterpret_cast<PUCHAR>(buffer.data()), static_cast<ULONG>(file.gcount()), 0) < 0) valid = false; }
    std::array<unsigned char, 32> digest{};
    if (BCryptFinishHash(hash, digest.data(), static_cast<ULONG>(digest.size()), 0) < 0) valid = false;
    BCryptDestroyHash(hash); BCryptCloseAlgorithmProvider(algorithm, 0);
    std::ostringstream out; for (auto b : digest) out << std::hex << std::setw(2) << std::setfill('0') << static_cast<unsigned>(b);
    return valid ? out.str() : "";
}
bool pin(std::uintptr_t rva, std::string_view hex) {
    std::string bytes; bytes.reserve(hex.size() / 2);
    auto digit = [](char c) { return c <= '9' ? c - '0' : c - 'a' + 10; };
    for (std::size_t i = 0; i < hex.size(); i += 2) bytes += static_cast<char>((digit(hex[i]) << 4) | digit(hex[i + 1]));
    return memcmp(reinterpret_cast<void*>(context.image + rva), bytes.data(), bytes.size()) == 0;
}
DWORD WINAPI initialize(void*) {
    auto main = GetModuleHandleW(nullptr); context.image = reinterpret_cast<std::uintptr_t>(main);
    auto bridge = GetModuleHandleW(L"iron_bridge.dll");
    auto add = bridge ? reinterpret_cast<IronRegisterOperationFn>(GetProcAddress(bridge, "IronRegisterOperation")) : nullptr;
    if (!add) return 11;
    auto marker = reinterpret_cast<unsigned(*)()>(GetProcAddress(main, "IronBridgeTestHostMarker"));
    const bool test = marker && marker() == 0x49524f4e;
    if (test) api = {nullptr, fake_read, fake_arm, fake_pause};
    else {
        wchar_t path[32768]{};
        if (!GetModuleFileNameW(nullptr, path, 32768) || hash_file(path) != hash_pin) return 12;
        if (!pin(0x204770,"48895c2408574883ec20488bd98bfa488b0d82931d034883") ||
            !pin(0xE52520,"48895c2410488974241848894c2408574883ec20410fb6f1") ||
            !pin(0xE52A30,"48895c2408574883ec20488d05cf43b9018bfa488901488b") ||
            !pin(0x1F1A40,"40534883ec20488bd9488b0db8c01e03488b01ff90880000") ||
            !pin(0xB8E400,"488b8930050000488b0148ffa088000000") ||
            !pin(0x22C4250,"48895c2408555657488dac2470ffffff4881ec9001000048") ||
            !pin(0x25A01DC,"40534883ec20488bd9eb0f488bcbe89dea040085c0741348")) return 13;
        api = {&context, native_read, native_arm, native_pause};
    }
    return add("native_clock_v1", callback);
}
}
BOOL WINAPI DllMain(HINSTANCE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        HANDLE thread = CreateThread(nullptr, 0, initialize, nullptr, 0, nullptr);
        if (!thread) return FALSE; CloseHandle(thread);
    }
    return TRUE;
}
