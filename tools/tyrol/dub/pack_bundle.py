"""Build the private dubbing ZIP from an exported silent cut and the local cast."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DELIVERY = ROOT / "exports/tyrol_two_flags_20261109/完整静音粗剪"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cut", type=Path, default=DELIVERY / "蒂罗尔的两面旗·完整静音粗剪.mp4")
    parser.add_argument("--sheets", type=Path, default=DELIVERY / "服务器配音包")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--engine", type=Path, help="Prepared engine/ directory from the pinned standalone runtime")
    args = parser.parse_args()
    manifest = json.loads((args.sheets / "manifest.json").read_text("utf-8-sig"))
    inputs = {"reference.mp4": (args.cut, manifest["video_sha256"])}
    for name in ("run.py", "run.sh", "batch_dub.py", "inference-requirements.txt"):
        inputs[name] = (HERE / name, None)
    inputs["gpt_sovits.py"] = (ROOT / "tools/gpt_sovits.py", None)
    for name in ("manifest.json", "voice_lines.jsonl", "整期字幕.srt", "整期台词_按时间.csv", "章节.csv"):
        inputs[name] = (args.sheets / name, None)
    for path in sorted((args.sheets / "roles").iterdir()):
        if path.suffix in {".jsonl", ".csv"}:
            inputs["roles/" + path.name] = (path, None)
    cast = json.loads((ROOT / "configs/tts.cast.json").read_text("utf-8"))
    cast.update(json.loads((HERE / "pickups.json").read_text("utf-8")))
    for role, voice in cast["voices"].items():
        inventory = json.loads((ROOT / "assets/voices" / role / "manifest.json").read_text("utf-8"))
        hashes = {r["path"]: r["sha256"] for r in inventory["models"] + inventory["references"]}
        for name in [voice["gpt_weights"], voice["sovits_weights"], voice["ref_audio_path"], *voice["aux_ref_audio_paths"]]:
            inputs[name] = (ROOT / name, hashes[name])
    if args.engine:
        for line in (HERE / "engine.sha256").read_text("utf-8").splitlines():
            checksum, name = line.split("  ", 1)
            inputs[name] = (args.engine / name.removeprefix("engine/"), checksum)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.output.parent) as tmp:
        folder = Path(tmp)
        config = folder / "tts.cast.json"
        config.write_text(json.dumps(cast, ensure_ascii=False, indent=2), "utf-8")
        inputs[config.name] = (config, None)
        archive = folder / "bundle.zip"
        checksums = []
        with zipfile.ZipFile(archive, "w") as bundle:
            for name, (path, expected) in sorted(inputs.items()):
                h = hashlib.sha256()
                with path.open("rb") as src, bundle.open("tyrol-dub/" + name, "w", force_zip64=True) as dest:
                    for chunk in iter(lambda: src.read(1024 * 1024), b""):
                        h.update(chunk)
                        dest.write(chunk)
                checksum = h.hexdigest()
                if expected and checksum != expected:
                    raise ValueError(f"Source SHA-256 mismatch: {name}")
                checksums.append(f"{checksum}  {name}\n")
            bundle.writestr("tyrol-dub/SHA256SUMS", "".join(checksums))
        archive.replace(args.output)
    print(args.output)


if __name__ == "__main__":
    main()
