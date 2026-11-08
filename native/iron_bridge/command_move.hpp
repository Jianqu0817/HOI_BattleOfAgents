#pragma once
#include <cstdint>
#include <string>
#include <string_view>

namespace iron {
// Called by the pinned-build bridge on the HOI4 main thread.
// inspect PTR | build PTR HANDLE FROM TO | post PTR HANDLE FROM TO
std::string command_move(std::uintptr_t module_base, std::string_view payload);
}
