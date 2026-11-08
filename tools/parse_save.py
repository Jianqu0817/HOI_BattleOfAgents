#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HOI4 文本存档解析器（Phase 1 感知层核心工具）。

用法:
  python parse_save.py <save.hoi4>                       # 输出状态摘要 JSON
  python parse_save.py <save.hoi4> -o runs/state.json    # 写入文件
  python parse_save.py <save.hoi4> --tags ITA,GER        # 额外关注的国家
  python parse_save.py <save.hoi4> --list                # 列出顶层键及块大小（探索模式）
  python parse_save.py <save.hoi4> --dump countries.ITA.politics  # 打印子树（探索模式）
  python parse_save.py --selftest                        # 内置自测

说明:
  - 存档必须是文本格式（settings.txt: save_as_binary=no）。
  - 采用流式解析 + 大块跳过（provinces 等不入内存），内存占用与耗时可控。
  - 顶层保留 version/date/player/countries(指定tag)/states/provinces/combat/wars/equipments，其余大块跳过。
"""

import argparse
import json
import re
import sys

TOKEN_RE = re.compile(r'("(?:\\.|[^"\\])*")|(#[^\n]*)|([{}=])|([^\s{}="#]+)')
CHUNK = 4 << 20

MAJOR_TAGS = ["GER", "SOV", "ENG", "FRA", "USA", "ITA", "JAP", "CHI"]
TOP_KEEP = {"version", "date", "player", "countries", "states", "provinces", "combat", "wars", "equipments"}

# 国家块内递归收集的关键字段（按 1.19 真实存档校准；找不到的会报告在 meta 中）
COUNTRY_KEYS = [
    "manpower", "political_power", "stability", "war_support", "command_power",
    "army_experience", "navy_experience", "air_experience",
    "capital", "faction", "focus", "parties", "ruling_party",
    "research_slot", "num_ships", "nukes",
]
SKIP_KEYS = {"province", "history"}  # 任何层级遇到这些键直接跳过子树
FACTORIES = ("industrial_complex", "arms_factory", "dockyard")


# ---------------------------------------------------------------- tokenizer

def _first_open_quote(buf, spans):
    """返回第一个不被完整字符串匹配覆盖的引号位置（即未闭合字符串起点），无则 None。"""
    spans = sorted(spans)
    idx, pos = 0, 0
    while True:
        j = buf.find('"', pos)
        if j == -1:
            return None
        while idx < len(spans) and spans[idx][1] <= j:
            idx += 1
        if idx < len(spans) and spans[idx][0] <= j:
            pos = spans[idx][1]
            continue
        return j


def tokenize_file(path):
    """流式分词: 返回 (type, value) 生成器。type ∈ {str, word, {, }, =}。

    跨 chunk 安全策略: 每轮只 yield 完全落在安全截断点之前的 token；
    截断点回退以避免裸词被 chunk 边界切断、跳过未闭合字符串内部。
    """
    buf = ""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        while True:
            data = f.read(CHUNK)
            if not data:
                break
            buf += data
            ms = list(TOKEN_RE.finditer(buf))
            cut = len(buf)
            if ms:
                m = ms[-1]
                if m.group(2) is not None and not m.group(2).endswith("\n"):
                    cut = m.start()  # 未完结的注释，留到下轮
                elif m.group(3) is None and m.end() == len(buf) and not m.group(1):
                    cut = m.start()  # 顶到缓冲区末尾的裸词可能被截断
                qpos = _first_open_quote(
                    buf, [(m.start(), m.end()) for m in ms if m.group(1) is not None])
                if qpos is not None:
                    cut = min(cut, qpos)  # 未闭合字符串，其后全是字符串内部
            for m in ms:
                if m.end() > cut:
                    break
                if m.group(1) is not None:
                    yield ("str", m.group(1)[1:-1].replace('\\"', '"'))
                elif m.group(3) is not None:
                    yield (m.group(3), m.group(3))
                else:
                    yield ("word", m.group(4))
            buf = buf[cut:]
    for m in TOKEN_RE.finditer(buf):  # 收尾: 文件末尾不存在截断问题
        if m.group(1) is not None:
            yield ("str", m.group(1)[1:-1].replace('\\"', '"'))
        elif m.group(3) is not None:
            yield (m.group(3), m.group(3))
        else:
            yield ("word", m.group(4))


class TokStream:
    """分词器包装，支持单 token 前进。"""

    def __init__(self, tokens):
        self._gen = tokens
        self.type, self.val = next(self._gen, (None, None))

    def next(self):
        t, v = self.type, self.val
        self.type, self.val = next(self._gen, (None, None))
        return t, v


def _skip_header(stream):
    """存档首行魔数 HOI4txt 后面不跟 '='，不跳过会导致键值错位。"""
    if stream.type == "word" and stream.val == "HOI4txt":
        stream.next()


def conv(word):
    """裸词 → int/float/bool，其余(日期/标识符)保留字符串。"""
    if word == "yes":
        return True
    if word == "no":
        return False
    try:
        return int(word)
    except ValueError:
        pass
    try:
        return float(word)
    except ValueError:
        return word


# ---------------------------------------------------------------- parser

def skip_value(st):
    """跳过一个值（标量或整块），不建结构。返回跳过的 token 数。"""
    n = 0
    if st.type == "{":
        depth = 0
        while True:
            t, _ = st.next()
            n += 1
            if t == "{":
                depth += 1
            elif t == "}" or t is None:
                depth -= 1
                if depth <= 0:
                    return n
    else:
        st.next()
        return 1


def parse_block(st, skip_keys=frozenset()):
    """解析一个 { ... } 块为 dict。同名键重复 → list。裸子块 → __items__。"""
    result = {}
    st.next()  # 吃掉 '{'
    while True:
        t, v = st.type, st.val
        if t is None:
            break
        if t == "}":
            st.next()
            break
        if t == "{":  # 无键裸块（列表元素）
            result.setdefault("__items__", []).append(parse_block(st, skip_keys))
            continue
        if t == "=":
            st.next()
            continue
        key = v
        st.next()
        if st.type == "=":
            st.next()
        elif st.type == "{":
            pass  # 键后直接跟块（罕见但合法）
        else:
            # 裸标量列表项，如 attackers={ "GER" "POL" }、owned_states={ 1 2 3 }
            result.setdefault("__items__", []).append(v if t == "str" else conv(v))
            continue
        if key in skip_keys:
            skip_value(st)
            continue
        if st.type == "{":
            val = parse_block(st, skip_keys)
        elif st.type in ("str", "word"):
            val = st.val if st.type == "str" else conv(st.val)
            st.next()
        else:
            val = None
        if key in result:
            if isinstance(result[key], list):
                result[key].append(val)
            else:
                result[key] = [result[key], val]
        else:
            result[key] = val
    return result


def parse_top_countries(st, wanted):
    """countries 块: 只完整解析 wanted 集合里的 tag，其余整块跳过。"""
    out = {}
    st.next()  # '{'
    while True:
        t, v = st.type, st.val
        if t is None or t == "}":
            st.next()
            break
        if t in ("word", "str"):
            tag = v
            st.next()
            if st.type == "=":
                st.next()
            if st.type == "{":
                if tag in wanted:
                    out[tag] = parse_block(st, SKIP_KEYS)
                else:
                    skip_value(st)
        else:
            st.next()
    return out


def parse_top_states(st):
    """states 块: 每个州跳过 province/history，保留 owner、victory_points 等小字段。"""
    out = {}
    st.next()
    while True:
        t, v = st.type, st.val
        if t is None or t == "}":
            st.next()
            break
        if t in ("word", "str"):
            sid = v
            st.next()
            if st.type == "=":
                st.next()
            if st.type == "{":
                out[sid] = parse_block(st, SKIP_KEYS | {"demographics", "resistance"})
        else:
            st.next()
    return out


def load_save(path, wanted_tags):
    stream = TokStream(tokenize_file(path))
    _skip_header(stream)
    save = {}
    while stream.type is not None:
        t, v = stream.type, stream.val
        if t in ("word", "str"):
            key = v
            stream.next()
            if stream.type == "=":
                stream.next()
            if stream.type == "{":
                if key == "countries":
                    save["countries"] = parse_top_countries(stream, wanted_tags)
                elif key == "states":
                    save["states"] = parse_top_states(stream)
                elif key in TOP_KEEP:
                    save[key] = parse_block(stream, SKIP_KEYS)
                else:
                    skip_value(stream)
            elif stream.type in ("str", "word"):
                save[key] = stream.val if stream.type == "str" else conv(stream.val)
                stream.next()
        else:
            stream.next()
    return save


# ---------------------------------------------------------------- 摘要

def collect_keys(node, keys, out, depth=0):
    """在子树中递归收集指定键的首次出现值。"""
    if depth > 12 or not isinstance(node, dict):
        return
    for k, v in node.items():
        if k in keys and k not in out:
            out[k] = v
        if isinstance(v, dict):
            collect_keys(v, keys, out, depth + 1)
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    collect_keys(item, keys, out, depth + 1)


def factory_summary(save):
    """按所有国统计民/军/船坞工厂等级总和（来自各州 buildings）。"""
    out = {}
    for s in save.get("states", {}).values():
        o, b = s.get("owner"), s.get("buildings")
        if not isinstance(o, str) or not isinstance(b, dict):
            continue
        slot = out.setdefault(o, {k: 0 for k in FACTORIES})
        for name in FACTORIES:
            blk = b.get(name)
            if isinstance(blk, dict):
                lvl = blk.get("level")
                if isinstance(lvl, (int, float)):
                    slot[name] += lvl
                elif isinstance(lvl, list):
                    slot[name] += sum(v for v in lvl if isinstance(v, (int, float)))
    return out


def clean_focus(focus):
    """focus={ current=... progress=... paused=... } → 精简摘要。"""
    if isinstance(focus, dict):
        return {"current": focus.get("current"), "progress": focus.get("progress")}
    return focus


def clean_parties(parties):
    """politics.parties={ fascism={ popularity=40 ... } } → { fascism: 40 }。"""
    if isinstance(parties, dict):
        return {k: v.get("popularity") if isinstance(v, dict) else v
                for k, v in parties.items() if k != "__items__"}
    return parties


def as_list(node):
    """把 dict 的 __items__ / 标量 / None 归一为 list。"""
    if node is None:
        return []
    if isinstance(node, list):
        return node
    if isinstance(node, dict):
        return node.get("__items__", [])
    return [node]


def clean_nukes(nukes):
    """nukes={ { amount=0 nukes_ready=0 } ... } → 总量摘要。"""
    items = nukes.get("__items__", []) if isinstance(nukes, dict) else []
    if not items:
        return nukes
    amount = sum(i.get("amount", 0) for i in items if isinstance(i, dict))
    ready = sum(i.get("nukes_ready", 0) for i in items if isinstance(i, dict))
    return {"amount": amount, "ready": ready}


def summarize(save, wanted_tags):
    player = save.get("player")
    tags = [t for t in wanted_tags if t in save.get("countries", {})]
    factories = factory_summary(save)
    countries = {}
    for tag in tags:
        c = save["countries"][tag]
        flat = {}
        collect_keys(c, set(COUNTRY_KEYS), flat)
        if "focus" in flat:
            flat["focus"] = clean_focus(flat["focus"])
        if "parties" in flat:
            flat["parties"] = clean_parties(flat["parties"])
        if "nukes" in flat:
            flat["nukes"] = clean_nukes(flat["nukes"])
        flat["factories"] = factories.get(tag, {k: 0 for k in FACTORIES})
        countries[tag] = flat

    owners = {}
    for s in save.get("states", {}).values():
        o = s.get("owner")
        if isinstance(o, str):
            owners[o] = owners.get(o, 0) + 1

    raw_wars = save.get("wars") or {}
    wv = raw_wars.get("war") if isinstance(raw_wars, dict) else None
    if isinstance(wv, list):
        war_list = wv
    elif isinstance(wv, dict):
        war_list = [wv]
    else:
        war_list = []
    wars = []
    for w in war_list:
        if isinstance(w, dict):
            wars.append({
                "attackers": as_list(w.get("attackers")),
                "defenders": as_list(w.get("defenders")),
            })

    return {
        "version": save.get("version"),
        "date": save.get("date"),
        "player": player,
        "countries": countries,
        "owned_states": {t: owners.get(t, 0) for t in tags},
        "wars": wars,
        "meta": {
            "country_keys_found": sorted(countries.get(player or tags[0], {}).keys()) if (player or tags) else [],
            "country_keys_missing": sorted(set(COUNTRY_KEYS) - set(countries.get(player or tags[0], {}).keys())) if (player or tags) else [],
        },
    }


# ---------------------------------------------------------------- 探索模式

def top_keys_with_sizes(path):
    """列出顶层键及每个块的 token 数（--list）。"""
    stream = TokStream(tokenize_file(path))
    _skip_header(stream)
    rows = []
    while stream.type is not None:
        t, v = stream.type, stream.val
        if t in ("word", "str"):
            key = v
            stream.next()
            if stream.type == "=":
                stream.next()
            if stream.type == "{":
                n = skip_value(stream)
                rows.append((key, f"block({n} tokens)"))
            elif stream.type in ("str", "word"):
                rows.append((key, stream.val if stream.type == "str" else conv(stream.val)))
                stream.next()
        else:
            stream.next()
    return rows


def navigate(root, dotted):
    cur = root
    for seg in dotted.split("."):
        if isinstance(cur, list):
            cur = cur[int(seg)]
        elif isinstance(cur, dict):
            cur = cur[seg]
        else:
            raise KeyError(f"无法在标量上继续寻址: {seg}")
    return cur


def dump_subtree(path, dotted, max_chars=20000):
    """解析完整存档（跳过 province/history），按点路径取子树打印。"""
    stream = TokStream(tokenize_file(path))
    _skip_header(stream)
    save = {}
    while stream.type is not None:
        t, v = stream.type, stream.val
        if t in ("word", "str"):
            key = v
            stream.next()
            if stream.type == "=":
                stream.next()
            if stream.type == "{":
                if key == dotted.split(".")[0]:
                    save[key] = parse_block(stream, SKIP_KEYS)
                    break
                skip_value(stream)
            elif stream.type in ("str", "word"):
                save[key] = stream.val if stream.type == "str" else conv(stream.val)
                stream.next()
        else:
            stream.next()
    node = navigate(save, dotted)
    text = json.dumps(node, ensure_ascii=False, indent=1)
    return text[:max_chars]


# ---------------------------------------------------------------- 自测

SAMPLE_SAVE = r'''
HOI4txt
version="1.19.2.0.a729"
date="1939.11.9.20"
player="ITA"
countries={
    ITA={
        manpower=123456.789
        political_power=118.000
        stability=0.62
        capital=95
        politics={
            ruling_party="fascism"
            parties={ fascism={ popularity=60 } democratic={ popularity=40 } }
            popularity={ fascism=0.8 democracy=0.1 }
        }
        idea={
            bonuses={ { name="test#notcomment" } { name="t2" } }
        }
    }
    GER={ manpower=999.0 }
    SOV={ manpower=888.0 }
}
states={
    95={ owner="ITA" victory_points={ 379=15 380=5 } province={ 379={ x=1 } }
        buildings={ industrial_complex={ level=3 partial_health=0 } arms_factory={ level=2 } } }
    96={ owner="GER" victory_points={ 400=3 } province={ 400={ x=2 } } }
    97={ owner="ITA" victory_points={ 381=1 } }
}
wars={
    war={
        attackers={ "GER" }
        defenders={ "POL" "ENG" }
    }
}
equipments={
    infantry_equipment_1={ id={ id=1326 type=70 } name="Kar98k" }
    infantry_equipment_1={ id={ id=2073 type=70 } name="九九式步枪" }
}
provinces={ 1={ sea=no controller="ETH" } 2={ sea=yes } }
'''


def selftest():
    import tempfile, os
    with tempfile.NamedTemporaryFile("w", suffix=".hoi4", delete=False, encoding="utf-8") as f:
        f.write(SAMPLE_SAVE)
        tmp = f.name
    try:
        save = load_save(tmp, {"ITA", "GER", "SOV"})
        s = summarize(save, ["ITA", "GER", "SOV"])
        assert s["date"] == "1939.11.9.20", s["date"]
        assert s["player"] == "ITA"
        assert abs(s["countries"]["ITA"]["manpower"] - 123456.789) < 1e-6
        assert "politics" not in s["countries"]["ITA"]  # 未在 COUNTRY_KEYS，flat 里不该有
        assert s["countries"]["ITA"]["ruling_party"] == "fascism"
        assert s["countries"]["GER"]["manpower"] == 999.0
        assert s["owned_states"]["ITA"] == 2
        assert s["owned_states"]["GER"] == 1
        assert s["countries"]["ITA"]["parties"] == {"fascism": 60, "democratic": 40}
        assert s["countries"]["ITA"]["factories"] == {
            "industrial_complex": 3, "arms_factory": 2, "dockyard": 0}
        assert s["countries"]["GER"]["factories"]["arms_factory"] == 0
        assert s["wars"][0]["attackers"] == ["GER"]
        assert s["wars"][0]["defenders"] == ["POL", "ENG"]
        # 单省controller可覆盖州owner；state内旧province子树仍跳过。
        assert save["provinces"]["1"]["controller"] == "ETH"
        assert save["provinces"]["2"]["sea"] is True
        assert "province" not in save["states"]["95"]
        assert save["equipments"]["infantry_equipment_1"][1]["id"]["id"] == 2073
        assert save["equipments"]["infantry_equipment_1"][1]["name"] == "九九式步枪"
        # 引号内 # 不当注释
        top = top_keys_with_sizes(tmp)
        assert any(k == "countries" for k, _ in top)
        print("SELFTEST OK")
    finally:
        os.unlink(tmp)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="HOI4 文本存档解析器")
    ap.add_argument("save", nargs="?", help=".hoi4 存档路径")
    ap.add_argument("-o", "--out", help="输出 JSON 路径（默认 stdout）")
    ap.add_argument("--tags", help="额外关注的国家 tag，逗号分隔")
    ap.add_argument("--list", action="store_true", help="列出顶层键")
    ap.add_argument("--dump", help="按点路径打印子树，如 countries.ITA.politics")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        return
    if not args.save:
        ap.error("需要存档路径（或 --selftest）")

    with open(args.save, "rb") as f:
        head = f.read(6)
    if head == b"HOI4bin":
        print("错误: 这是二进制存档（HOI4bin）。请在 settings.txt 中 save_as_binary=no 后重新存档。",
              file=sys.stderr)
        sys.exit(2)

    tags = set(MAJOR_TAGS)
    if args.tags:
        tags |= {t.strip().upper() for t in args.tags.split(",")}

    if args.list:
        for k, v in top_keys_with_sizes(args.save):
            print(f"{k}\t{v}")
        return
    if args.dump:
        print(dump_subtree(args.save, args.dump))
        return

    save = load_save(args.save, tags)
    summary = summarize(save, sorted(tags))
    js = json.dumps(summary, ensure_ascii=False, indent=1)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(js)
        print(f"已写入 {args.out}")
    else:
        print(js)


if __name__ == "__main__":
    main()
