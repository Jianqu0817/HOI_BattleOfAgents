#include "control.hpp"
#include <charconv>
#include <cstdio>
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
std::string error(const char* text) { return "{\"ok\":false,\"error\":" + quote(text) + "}"; }
std::vector<std::string_view> split(std::string_view text) {
    std::vector<std::string_view> out;
    while (!text.empty()) {
        auto first = text.find_first_not_of(" \t\r\n"); if (first == text.npos) break;
        text.remove_prefix(first); auto end = text.find_first_of(" \t\r\n"); out.push_back(text.substr(0, end));
        if (end == text.npos) break; text.remove_prefix(end);
    }
    return out;
}
bool valid_tag(std::string_view tag) { return tag.size() == 3 && tag.find_first_not_of("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") == tag.npos; }
bool state(std::string_view text, bool& value) { if (text != "0" && text != "1") return false; value = text == "1"; return true; }
std::string status_json(std::string_view tag, const ControlSnapshot& s) {
    auto hours = s.date_raw - 5000LL * 365 * 24;
    std::string date = "null";
    if (hours >= 0) {
        int year = static_cast<int>(hours / (365 * 24)), day = static_cast<int>(hours / 24 % 365), month = 1;
        constexpr int months[] = {31,28,31,30,31,30,31,31,30,31,30,31};
        while (month <= 12 && day >= months[month - 1]) day -= months[month++ - 1];
        char text[64]; sprintf_s(text, "%04d-%02d-%02dT%02d:00:00", year, month, day + 1, static_cast<int>(hours % 24)); date = quote(text);
    }
    return "{\"tag\":" + quote(tag) + ",\"country_index\":" + std::to_string(s.country) +
        ",\"played_index\":" + std::to_string(s.played) + ",\"paused\":" + (s.paused ? "true" : "false") +
        ",\"ai_enabled\":" + (s.ai_enabled ? "true" : "false") + ",\"global_ai\":" + (s.global_ai ? "true" : "false") +
        ",\"ai_override\":" + (s.ai_override ? "true" : "false") + ",\"date_raw\":" + std::to_string(s.date_raw) + ",\"date\":" + date + "}";
}
}
std::string control_command(const ControlApi& api, std::string_view payload) {
    const auto args = split(payload);
    if (args.empty()) return error("expected status TAG, tag EXPECTED_TAG TARGET_TAG, ai TAG EXPECTED DESIRED or save NAME");
    const bool status = args[0] == "status", tag = args[0] == "tag", ai = args[0] == "ai", save = args[0] == "save";
    if ((!status && !tag && !ai && !save) || args.size() != (tag ? 3u : ai ? 4u : 2u)) return error("invalid control payload");
    if (save) {
        if (args[1].empty() || args[1].size() > 96 || args[1].find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_") != args[1].npos)
            return error("save requires a basename of letters, digits, hyphen or underscore");
        std::string message;
        if (!api.save(api.context, args[1], message)) return error("native save handler rejected request");
        return "{\"ok\":true,\"kind\":\"native_save_ack\",\"basename\":" + quote(args[1]) + ",\"message\":" + quote(message) + ",\"file_verified\":false}";
    }
    const auto target = tag ? args[2] : args[1];
    if (!valid_tag(target) || (tag && !valid_tag(args[1]))) return error("country tags must be three uppercase letters or digits");
    bool expected{}, desired{};
    if (ai && (!state(args[2], expected) || !state(args[3], desired))) return error("AI states must be 0 or 1");
    ControlSnapshot before;
    if (!api.read(api.context, target, before)) return error("country or native status unavailable");
    if (status) return "{\"ok\":true,\"kind\":\"native_control_status\",\"state\":" + status_json(target, before) + "}";
    if (!before.paused) return error("country and AI changes require a paused game");
    if (tag) {
        if (before.played == before.country) return "{\"ok\":true,\"kind\":\"native_country_control\",\"already_desired\":true,\"state\":" + status_json(target, before) + "}";
        ControlSnapshot expected_country;
        if (!api.read(api.context, args[1], expected_country) || expected_country.country != before.played) return error("played country changed from expected tag");
        if (!api.tag(api.context, target)) return error("native country control routine failed");
    } else {
        if (before.ai_enabled == desired) return "{\"ok\":true,\"kind\":\"native_country_ai\",\"already_desired\":true,\"state\":" + status_json(target, before) + "}";
        if (before.ai_enabled != expected) return error("AI state changed from expected value");
        std::string message;
        if (!api.toggle_ai(api.context, target, message)) return error("native country AI handler failed");
    }
    ControlSnapshot after;
    if (!api.read(api.context, target, after) || !after.paused || (tag ? after.played != after.country : after.ai_enabled != desired))
        return error("native control mutation readback failed; inspect status before continuing");
    return "{\"ok\":true,\"kind\":" + quote(tag ? "native_country_control" : "native_country_ai") +
        ",\"effect_verified\":true,\"before\":" + status_json(target, before) + ",\"state\":" + status_json(target, after) + "}";
}
}
