#include "event_validation.hpp"
#include <cassert>
int main() {
 iron::events::Witness w{60764811,11,11,430,7,0,0x15276605ba0ULL,0x1ae4c8200000032ULL,true,true,true,true,"news.66"};
 const auto good = [&] { return iron::events::allowed(w,60764811,11,0x15276605ba0ULL,0x1ae4c8200000032ULL); };
 assert(good()); w.scope_country=7; assert(!good()); w.scope_country=11;
 w.record_matches=false; assert(!good()); w.record_matches=true;
 w.eligible=false; assert(!good()); w.eligible=true;
 w.option=1; assert(!good()); w.option=0;
 w.date++; assert(!good()); w.date--;
 w.handle++; assert(!good()); w.handle--;
 w.key="news.65"; assert(!good()); w.key="news.66";
 w.paused=false; assert(!good());
 return 0;
}
