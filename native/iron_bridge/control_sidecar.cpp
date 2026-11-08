#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <array>
#include <cstdint>
#include <cstring>
#include <unordered_map>
#include "bridge_api.h"
#include "control.hpp"
#include "sidecar_pins.hpp"
namespace {
constexpr char hash_pin[] = "bccc62909b6a1ec2c1d5a46ef18608e4d0c7cdc89ad341b7402a23c40f7a8037";
std::uintptr_t image{};
struct NativeString { union { char small[16]; const char* pointer; } data{}; std::uint64_t size{}, capacity = 15; };
struct Vector { const NativeString* data; int capacity, count; std::uintptr_t allocator; };
struct Result { unsigned char flag{}; unsigned char pad[7]{}; NativeString message{}; };
static_assert(sizeof(NativeString) == 32 && sizeof(Vector) == 24 && sizeof(Result) == 40);
template<class T> bool read(std::uintptr_t address, T& out) {
    SIZE_T count{}; return ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(address), &out, sizeof(out), &count) && count == sizeof(out);
}
NativeString borrowed(std::string_view text) {
    NativeString out; out.size = text.size();
    if (text.size() <= 15) memcpy(out.data.small, text.data(), text.size());
    else { out.data.pointer = text.data(); out.capacity = text.size(); }
    return out;
}
bool resolve(std::string_view tag, int& index, std::uintptr_t& country, std::uintptr_t& state) {
    auto name = borrowed(tag); index = 0;
    alignas(8) std::array<unsigned char, 16> native_tag{};
    using Parse = void* (__fastcall*)(void*, const NativeString*);
    reinterpret_cast<Parse>(image + 0x1168AE0)(native_tag.data(), &name);
    memcpy(&index, native_tag.data(), sizeof(index));
    std::uintptr_t table{}, vtable{}; int count{}, checked{};
    if (index <= 0 || !read(image + 0x33DD6B0, state) || !state || !read(state + 0x310, table) ||
        !read(state + 0x31C, count) || index >= count || !table || !read(table + index * sizeof(void*), country) || !country ||
        !read(country, vtable) || vtable != image + 0x2864730 || !read(country + 8, checked) || checked != index) return false;
    return true;
}
bool native_read(void*, std::string_view tag, iron::ControlSnapshot& out) {
    std::uintptr_t country{}, state{}, idler{}, list{};
    if (!resolve(tag, out.country, country, state)) return false;
    unsigned char paused{}, global{}; int count{}, capacity{};
    if (!read(state + 0x520, out.played) || !read(state + 0x468, out.date_raw) || !read(image + 0x33DDB10, idler) || !idler ||
        !read(idler + 0x6C1, paused) || paused > 1 || !read(image + 0x33DDAAC, global) || global > 1 ||
        !read(image + 0x33DDAD8, list) || !read(image + 0x33DDAE4, count) || !read(image + 0x33DDAE0, capacity) ||
        count < 0 || count > 4096 || capacity < count || (count && !list)) return false;
    out.ai_override = false;
    for (int i = 0; i < count; ++i) { std::uintptr_t entry{}; if (!read(list + i * sizeof(void*), entry)) return false; if (entry == country) out.ai_override = true; }
    out.paused = paused != 0; out.global_ai = global != 0; out.ai_enabled = out.global_ai != out.ai_override;
    return true;
}
bool native_tag(void*, std::string_view tag) {
    int index{}; std::uintptr_t country{}, state{};
    if (!resolve(tag, index, country, state)) return false;
    // The tag action calls getter 0x73EBF0 (country+0x410), then checks vector+0x0C.
    int available_states{};
    if (!read(country + 0x41C, available_states) || available_states <= 0) return false;
    using Change = void (__fastcall*)(const int*);
    reinterpret_cast<Change>(image + 0x1339360)(&index);
    int played{}; return read(state + 0x520, played) && played == index;
}
bool action(std::uintptr_t rva, std::string_view token, std::string& message) {
    const auto argument = borrowed(token); const Vector argv{&argument, 1, 1, 0}; Result result;
    using Action = Result* (__fastcall*)(Result*, const Vector*, std::uint64_t);
    reinterpret_cast<Action>(image + rva)(&result, &argv, 0);
    const auto& text = result.message;
    bool valid = text.size <= text.capacity && text.size <= 1048576 && text.capacity <= 1048576;
    if (valid) {
        const char* bytes = text.capacity <= 15 ? text.data.small : text.data.pointer;
        if (text.size) message.assign(bytes, static_cast<std::size_t>(text.size)); else message.clear();
    }
    if (valid) {
        using DestroyString = void (__fastcall*)(NativeString*);
        reinterpret_cast<DestroyString>(image + 0x120530)(&result.message);
    }
    return valid && result.flag != 0;
}
bool native_ai(void*, std::string_view tag, std::string& message) { return action(0x2A9630, tag, message); }
bool native_save(void*, std::string_view name, std::string& message) {
    std::uintptr_t idler{}; unsigned char paused{};
    if (!read(image + 0x33DDB10, idler) || !idler || !read(idler + 0x6C1, paused) || paused != 1) return false;
    return action(0x297C90, name, message);
}
struct FakeCountry { int index; bool enabled = true; };
std::unordered_map<std::string, FakeCountry> fake{{"AUS",{11}}, {"GER",{1}}, {"ITA",{2}}, {"HUN",{3}}, {"CZE",{4}}};
int fake_played = 11;
bool fake_read(void*, std::string_view tag, iron::ControlSnapshot& out) {
    const auto it = fake.find(std::string(tag)); if (it == fake.end()) return false;
    out = {fake_played, it->second.index, 60762746, true, it->second.enabled, true, !it->second.enabled}; return true;
}
bool fake_tag(void*, std::string_view tag) { fake_played = fake.at(std::string(tag)).index; return true; }
bool fake_ai(void*, std::string_view tag, std::string& message) { auto& enabled = fake.at(std::string(tag)).enabled; enabled = !enabled; message = "synthetic AI toggle"; return true; }
bool fake_save(void*, std::string_view, std::string& message) { message = "synthetic save acknowledgement"; return true; }
iron::ControlApi api;
const char* __cdecl callback(std::uintptr_t base, const char* payload) {
    static thread_local std::string result;
    if (base != image) result = "{\"ok\":false,\"error\":\"image mismatch\"}";
    else result = iron::control_command(api, payload);
    return result.c_str();
}
DWORD WINAPI initialize(void*) {
    const auto main = GetModuleHandleW(nullptr); image = reinterpret_cast<std::uintptr_t>(main);
    const auto bridge = GetModuleHandleW(L"iron_bridge.dll");
    const auto add = bridge ? reinterpret_cast<IronRegisterOperationFn>(GetProcAddress(bridge, "IronRegisterOperation")) : nullptr;
    if (!add) return 11;
    const auto marker = reinterpret_cast<unsigned(*)()>(GetProcAddress(main, "IronBridgeTestHostMarker"));
    if (marker && marker() == 0x49524f4e) api = {nullptr, fake_read, fake_tag, fake_ai, fake_save};
    else {
        wchar_t path[32768]{};
        if (!GetModuleFileNameW(nullptr, path, 32768) || iron::sidecar::hash_file(path) != hash_pin) return 12;
        if (!iron::sidecar::pin(image,0x1339360,"48895c2408488974241848897c2420554154415541564157") ||
            !iron::sidecar::pin(image,0x2A9630,"488bc4488958184889501048894808555657415441554156") ||
            !iron::sidecar::pin(image,0x297C90,"488bc4488958184889702048894808555741544155415748") ||
            !iron::sidecar::pin(image,0x1168AE0,"40534883ec50488bd9488d4c2430e8dd6efbfe90488d4c24") ||
            !iron::sidecar::pin(image,0x120530,"40534883ec20488b5118488bd94883fa0f762c488b0948ff") ||
            !iron::sidecar::pin(image,0x25A0218,"e943eec8ffcccccc498bd0e9c8c7f2ff4883ec28e84b1200")) return 13;
        api = {nullptr, native_read, native_tag, native_ai, native_save};
    }
    return add("native_control_v1", callback);
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
