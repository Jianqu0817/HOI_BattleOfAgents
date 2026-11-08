"""Prepare an explicitly modified European land-war test save, never force a declaration."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from campaign import establish_control, load_config, name_ok
from m0_probe import ROOT, Probe, settled_pause_state, write_json
from agent_contract import country_fingerprint
from diplomacy_actions import war_pairs, wargoals

TARGETS = {"GER": "FRA", "FRA": "ITA", "ITA": "SWI", "AUS": "ITA", "SWI": "GER"}


def prepare(name, known_disabled_pid=None):
    name_ok(name)
    config = load_config("configs/campaign.europe.example.json")
    directory = ROOT / "runs/scenarios" / name
    path = directory / "setup.json"
    if path.exists():
        raise ValueError("场景记录已存在，请检查原记录；不会重复执行初始化。")
    pid = establish_control(config, directory, known_disabled_pid)
    result = {"schema_version": 1, "name": name, "mode": "test_fixture", "status": "starting",
              "countries": config["countries"], "targets": TARGETS, "game_pid": pid,
              "starting_changes": ["Add one annexation war goal per participating country"],
              "notice": "预设战争目标属于测试起点，不是模型取得的成果。没有强制宣战、瞬移或添加部队。",
              "started_at": datetime.now(timezone.utc).isoformat()}
    write_json(path, result)
    probe = Probe(directory, config["countries"])
    try:
        with probe.session.lock.hold("IRON explicit European test fixture"):
            status = probe.call("hoi4_status", {})
            if status["game"]["pids"] != [pid] or settled_pause_state(probe.session)[0] != "paused":
                raise ValueError("同一已受控游戏进程与暂停状态是必要条件")
            result["before_save"] = probe.save("iron_" + name + "_before")
            before = json.loads((directory / ("iron_" + name + "_before.json")).read_text(encoding="utf8"))
            result["steps"] = []
            for actor, target in TARGETS.items():
                existing = [goal for goal in wargoals(before, actor) if goal["target_country"] == target]
                if existing:
                    result["steps"].append({"country": actor, "target": target, "status": "existing_goal", "goals": existing})
                    continue
                commands = ["tag " + actor,
                            "eval_effect FROM = { create_wargoal = { type = annex_everything target = " + target + " } }"]
                entry = {"country": actor, "target": target, "status": "sending", "commands": commands}
                result["steps"].append(entry)
                write_json(path, result)
                entry["receipt"] = probe.command(commands)
                entry["status"] = "sent_pending_save"
                write_json(path, result)
            result["after_save"] = probe.save("iron_" + name + "_ready")
            after = json.loads((directory / ("iron_" + name + "_ready.json")).read_text(encoding="utf8"))
            checks = {"same_date": before["date"] == after["date"],
                      "no_forced_war": war_pairs(before) == war_pairs(after),
                      "gameplay_unchanged": all(country_fingerprint(before, tag) == country_fingerprint(after, tag)
                                                for tag in config["countries"]),
                      "required_goals_present": all(any(goal["target_country"] == target for goal in wargoals(after, actor))
                                                    for actor, target in TARGETS.items())}
            result.update(checks=checks, goals={tag: wargoals(after, tag) for tag in config["countries"]},
                          date=after["date"], status="prepared" if all(checks.values()) else "needs_inspection")
            result["identity_receipts"] = probe.receipts(config["countries"] + [config["countries"][0]], "europe_fixture_ready")
            from hoi4_live.game import console, panels
            console.close_console(probe.session)
            panels.to_map(probe.session)
            result["screenshot"] = probe.session.artifact(probe.session.grab(), "europe_fixture_ready")
    except Exception as error:
        result.update(status="interrupted", error=str(error))
        raise
    finally:
        result["ended_at"] = datetime.now(timezone.utc).isoformat()
        write_json(path, result)
        probe.session.shutdown()
    if result["status"] != "prepared":
        raise RuntimeError("场景未通过核验，请检查 setup.json，不要重复初始化。")
    return result


def main():
    import sys
    sys.stdout.reconfigure(encoding="utf8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--known-disabled-pid", type=int)
    args = parser.parse_args()
    result = prepare(args.name, args.known_disabled_pid)
    print(json.dumps({key: result[key] for key in ("name", "status", "date", "checks", "goals")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
