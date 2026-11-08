#include "diplomacy.hpp"
#include <charconv>
#include <sstream>
namespace iron {
namespace {
std::vector<std::string_view> split(std::string_view text) {
    std::vector<std::string_view> out;
    while (!text.empty()) {
        const auto begin = text.find_first_not_of(" \t\r\n");
        if (begin == text.npos) break;
        text.remove_prefix(begin);
        const auto end = text.find_first_of(" \t\r\n");
        out.push_back(text.substr(0, end));
        if (end == text.npos) break;
        text.remove_prefix(end);
    }
    return out;
}
std::string error(const char* message) { return std::string("{\"ok\":false,\"error\":\"") + message + "\"}"; }
bool tag(std::string_view value) {
    return value.size() == 3 && value.find_first_not_of("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") == value.npos;
}
void append_state(std::ostringstream& out, const DiplomacySnapshot& state) {
    out << "\"paused\":" << (state.paused ? "true" : "false") << ",\"played_index\":" << state.played
        << ",\"actor_index\":" << state.actor << ",\"recipient_index\":" << state.recipient
        << ",\"date_raw\":" << state.date_raw << ",\"at_war\":" << (state.at_war ? "true" : "false")
        << ",\"shared_war_relation_count\":" << state.shared_war_relations << ",\"goals\":[";
    for (std::size_t i = 0; i < state.goals.size(); ++i) {
        if (i) out << ',';
        const auto& goal = state.goals[i];
        out << "{\"handle\":\"0x" << std::hex << goal.handle << std::dec
            << "\",\"actor_index\":" << goal.actor << ",\"recipient_index\":" << goal.recipient
            << ",\"type_token\":" << goal.type << ",\"available\":" << (goal.available ? "true" : "false") << '}';
    }
    out << ']';
}
}
std::string diplomacy_command(const DiplomacyApi& api, std::string_view payload) {
    const auto args = split(payload);
    if (args.empty()) return error("expected goals, read, build_declare or post_declare");
    const bool goals = args[0] == "goals", inspect = args[0] == "read";
    const bool build = args[0] == "build_declare", post = args[0] == "post_declare";
    if ((!goals && !inspect && !build && !post) || args.size() != (goals ? 2u : inspect ? 3u : 4u))
        return error("invalid diplomacy payload");
    if (!tag(args[1]) || (!goals && !tag(args[2]))) return error("invalid country tag");
    if (!goals && args[1] == args[2]) return error("actor and recipient must differ");
    std::uint64_t handle{};
    if (build || post) {
        auto value = args[3];
        if (value.starts_with("0x")) value.remove_prefix(2);
        const auto parsed = std::from_chars(value.data(), value.data() + value.size(), handle, 16);
        if (value.empty() || !handle || parsed.ec != std::errc{} || parsed.ptr != value.data() + value.size())
            return error("invalid goal handle");
    }
    DiplomacySnapshot before;
    if (!api.read(api.context, args[1], goals ? std::string_view{} : args[2], before))
        return error("native diplomacy snapshot unavailable");
    if (before.played != before.actor) return error("actor is not the currently played country");
    std::ostringstream out;
    if (goals || inspect) {
        out << "{\"ok\":true,\"kind\":\"native_diplomacy_snapshot\",\"actor\":\"" << args[1] << "\",";
        append_state(out, before); out << '}'; return out.str();
    }
    if (!before.paused) return error("declaration requires a paused game");
    if (before.at_war) return error("countries are already at war");
    bool owned = false;
    for (const auto& goal : before.goals)
        if (goal.handle == handle && goal.actor == before.actor && goal.recipient == before.recipient && goal.available) owned = true;
    if (!owned) return error("goal is not an available owned goal for this recipient");
    DeclareResult result;
    if (!api.declare(api.context, handle, post, result) || !result.constructed)
        return error("native declaration construction or field verification failed");
    out << "{\"ok\":" << ((!post || (result.can_execute && result.accepted)) ? "true" : "false")
        << ",\"kind\":\"native_declare_war\",\"actor\":\"" << args[1] << "\",\"recipient\":\"" << args[2]
        << "\",\"goal_handle\":\"0x" << std::hex << handle << std::dec
        << "\",\"constructed_verified\":true,\"can_execute\":" << (result.can_execute ? "true" : "false")
        << ",\"posted\":" << (post && result.can_execute ? "true" : "false")
        << ",\"queue_accepted\":" << (result.accepted ? "true" : "false") << ",\"war_effect_verified\":false,\"before\":{";
    append_state(out, before); out << "}}"; return out.str();
}
}
