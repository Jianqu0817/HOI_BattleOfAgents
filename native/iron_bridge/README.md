# IRON native bridge

This Windows x64 DLL calls the game's ordinary command queue from its window
tick thread. It does not open the console or send keyboard/mouse input.

The production DLL accepts only HOI4 `6ae9f874_35ea878`, executable SHA-256
`bccc62909b6a1ec2c1d5a46ef18608e4d0c7cdc89ad341b7402a23c40f7a8037`.
It checks the unmodified tick prologue before installing its MinHook detour at
RVA `0xBDB570`. A different executable or an existing detour is refused.

Run `./native/iron_bridge/setup-dependency.ps1` to fetch the pinned MinHook
source, then `./native/iron_bridge/build.ps1`. Default build output is
`runs/native-build` and is not tracked.
The build only compiles. It does not access or inject a game process.
`python tools/test_native_rpc.py` injects only the owned synthetic test host.
`iron_injector.exe PID ABSOLUTE_DLL_PATH` loads a DLL once into an explicitly
selected process. Successful loading is separate from bridge readiness; query
`ping` and `main_status` afterwards. A loaded bridge remains until process exit;
live unloading/rebuilding is unsupported.

Additional native modules may register versioned operations without replacing
this loaded DLL. Resolve the C export `IronRegisterOperation` with
`GetProcAddress`; its ABI is in `bridge_api.h`. Call registration outside
`DllMain`. A handler receives the game image base and payload on the same tick
thread as builtin operations, with no registration mutex held. It returns JSON
in static or thread-local storage, which the bridge immediately copies.
Registration returns 0 on success, 1 for invalid/reserved names or null callbacks,
and 2 for duplicate names. Registered modules must stay loaded until process
exit. There is no operation to load arbitrary modules over RPC.
`python tools/test_native_registration.py` verifies this ABI in an owned host.

## RPC protocol 1

The local pipe is `\\.\pipe\iron-native-<PID>`. One UTF-8 line per connection:

```text
request_id<TAB>operation<TAB>payload<LF>
```

The response is one JSON line containing `id`, `ok`, `kind`, `result` or `error`,
`thread_id`, `main_thread_id`, and `tick`. Request IDs are 1–64 ASCII letters,
digits, `-`, `_`, or `.`. Requests are at most 65536 bytes. The Python transport
is `tools/native_rpc.py`; it never retries a submitted operation automatically.

Operations:

| Operation | Payload | Meaning |
|---|---|---|
| `ping` | empty | Transport availability; worker thread, no game calls. |
| `main_status` | empty | Build/hash/image base and tick thread; queued after original tick. |
| `native_move` | `units TAG` | Read current played country's division snapshots. |
| `native_move` | `inspect_unit TAG UNITID` | Read one owned division. |
| `native_move` | `build_unit TAG UNITID FROM TO` | Construct, verify, and destroy a native move without posting. |
| `native_move` | `post_unit TAG UNITID FROM TO` | Construct and post one normal native move. |

`TAG` must identify the currently played country. IDs and provinces are decimal.
`native_move` returns a nested result whose own `ok` must also be checked. An
acknowledged queue post is not evidence that a division moved: inspect its path
and subsequent province separately. There is no generic console/script effect
operation. Synthetic hosts reject every game operation.

The bridge retains request IDs and original receipts for its lifetime. Reusing
the same ID and content retrieves the original receipt; changing the content
under an existing ID is rejected. A 5-second pending queue timeout cancels the
operation before dispatch (`kind: not_dispatched`). A client transport timeout
has an **unknown** outcome: retain the ID and query the same request for its
receipt. Receipt deduplication does not persist across a game restart.

## Native clock sidecar

`build-clock.ps1` builds the separate `runs/native-clock-v1/iron_clock_v1.dll`.
It registers `native_clock_v1` from an initialization worker; initialization
verifies the production executable hash and exact function prologues without
calling game functions. The existing bridge remains loaded.

