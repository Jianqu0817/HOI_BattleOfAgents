"""Resolve the host's reading when a show is created and reuse it on resume."""
from pathlib import Path


LIBRARY = Path(__file__).resolve().parents[1] / "third_party" / "host_skills"
READINGS = {
    "reality-tv-narrative-analyzer": ("SKILL.md", ["### The Story Producer's Questions", "### Power Dynamic Mapping"]),
    "director-agent": ("references/screenplay-writing-core.md", ["## 4. Write Dialogue As Behavior"]),
}


def load_host_readings(names):
    readings = {}
    for name in names:
        file, headings = READINGS[name]
        lines = (LIBRARY / name / file).read_text(encoding="utf-8-sig").splitlines()
        sections = []
        for heading in headings:
            start = lines.index(heading)
            end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("#")), len(lines))
            sections.append("\n".join(lines[start:end]).strip())
        readings[name] = "\n\n".join(sections)
    return readings


def host_guidance(show):
    readings = (show or {}).get("host_readings")
    if not readings:
        return ""
    return ("\n你负责现场主持，用户和 Codex 在录制前商定桥段。以下是主持备课材料。"
            "场上只说智谱娘自己的话，不念术语、讲义或导演意见。以本次任务、可见公开来源和输出格式为准。\n"
            + "\n\n".join(f"{name}\n{readings[name]}" for name in show["host_skills"])
            + "\n同时留意地图上出了什么事、桌边谁在接谁的话。用具体公开措辞承接这两条线。"
              "一轮先挑最值得接的一句话，请一个人回应。需要第二个人回答同一件事时再递话。玩家可以接话、岔开或拒绝。"
              "地图上的交战关系只说明两国处于战争状态，具体交火、进军或伤亡须由公开记录证明。"
              "关系、态度和比赛结果都以实际言行为准，不能给人安角色、编造动机或预写回应。")
