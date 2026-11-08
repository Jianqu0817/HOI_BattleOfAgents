#pragma once
#include <cstdint>
#include <string>
#include <string_view>
#include <vector>
namespace iron {
struct DiplomacyGoal { std::uint64_t handle{}; int actor{}, recipient{}, type{}; bool available{}; };
struct DiplomacySnapshot {
    bool paused{}, at_war{};
    int played{}, actor{}, recipient{}, date_raw{}, shared_war_relations{};
    std::vector<DiplomacyGoal> goals;
};
struct DeclareResult { bool constructed{}, can_execute{}, accepted{}; };
struct DiplomacyApi {
    void* context{};
    bool (*read)(void*, std::string_view actor, std::string_view recipient, DiplomacySnapshot&){};
    bool (*declare)(void*, std::uint64_t goal_handle, bool post, DeclareResult&){};
};
std::string diplomacy_command(const DiplomacyApi&, std::string_view payload);
}
