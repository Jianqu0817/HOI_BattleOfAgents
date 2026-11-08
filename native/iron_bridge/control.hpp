#pragma once
#include <string>
#include <string_view>
namespace iron {
struct ControlSnapshot {
    int played{}, country{}, date_raw{};
    bool paused{}, ai_enabled{}, global_ai{}, ai_override{};
};
struct ControlApi {
    void* context{};
    bool (*read)(void*, std::string_view, ControlSnapshot&){};
    bool (*tag)(void*, std::string_view){};
    bool (*toggle_ai)(void*, std::string_view, std::string&){};
    bool (*save)(void*, std::string_view, std::string&){};
};
std::string control_command(const ControlApi&, std::string_view);
}
