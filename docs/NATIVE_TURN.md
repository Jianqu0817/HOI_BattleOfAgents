# Native military turns

`tools/native_turn.py` runs 3–5 countries through `NativeGame` and the injected
native bridge. It supports division moves, owned-goal war declarations, air
missions/regions, and navy missions/regions. It is separate from the earlier
UI campaign runner. It uses no screenshots, keyboard/mouse input, model API,
or API key.

The core bridge and current clock/control/diplomacy-v2/air-navy-v2 sidecars must
already be loaded in the selected game process. Start paused with the currently
played country included in the plan. Obtain current IDs, owned goal handles,
and expected values from `tools/native_game.py` before preparing a plan.
`configs/native_turn.example.json` contains placeholders, not a live scenario.

Validate the whole JSON schema without connecting to the game:

```powershell
./.venv/Scripts/python.exe tools/native_turn.py --plan configs/native_turn.example.json --output runs/my_native_turn
```

Execute a prepared plan with a fresh output directory:

```powershell
./.venv/Scripts/python.exe tools/native_turn.py --execute --pid YOUR_GAME_PID --plan YOUR_PLAN.json --output runs/my_native_turn --save-dir "YOUR_HOI4_SAVE_DIRECTORY"
```

The plan's `schema` is `iron.native-turn.v1`. `countries` contains 3–5 distinct
`{tag, actions}` objects, with at most six actions per country. Every action has
a unique `id`. The aggregate `step_hours` is 1–720; 720 hours is thirty game days.
The native bridge still receives steps of at most six hours, with event checks
between them. AI is
disabled for the planned countries by default. Set `disable_ai` to `false` only
when all planned countries already have AI disabled.

| `kind` | Required action fields, besides `id` and `kind` |
|---|---|
| `division.move` | `unit_id`, `source`, `target` |
| `diplomacy.declare_war` | `recipient`, `goal_handle` (`0x…` string) |
| `air.mission` | `wing_id`, `expected_mask`, `mission_bit` (one bit), `enabled` (boolean) |
| `air.region` | `wing_id`, `expected_region`, `target_region` |
| `navy.mission` | `task_force_id`, `expected_type` (0–9), `mission_type` (0–9 except internal type 8) |
| `navy.regions` | `task_force_id`, `expected_regions`, `target_regions` (ID arrays) |

All IDs come from native owned-object snapshots. Navy region changes affect the
task force's fleet; the runner rejects two region changes for the same fleet.
An idle division is required for `division.move`. The runner rejects unknown
fields, duplicate action IDs, duplicate guarded fields, and stale baselines.

Schema validation finishes before any game connection. Live preflight switches
the played country through guarded native calls while the game remains paused
at one raw date. It collects country/unit snapshots and constructs each planned
command without posting it. After every action passes preflight, it restores
the initial country, disables and verifies country AI, then posts orders. Each
order gets a fresh baseline check and a separate observation of its effect.

After orders, the runner restores the initial country and advances the requested
hours using the game's native bounded auto-pause. Completion requires exactly
the requested raw-hour delta, an actual paused state, and a zero remaining-hour
counter. It then observes the actions again and invokes a native save with a
unique basename. With `--save-dir`, it verifies that save file and records its
size/hash. Without that argument, save output is an acknowledgement whose file
has not been verified. Native save-file hour labels use a different convention
from the native ISO date; the clock check uses raw-hour differences.

Outputs are `plan.json`, per-request intent/receipt records under `requests/`,
`audience_facts.jsonl`, and `result.json` on completion. Facts distinguish order
intent, queue acknowledgement, observed path/mask/region/war state, clock effect,
and save-file evidence. A move with a generated path can still be travelling at
the end of the turn; its arrival is recorded separately.

Any failure stops further dispatch. Earlier accepted orders may remain active;
there is no automatic rollback or recovery toggle. A transport timeout has an
unknown outcome. The output directory is never reused, so a failed run cannot
silently resend its orders with new IDs. Inspect its existing request IDs and
receipts to reconcile the game before preparing another turn. On failure the
last known controller is recorded rather than automatically changing it.