| Payload | Meaning |
|---|---|
| `status` | Read actual pause flag, remaining native auto-pause hours, and canonical player name. |
| `build_pause` | Construct, verify, and destroy the ordinary pause command without posting it. |
| `arm HOURS` | Require paused state; call the native bounded-time method and verify its hour counter. |
| `pause EXPECTED DESIRED` | States are `0`/`1`. Verify expected state before posting a toggle; already desired means no post. |
| `step HOURS` | Require paused state; arm and verify the bound before enqueueing ordinary unpause. |

Hours must be positive. Date is currently explicitly unavailable. Pause post
receipts acknowledge the queue, so poll `status` to observe the actual result.
After `step`, the game's own hourly path decrements the armed counter and pauses
at zero. The sidecar does not use host sleeps to decide when to pause.
`python tools/test_native_clock.py` checks registration and command sequencing in
an owned synthetic process, with game operations represented by a local model.
Native constructor ABI is separately checked by `build_pause` in the game.

Clock ABI pins for this executable: `CPauseGame` constructor `0xE52520`, size
`0x50`, vtable `0x29E6E10`, scalar deleting destructor `0xE52A30`; canonical
player getter `0x1F1A40`; command queue post `0x22C4250`; bounded-hour method
`0x204770`. The constructor receives the canonical player's native string and
two zero flag bytes, matching the game's normal pause caller. Pause status is
read at idler `+0x6C1`; the bounded counter is game state `+0x928`.

## Native country, AI, and save sidecar

`build-control.ps1` builds `runs/native-control-v1/iron_control_v1.dll` and
registers `native_control_v1`. It verifies the same executable hash and exact
callee prologues before registration. Supported payloads:

| Payload | Meaning |
|---|---|
| `status TAG` | Read played country index, requested country index, actual pause flag, AI override/global/effective state, and current game date. |
| `tag EXPECTED_TAG TARGET_TAG` | Require paused state and expected played country; call the game's country-control routine and verify the result. |
| `ai TAG EXPECTED DESIRED` | Require paused state; toggle the game's country AI handler only when needed, then verify the exact effective state. States are `0`/`1`. |
| `save NAME` | Require paused state; invoke the native save handler with a safe basename. Verify the resulting file separately. |

Save names allow 1–96 letters, digits, hyphens, or underscores, with no path or
extension. There is no generic console-command operation. Tag/AI requests
already in the desired state return without toggling. Reusing a request ID
retrieves the original receipt rather than repeating an AI toggle.

The tag implementation calls `0x1339360`, the same native routine the vanilla
tag handler `0x2A4570` uses. Its target preflight matches that handler's
`0x73EBF0` getter (`country+0x410`, count at `+0x0C`). AI uses the exact handler
`0x2A9630`, with global flag `0x33DDAAC` and country override list
`0x33DDAD8`/count `0x33DDAE4`. The effective switch is global XOR membership.
Save uses handler `0x297C90`; both handlers receive the proven native action
result/vector/string ABI. Result text is copied and then destroyed by the
game's string destructor `0x120530`.

Date reads game state `+0x468`, subtracts the game's verified
`5000*365*24` epoch, then uses the game's fixed 365-day calendar. Current binary
function `0x1EDD80` proves this field and epoch; no wall-clock estimate is used.
`python tools/test_native_control.py` exercises five-country switching,
per-country AI state/readback, receipt reuse, date conversion, and save-name
restrictions in the owned synthetic host.

Tick ABI/address evidence: CAPIE source revision
`0350d1f6cd662d75936f8cce43afedbcc6b81544`,
`plugin/capie/src/core/signature_table.hpp` and
`plugin/capie/src/dll/capie_editor.cpp`. The bridge implementation is independent
of CAPIE. MinHook retains its upstream license; no game executable or external
DLL is included in this source tree.

## Native diplomacy and air/navy sidecars

Build the separate sidecars with `build-diplomacy.ps1` and
`build-air-navy.ps1`; they register `native_diplomacy_v2` and
`native_air_navy_v2` respectively. Load each DLL into the explicitly selected
game PID with `iron_injector.exe`, then verify operation readiness with a
read-only request. DLL loading success alone is not registration readiness.
All posts require the game to be paused and the requested country to be the
currently played country.

Build commands (each only compiles its module):

```powershell
pwsh -File native/iron_bridge/build.ps1
pwsh -File native/iron_bridge/build-clock.ps1
pwsh -File native/iron_bridge/build-control.ps1
pwsh -File native/iron_bridge/build-diplomacy.ps1
pwsh -File native/iron_bridge/build-air-navy.ps1
```

