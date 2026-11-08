#pragma once
#include <cstdint>
#include <string_view>
namespace iron::events {
struct Witness {
 int date{}, played{}, scope_country{}, pending_id{}, origin_country{}, option{};
 std::uint64_t window{}, handle{};
 bool paused{}, registered{}, record_matches{}, eligible{};
 std::string_view key;
};
inline bool allowed(const Witness& w, int date, int played, std::uint64_t window, std::uint64_t handle) {
 return w.paused && w.registered && w.record_matches && w.eligible && w.date == date &&
  w.played == played && played > 0 && w.scope_country == played && w.pending_id > 0 &&
  w.origin_country > 0 && w.window == window && w.handle == handle && w.key == "news.66" &&
  w.option == (played == 1 ? 1 : 0);
}
}
