#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <charconv>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <string>
#include <string_view>
#include <vector>
#include "bridge_api.h"
#include "sidecar_pins.hpp"

namespace {
std::uintptr_t image{};
bool synthetic{};
bool fake_hidden{};
bool fake_locked{};
float fake_x{}, fake_z{};
struct NativeString { char data[16]{}; std::uint64_t size{}, capacity = 15; };
struct Result { unsigned char flag{}; unsigned char pad[7]{}; NativeString message{}; };
static_assert(sizeof(Result) == 40);
struct Snapshot {
    bool paused{}, hidden{}, locked{};
    int date{}, province_count{};
    std::uintptr_t idler{}, camera{};
    float position[3]{}, target[3]{}, origin[3]{};
};
template<class T> bool read(std::uintptr_t address, T& value) {
    SIZE_T count{};
    return ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(address), &value, sizeof(value), &count) && count == sizeof(value);
}
bool snapshot(Snapshot& out) {
    if (synthetic) {
        out.paused = true; out.hidden = fake_hidden; out.locked = fake_locked; out.date = 60765285; out.province_count = 20000;
        out.position[0] = out.target[0] = fake_x; out.position[2] = out.target[2] = fake_z;
        return true;
    }
    std::uintptr_t state{}, vtable{}, getter{};
    unsigned char paused{}, hidden{}, locked{};
    if (!read(image + 0x33DDB08, out.idler) || !out.idler || !read(out.idler, vtable) || vtable != image + 0x29E58D8 ||
        !read(vtable + 0x130, getter) || getter != image + 0xE33A10 ||
        !read(out.idler + 0x760, out.camera) || !out.camera ||
        !read(out.idler + 0x6C1, paused) || paused > 1 || !read(image + 0x33DDB19, hidden) || hidden > 1 ||
        !read(image + 0x33DD6B0, state) || !state || !read(state + 0x468, out.date) ||
        !read(state + 0x2BC, out.province_count) || out.province_count < 1 || out.province_count > 100000 ||
        !read(out.camera + 0x6F0, locked) || locked > 1 ||
        !read(out.camera + 0x680, out.position) || !read(out.camera + 0x698, out.target) ||
        !read(out.camera + 0x6A4, out.origin)) return false;
    for (float value : out.position) if (!std::isfinite(value)) return false;
    for (float value : out.target) if (!std::isfinite(value)) return false;
    out.paused = paused != 0; out.hidden = hidden != 0; out.locked = locked != 0;
    return true;
}
std::string state_json(const Snapshot& s) {
    auto vector = [](const float* p) { return "[" + std::to_string(p[0]) + "," + std::to_string(p[1]) + "," + std::to_string(p[2]) + "]"; };
    return "{\"paused\":" + std::string(s.paused ? "true" : "false") + ",\"hud_hidden\":" + (s.hidden ? "true" : "false") +
        ",\"camera_locked\":" + (s.locked ? "true" : "false") + ",\"date_raw\":" + std::to_string(s.date) +
        ",\"camera_position\":" + vector(s.position) + ",\"camera_target\":" + vector(s.target) + "}";
}
std::string error(const char* text) { return "{\"ok\":false,\"error\":\"" + std::string(text) + "\"}"; }
std::vector<std::string_view> split(std::string_view text) {
    std::vector<std::string_view> words;
    while (!text.empty()) {
        const auto first = text.find_first_not_of(" \t"); if (first == text.npos) break;
        text.remove_prefix(first); const auto end = text.find_first_of(" \t"); words.push_back(text.substr(0, end));
        if (end == text.npos) break; text.remove_prefix(end);
    }
    return words;
}
std::string command(std::string_view payload) {
    const auto args = split(payload);
    const bool status = args.size() == 1 && args[0] == "status";
    const bool hud = args.size() == 3 && args[0] == "hud";
    const bool lock = args.size() == 3 && args[0] == "lock_camera";
    const bool center = args.size() == 2 && args[0] == "center_province";
    const bool frame = args.size() == 3 && args[0] == "frame_province";
    if (!status && !hud && !lock && !center && !frame) return error("expected status, hud/lock_camera EXPECTED DESIRED, center_province ID or frame_province ID HEIGHT");
    Snapshot before;
    if (!snapshot(before)) return error("native recording-view snapshot unavailable");
    if (status) return "{\"ok\":true,\"state\":" + state_json(before) + "}";
    if (!before.paused) return error("pause the game before changing the recording view");
    bool already = false;
    int province{}, height{};
    if (frame) {
        const auto parsed = std::from_chars(args[2].data(), args[2].data() + args[2].size(), height);
        if (parsed.ec != std::errc{} || parsed.ptr != args[2].data() + args[2].size() || height < 50 || height > 1200)
            return error("camera height must be an integer 50..1200");
    }
    if (hud || lock) {
        if ((args[1] != "0" && args[1] != "1") || (args[2] != "0" && args[2] != "1")) return error("states must be 0 or 1");
        const bool expected = args[1] == "1", desired = args[2] == "1";
        const bool actual = hud ? before.hidden : before.locked;
        already = actual == desired;
        if (!already && actual != expected) return error("expected state mismatch");
        if (!already) {
            if (synthetic) { if (hud) fake_hidden = desired; else fake_locked = desired; }
            else {
                Result result;
                // Exact vanilla debug_nogui handler: only initializes Result and toggles the GUI flag.
                using Toggle = Result* (__fastcall*)(Result*);
                reinterpret_cast<Toggle>(image + (hud ? 0x263D70 : 0x263AD0))(&result);
                const bool success = result.flag == 1;
                // The camera-lock handler returns an allocated status string.
                using Destroy = void (__fastcall*)(NativeString*);
                reinterpret_cast<Destroy>(image + 0x120530)(&result.message);
                if (!success) return error("native view handler failed");
            }
        }
    } else {
        const auto parsed = std::from_chars(args[1].data(), args[1].data() + args[1].size(), province);
        if (parsed.ec != std::errc{} || parsed.ptr != args[1].data() + args[1].size() || province <= 0 || province >= before.province_count)
            return error("province ID is outside the current map");
        if (synthetic) { fake_x = static_cast<float>(province); fake_z = 1; }
        else {
            // Vanilla goto_province calls this idler method through vslot 0x1B8.
            // It resolves the province position and uses the ordinary camera transition.
            using Center = void (__fastcall*)(void*, int);
            reinterpret_cast<Center>(image + 0xE347C0)(reinterpret_cast<void*>(before.idler), province);
        }
        if (frame && !synthetic) {
            Snapshot centered;
            if (!snapshot(centered)) return error("province camera target unavailable");
            const float point[3]{centered.origin[0], static_cast<float>(height), centered.origin[2]};
            // The ordinary idler camera method calls this same camera-target setter.
            using Target = void (__fastcall*)(void*, const float*);
            reinterpret_cast<Target>(image + 0x12F15C0)(reinterpret_cast<void*>(centered.camera), point);
        }
    }
    Snapshot after;
    if (!snapshot(after) || !after.paused || after.date != before.date) return error("recording view changed without a stable paused date");
    if (frame && synthetic) after.target[1] = static_cast<float>(height);
    if (hud && after.hidden != (args[2] == "1")) return error("HUD effect was not observed");
    if (lock && after.locked != (args[2] == "1")) return error("camera lock effect was not observed");
    return "{\"ok\":true,\"already_desired\":" + std::string(already ? "true" : "false") +
        ((center || frame) ? ",\"center_requested_province\":" + std::to_string(province) + ",\"camera_request_accepted\":true" : ",\"effect_verified\":true") +
        ",\"state\":" + state_json(after) + "}";
}
const char* __cdecl callback(std::uintptr_t base, const char* payload) {
    static thread_local std::string result;
    result = base == image ? command(payload) : error("image mismatch");
    return result.c_str();
}
DWORD WINAPI initialize(void*) {
    const auto main = GetModuleHandleW(nullptr); image = reinterpret_cast<std::uintptr_t>(main);
    const auto bridge = GetModuleHandleW(L"iron_bridge.dll");
    const auto add = bridge ? reinterpret_cast<IronRegisterOperationFn>(GetProcAddress(bridge, "IronRegisterOperation")) : nullptr;
    if (!add) return 11;
    const auto marker = reinterpret_cast<unsigned(*)()>(GetProcAddress(main, "IronBridgeTestHostMarker"));
    synthetic = marker && marker() == 0x49524F4E;
    if (!synthetic) {
        wchar_t path[32768]{};
        if (!GetModuleFileNameW(nullptr, path, 32768) || iron::sidecar::hash_file(path) != "bccc62909b6a1ec2c1d5a46ef18608e4d0c7cdc89ad341b7402a23c40f7a8037") return 12;
        if (!iron::sidecar::pin(image, 0x263D70, "4883ec1833c00f57c088010f1141084889411848c741200f0000008841083805859d1703") ||
            !iron::sidecar::pin(image, 0xE347C0, "40574883ec30803d44ae850200488bf9") ||
            !iron::sidecar::pin(image, 0xE33A10, "488b8160070000c3") ||
            !iron::sidecar::pin(image, 0x263AD0, "48895c2410488974241848897c242048894c240841564883ec30") ||
            !iron::sidecar::pin(image, 0x120530, "40534883ec20488b5118488bd94883fa0f762c488b0948ff") ||
            !iron::sidecar::pin(image, 0x12F15C0, "48895c2408574883ec20f20f1002488bda")) return 13;
    }
    return add("native_view_v3", callback);
}
}
BOOL WINAPI DllMain(HINSTANCE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        HANDLE thread = CreateThread(nullptr, 0, initialize, nullptr, 0, nullptr);
        if (!thread) return FALSE;
        CloseHandle(thread);
    }
    return TRUE;
}