Diplomacy payloads: `goals TAG`, `read TAG RECIPIENT`, and
`build_declare TAG RECIPIENT GOAL_HANDLE` / `post_declare TAG RECIPIENT
GOAL_HANDLE`. The handle must be an available goal owned by the actor for that
recipient. A post receipt is not proof of war; read again and check the shared
war relation. Actual AUS-to-ITA declaration was posted and verified with the
v1 implementation. The current v2 build was read against all five controlled
countries and constructed a GER-to-FRA declaration with `CanExecute=true`
without posting it.

Air/navy read payloads include `air_list TAG`, `navy_list TAG`, and `regions`.
The supported command families are `air_build`/`air_post`,
`navy_build`/`navy_post`, `air_region_build`/`air_region_post`, and
`navy_regions_build`/`navy_regions_post`; build operations construct and
validate without posting. Region-post forms carry an expected prior assignment
to detect stale state. See `command_air_navy.cpp` for exact argument order.
Mission payloads are `air_post TAG WING_ID EXPECTED_MASK MISSION ENABLED` and
`navy_post TAG TASK_FORCE_ID EXPECTED_MISSION MISSION` (use the corresponding
`*_build` name to construct without posting). Region payloads are
`air_region_post TAG WING_ID EXPECTED_REGION TARGET_REGION` and
`navy_regions_post TAG TASK_FORCE_ID EXPECTED_REGIONS TARGET_REGIONS`; a region
list is comma-separated, and `-` means empty. Their `*_build` forms do not post.
Acceptance changed an air wing task and region, and a naval task force type
and its parent fleet's sea region. Sibling task forces share fleet regions;
these checks do not demonstrate actual air or naval combat.

## Native recording view

`build-view.ps1` builds `runs/native-view-v3/iron_view_v3.dll`. Load it with the
existing injector, then use `tools/native_view.py` with a per-process journal.
`native_view_v3` runs on the bridge's tick thread; it does not send input, open
the console, change focus, or advance the game clock.

| Payload | Meaning |
|---|---|
| `status` | Read pause/date, actual HUD-hidden flag, camera lock, position and target. |
| `hud EXPECTED DESIRED` | Set the hidden state (`1` hidden, `0` visible); already-desired requests do not toggle. |
| `lock_camera EXPECTED DESIRED` | Set the native camera-input lock; prevents edge scrolling while recording. |
| `center_province ID` | Request the ordinary province-camera transition. Inspect the settled image afterwards. |
| `frame_province ID HEIGHT` | Center a province and set camera height, an integer 50–1200. Use 90 for a Tyrol military close view; 310 is the central-European overview. |

Mutations require a paused game. The module pins the production executable and
callee bytes. HUD uses the vanilla `debug_nogui` handler at `0x263D70`, whose
body only toggles the flag at `0x33DDB19`. Camera lock uses the vanilla handler
at `0x263AD0`, with readback at camera `+0x6F0`. Province centering calls the
idler method `0xE347C0` used by vanilla `goto_province` (idler vslot `0x1B8`).
The idler camera getter is `0xE33A10`; current position is camera `+0x680`,
target is `+0x698`. `frame_province` uses the ordinary camera-target setter
`0x12F15C0`, retaining the resolved province's world position. No arbitrary
command strings or memory-write API is exposed.

`python tools/test_native_view.py` tests registration, thread dispatch, state
guards, receipt reuse, province bounds and date preservation in the owned host.
On 2026-11-10, the production bridge read back hidden HUD and a locked camera,
centered province 671, and OBS confirmed a clean central-European map at the
unchanged opening date. Per-process receipts and preview are in
`runs/tyrol_new_table_20261110/preflight_18992/` and
`runs/tyrol_new_table_20261110/native_clean_locked_preview.png`.

Use `hud 1 0` and `lock_camera 1 0` to restore normal manual viewing, with new
request IDs after reading the current state. As with other sidecars, do not
unload or overwrite an already-loaded DLL.

Use `tools/native_campaign.py` for campaign execution. The legacy desktop executor has been removed.
