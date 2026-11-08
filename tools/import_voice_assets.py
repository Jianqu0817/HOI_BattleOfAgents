"""Copy selected local voice assets into IRON and freeze source fingerprints."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import wave

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def epoch(path):
    match = re.search(r"[-_]e(\d+)(?:_|\.)", path.name, re.IGNORECASE)
    if not match:
        raise ValueError(f"Model has no training epoch: {path}")
    return int(match[1])


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def import_voices(source_config, output, config_path):
    rows = json.loads(source_config.read_text(encoding="utf-8-sig"))["voices"]
    config = {"backend": "gpt_sovits", "base_url": "http://127.0.0.1:9880", "api_version": "api_v2",
              "backend_revision": "04a44eb977586645f33594b72af13a035a2056c6", "timeout_seconds": 300,
              "request_parameters": {"text_split_method": "cut5", "seed": 0, "speed_factor": 1.0}, "voices": {}}
    manifest = {"schema": "iron.voice-assets.v1", "model_selection": "highest training epoch, not download timestamp",
                "voices": {}}
    for row in rows:
        identity = row["character_id"]
        if not identity.isalnum():
            raise ValueError("Unsafe character ID")
        root = Path(row["root"])
        models = [max(root.rglob(extension), key=epoch) for extension in ("*.ckpt", "*.pth")]
        references = sorted(Path(row["references"]).glob("*.wav"), key=lambda path: path.name.lower())
        references += [Path(path) for path in row.get("extra_references", []) if Path(path) not in references]
        primary = next(path for path in references if path.name.lower() == row["primary"].lower())
        references = [primary] + [path for path in references if path != primary]
        transcripts = {}
        for line in Path(row["transcript"]).read_text(encoding="utf-8-sig").splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                transcripts[parts[0].replace("\\", "/").rsplit("/", 1)[-1].lower()] = parts[3].strip()
        # Check every selected reference before copying or requesting synthesis.
        reference_rows = []
        for path in references:
            text = transcripts[path.name.lower()]
            if not text:
                raise ValueError(f"Empty reference transcript: {path}")
            with wave.open(str(path), "rb") as stream:
                seconds = stream.getnframes() / stream.getframerate()
            if not 3 <= seconds <= 10:
                raise ValueError(f"Reference outside 3-10 seconds: {path}")
            reference_rows.append({"prompt_text": text, "seconds": round(seconds, 6)})
        directory = output / identity
        copied = []
        for index, source in enumerate(models + references):
            destination = directory / ("models" if index < 2 else "references") / source.name
            source_sha = digest(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.is_file() or digest(destination) != source_sha:
                shutil.copy2(source, destination)
            if digest(destination) != source_sha:
                raise RuntimeError(f"Copy verification failed: {destination}")
            copied.append({"source": str(source), "path": destination.relative_to(ROOT).as_posix(),
                           "sha256": source_sha, "bytes": destination.stat().st_size})
        voice = {"voice_name": row["voice_name"], "gpt_weights": copied[0]["path"],
                 "sovits_weights": copied[1]["path"], "ref_audio_path": copied[2]["path"],
                 "aux_ref_audio_paths": [item["path"] for item in copied[3:]],
                 "prompt_text": reference_rows[0]["prompt_text"], "prompt_lang": "ja", "text_lang": "zh"}
        config["voices"][identity] = voice
        details = {"voice_name": row["voice_name"], "version": models[1].parent.name.removeprefix("SoVITS_weights_"),
                   "gpt_epoch": epoch(models[0]), "sovits_epoch": epoch(models[1]),
                   "models": copied[:2], "references": [{**item, **reference} for item, reference in zip(copied[2:], reference_rows)]}
        manifest["voices"][identity] = details
        write(directory / "manifest.json", details)
        print(json.dumps({"character_id": identity, "voice_name": row["voice_name"],
                          "epochs": [epoch(path) for path in models], "references": len(references)}, ensure_ascii=False), flush=True)
    write(output / "manifest.json", manifest)
    write(config_path, config)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=ROOT / "configs/voices.sources.local.json")
    parser.add_argument("--output", type=Path, default=ROOT / "assets/voices")
    parser.add_argument("--config", type=Path, default=ROOT / "configs/tts.cast.json")
    args = parser.parse_args()
    import_voices(args.sources, args.output.resolve(), args.config)
