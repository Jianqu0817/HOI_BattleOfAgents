#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <bcrypt.h>
#include <MinHook.h>
#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <fstream>
#include <iomanip>
#include <memory>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <unordered_map>
#define IRON_BRIDGE_EXPORTS
#include "bridge_api.h"
#ifdef IRON_HAS_NATIVE_MOVE
#include "command_move.hpp"
#endif

namespace {
constexpr char game_hash[] = "bccc62909b6a1ec2c1d5a46ef18608e4d0c7cdc89ad341b7402a23c40f7a8037";
constexpr std::uintptr_t tick_rva = 0xBDB570;
constexpr unsigned char tick_signature[] = {0x48,0x89,0x5c,0x24,0x10,0x55,0x56,0x57,0x41,0x56,0x41,0x57,0x48,0x81,0xec,0x20,0x01,0x00,0x00,0x44,0x0f,0xb6,0xf2,0x48,0x8b,0xd9};
using Tick = void(__fastcall*)(void*, char);
Tick original_tick{};
std::uintptr_t image{};
std::atomic<DWORD> main_thread{};
std::atomic<std::uint64_t> ticks{};
bool test_host{};
struct Job {
    std::string id, op, payload, response;
    std::mutex mutex;
    std::condition_variable done;
    enum { pending, running, complete, cancelled } state = pending;
};
std::mutex queue_mutex;
std::deque<std::shared_ptr<Job>> queue;
std::mutex handlers_mutex;
std::unordered_map<std::string, IronOperationCallback> handlers;

std::string quote(std::string_view s) {
    std::string out = "\"";
    for (unsigned char c : s) {
        if (c == '"' || c == '\\') { out += '\\'; out += static_cast<char>(c); }
        else if (c < 32) { char b[7]; sprintf_s(b, "\\u%04x", c); out += b; }
        else out += static_cast<char>(c);
    }
    return out + '"';
}
std::string reply(std::string_view id, bool ok, std::string_view kind, std::string_view value, bool raw = false) {
    return "{\"id\":" + quote(id) + ",\"ok\":" + (ok ? "true" : "false") +
        ",\"kind\":" + quote(kind) + (ok ? ",\"result\":" : ",\"error\":") +
        (raw ? std::string(value) : quote(value)) + ",\"thread_id\":" + std::to_string(GetCurrentThreadId()) +
        ",\"main_thread_id\":" + std::to_string(main_thread.load()) + ",\"tick\":" + std::to_string(ticks.load()) + "}\n";
}
std::string dispatch(const Job& job) {
    if (job.op == "main_status") {
        std::ostringstream base; base << "0x" << std::hex << image;
        return reply(job.id, true, "read_only", "{\"build\":" + quote(test_host ? "synthetic" : "6ae9f874_35ea878") + ",\"sha256\":" + quote(test_host ? "synthetic" : game_hash) +
            ",\"image_base\":" + quote(base.str()) + ",\"synthetic\":" + (test_host ? "true" : "false") + "}", true);
    }
#ifdef IRON_HAS_NATIVE_MOVE
    if (job.op == "native_move" && !test_host) {
        if (!(job.payload.starts_with("units ") || job.payload.starts_with("inspect_unit ") ||
              job.payload.starts_with("build_unit ") || job.payload.starts_with("post_unit ")))
            return reply(job.id, false, "rejected", "expected units, inspect_unit, build_unit or post_unit");
        const auto kind = (job.payload.starts_with("units ") || job.payload.starts_with("inspect_unit ")) ? "read_only" :
            job.payload.starts_with("build_unit ") ? "native_construct_check" : "native_command_ack";
        return reply(job.id, true, kind, iron::command_move(image, job.payload), true);
    }
#endif
    if (job.op == "test_echo" && test_host) return reply(job.id, true, "synthetic", job.payload);
    IronOperationCallback handler{};
    { std::lock_guard lock(handlers_mutex); const auto found = handlers.find(job.op); if (found != handlers.end()) handler = found->second; }
    if (handler) {
        const char* result = handler(image, job.payload.c_str());
        if (!result) return reply(job.id, false, "native_exception", "registered handler returned null");
        const auto length = strnlen_s(result, 1048577);
        if (length > 1048576) return reply(job.id, false, "native_exception", "registered handler result exceeds 1 MiB");
        return reply(job.id, true, "registered_operation", std::string_view(result, length), true);
    }
    return reply(job.id, false, "rejected", "unsupported operation");
}
void __fastcall tick_hook(void* window, char force) {
    original_tick(window, force);
    DWORD expected = 0;
    main_thread.compare_exchange_strong(expected, GetCurrentThreadId());
    if (main_thread.load() != GetCurrentThreadId()) return;
    ++ticks;
    std::shared_ptr<Job> job;
    { std::lock_guard lock(queue_mutex); if (queue.empty()) return; job = queue.front(); queue.pop_front(); }
    { std::lock_guard lock(job->mutex); if (job->state == Job::cancelled) return; job->state = Job::running; }
    std::string response;
    try { response = dispatch(*job); }
    catch (const std::exception& ex) { response = reply(job->id, false, "native_exception", ex.what()); }
    catch (...) { response = reply(job->id, false, "native_exception", "unknown C++ exception"); }
    { std::lock_guard lock(job->mutex); job->response = std::move(response); job->state = Job::complete; }
    job->done.notify_one();
}
std::string hash_file(const wchar_t* path) {
    BCRYPT_ALG_HANDLE algorithm{};
    BCRYPT_HASH_HANDLE hash{};
    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) < 0) return {};
    DWORD size{}, returned{};
    BCryptGetProperty(algorithm, BCRYPT_OBJECT_LENGTH, reinterpret_cast<PUCHAR>(&size), sizeof(size), &returned, 0);
    std::string object(size, '\0');
    if (BCryptCreateHash(algorithm, &hash, reinterpret_cast<PUCHAR>(object.data()), size, nullptr, 0, 0) < 0) { BCryptCloseAlgorithmProvider(algorithm, 0); return {}; }
    std::ifstream file(path, std::ios::binary);
    std::array<char, 65536> buffer{};
    bool valid = file.is_open();
    while (file) { file.read(buffer.data(), buffer.size()); if (file.gcount() && BCryptHashData(hash, reinterpret_cast<PUCHAR>(buffer.data()), static_cast<ULONG>(file.gcount()), 0) < 0) valid = false; }
    std::array<unsigned char, 32> digest{};
    if (BCryptFinishHash(hash, digest.data(), static_cast<ULONG>(digest.size()), 0) < 0) valid = false;
    BCryptDestroyHash(hash); BCryptCloseAlgorithmProvider(algorithm, 0);
    std::ostringstream hex;
    for (auto b : digest) hex << std::hex << std::setw(2) << std::setfill('0') << static_cast<unsigned>(b);
    return valid ? hex.str() : "";
}
std::string serve(std::string_view request) {
    const auto first = request.find('\t'), second = request.find('\t', first == request.npos ? first : first + 1);
    if (first == request.npos || second == request.npos) return reply("", false, "rejected", "expected id TAB op TAB payload");
    auto job = std::make_shared<Job>();
    job->id = request.substr(0, first); job->op = request.substr(first + 1, second - first - 1); job->payload = request.substr(second + 1);
    if (job->id.empty() || job->id.size() > 64 || job->id.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.") != std::string::npos)
        return reply("", false, "rejected", "invalid request id");
    // This cache lasts for the DLL lifetime; an old mutation ID is never executed twice.
    static std::unordered_map<std::string, std::pair<std::string, std::string>> receipts;
    const auto fingerprint = job->op + '\t' + job->payload;
    if (const auto it = receipts.find(job->id); it != receipts.end())
        return it->second.first == fingerprint ? it->second.second : reply(job->id, false, "rejected", "request id already used with different content");
    std::string response;
    if (job->op == "ping") response = reply(job->id, true, "transport", "{\"protocol\":1}", true);
    else {
        { std::lock_guard lock(queue_mutex); queue.push_back(job); }
        std::unique_lock lock(job->mutex);
        if (!job->done.wait_for(lock, std::chrono::seconds(5), [&]{ return job->state == Job::complete; })) {
            if (job->state == Job::pending) { job->state = Job::cancelled; response = reply(job->id, false, "not_dispatched", "main-thread queue timed out; operation cancelled"); }
            else job->done.wait(lock, [&]{ return job->state == Job::complete; });
        }
        if (response.empty()) response = job->response;
    }
    receipts.emplace(job->id, std::make_pair(fingerprint, response));
    return response;
}
DWORD WINAPI worker(void*) {
    auto module = GetModuleHandleW(nullptr); image = reinterpret_cast<std::uintptr_t>(module);
    const auto marker = reinterpret_cast<unsigned(*)()>(GetProcAddress(module, "IronBridgeTestHostMarker"));
    test_host = marker && marker() == 0x49524f4e;
    void* target{};
    if (test_host) target = reinterpret_cast<void*>(GetProcAddress(module, "IronBridgeTestTick"));
    else {
        wchar_t path[32768]{};
        if (!GetModuleFileNameW(nullptr, path, 32768) || hash_file(path) != game_hash) return 11;
        target = reinterpret_cast<void*>(image + tick_rva);
        if (memcmp(target, tick_signature, sizeof(tick_signature))) return 12;
    }
    if (!target || MH_Initialize() != MH_OK || MH_CreateHook(target, reinterpret_cast<void*>(&tick_hook), reinterpret_cast<void**>(&original_tick)) != MH_OK || MH_EnableHook(target) != MH_OK) return 13;
    const auto pipe_name = L"\\\\.\\pipe\\iron-native-" + std::to_wstring(GetCurrentProcessId());
    for (;;) {
        HANDLE pipe = CreateNamedPipeW(pipe_name.c_str(), PIPE_ACCESS_DUPLEX | FILE_FLAG_FIRST_PIPE_INSTANCE,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS, 1, 1048576, 65536, 0, nullptr);
        if (pipe == INVALID_HANDLE_VALUE) return 14;
        if (ConnectNamedPipe(pipe, nullptr) || GetLastError() == ERROR_PIPE_CONNECTED) {
            std::string request; char buffer[4096]; DWORD count{};
            while (request.size() <= 65536 && ReadFile(pipe, buffer, sizeof(buffer), &count, nullptr) && count) {
                request.append(buffer, count); if (request.find('\n') != std::string::npos) break;
            }
            auto newline = request.find('\n');
            std::string response = newline != std::string::npos && newline <= 65536 ? serve(std::string_view(request).substr(0, newline)) : reply("", false, "rejected", "request too large or missing newline");
            WriteFile(pipe, response.data(), static_cast<DWORD>(response.size()), &count, nullptr);
            FlushFileBuffers(pipe); DisconnectNamedPipe(pipe);
        }
        CloseHandle(pipe);
    }
}
}
extern "C" __declspec(dllexport) unsigned __cdecl IronRegisterOperation(const char* operation, IronOperationCallback callback) {
    if (!operation || !callback) return 1;
    const auto length = strnlen_s(operation, 65);
    if (!length || length > 64) return 1;
    const std::string name(operation, length);
    if (name.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.") != name.npos ||
        name == "ping" || name == "main_status" || name == "native_move" || name == "test_echo") return 1;
    std::lock_guard lock(handlers_mutex);
    return handlers.emplace(name, callback).second ? 0u : 2u;
}
BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(instance);
        HANDLE thread = CreateThread(nullptr, 0, worker, nullptr, 0, nullptr);
        if (thread) CloseHandle(thread); else return FALSE;
    }
    return TRUE;
}
