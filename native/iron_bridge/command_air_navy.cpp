#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <bcrypt.h>
#include <array>
#include <charconv>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <vector>
#include "bridge_api.h"
#include "command_air_navy.hpp"

namespace {
constexpr char game_hash[] = "bccc62909b6a1ec2c1d5a46ef18608e4d0c7cdc89ad341b7402a23c40f7a8037";
std::uintptr_t registered_image{};
template<class T> bool read(std::uintptr_t p, T& value) {
    SIZE_T got{};
    return ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(p), &value, sizeof(value), &got) && got == sizeof(value);
}
std::string error(const char* message) { return std::string("{\"ok\":false,\"error\":\"") + message + "\"}"; }
std::vector<std::string_view> words(std::string_view text) {
    std::vector<std::string_view> out;
    while (!text.empty()) {
        const auto p = text.find_first_not_of(" \r\n\t"); if (p == text.npos) break;
        text.remove_prefix(p); const auto n = text.find_first_of(" \r\n\t"); out.push_back(text.substr(0, n));
        if (n == text.npos) break; text.remove_prefix(n);
    }
    return out;
}
bool number(std::string_view text, unsigned& out) {
    const auto result = std::from_chars(text.data(), text.data() + text.size(), out);
    return result.ec == std::errc{} && result.ptr == text.data() + text.size();
}
bool array(std::uintptr_t address, std::vector<std::uintptr_t>& out) {
    std::uintptr_t data{}; int capacity{}, count{};
    if (!read(address, data) || !read(address + 8, capacity) || !read(address + 12, count) || count < 0 ||
        count > 200000 || capacity < count || capacity > 1000000 || (count && !data)) return false;
    out.resize(count); SIZE_T got{};
    return !count || (ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(data), out.data(), out.size() * 8, &got) && got == out.size() * 8);
}
bool typed(std::uintptr_t image, std::uintptr_t pointer, std::uintptr_t vtable) {
    std::uintptr_t actual{}; return pointer && read(pointer, actual) && actual == image + vtable;
}
struct Country { std::uintptr_t state{}, pointer{}; int index{}, played{}; };
bool country(std::uintptr_t image, std::string_view tag, Country& result) {
    if (tag.size() != 3) return false;
    for (char c : tag) if (c < 'A' || c > 'Z') return false;
    struct NativeString { char text[16]; std::uint64_t size, capacity; } name{};
    memcpy(name.text, tag.data(), 3); name.size = 3; name.capacity = 15;
    alignas(8) std::array<unsigned char, 16> native_tag{};
    using Parse = void* (__fastcall*)(void*, const NativeString*);
    using Lookup = void* (__fastcall*)(const void*);
    reinterpret_cast<Parse>(image + 0x1168ae0)(native_tag.data(), &name);
    memcpy(&result.index, native_tag.data(), 4);
    std::vector<std::uintptr_t> table;
    if (result.index <= 0 || !read(image + 0x33dd6b0, result.state) || !result.state ||
        !read(result.state + 0x520, result.played) || !array(result.state + 0x310, table) ||
        static_cast<std::size_t>(result.index) >= table.size()) return false;
    result.pointer = table[result.index];
    const auto native = reinterpret_cast<std::uintptr_t>(reinterpret_cast<Lookup>(image + 0xc07690)(native_tag.data()));
    int actual{};
    return native == result.pointer && typed(image, native, 0x2864730) && read(native + 8, actual) && actual == result.index;
}
struct Unit { std::uintptr_t pointer{}, fleet{}; std::uint64_t handle{}, fleet_handle{}; unsigned value{}, capability{}, region{}; std::vector<unsigned> regions; };
bool units(std::uintptr_t image, const Country& owner, bool air, std::vector<Unit>& out) {
    std::vector<std::uintptr_t> parents;
    if (air) {
        std::uintptr_t manager{}; std::vector<std::uintptr_t> countries;
        if (!read(owner.state + 0x690, manager) || !typed(image, manager, 0x29d5218) ||
            !array(manager + 0x30, countries) || static_cast<std::size_t>(owner.index) >= countries.size()) return false;
        const auto strategic = countries[owner.index];
        if (!strategic) return true;
        if (!typed(image, strategic, 0x29d50f8) || !array(strategic + 0x58, parents)) return false;
        for (const auto pool : parents) {
            std::uintptr_t actual_owner{}; std::vector<std::uintptr_t> wings;
            if (!typed(image, pool, 0x29f77d8) || !read(pool + 0x40, actual_owner) || actual_owner != strategic || !array(pool + 0x28, wings)) return false;
            for (const auto wing : wings) {
                Unit unit; unit.pointer = wing; std::uintptr_t actual_pool{};
                if (!typed(image, wing, 0x29f7748) || !read(wing + 0x18, unit.handle) || static_cast<unsigned>(unit.handle) != 69 ||
                    !read(wing + 0x38, actual_pool) || actual_pool != pool || !read(wing + 0xa0, unit.value) || !read(wing + 0x30, unit.capability)) return false;
                std::uintptr_t region{};
                if (!read(wing + 0xc0, region) || (region && (!typed(image, region, 0x29ea078) || !read(region + 0x58, unit.region)))) return false;
                out.push_back(unit);
            }
        }
    } else {
        if (!array(owner.pointer + 0x278, parents)) return false;
        for (const auto fleet : parents) {
            std::uintptr_t actual_owner{}; std::vector<std::uintptr_t> forces;
            if (!typed(image, fleet, 0x29df3d8) || !read(fleet + 0xa8, actual_owner) || actual_owner != owner.pointer || !array(fleet + 0xb8, forces)) return false;
            for (const auto force : forces) {
                Unit unit; unit.pointer = force; std::uintptr_t actual_fleet{};
                if (!typed(image, force, 0x29df900) || !read(force + 0x18, unit.handle) || !unit.handle ||
                    !read(force + 0x340, actual_fleet) || actual_fleet != fleet || !typed(image, force + 0x360, 0x29fb658) || !read(force + 0x374, unit.value)) return false;
                unit.fleet = fleet;
                if (!read(fleet + 8, unit.fleet_handle)) return false;
                std::vector<std::uintptr_t> regions;
                if (!array(fleet + 0x48, regions)) return false;
                for (const auto region : regions) { unsigned id{}; if (!typed(image, region, 0x29ea078) || !read(region + 0x58, id)) return false; unit.regions.push_back(id); }
                out.push_back(unit);
            }
        }
    }
    return true;
}
void describe(std::ostringstream& out, const Unit& unit, bool air) {
    out << "{\"id\":" << static_cast<unsigned>(unit.handle >> 32) << ",\"handle\":\"0x" << std::hex << unit.handle << std::dec
        << "\",\"current\":" << unit.value;
    if (air) out << ",\"capability_mask\":" << unit.capability << ",\"region\":" << unit.region;
    else { out << ",\"fleet_id\":" << static_cast<unsigned>(unit.fleet_handle >> 32) << ",\"regions\":["; for (std::size_t i = 0; i < unit.regions.size(); ++i) { if (i) out << ","; out << unit.regions[i]; } out << "]"; }
    out << '}';
}
using Destroy = void* (__fastcall*)(void*, unsigned);
bool region_at(std::uintptr_t image, std::uintptr_t state, unsigned id, std::uintptr_t& region) {
    std::vector<std::uintptr_t> table;
    return id && array(state + 0x2e0, table) && id < table.size() &&
        typed(image, region = table[id], 0x29ea078) && [&] { unsigned actual{}; return read(region + 0x58, actual) && actual == id; }();
}
bool region_ids(std::string_view text, std::vector<unsigned>& out) {
    if (text == "-") return true;
    while (!text.empty()) {
        const auto comma = text.find(','); unsigned id{};
        if (!number(text.substr(0, comma), id) || !id || out.size() >= 32) return false;
        for (auto prior : out) if (prior == id) return false;
        out.push_back(id); if (comma == text.npos) return true; text.remove_prefix(comma + 1);
    }
    return false;
}
std::string region_command(std::uintptr_t image, const std::vector<std::string_view>& args) {
    if (args[0] == "regions") {
        if (args.size() != 1) return error("regions accepts no arguments");
        std::uintptr_t state{}; std::vector<std::uintptr_t> table;
        if (!read(image + 0x33dd6b0, state) || !state || !array(state + 0x2e0, table)) return error("strategic region table unavailable");
        std::ostringstream out; out << "{\"ok\":true,\"regions\":["; bool first = true;
        for (unsigned id = 1; id < table.size(); ++id) {
            std::uintptr_t region{}; std::int64_t naval_count{};
            if (!region_at(image, state, id, region) || !read(region + 0xa0, naval_count)) return error("strategic region validation failed");
            if (!first) out << ','; first = false;
            out << "{\"id\":" << id << ",\"naval_eligible\":" << (naval_count > 0 ? "true" : "false") << '}';
        }
        out << "]}"; return out.str();
    }
    const bool air = args[0] == "air_region_build" || args[0] == "air_region_post";
    const bool post = args[0] == "air_region_post" || args[0] == "navy_regions_post";
    if (args.size() != 5) return error("expected region operation TAG id expectedRegions targetRegions");
    Country owner; unsigned id{};
    if (!country(image, args[1], owner) || !number(args[2], id)) return error("country or id resolver failed");
    if (post && owner.index != owner.played) return error("posting requires the current played country");
    std::vector<Unit> owned;
    if (!units(image, owner, air, owned)) return error("owned object validation failed");
    const Unit* selected{};
    for (const auto& unit : owned) if (static_cast<unsigned>(unit.handle >> 32) == id) { if (selected) return error("ambiguous owned id"); selected = &unit; }
    if (!selected) return error("id is not owned by requested country");
    unsigned expected{}, target{}; std::vector<unsigned> targets;
    if (air) {
        if (!number(args[3], expected) || !number(args[4], target) || expected != selected->region) return error("invalid or changed air region");
        if (target) targets.push_back(target);
    } else {
        std::vector<unsigned> before;
        if (!region_ids(args[3], before) || !region_ids(args[4], targets) || before != selected->regions) return error("invalid or changed fleet regions");
    }
    for (const auto region_id : targets) {
        std::uintptr_t region{}; std::int64_t naval_count{};
        if (!region_at(image, owner.state, region_id, region) || (!air && (!read(region + 0xa0, naval_count) || naval_count <= 0))) return error("invalid strategic region for operation");
    }
    std::uintptr_t idler{}; unsigned char paused{};
    if (!read(image + 0x33ddb10, idler) || !typed(image, idler, 0x29e58d8)) return error("idler unavailable");
    if (post && (!read(idler + 0x6c1, paused) || paused != 1)) return error("posting requires a paused game");
    using GetQueue = void* (__fastcall*)(void*); using Allocate = void* (__fastcall*)(std::size_t);
    auto queue = reinterpret_cast<GetQueue>(image + 0xb8e400)(reinterpret_cast<void*>(idler));
    if (!queue) return error("command queue unavailable");
    auto command = reinterpret_cast<Allocate>(image + 0x25a01dc)(air ? 0x60 : 0x48);
    if (!command) return error("game allocator returned null");
    if (air) {
        // UI caller 164F0BD uses the selected wing as both the target and settings source.
        using Ctor = void* (__fastcall*)(void*, void*, int, void*);
        reinterpret_cast<Ctor>(image + 0x19cc8e0)(command, reinterpret_cast<void*>(selected->pointer), static_cast<int>(target), reinterpret_cast<void*>(selected->pointer));
    } else {
        struct NativeIds { const unsigned* data; int capacity, count; void* allocator; } ids{targets.data(), static_cast<int>(targets.size()), static_cast<int>(targets.size()), nullptr};
        using Ctor = void* (__fastcall*)(void*, void*, const NativeIds*);
        reinterpret_cast<Ctor>(image + 0x13dc1e0)(command, reinterpret_cast<void*>(selected->fleet), &ids);
    }
    const auto address = reinterpret_cast<std::uintptr_t>(command); std::uintptr_t vtable{}, data{}; unsigned count{}, value{}; std::uint64_t handle{};
    bool verified = read(address, vtable) && vtable == image + (air ? 0x2a96b28 : 0x2a2a8c8);
    if (air) {
        std::uintptr_t settings{}; unsigned settings_count{}; std::uint64_t settings_handle{}; unsigned char flag{};
        verified = verified && read(address + 0x28, data) && read(address + 0x34, count) && count == 1 && read(data, handle) && handle == selected->handle &&
            read(address + 0x40, settings) && read(address + 0x4c, settings_count) && settings_count == 1 && read(settings, settings_handle) && settings_handle == selected->handle &&
            read(address + 0x58, value) && value == target && read(address + 0x5c, flag) && !flag;
    } else {
        verified = verified && read(address + 0x28, handle) && handle == selected->fleet_handle && read(address + 0x30, data) && read(address + 0x3c, count) && count == targets.size();
        for (std::size_t i = 0; verified && i < targets.size(); ++i) verified = read(data + 4 * i, value) && value == targets[i];
    }
    const auto destructor = image + (air ? 0x13e05e0 : 0xe52910);
    if (!verified) { reinterpret_cast<Destroy>(destructor)(command, 1); return error("constructed region command field verification failed"); }
    using CanExecute = bool (__fastcall*)(void*, void*);
    const bool can_execute = reinterpret_cast<CanExecute>(image + (air ? 0x19d4740 : 0x13ee750))(command, nullptr);
    bool accepted = false;
    if (post && can_execute) { using Post = bool (__fastcall*)(void*, void*, unsigned char); accepted = reinterpret_cast<Post>(image + 0x22c4250)(queue, command, 0); }
    else reinterpret_cast<Destroy>(destructor)(command, 1);
    std::ostringstream out; out << "{\"ok\":" << ((!post || accepted) ? "true" : "false") << ",\"constructed_verified\":true,\"can_execute\":" << (can_execute ? "true" : "false")
        << ",\"posted\":" << (post && can_execute ? "true" : "false") << ",\"queue_accepted\":" << (accepted ? "true" : "false") << ",\"scope\":\"" << (air ? "wing" : "fleet") << "\",\"before\":";
    describe(out, *selected, air); out << ",\"target_regions\":[";
    for (std::size_t i = 0; i < targets.size(); ++i) { if (i) out << ','; out << targets[i]; }
    out << "]}"; return out.str();
}
}

