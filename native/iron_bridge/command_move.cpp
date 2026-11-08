#include "command_move.hpp"
#include <Windows.h>
#include <array>
#include <charconv>
#include <cstring>
#include <sstream>
#include <vector>

namespace iron {
namespace {
constexpr std::uintptr_t kArmyVtable = 0x29d6d98;
constexpr std::uintptr_t kIdlerRoot = 0x33ddb10;
constexpr std::uintptr_t kIdlerVtable = 0x29e58d8;
constexpr std::uintptr_t kGetQueue = 0xb8e400;
constexpr std::uintptr_t kNew = 0x25a01dc;
constexpr std::uintptr_t kMoveCtor = 0x13f6c30;
constexpr std::uintptr_t kMoveVtable = 0x2a2cde0;
constexpr std::uintptr_t kMoveActionVtable = 0x2a1fac8;
constexpr std::uintptr_t kMoveDelete = 0x13f8610;
constexpr std::uintptr_t kPost = 0x22c4250;

using Allocate = void* (__fastcall*)(std::size_t);
using MoveCtor = void* (__fastcall*)(void*, void*, int, unsigned char, int,
    unsigned char, unsigned char, unsigned char, unsigned char, unsigned char);
using DeleteMove = void* (__fastcall*)(void*, unsigned int);
using GetQueue = void* (__fastcall*)(void*);
using Post = bool (__fastcall*)(void*, void*, unsigned char);

template<typename T> bool read(std::uintptr_t address, T& value) {
    SIZE_T received = 0;
    return ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(address),
        &value, sizeof(value), &received) && received == sizeof(value);
}
std::string error(const char* message) {
    return std::string("{\"ok\":false,\"error\":\"") + message + "\"}";
}
bool number(std::string_view text, std::uint64_t& value, int radix) {
    if (radix == 16 && text.size() > 2 && text.substr(0, 2) == "0x") text.remove_prefix(2);
    if (text.empty()) return false;
    auto result = std::from_chars(text.data(), text.data() + text.size(), value, radix);
    return result.ec == std::errc{} && result.ptr == text.data() + text.size();
}
std::vector<std::string_view> split(std::string_view value) {
    std::vector<std::string_view> result;
    while (!value.empty()) {
        const auto first = value.find_first_not_of(" \t\r\n");
        if (first == std::string_view::npos) break;
        value.remove_prefix(first);
        const auto end = value.find_first_of(" \t\r\n");
        result.push_back(value.substr(0, end));
        if (end == std::string_view::npos) break;
        value.remove_prefix(end);
    }
    return result;
}
struct Snapshot {
    std::uint64_t handle{};
    int province{};
    std::vector<int> path;
};
bool snapshot(std::uintptr_t base, std::uintptr_t army, Snapshot& result) {
    std::uintptr_t vtable{}, province{}, path{};
    int count{};
    if (!read(army, vtable) || vtable != base + kArmyVtable ||
        !read(army + 0x18, result.handle) || !read(army + 0x1f0, province) ||
        !province || !read(province + 0xa4, result.province) ||
        !read(army + 0x200, path) || !read(army + 0x20c, count) ||
        count < 0 || count > 8192 || (count && !path)) return false;
    result.path.resize(count);
    if (count) {
        SIZE_T received{};
        if (!ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(path),
            result.path.data(), count * sizeof(int), &received) ||
            received != count * sizeof(int)) return false;
    }
    return true;
}
void append_snapshot(std::ostringstream& out, const Snapshot& state) {
    out << "\"handle\":\"0x" << std::hex << state.handle << std::dec
        << "\",\"province\":" << state.province << ",\"path\":[";
    for (std::size_t i = 0; i < state.path.size(); ++i) {
        if (i) out << ',';
        out << state.path[i];
    }
    out << ']';
}
// Semantic resolution uses the game's tag lookup, then its current owned-division array.
bool own_armies(std::uintptr_t base, std::string_view tag, std::vector<std::uintptr_t>& armies) {
    if (tag.size() != 3) return false;
    for (const auto c : tag) if (c < 'A' || c > 'Z') return false;
    struct NativeString { char text[16]; std::uint64_t size; std::uint64_t capacity; };
    NativeString name{};
    std::memcpy(name.text, tag.data(), tag.size());
    name.size = tag.size();
    name.capacity = 15;
    alignas(8) std::array<unsigned char, 16> native_tag{};
    using ParseTag = void* (__fastcall*)(void*, const NativeString*);
    using CountryOf = void* (__fastcall*)(const void*);
    reinterpret_cast<ParseTag>(base + 0x1168ae0)(native_tag.data(), &name);
    int index{};
    std::memcpy(&index, native_tag.data(), sizeof(index));
    std::uintptr_t state{}, country{}, table{}, vtable{}, army_data{};
    int played{}, country_count{}, country_index{}, count{}, capacity{};
    if (index <= 0 || !read(base + 0x33dd6b0, state) || !state ||
        !read(state + 0x520, played) || played != index ||
        !read(state + 0x310, table) || !read(state + 0x31c, country_count) ||
        index >= country_count || !read(table + index * sizeof(void*), country)) return false;
    const auto native_country = reinterpret_cast<std::uintptr_t>(
        reinterpret_cast<CountryOf>(base + 0xc07690)(native_tag.data()));
    if (!country || country != native_country || !read(country, vtable) || vtable != base + 0x2864730 ||
        !read(country + 8, country_index) || country_index != index ||
        !read(country + 0x290, army_data) || !read(country + 0x298, capacity) ||
        !read(country + 0x29c, count) || count < 0 || count > 200000 ||
        capacity < count || (count && !army_data)) return false;
    armies.resize(count);
    if (count) {
        SIZE_T received{};
        if (!ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(army_data), armies.data(),
            count * sizeof(void*), &received) || received != count * sizeof(void*)) return false;
    }
    return true;
}
}

