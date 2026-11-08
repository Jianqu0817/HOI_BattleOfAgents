"""Explicit end-of-match scores from saved province controllers, never inferred capitulations."""
import json


def validate_rules(rules):
    rules = rules or {"mode": "exhibition"}
    if not isinstance(rules, dict) or rules.get("mode") not in ("exhibition", "territory_delta", "objectives"):
        raise ValueError("scoring.mode 必须是 exhibition、territory_delta 或 objectives")
    if rules["mode"] == "objectives":
        ids = rules.get("province_ids")
        if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids) or any(type(i) is not int or i <= 0 for i in ids):
            raise ValueError("objectives 需要非空、互不重复的正整数 province_ids")
    return rules


def controllers(state, index):
    if "provinces" not in state:
        raise ValueError("计分需要包含 provinces 控制权的新存档")
    result = {}
    for province, row in index.get("provinces", {}).items():
        if row.get("kind") != "land":
            continue
        owner = state.get("states", {}).get(str(row.get("state")), {}).get("owner")
        result[int(province)] = state["provinces"].get(str(province), {}).get("controller", owner)
    if not result:
        raise ValueError("当前地图没有已校准的陆地省份索引")
    return result


def score(before, after, countries, index, rules=None, final=False):
    rules = validate_rules(rules)
    mode = rules["mode"]
    if mode == "exhibition" and not index.get("provinces"):
        first, last = {}, {}
    else:
        first, last = controllers(before, index), controllers(after, index)
    objectives = rules.get("province_ids", [])
    if mode == "objectives" and any(i not in last for i in objectives):
        raise ValueError("目标点不是当前地图已校准的陆地省份")
    rows = []
    for tag in countries:
        initial = sum(owner == tag for owner in first.values()) if first else None
        current = sum(owner == tag for owner in last.values()) if last else None
        delta = current - initial if current is not None else None
        points = delta if mode == "territory_delta" else sum(last[i] == tag for i in objectives) if mode == "objectives" else None
        rows.append({"country": tag, "initial_controlled_land": initial, "controlled_land": current,
                     "land_delta": delta, "points": points,
                     "owned_states": sum(row.get("owner") == tag for row in after.get("states", {}).values())})
    best = max(row["points"] for row in rows) if mode != "exhibition" else None
    winners = [row["country"] for row in rows if row["points"] == best] if final and mode != "exhibition" else []
    return {"mode": mode, "from_date": before["date"], "to_date": after["date"], "final": final,
            "rows": rows, "winners": winners, "tied": len(winners) > 1,
            "rule": {"exhibition": "展演：仅展示统计，不评定冠军", "territory_delta": "固定终点控制陆地省份数减起点，最高分获胜；同分并列",
                     "objectives": "固定终点每控制一个配置目标省份得1分；最高分获胜，同分并列"}[mode],
            "notice": "这是本场预设计分规则，不等同于原版战争胜负或投降判定。"}


def rules_text(rules, turn_limit, step_hours):
    rules = validate_rules(rules)
    return f"\n本场固定运行{turn_limit}个回合，每回合{step_hours}游戏小时。计分模式={rules['mode']}。" + (
        "本场为展演，不评唯一胜者，目标是发展本国实力并验证正常操作。" if rules["mode"] == "exhibition" else
        "按固定终点相对起点增加的控制陆地省份数计分，最高分获胜，同分并列。" if rules["mode"] == "territory_delta" else
        "固定终点每控制一个目标省份得1分，最高分获胜，同分并列。目标省份：" + json.dumps(rules["province_ids"]))