namespace iron {
std::string command_air_navy(std::uintptr_t image, std::string_view payload) {
    const auto args = words(payload);
    if (args.empty()) return error("expected air_list, navy_list, air_build, navy_build, air_post or navy_post");
    if (args[0] == "regions" || args[0] == "air_region_build" || args[0] == "air_region_post" || args[0] == "navy_regions_build" || args[0] == "navy_regions_post") return region_command(image, args);
    const bool air = args[0] == "air_list" || args[0] == "air_build" || args[0] == "air_post";
    const bool navy = args[0] == "navy_list" || args[0] == "navy_build" || args[0] == "navy_post";
    const bool list = args[0] == "air_list" || args[0] == "navy_list";
    const bool post = args[0] == "air_post" || args[0] == "navy_post";
    if ((!air && !navy) || args.size() != (list ? 2u : air ? 6u : 5u)) return error("invalid air/navy payload");
    Country owner;
    if (!country(image, args[1], owner)) return error("country resolver failed");
    std::vector<Unit> owned;
    if (!units(image, owner, air, owned)) return error("owned object validation failed");
    if (list) {
        std::ostringstream out; out << "{\"ok\":true,\"country\":\"" << args[1] << "\",\"played\":" << (owner.index == owner.played ? "true" : "false") << ",\"units\":[";
        for (std::size_t i = 0; i < owned.size(); ++i) { if (i) out << ','; describe(out, owned[i], air); }
        out << "]}"; return out.str();
    }
    unsigned id{}, expected{}, target{}, enabled{};
    if (!number(args[2], id) || !number(args[3], expected) || !number(args[4], target) ||
        (air && (!number(args[5], enabled) || enabled > 1 || !target || (target & (target - 1)))) ||
        (!air && target > 9)) return error("invalid id, expected state or mission");
    if (post && owner.index != owner.played) return error("posting requires the current played country");
    const Unit* selected{};
    for (const auto& unit : owned) if (static_cast<unsigned>(unit.handle >> 32) == id) { if (selected) return error("ambiguous owned id"); selected = &unit; }
    if (!selected) return error("id is not owned by the requested country");
    if (selected->value != expected) return error("mission state changed since snapshot");
    if (air && enabled && !(selected->capability & target)) return error("wing does not support mission");
    std::uintptr_t idler{};
    if (!read(image + 0x33ddb10, idler) || !typed(image, idler, 0x29e58d8)) return error("idler unavailable");
    unsigned char paused{};
    if (post && (!read(idler + 0x6c1, paused) || paused != 1)) return error("posting requires a paused game");
    using GetQueue = void* (__fastcall*)(void*);
    auto queue = reinterpret_cast<GetQueue>(image + 0xb8e400)(reinterpret_cast<void*>(idler));
    if (!queue) return error("command queue unavailable");
    using Allocate = void* (__fastcall*)(std::size_t);
    auto command = reinterpret_cast<Allocate>(image + 0x25a01dc)(0x48);
    if (!command) return error("game allocator returned null");
    if (air) {
        using Ctor = void* (__fastcall*)(void*, void*, int, unsigned char);
        reinterpret_cast<Ctor>(image + 0x19ccb20)(command, reinterpret_cast<void*>(selected->pointer), static_cast<int>(target), static_cast<unsigned char>(enabled));
    } else {
        using Ctor = void* (__fastcall*)(void*, const std::uintptr_t*, int, int, unsigned char);
        reinterpret_cast<Ctor>(image + 0x13dc950)(command, &selected->pointer, 1, static_cast<int>(target), 1);
    }
    const auto address = reinterpret_cast<std::uintptr_t>(command);
    std::uintptr_t vtable{}, data{}; unsigned count{}, capacity{}, mission{}; std::uint64_t handle{}; unsigned char flag1{}, flag2{}, flag3{};
    const bool verified = read(address, vtable) && vtable == image + (air ? 0x2a96be8 : 0x2a2a808) &&
        read(address + 0x28, data) && data && read(address + 0x30, capacity) && capacity >= 1 && read(address + 0x34, count) && count == 1 &&
        read(data, handle) && handle == selected->handle && read(address + 0x40, mission) && mission == target &&
        read(address + 0x44, flag1) && flag1 == (air ? enabled : 0) && read(address + 0x45, flag2) && flag2 == (air ? 0 : 1) &&
        (!air || (read(address + 0x46, flag3) && !flag3));
    if (!verified) { reinterpret_cast<Destroy>(image + 0xf012e0)(command, 1); return error("constructed command field verification failed"); }
    using CanExecute = bool (__fastcall*)(void*, void*);
    const bool can_execute = reinterpret_cast<CanExecute>(image + (air ? 0x19d4b30 : 0x13ee8c0))(command, nullptr);
    bool accepted = false;
    if (post && can_execute) {
        using Post = bool (__fastcall*)(void*, void*, unsigned char);
        accepted = reinterpret_cast<Post>(image + 0x22c4250)(queue, command, 0);
    } else reinterpret_cast<Destroy>(image + 0xf012e0)(command, 1);
    std::ostringstream out;
    out << "{\"ok\":" << ((!post || accepted) ? "true" : "false") << ",\"constructed_verified\":true,\"can_execute\":" << (can_execute ? "true" : "false")
        << ",\"posted\":" << (post && can_execute ? "true" : "false") << ",\"queue_accepted\":" << (accepted ? "true" : "false") << ",\"mission\":" << target << ",\"before\":";
    describe(out, *selected, air); out << '}'; return out.str();
}
}

