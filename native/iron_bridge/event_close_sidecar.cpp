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
 std::istringstream in(payload?payload:""); std::string action,extra; int wanted_date{},wanted_played{},wanted_pending{}; std::uint64_t window{},handle{};
 if(!(in>>action>>wanted_date>>wanted_played>>std::hex>>window>>handle>>std::dec>>wanted_pending)||(in>>extra)||(action!="probe_close"&&action!="close_resolved"))return "{\"ok\":false,\"error\":\"expected probe_close|close_resolved DATE PLAYED WINDOW_HEX HANDLE_HEX PENDING_ID\"}";
 std::uintptr_t state{},idler{},manager{},event{},records{},attached{}; int date{},played{},pending{},origin{},scope_country{},cap{},count{}; unsigned char paused{},active{},network{},notification{};
 if(!read(image+0x33dd6b0,state)||!state||!read(image+0x33ddb10,idler)||!idler||!ptr(idler,image+0x29e58d8)||!read(state+0x468,date)||date!=wanted_date||!read(state+0x520,played)||played!=wanted_played||played<=0||!read(idler+0x6c1,paused)||paused!=1||!read(idler+0x4f8,manager)||!ptr(manager,image+0x29c7580))return "{\"ok\":false,\"error\":\"world witness\"}";
 std::vector<std::uintptr_t> popups;if(!array(manager+0xd48,popups))return "{\"ok\":false,\"error\":\"popup collection\"}";
 bool registered=false;for(auto p:popups)if(p==window+0x28)registered=true;
 std::uint64_t current_handle{};
 if(!registered||!ptr(window,image+0x2a20830)||!ptr(window+0x28,image+0x2a20858)||!read(window+0x58,attached)||attached!=idler||!read(window+0x68,event)||!ptr(event,image+0x2a16008)||!read(event+8,current_handle)||current_handle!=handle||!string_is(event+0x20,"news.66")||!read(window+0x60,pending)||pending!=wanted_pending||pending<=0||!read(window+0x70,origin)||origin<=0||!ptr(window+0x78,image+0x2852168)||!read(window+0x80,scope_country)||scope_country!=played||!read(window+0x338,active)||active!=1)return "{\"ok\":false,\"error\":\"resolved event window witness\"}";
 if(!read(state+0x560,records)||!read(state+0x568,cap)||!read(state+0x56c,count)||count<0||count>4096||cap<count)return "{\"ok\":false,\"error\":\"pending collection\"}";
 for(int i=0;i<count;++i){int id{};std::uintptr_t e{};if(!read(records+i*0x38,id)||!read(records+i*0x38+8,e))return "{\"ok\":false,\"error\":\"pending record unreadable\"}";if(id==pending||e==event)return "{\"ok\":false,\"error\":\"event is still unresolved; close forbidden\"}";}
 // Qualify the handler route which cannot construct or Post a selection.
 if(!read(image+0x33dd099,network)||network!=0||!read(event+0x41d,notification)||notification!=0)return "{\"ok\":false,\"error\":\"alternate notification route not qualified\"}";
 bool invoked=false;
 if(action=="close_resolved"){
  struct CallbackArgument {std::uintptr_t reserved{},option{};} argument;
  using Dismiss=void(__fastcall*)(void*,const CallbackArgument*);
  reinterpret_cast<Dismiss>(image+0x12c7580)(reinterpret_cast<void*>(window),&argument);
  invoked=true;
  if(!read(window+0x338,active))return "{\"ok\":false,\"error\":\"postclose witness unavailable\"}";
 }
 std::ostringstream out;out<<"{\"ok\":true,\"operation\":\""<<action<<"\",\"date_raw\":"<<date<<",\"played_index\":"<<played<<",\"pending_instance_id\":"<<pending<<",\"origin_country_index\":"<<origin<<",\"pending_record_absent\":true,\"native_dismiss_invoked\":"<<(invoked?"true":"false")<<",\"active_after\":"<<(active?"true":"false")<<",\"selection_posted\":false,\"window_removal_observed\":false}";return out.str();
}
const char* __cdecl callback(std::uintptr_t base,const char* payload) { static thread_local std::string result; result=base==image?command(payload):"{\"ok\":false,\"error\":\"image mismatch\"}";return result.c_str(); }
DWORD WINAPI initialize(void*) {
 image=reinterpret_cast<std::uintptr_t>(GetModuleHandleW(nullptr)); auto bridge=GetModuleHandleW(L"iron_bridge.dll"); auto add=bridge?reinterpret_cast<IronRegisterOperationFn>(GetProcAddress(bridge,"IronRegisterOperation")):nullptr; if(!add)return 11;
 wchar_t path[32768]{};if(!GetModuleFileNameW(nullptr,path,32768)||iron::sidecar::hash_file(path)!="bccc62909b6a1ec2c1d5a46ef18608e4d0c7cdc89ad341b7402a23c40f7a8037")return 12;
 const auto pin=[](std::uintptr_t rva,std::string_view hex){return iron::sidecar::pin(image,rva,hex);};
 if(!pin(0x12c7580,"48895c24084889742410574883ec30488bfa488bd9")||!pin(0x12c761c,"488b7f084885ff7466488bd7488bcb")||!pin(0x12c768b,"c6833803000000"))return 13;
 return add("native_events_v2",callback);
}
}
BOOL WINAPI DllMain(HINSTANCE module,DWORD reason,LPVOID) { if(reason==DLL_PROCESS_ATTACH){DisableThreadLibraryCalls(module);auto thread=CreateThread(nullptr,0,initialize,nullptr,0,nullptr);if(!thread)return FALSE;CloseHandle(thread);}return TRUE; }
