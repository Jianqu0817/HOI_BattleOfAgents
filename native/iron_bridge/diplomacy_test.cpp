#include "diplomacy.hpp"
#include <cassert>
#include <iostream>
struct Fake {
    iron::DiplomacySnapshot state{true, false, 11, 11, 16, 60762746, 0, {{0x10e600001269,11,16,0x821d,true}}};
    bool legal = true, readable = true, verified = true;
    unsigned read_count{}, declarations{}, posts{};
};
bool read(void* opaque, std::string_view, std::string_view, iron::DiplomacySnapshot& out) {
    auto& f = *static_cast<Fake*>(opaque); ++f.read_count; out = f.state; return f.readable;
}
bool declare(void* opaque, std::uint64_t, bool post, iron::DeclareResult& out) {
    auto& f = *static_cast<Fake*>(opaque); ++f.declarations;
    out.constructed = f.verified; out.can_execute = f.legal;
    if (post && f.legal && f.verified) { ++f.posts; out.accepted = true; }
    return f.verified;
}
int main() {
    Fake fake;
    const iron::DiplomacyApi api{&fake, read, declare};
    auto call = [&](std::string_view command) { return iron::diplomacy_command(api, command); };
    auto has = [](const std::string& text, const char* expected) { assert(text.find(expected) != text.npos); };
    has(call("goals AUS"), "\"available\":true"); assert(fake.declarations == 0);
    has(call("read AUS ITA"), "\"at_war\":false");
    has(call("build_declare AUS ITA 10e600001269"), "\"can_execute\":true"); assert(fake.posts == 0);
    fake.legal = false;
    has(call("build_declare AUS ITA 10e600001269"), "\"can_execute\":false");
    has(call("post_declare AUS ITA 10e600001269"), "\"posted\":false"); assert(fake.posts == 0);
    fake.legal = true;
    fake.state.paused = false;
    const auto calls = fake.declarations;
    has(call("post_declare AUS ITA 10e600001269"), "requires a paused"); assert(fake.declarations == calls);
    fake.state.paused = true; fake.state.played = 16;
    has(call("post_declare AUS ITA 10e600001269"), "currently played"); assert(fake.declarations == calls);
    fake.state.played = 11; fake.state.at_war = true;
    has(call("post_declare AUS ITA 10e600001269"), "already at war"); assert(fake.declarations == calls);
    fake.state.at_war = false; fake.state.goals[0].available = false;
    has(call("post_declare AUS ITA 10e600001269"), "available owned goal"); assert(fake.declarations == calls);
    fake.state.goals[0].available = true; fake.state.goals[0].recipient = 1;
    has(call("post_declare AUS ITA 10e600001269"), "available owned goal"); assert(fake.declarations == calls);
    fake.state.goals[0].recipient = 16;
    has(call("post_declare AUS ITA 10e600001270"), "available owned goal"); assert(fake.declarations == calls);
    fake.verified = false;
    has(call("post_declare AUS ITA 10e600001269"), "field verification failed"); assert(fake.posts == 0);
    fake.verified = true;
    has(call("post_declare AUS ITA 10e600001269"), "\"queue_accepted\":true"); assert(fake.posts == 1);
    fake.state.at_war = true; fake.state.shared_war_relations = 1;
    has(call("read AUS ITA"), "\"shared_war_relation_count\":1");
    const auto reads = fake.read_count;
    has(call("post_declare AUS ITA 0xnope"), "invalid goal handle");
    has(call("post_declare AUS AUS 123"), "must differ");
    has(call("post_declare AUS ITA 123 extra"), "invalid diplomacy payload"); assert(fake.read_count == reads);
    Fake other;
    other.state.actor = 16; other.state.recipient = 11; other.state.played = 16;
    other.state.goals[0].actor = 16; other.state.goals[0].recipient = 11;
    const iron::DiplomacyApi other_api{&other, read, declare};
    has(iron::diplomacy_command(other_api, "build_declare ITA AUS 10e600001269"), "\"can_execute\":true");
    assert(other.declarations == 1 && other.posts == 0);
    other.state.played = 11;
    has(iron::diplomacy_command(other_api, "post_declare ITA AUS 10e600001269"), "currently played");
    assert(other.declarations == 1 && other.posts == 0);
    std::cout << "Diplomacy: 19 policy and sequencing checks passed\n";
}
