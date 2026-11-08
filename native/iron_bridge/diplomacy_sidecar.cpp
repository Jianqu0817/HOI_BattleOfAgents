#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <bcrypt.h>
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <unordered_map>
#include "bridge_api.h"
#include "diplomacy.hpp"
#include "sidecar_pins.hpp"
namespace {
constexpr char hash_pin[] = "bccc62909b6a1ec2c1d5a46ef18608e4d0c7cdc89ad341b7402a23c40f7a8037";
struct NativeString { char text[16]; std::uint64_t size{}, capacity{15}; };
struct NativeContext {
    std::uintptr_t image{}, state{}, idler{}, queue{}, actor{}, recipient{};
    iron::DiplomacySnapshot last;
    std::unordered_map<std::uint64_t, std::uintptr_t> goals;
} context;
template<class T> bool read(std::uintptr_t address, T& value) {
    SIZE_T count{};
    return ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(address), &value, sizeof(value), &count) && count == sizeof(value);
}
bool array(std::uintptr_t address, std::vector<std::uintptr_t>& values) {
    std::uintptr_t data{}; int capacity{}, count{};
    if (!read(address, data) || !read(address + 8, capacity) || !read(address + 12, count) ||
        count < 0 || count > 200000 || capacity < count || (count && !data)) return false;
    values.resize(count);
    if (count) {
        SIZE_T got{};
        if (!ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(data), values.data(), count * sizeof(void*), &got) ||
            got != count * sizeof(void*)) return false;
    }
    return true;
}
bool country(std::string_view tag, int& index, std::uintptr_t& pointer) {
    NativeString name{}; name.size = tag.size(); memcpy(name.text, tag.data(), tag.size());
    alignas(8) std::array<unsigned char, 16> native_tag{};
    using Parse = void* (__fastcall*)(void*, const NativeString*);
    using Lookup = void* (__fastcall*)(const void*);
    reinterpret_cast<Parse>(context.image + 0x1168AE0)(native_tag.data(), &name);
    memcpy(&index, native_tag.data(), sizeof(index));
    if (index <= 0) return false;
    pointer = reinterpret_cast<std::uintptr_t>(reinterpret_cast<Lookup>(context.image + 0xC07690)(native_tag.data()));
    std::uintptr_t table{}, expected{}, vtable{}; int count{}, own_index{};
    return pointer && read(pointer, vtable) && vtable == context.image + 0x2864730 &&
        read(pointer + 8, own_index) && own_index == index && read(context.state + 0x310, table) &&
        read(context.state + 0x31C, count) && index < count && read(table + index * sizeof(void*), expected) && expected == pointer;
}
bool diplomacy(std::uintptr_t owner, std::uintptr_t& pointer) {
    std::uintptr_t vtable{};
    return read(owner + 0xF88, pointer) && pointer && read(pointer, vtable) && vtable == context.image + 0x29DE5E8;
}
bool native_read(void*, std::string_view actor, std::string_view recipient, iron::DiplomacySnapshot& out) {
    context.goals.clear(); out = {};
    std::uintptr_t vtable{}, actor_diplomacy{}, recipient_diplomacy{}; unsigned char paused{};
    if (!read(context.image + 0x33DD6B0, context.state) || !context.state ||
        !read(context.image + 0x33DDB10, context.idler) || !context.idler ||
        !read(context.idler, vtable) || vtable != context.image + 0x29E58D8 ||
        !read(context.idler + 0x6C1, paused) || paused > 1 ||
        !read(context.state + 0x520, out.played) || !read(context.state + 0x468, out.date_raw) ||
        !country(actor, out.actor, context.actor) || !diplomacy(context.actor, actor_diplomacy)) return false;
    out.paused = paused != 0;
    std::vector<std::uintptr_t> owned, available, actor_relations, recipient_relations;
    if (!array(actor_diplomacy + 0x80, owned) || !array(actor_diplomacy + 0x68, available)) return false;
    for (const auto goal : owned) {
        iron::DiplomacyGoal value;
        if (!read(goal, vtable) || vtable != context.image + 0x2A59250 || !read(goal + 8, value.handle) ||
            !read(goal + 0x38, value.actor) || !read(goal + 0x3C, value.recipient) || !read(goal + 0x30, value.type)) return false;
        value.available = std::find(available.begin(), available.end(), goal) != available.end();
        out.goals.push_back(value);
        if (value.actor == out.actor && value.available) context.goals.emplace(value.handle, goal);
    }
    context.recipient = 0;
    if (!recipient.empty()) {
        if (!country(recipient, out.recipient, context.recipient) || !diplomacy(context.recipient, recipient_diplomacy) ||
            !array(actor_diplomacy + 0x20, actor_relations) || !array(recipient_diplomacy + 0x20, recipient_relations)) return false;
        for (const auto relation : actor_relations) {
            if (!read(relation, vtable)) return false;
            // Active relations also contain guarantees. Only CWarRelation proves war.
            if (vtable != context.image + 0x29DCC08 ||
                std::find(recipient_relations.begin(), recipient_relations.end(), relation) == recipient_relations.end()) continue;
            int first{}, second{};
            if (!read(relation + 0x10, first) || !read(relation + 0x14, second)) return false;
            if ((first == out.actor && second == out.recipient) || (first == out.recipient && second == out.actor))
                ++out.shared_war_relations;
        }
        out.at_war = out.shared_war_relations != 0;
    }
    using GetQueue = void* (__fastcall*)(void*);
    context.queue = reinterpret_cast<std::uintptr_t>(reinterpret_cast<GetQueue>(context.image + 0xB8E400)(reinterpret_cast<void*>(context.idler)));
    context.last = out;
    return context.queue != 0;
}
bool native_declare(void*, std::uint64_t handle, bool post, iron::DeclareResult& result) {
    const auto found = context.goals.find(handle);
    if (found == context.goals.end() || !context.last.paused || context.last.played != context.last.actor || context.last.at_war) return false;
    unsigned char paused{}; int played{}, actor{}, recipient{}; std::uint64_t current_handle{};
    if (!read(context.idler + 0x6C1, paused) || paused != 1 || !read(context.state + 0x520, played) ||
        played != context.last.actor || !read(found->second + 8, current_handle) || current_handle != handle ||
        !read(found->second + 0x38, actor) || actor != played || !read(found->second + 0x3C, recipient) || recipient != context.last.recipient) return false;
    struct Scope { std::uintptr_t first; int date; unsigned int padding; std::uintptr_t second; };
    Scope scope{context.image + 0x27963D0, context.last.date_raw, 0, context.image + 0x2796400};
    using Allocate = void* (__fastcall*)(std::size_t);
    using ActionCtor = void* (__fastcall*)(void*, const void*, const void*, void*, unsigned char, void*);
    using CommandCtor = void* (__fastcall*)(void*, void*);
    using Destroy = void* (__fastcall*)(void*, unsigned int);
    using CanExecute = bool (__fastcall*)(void*, void*);
    using Post = bool (__fastcall*)(void*, void*, unsigned char);
    const auto allocate = reinterpret_cast<Allocate>(context.image + 0x25A01DC);
    auto action = allocate(0x88);
    if (!action) return false;
    reinterpret_cast<ActionCtor>(context.image + 0x1180B70)(action, reinterpret_cast<void*>(context.actor + 8),
        reinterpret_cast<void*>(context.recipient + 8), reinterpret_cast<void*>(found->second), 0, &scope);
    const auto address = reinterpret_cast<std::uintptr_t>(action);
    std::uintptr_t vtable{}; int token{}, from{}, from_copy{}, to{}, to_copy{}, action_date{}; std::uint64_t goal{}; unsigned char flag{};
    const bool verified = read(address, vtable) && vtable == context.image + 0x2A07548 &&
        read(address + 8, token) && token == 0x277F && read(address + 0x14, from) && from == actor &&
        read(address + 0x18, from_copy) && from_copy == actor && read(address + 0x1C, to) && to == recipient &&
        read(address + 0x20, to_copy) && to_copy == recipient && read(address + 0x78, goal) && goal == handle &&
        read(address + 0x80, flag) && !flag && read(address + 0x30, action_date) && action_date == scope.date;
    if (!verified) { reinterpret_cast<Destroy>(context.image + 0x1183A00)(action, 1); return false; }
    auto command = allocate(0x30);
    if (!command) { reinterpret_cast<Destroy>(context.image + 0x1183A00)(action, 1); return false; }
    reinterpret_cast<CommandCtor>(context.image + 0x1180EE0)(command, action);
    const auto command_address = reinterpret_cast<std::uintptr_t>(command);
    std::uintptr_t attached{};
    if (!read(command_address, vtable) || vtable != context.image + 0x2A073C8 ||
        !read(command_address + 0x28, attached) || attached != address) {
        reinterpret_cast<Destroy>(context.image + 0x1183A40)(command, 1); return false;
    }
    result.constructed = true;
    result.can_execute = reinterpret_cast<CanExecute>(context.image + 0x11C2420)(command, nullptr);
    if (!post || !result.can_execute) {
        reinterpret_cast<Destroy>(context.image + 0x1183A40)(command, 1); return true;
    }
    // Post consumes the complete command/action tree on success and rejection.
    result.accepted = reinterpret_cast<Post>(context.image + 0x22C4250)(reinterpret_cast<void*>(context.queue), command, 0);
    return true;
}
const char* __cdecl callback(std::uintptr_t image, const char* payload) {
    static thread_local std::string result;
    if (image != context.image) { result = "{\"ok\":false,\"error\":\"image base mismatch\"}"; return result.c_str(); }
    const iron::DiplomacyApi api{nullptr, native_read, native_declare};
    result = iron::diplomacy_command(api, payload);
    return result.c_str();
}
DWORD WINAPI initialize(void*) {
    context.image = reinterpret_cast<std::uintptr_t>(GetModuleHandleW(nullptr));
    auto bridge = GetModuleHandleW(L"iron_bridge.dll");
    const auto add = bridge ? reinterpret_cast<IronRegisterOperationFn>(GetProcAddress(bridge, "IronRegisterOperation")) : nullptr;
    if (!add) return 11;
    wchar_t path[32768]{};
    if (!GetModuleFileNameW(nullptr, path, 32768) || iron::sidecar::hash_file(path) != hash_pin) return 12;
    const auto pin = [](std::uintptr_t rva, std::string_view hex) { return iron::sidecar::pin(context.image, rva, hex); };
    if (!pin(0x1180B70,"4c8bdc49895b104989731849894b08574883ec60498bf948") ||
        !pin(0x1180EE0,"48894c2408534883ec30488bd9c6410800c7410cffffffff") ||
        !pin(0x11C2420,"48895c2410488974241848897c242055488bec4883ec7048") ||
        !pin(0x1183A00,"40534883ec20488d053b356401488bd9488901488d05be27") ||
        !pin(0x1183A40,"48895c2408574883ec20488d0577398801488bd94889018b") ||
        !pin(0x1168AE0,"40534883ec50488bd9488d4c2430e8dd6efbfe90488d4c24") ||
        !pin(0xC07690,"48895c2410574883ec30803d707fa80200488bd90f84dd00") ||
        !pin(0xB8E400,"488b8930050000488b0148ffa088000000") ||
        !pin(0x22C4250,"48895c2408555657488dac2470ffffff4881ec9001000048") ||
        !pin(0x25A01DC,"40534883ec20488bd9eb0f488bcbe89dea040085c0741348")) return 13;
    return add("native_diplomacy_v2", callback);
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