namespace {
const char* __cdecl callback(std::uintptr_t image, const char* payload) {
    static thread_local std::string result;
    result = image == registered_image ? iron::command_air_navy(image, payload) : error("image base mismatch"); return result.c_str();
}
std::string hash_file(const wchar_t* path) {
    BCRYPT_ALG_HANDLE algorithm{}; BCRYPT_HASH_HANDLE hash{};
    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) < 0) return {};
    DWORD size{}, returned{}; BCryptGetProperty(algorithm, BCRYPT_OBJECT_LENGTH, reinterpret_cast<PUCHAR>(&size), sizeof(size), &returned, 0);
    std::string object(size, '\0');
    if (BCryptCreateHash(algorithm, &hash, reinterpret_cast<PUCHAR>(object.data()), size, nullptr, 0, 0) < 0) { BCryptCloseAlgorithmProvider(algorithm, 0); return {}; }
    std::ifstream file(path, std::ios::binary); std::array<char, 65536> buffer{}; bool valid = file.is_open();
    while (file) { file.read(buffer.data(), buffer.size()); if (file.gcount() && BCryptHashData(hash, reinterpret_cast<PUCHAR>(buffer.data()), static_cast<ULONG>(file.gcount()), 0) < 0) valid = false; }
    std::array<unsigned char, 32> digest{}; if (BCryptFinishHash(hash, digest.data(), static_cast<ULONG>(digest.size()), 0) < 0) valid = false;
    BCryptDestroyHash(hash); BCryptCloseAlgorithmProvider(algorithm, 0);
    std::ostringstream out; for (auto b : digest) out << std::hex << std::setw(2) << std::setfill('0') << static_cast<unsigned>(b);
    return valid ? out.str() : "";
}
DWORD WINAPI initialize(void*) {
    auto main = GetModuleHandleW(nullptr); registered_image = reinterpret_cast<std::uintptr_t>(main);
    auto bridge = GetModuleHandleW(L"iron_bridge.dll");
    auto add = bridge ? reinterpret_cast<IronRegisterOperationFn>(GetProcAddress(bridge, "IronRegisterOperation")) : nullptr;
    if (!add) return 11;
    wchar_t path[32768]{};
    if (!GetModuleFileNameW(nullptr, path, 32768) || hash_file(path) != game_hash) return 12;
    return add("native_air_navy_v2", callback);
}
}
BOOL WINAPI DllMain(HINSTANCE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module); HANDLE thread = CreateThread(nullptr, 0, initialize, nullptr, 0, nullptr);
        if (!thread) return FALSE; CloseHandle(thread);
    }
    return TRUE;
}
