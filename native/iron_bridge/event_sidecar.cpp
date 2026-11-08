#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <cstdint>
#include <cstring>
#include <sstream>
#include <string>
#include <vector>
#include "bridge_api.h"
#include "sidecar_pins.hpp"
#include "event_validation.hpp"
namespace {
std::uintptr_t image{};
template<class T> bool read(std::uintptr_t p,T& v) { SIZE_T n{}; return ReadProcessMemory(GetCurrentProcess(),reinterpret_cast<void*>(p),&v,sizeof(v),&n) && n==sizeof(v); }
bool ptr(std::uintptr_t p,std::uintptr_t expected) { std::uintptr_t v{}; return read(p,v)&&v==expected; }
bool array(std::uintptr_t p,std::vector<std::uintptr_t>& out) {
 std::uintptr_t data{}; int cap{},n{};
 if(!read(p,data)||!read(p+8,cap)||!read(p+12,n)||n<0||n>4096||cap<n||cap>65536) return false;
 out.resize(n); SIZE_T got{};
 return !n||(data&&ReadProcessMemory(GetCurrentProcess(),reinterpret_cast<void*>(data),out.data(),out.size()*8,&got)&&got==out.size()*8);
}
bool string_is(std::uintptr_t p,std::string_view expected) {
 struct S { char text[16]; std::uint64_t size,capacity; } s{};
 if(!read(p,s)||s.size!=expected.size()||s.capacity<s.size||s.capacity>65536) return false;
 std::string value(s.size,'\0');
 if(s.capacity<=15) memcpy(value.data(),s.text,s.size);
 else { std::uintptr_t data{}; memcpy(&data,s.text,8); SIZE_T n{}; if(!ReadProcessMemory(GetCurrentProcess(),reinterpret_cast<void*>(data),value.data(),value.size(),&n)||n!=value.size())return false; }
 return value==expected;
}
std::string command(const char* payload) {
 std::istringstream in(payload?payload:""); std::string action,extra; int wanted_date{},wanted_played{},option{}; std::uint64_t window{},handle{};
 if(!(in>>action>>wanted_date>>wanted_played>>std::hex>>window>>handle>>std::dec>>option)||(in>>extra)||(action!="probe"&&action!="select")) return "{\"ok\":false,\"error\":\"expected probe|select DATE PLAYED WINDOW_HEX HANDLE_HEX OPTION\"}";
 std::uintptr_t state{},idler{},manager{},event{},records{},record{},selected{}; int count{},cap{}; unsigned char paused{},mode{},active{};
 iron::events::Witness w{}; w.window=window; w.option=option; w.key="news.66";
 if(!read(image+0x33dd6b0,state)||!state||!read(image+0x33ddb10,idler)||!idler||
 !ptr(idler,image+0x29e58d8)||!read(state+0x468,w.date)||!read(state+0x520,w.played)||
 !read(idler+0x6c1,paused)||!read(idler+0x4f8,manager)||!ptr(manager,image+0x29c7580))return "{\"ok\":false,\"error\":\"world witness\"}";
 w.paused=paused==1; std::vector<std::uintptr_t> popups;
 if(!array(manager+0xd48,popups))return "{\"ok\":false,\"error\":\"popup collection\"}";
 for(auto p:popups)if(p==window+0x28)w.registered=true;
 if(!w.registered||!ptr(window,image+0x2a20830)||!ptr(window+0x28,image+0x2a20858)||
 !read(window+0x58,event)||event!=idler||!read(window+0x68,event)||!ptr(event,image+0x2a16008)||
 !read(event+8,w.handle)||!string_is(event+0x20,"news.66")||!read(window+0x60,w.pending_id)||
 !read(window+0x70,w.origin_country)||!ptr(window+0x78,image+0x2852168)||!read(window+0x80,w.scope_country)||
 !read(window+0x13fc,mode)||!read(window+0x338,active)||active!=1)return "{\"ok\":false,\"error\":\"event instance witness\"}";
 if(!read(state+0x560,records)||!read(state+0x568,cap)||!read(state+0x56c,count)||count<0||count>4096||cap<count)return "{\"ok\":false,\"error\":\"pending records\"}";
 for(int i=0;i<count;++i){ const auto r=records+i*0x38; int id{},country{}; std::uintptr_t e{}; if(read(r,id)&&id==w.pending_id&&read(r+8,e)&&e==event&&read(r+0x18,country)&&country==w.origin_country){record=r;w.record_matches=true;} }
 std::vector<std::uintptr_t> options;
 if(!array(event+0x368,options)||options.size()!=2||option<0||static_cast<std::size_t>(option)>=options.size())return "{\"ok\":false,\"error\":\"option bounds\"}";
 selected=options[option]; int native_index{};
 if(!ptr(selected,image+0x2a15f68)||!read(selected+0x1f0,native_index)||native_index!=option||!string_is(selected+0x178,option?"news.66.b":"news.66.a"))return "{\"ok\":false,\"error\":\"option identity\"}";
 using Eligible=bool(__fastcall*)(void*,void*,unsigned char);
 w.eligible=reinterpret_cast<Eligible>(image+0x15d4f80)(reinterpret_cast<void*>(selected),reinterpret_cast<void*>(window+0x78),mode);
 if(!iron::events::allowed(w,wanted_date,wanted_played,window,handle))return "{\"ok\":false,\"error\":\"news choice guard\"}";
 using Allocate=void*(__fastcall*)(std::size_t); using Ctor=void*(__fastcall*)(void*,int,void*,void*,void*,int); using Destroy=void*(__fastcall*)(void*,unsigned int); using CanExecute=bool(__fastcall*)(void*,void*);
 auto object=reinterpret_cast<Allocate>(image+0x25a01dc)(0xf0);
 if(!object)return "{\"ok\":false,\"error\":\"allocation\"}";
 reinterpret_cast<Ctor>(image+0x15d5110)(object,w.pending_id,reinterpret_cast<void*>(event),reinterpret_cast<void*>(window+0x70),reinterpret_cast<void*>(window+0x78),option);
 const auto address=reinterpret_cast<std::uintptr_t>(object); int id{},country{},scope_country{},chosen{}; std::uint64_t event_handle{};
 const bool constructed=ptr(address,image+0x29b6ce0)&&read(address+0x28,id)&&id==w.pending_id&&read(address+0x2c,event_handle)&&event_handle==handle&&read(address+0x34,country)&&country==w.origin_country&&ptr(address+0x38,image+0x2852168)&&read(address+0x40,scope_country)&&scope_country==w.played&&read(address+0xe8,chosen)&&chosen==option;
 const bool can_execute=constructed&&reinterpret_cast<CanExecute>(image+0x15d66c0)(object,nullptr);
 bool accepted=false;
 if(action=="select"&&can_execute) {
  // A fresh witness immediately before ownership transfers to the normal queue.
  int date{},played{},pending_id{}; unsigned char still_paused{}; std::uintptr_t current_event{};
  if(!read(state+0x468,date)||date!=wanted_date||!read(state+0x520,played)||played!=wanted_played||!read(idler+0x6c1,still_paused)||still_paused!=1||!read(record,pending_id)||pending_id!=w.pending_id||!read(record+8,current_event)||current_event!=event){reinterpret_cast<Destroy>(image+0xa58c10)(object,1);return "{\"ok\":false,\"error\":\"prepost witness\"}";}
  using Queue=void*(__fastcall*)(void*); using Post=bool(__fastcall*)(void*,void*,unsigned char);
  auto queue=reinterpret_cast<Queue>(image+0xb8e400)(reinterpret_cast<void*>(idler));
  accepted=reinterpret_cast<Post>(image+0x22c4250)(queue,object,0);
 } else reinterpret_cast<Destroy>(image+0xa58c10)(object,1);
 std::ostringstream out; out<<"{\"ok\":"<<(constructed&&can_execute?"true":"false")<<",\"operation\":\""<<action<<"\",\"date_raw\":"<<w.date<<",\"played_index\":"<<w.played<<",\"pending_instance_id\":"<<w.pending_id<<",\"origin_country_index\":"<<w.origin_country<<",\"scope_country_index\":"<<w.scope_country<<",\"option_index\":"<<option<<",\"eligible\":"<<(w.eligible?"true":"false")<<",\"constructed\":"<<(constructed?"true":"false")<<",\"can_execute\":"<<(can_execute?"true":"false")<<",\"posted\":"<<(action=="select"&&can_execute?"true":"false")<<",\"accepted\":"<<(accepted?"true":"false")<<",\"effect_observed\":false}";return out.str();
}
const char* __cdecl callback(std::uintptr_t base,const char* payload) { static thread_local std::string result; result=base==image?command(payload):"{\"ok\":false,\"error\":\"image mismatch\"}";return result.c_str(); }
DWORD WINAPI initialize(void*) {
 image=reinterpret_cast<std::uintptr_t>(GetModuleHandleW(nullptr)); auto bridge=GetModuleHandleW(L"iron_bridge.dll"); auto add=bridge?reinterpret_cast<IronRegisterOperationFn>(GetProcAddress(bridge,"IronRegisterOperation")):nullptr; if(!add)return 11;
 wchar_t path[32768]{};if(!GetModuleFileNameW(nullptr,path,32768)||iron::sidecar::hash_file(path)!="bccc62909b6a1ec2c1d5a46ef18608e4d0c7cdc89ad341b7402a23c40f7a8037")return 12;
 const auto pin=[](std::uintptr_t rva,std::string_view hex){return iron::sidecar::pin(image,rva,hex);};
 if(!pin(0x15d5110,"48895c241848894c24085556574883ec30498bf9498bf08b")||!pin(0x15d66c0,"48895c24184889742420574883ec30488bf1488bfa4883c1")||!pin(0x15d4f80,"80b9f501000000740c443881f4010000740332c0c34881c1")||!pin(0xa58c10,"48895c2408574883ec20488bf98bda4883c138e8c839b2ff")||!pin(0x22c4250,"48895c2408555657488dac2470ffffff4881ec9001000048")||!pin(0x25a01dc,"40534883ec20488bd9eb0f488bcbe89dea040085c0741348")||!pin(0xb8e400,"488b8930050000488b0148ffa088000000"))return 13;
 return add("native_events_v1",callback);
}
}
BOOL WINAPI DllMain(HINSTANCE module,DWORD reason,LPVOID) { if(reason==DLL_PROCESS_ATTACH){DisableThreadLibraryCalls(module);auto thread=CreateThread(nullptr,0,initialize,nullptr,0,nullptr);if(!thread)return FALSE;CloseHandle(thread);}return TRUE; }
