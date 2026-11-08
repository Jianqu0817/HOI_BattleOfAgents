#pragma once
#include <string>
#include <string_view>
namespace iron {
struct ClockSnapshot {
    bool paused{};
    int remaining_hours{};
    std::string player;
};
struct ClockApi {
    void* context{};
    bool (*read)(void*, ClockSnapshot&){};
    bool (*arm)(void*, int){};
    bool (*pause_command)(void*, bool post){};
};
// Common command sequencing, used by both the native adapter and owned tests.
std::string clock_command(const ClockApi& api, std::string_view payload);
}
