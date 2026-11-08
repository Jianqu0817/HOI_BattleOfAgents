#include "clock.hpp"
#include <charconv>
#include <sstream>
#include <vector>
namespace iron {
namespace {
std::string quote(std::string_view text) {
    std::string out = "\"";
    for (unsigned char c : text) {
        if (c == '"' || c == '\\') { out += '\\'; out += static_cast<char>(c); }
        else if (c < 32) { char buffer[7]; sprintf_s(buffer, "\\u%04x", c); out += buffer; }
        else out += static_cast<char>(c);
    }
    return out + '"';
}
std::string error(const char* message) { return "{\"ok\":false,\"error\":" + quote(message) + "}"; }
std::vector<std::string_view> words(std::string_view text) {
    std::vector<std::string_view> out;
    while (!text.empty()) {
        auto first = text.find_first_not_of(" \t\r\n");
        if (first == text.npos) break;
        text.remove_prefix(first);
        auto end = text.find_first_of(" \t\r\n");
        out.push_back(text.substr(0, end));
        if (end == text.npos) break;
        text.remove_prefix(end);
    }
    return out;
}
bool number(std::string_view text, int& out) {
    auto parsed = std::from_chars(text.data(), text.data() + text.size(), out);
    return parsed.ec == std::errc{} && parsed.ptr == text.data() + text.size();
}
std::string snapshot_json(const ClockSnapshot& state) {
    return "{\"paused\":" + std::string(state.paused ? "true" : "false") +
        ",\"remaining_hours\":" + std::to_string(state.remaining_hours) +
        ",\"player_name\":" + quote(state.player) + ",\"date\":null,\"date_available\":false}";
}
}
std::string clock_command(const ClockApi& api, std::string_view payload) {
    const auto args = words(payload);
    if (args.empty()) return error("expected status, build_pause, arm HOURS, pause EXPECTED DESIRED or step HOURS");
    const bool status = args[0] == "status", build = args[0] == "build_pause";
    const bool arm = args[0] == "arm", step = args[0] == "step", pause = args[0] == "pause";
    if ((!status && !build && !arm && !step && !pause) || args.size() != ((status || build) ? 1u : pause ? 3u : 2u))
        return error("invalid clock payload");
    int hours{}, expected{}, desired{};
    if ((arm || step) && (!number(args[1], hours) || hours < 1 || hours > 1000000)) return error("hours must be 1..1000000");
    if (pause && (!number(args[1], expected) || !number(args[2], desired) || expected < 0 || expected > 1 || desired < 0 || desired > 1))
        return error("pause states must be 0 or 1");
    ClockSnapshot before;
    if (!api.read(api.context, before)) return error("clock or canonical player unavailable");
    if (status) return "{\"ok\":true,\"kind\":\"clock_status\",\"state\":" + snapshot_json(before) + "}";
    if (build) {
        if (!api.pause_command(api.context, false)) return error("pause construction check failed");
        return "{\"ok\":true,\"kind\":\"native_pause_construct\",\"constructed_verified\":true,\"posted\":false,\"state\":" + snapshot_json(before) + "}";
    }
    if (pause && before.paused == (desired != 0))
        return "{\"ok\":true,\"kind\":\"native_pause\",\"posted\":false,\"already_desired\":true,\"state\":" + snapshot_json(before) + "}";
    if (pause && before.paused != (expected != 0)) return error("pause state changed from expected value");
    if ((arm || step) && !before.paused) return error("arming bounded time requires a paused game");
    if (arm || step) {
        if (!api.arm(api.context, hours)) return error("native auto-pause arm failed");
        ClockSnapshot armed;
        if (!api.read(api.context, armed) || !armed.paused || armed.remaining_hours != hours)
            return error("native auto-pause arm readback failed; do not unpause");
        if (arm) return "{\"ok\":true,\"kind\":\"native_auto_pause_arm\",\"armed_hours\":" + std::to_string(hours) + ",\"state\":" + snapshot_json(armed) + "}";
    }
    if (!api.pause_command(api.context, true)) return error("native pause post rejected; inspect current pause and remaining hours");
    return "{\"ok\":true,\"kind\":\"native_pause_ack\",\"posted\":true,\"queue_accepted\":true,\"desired_paused\":" +
        std::string(pause && desired ? "true" : "false") + ",\"armed_hours\":" + std::to_string(step ? hours : 0) +
        ",\"effect_verified\":false,\"before\":" + snapshot_json(before) + "}";
}
}