std::string command_move(std::uintptr_t base, std::string_view payload) {
    const auto words = split(payload);
    if (words.empty()) return error("expected inspect, build or post");
    if (words[0] == "units" || words[0] == "inspect_unit" ||
        words[0] == "build_unit" || words[0] == "post_unit") {
        const bool list = words[0] == "units";
        const bool inspect_unit = words[0] == "inspect_unit";
        if (words.size() != (list ? 2u : inspect_unit ? 3u : 5u)) return error("invalid semantic move payload");
        std::vector<std::uintptr_t> armies;
        if (!own_armies(base, words[1], armies)) return error("tag is not the current played country or army table unavailable");
        std::uint64_t unit_id{};
        if (!list && (!number(words[2], unit_id, 10) || unit_id > UINT32_MAX)) return error("invalid unit id");
        std::ostringstream out;
        if (list) out << "{\"ok\":true,\"country\":\"" << words[1] << "\",\"units\":[";
        bool first = true;
        for (const auto army : armies) {
            Snapshot current;
            if (!snapshot(base, army, current)) return error("owned division failed snapshot validation");
            const auto id = static_cast<std::uint32_t>(current.handle >> 32);
            if (list) {
                if (!first) out << ',';
                first = false;
                out << "{\"unit_id\":" << id << ',';
                append_snapshot(out, current);
                out << '}';
            } else if (id == unit_id) {
                std::ostringstream raw;
                raw << (inspect_unit ? "inspect" : words[0] == "build_unit" ? "build" : "post")
                    << ' ' << std::hex << army;
                if (!inspect_unit) raw << ' ' << current.handle << std::dec << ' ' << words[3] << ' ' << words[4];
                return command_move(base, raw.str());
            }
        }
        if (list) { out << "]}"; return out.str(); }
        return error("unit is not owned by the current played country");
    }
    const bool inspect = words[0] == "inspect";
    const bool build = words[0] == "build";
    const bool post = words[0] == "post";
    if ((!inspect && !build && !post) || words.size() != (inspect ? 2u : 5u))
        return error("invalid move payload");
    std::uint64_t army_value{}, expected_handle{}, from{}, target{};
    if (!number(words[1], army_value, 16)) return error("invalid division pointer");
    const auto army = static_cast<std::uintptr_t>(army_value);
    Snapshot before;
    if (!snapshot(base, army, before)) return error("division snapshot unavailable or wrong RTTI");
    if (inspect) {
        std::ostringstream out;
        out << "{\"ok\":true,\"kind\":\"move_snapshot\",\"division\":\"0x" << std::hex << army
            << std::dec << "\",";
        append_snapshot(out, before);
        out << '}';
        return out.str();
    }
    if (!number(words[2], expected_handle, 16) || !number(words[3], from, 10) ||
        !number(words[4], target, 10) || from > 1000000 || !target || target > 1000000)
        return error("invalid handle or province");
    if (before.handle != expected_handle || before.province != static_cast<int>(from))
        return error("division handle or source province changed");
    // Handle kind 51 is the CArmy kind; the full pair must match the caller's snapshot.
    if (static_cast<std::uint32_t>(before.handle) != 51) return error("wrong division handle kind");
    if (!before.path.empty()) return error("division already has a movement path");
    if (before.province == static_cast<int>(target)) return error("target equals source");
    std::uintptr_t idler{}, idler_vtable{};
    if (!read(base + kIdlerRoot, idler) || !idler || !read(idler, idler_vtable) ||
        idler_vtable != base + kIdlerVtable) return error("in-game idler unavailable");
    auto queue = reinterpret_cast<GetQueue>(base + kGetQueue)(reinterpret_cast<void*>(idler));
    if (!queue) return error("command queue unavailable");
    auto command = reinterpret_cast<Allocate>(base + kNew)(0x88);
    if (!command) return error("game allocator returned null");
    // Exact MSVC constructor ABI and allocation size recovered from the game's UI caller.
    reinterpret_cast<MoveCtor>(base + kMoveCtor)(command, reinterpret_cast<void*>(army),
        static_cast<int>(target), 1, 2, 1, 0, 0, 0, 0);
    const auto address = reinterpret_cast<std::uintptr_t>(command);
    std::uintptr_t command_vtable{}, action_vtable{}, target_path{};
    std::uint64_t command_handle{};
    int token{}, capacity{}, count{}, first{};
    const bool verified = read(address, command_vtable) && command_vtable == base + kMoveVtable &&
        read(address + 0x28, action_vtable) && action_vtable == base + kMoveActionVtable &&
        read(address + 0x30, token) && token == 0x3648 &&
        read(address + 0x38, command_handle) && command_handle == before.handle &&
        read(address + 0x40, target_path) && target_path &&
        read(address + 0x48, capacity) && read(address + 0x4c, count) &&
        count == 1 && capacity >= count && read(target_path, first) && first == static_cast<int>(target);
    if (!verified) {
        reinterpret_cast<DeleteMove>(base + kMoveDelete)(command, 1);
        return error("constructed command failed field verification");
    }
    bool accepted = false;
    if (post) {
        // Post owns the object on both success and rejection. Never read it afterwards.
        accepted = reinterpret_cast<Post>(base + kPost)(queue, command, 0);
    } else {
        reinterpret_cast<DeleteMove>(base + kMoveDelete)(command, 1);
    }
    std::ostringstream out;
    out << "{\"ok\":" << ((!post || accepted) ? "true" : "false")
        << ",\"kind\":\"native_move\",\"constructed_verified\":true,\"posted\":"
        << (post ? "true" : "false") << ",\"queue_accepted\":" << (accepted ? "true" : "false")
        << ",\"division\":\"0x" << std::hex << army << "\",\"queue\":\"0x"
        << reinterpret_cast<std::uintptr_t>(queue) << std::dec << "\",\"target\":" << target
        << ",\"before\":{";
    append_snapshot(out, before);
    out << "}}";
    return out.str();
}
}
