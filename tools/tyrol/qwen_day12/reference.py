"""Enroll the pilot cast on Qwen from the existing Japanese character recordings."""
import base64
import hashlib
import io
import json
import itertools
import random
import secrets
import time
import wave

import requests

from script import ROOT

API = "https://maas.qianwenaiapi.com/api/v1/services/audio/tts/customization"
CAST_IDS = {"GLM": "glm", "GER": "gpt", "ITA": "gemini", "AUS": "deepseek", "FRA": "claude", "SWI": "qwen"}


def enrollment(key, action, **fields):
    response = requests.post(API, headers={"Authorization": "Bearer " + key},
                             json={"model": "voice-enrollment", "input": {"action": action, **fields}},
                             timeout=(15, 90), allow_redirects=False)
    result = response.json()
    if response.status_code != 200 or "output" not in result:
        raise RuntimeError(f"{action}: HTTP {response.status_code}, {result.get('code')} {str(result.get('message',''))[:220]}")
    return result["output"]


def reference_clip(paths):
    target = io.BytesIO()
    with wave.open(target, "wb") as out:
        out.setparams((1, 2, 44100, 0, "NONE", "not compressed"))
        for index, source in enumerate(paths):
            with wave.open(str(ROOT / source), "rb") as audio:
                if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 44100):
                    raise ValueError(f"Unexpected reference format: {source}")
                if index:
                    out.writeframesraw(bytes(22050))  # 250 ms between complete recordings.
                out.writeframesraw(audio.readframes(audio.getnframes()))
    return target.getvalue()


def reference_assignments(tags, work):
    """Draw balanced, non-repeating pairs per speaker; persist the draw so interrupted jobs resume."""
    cast = json.loads((ROOT / "configs/tts.cast.json").read_text("utf8"))["voices"]
    state = work / "reference_draw.json"
    work.mkdir(parents=True, exist_ok=True)
    seed = json.loads(state.read_text("utf8"))["seed"] if state.exists() else secrets.randbits(63)
    rng = random.Random(seed)
    choices = {}
    for tag in sorted(set(tags)):
        role = CAST_IDS[tag]
        config = cast[role]
        sources = [config["ref_audio_path"], *config["aux_ref_audio_paths"]]
        if role == "glm":
            sources = [s for s in sources if not s.endswith(("CH0250_EventLobby_1.wav", "Kazusa_LogIn_1.wav"))]
        pairs = list(itertools.combinations(dict.fromkeys(sources), 2))
        if not pairs:
            raise ValueError(f"No reference pair available for {role}")
        choices[tag] = rng.sample(pairs, min(3, len(pairs)))
    bags, last, result = {}, {}, []
    for tag in tags:
        if not bags.get(tag):
            bag = choices[tag].copy()
            rng.shuffle(bag)
            if len(bag)>1 and bag[-1] == last.get(tag):
                bag[0], bag[-1] = bag[-1], bag[0]
            bags[tag] = bag
        pair = bags[tag].pop()
        last[tag] = pair
        result.append(pair)
    temp = state.with_suffix(".tmp")
    temp.write_text(json.dumps({"seed":seed,"tags":tags,"pairs":result},ensure_ascii=False,indent=2),"utf8")
    temp.replace(state)
    return result


def load_voice(key, model, tag, work, sources=None):
    role = CAST_IDS[tag]
    settings = json.loads((ROOT / "configs/tts.cast.json").read_text("utf8"))["voices"][role]
    fixed = sources is None
    sources = list(sources) if sources else [settings["ref_audio_path"], settings["aux_ref_audio_paths"][0]]
    audio = reference_clip(sources)
    sha = hashlib.sha256(audio).hexdigest()
    prefix = role[:3] + sha[:7]
    directory = work / "references"
    if not fixed:
        directory = directory / sha[:16]
    directory.mkdir(exist_ok=True, parents=True)
    path = directory / (role + ".json")
    prior = json.loads(path.read_text("utf8")) if path.is_file() else {}
    voice = prior.get("voice_id") if prior.get("sha256") == sha and prior.get("model") == model else None
    if not voice:
        listed = enrollment(key, "list_voice", prefix=prefix, page_index=0, page_size=10)
        for item in listed.get("voice_list", []):
            candidate = item.get("voice_id") or item.get("voice", "")
            if candidate.startswith(model + "-" + prefix + "-"):
                voice = candidate
                break
    if not voice:
        data = "data:audio/wav;base64," + base64.b64encode(audio).decode("ascii")
        reply = enrollment(key, "create_voice", prefix=prefix, target_model=model, audio=data, language_hints=["ja"])
        voice = reply["voice_id"]
    if not voice.startswith(model + "-" + prefix + "-"):
        raise RuntimeError(f"Unexpected enrolled voice for {role}")
    (directory / (role + ".wav")).write_bytes(audio)
    path.write_text(json.dumps(dict(role=role, model=model, name=settings["voice_name"], voice_id=voice,
                                    sources=sources, sha256=sha), ensure_ascii=False, indent=2), "utf8")
    for _ in range(45):
        status = enrollment(key, "query_voice", voice_id=voice)["status"]
        if status == "OK":
            return voice
        if status != "DEPLOYING":
            raise RuntimeError(f"{role}: enrollment state {status}")
        time.sleep(2)
    raise RuntimeError(f"{role}: enrollment still processing; rerun to resume")
