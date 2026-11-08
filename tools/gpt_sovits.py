"""Local GPT-SoVITS api_v2 adapter for already-frozen audience voice lines."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from functools import lru_cache
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave


API_VERSION = "api_v2"
PARAMETERS = {"top_k", "top_p", "temperature", "text_split_method", "speed_factor", "seed",
              "repetition_penalty", "fragment_interval"}
MAX_AUDIO_BYTES = 80 * 1024 * 1024


@dataclass(frozen=True)
class VoiceLine:
    character_id: str
    text: str
    event_id: str = ""
    visibility: str = "audience"
    line_id: str = ""

    @classmethod
    def from_dict(cls, value):
        return cls(**{field: value.get(field, "audience" if field == "visibility" else "")
                      for field in cls.__dataclass_fields__})


@lru_cache(maxsize=64)
def _file_digest(path, size, modified):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source(path):
    file = Path(path).resolve()
    stat = file.stat()
    return {"path": str(file), "sha256": _file_digest(str(file), stat.st_size, stat.st_mtime_ns)}


def voice_state(config, character_id):
    """Readiness of local assets; service readiness is checked separately."""
    voice = config.get("voices", {}).get(character_id)
    if not voice:
        return {"status": "missing_voice", "character_id": character_id}
    missing = [field for field in ("gpt_weights", "sovits_weights")
               if not voice.get(field) or not Path(voice[field]).is_file()]
    if missing:
        return {"status": "missing_weights", "missing": missing, "character_id": character_id}
    if not voice.get("ref_audio_path") or not Path(voice["ref_audio_path"]).is_file() or not voice.get("prompt_text"):
        return {"status": "missing_reference", "character_id": character_id}
    auxiliary = voice.get("aux_ref_audio_paths", [])
    if not isinstance(auxiliary, list) or any(not isinstance(path, str) or not path or not Path(path).is_file()
                                               for path in auxiliary):
        return {"status": "missing_reference", "character_id": character_id,
                "missing": ["aux_ref_audio_paths"]}
    return {"status": "available", "character_id": character_id}


def _request(base_url, endpoint, timeout, body=None):
    request = urllib.request.Request(base_url.rstrip("/") + endpoint,
        data=None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={} if body is None else {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(MAX_AUDIO_BYTES + 1)
            if len(data) > MAX_AUDIO_BYTES:
                raise RuntimeError("TTS response exceeds audio size limit")
            return data
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"GPT-SoVITS HTTP {error.code}: " + error.read(1500).decode("utf-8", "replace")) from None
    except (urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError("GPT-SoVITS unavailable: " + str(error)) from None


def backend_status(config):
    try:
        schema = json.loads(_request(config["base_url"], "/openapi.json", min(config.get("timeout_seconds", 300), 5)))
        if "/tts" not in schema.get("paths", {}) or "/set_gpt_weights" not in schema.get("paths", {}):
            return {"status": "wrong_api"}
        return {"status": "available", "api_version": API_VERSION, "base_url": config["base_url"]}
    except (RuntimeError, ValueError) as error:
        return {"status": "unavailable", "detail": str(error)}


@contextmanager
def _backend_lock(base_url, timeout):
    """Keep weight selection and synthesis together, even across IRON processes."""
    key = hashlib.sha256(base_url.rstrip("/").encode()).hexdigest()[:24]
    path = Path(tempfile.gettempdir()) / ("iron-gpt-sovits-" + key + ".lock")
    deadline = time.monotonic() + timeout
    while True:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, str(os.getpid()).encode())
            os.close(descriptor)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise RuntimeError("GPT-SoVITS busy; lock timeout at " + str(path))
            time.sleep(.1)
    try:
        yield
    finally:
        path.unlink()


def _wav_info(audio):
    try:
        with wave.open(io.BytesIO(audio), "rb") as stream:
            if stream.getnframes() <= 0:
                raise ValueError("empty WAV")
            return {"sample_rate": stream.getframerate(), "duration_seconds": round(stream.getnframes() / stream.getframerate(), 3)}
    except (wave.Error, EOFError, ValueError) as error:
        raise RuntimeError("GPT-SoVITS returned invalid WAV: " + str(error)) from None


def synthesize(config, line, output_dir):
    """Return an explicit receipt; input text and visibility are never rewritten."""
    if isinstance(line, dict):
        line = VoiceLine.from_dict(line)
    receipt = {"backend": "gpt_sovits", "api_version": API_VERSION, **asdict(line)}
    if not line.character_id or not isinstance(line.text, str) or not line.text.strip():
        return {**receipt, "status": "invalid_line"}
    state = voice_state(config, line.character_id)
    if state["status"] != "available":
        return {**receipt, **state}
    voice = config["voices"][line.character_id]
    timeout = config.get("timeout_seconds", 300)
    parameters = config.get("request_parameters", {}) | voice.get("request_parameters", {})
    if set(parameters) - PARAMETERS:
        raise ValueError("Unsupported GPT-SoVITS request parameter")
    payload = {"text": line.text, "text_lang": voice.get("text_lang", "zh"),
               "ref_audio_path": str(Path(voice["ref_audio_path"]).resolve()),
               "prompt_text": voice["prompt_text"], "prompt_lang": voice.get("prompt_lang", "zh"),
               "media_type": "wav", "streaming_mode": False, "batch_size": 1,
               "parallel_infer": False, "use_cuda_graph": False, "use_flash_attention": False,
               "seed": 0, **parameters}
    sources = {field: _source(voice[field]) for field in ("gpt_weights", "sovits_weights", "ref_audio_path")}
    auxiliary = voice.get("aux_ref_audio_paths", [])
    if auxiliary:
        payload["aux_ref_audio_paths"] = [str(Path(path).resolve()) for path in auxiliary]
        sources["aux_ref_audio_paths"] = [_source(path) for path in auxiliary]
    identity = {"payload": payload, "sources": sources, "character_id": line.character_id,
                "backend_revision": config.get("backend_revision", ""), "api_version": API_VERSION}
    cache_key = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (cache_key + ".wav")
    receipt.update(cache_key=cache_key, path=str(target.resolve()), sources=sources)
    try:
        with _backend_lock(config["base_url"], timeout):
            if target.is_file():
                return {**receipt, "status": "cached", **_wav_info(target.read_bytes())}
            for field, endpoint in (("gpt_weights", "/set_gpt_weights"), ("sovits_weights", "/set_sovits_weights")):
                query = urllib.parse.urlencode({"weights_path": sources[field]["path"]})
                _request(config["base_url"], endpoint + "?" + query, timeout)
            audio = _request(config["base_url"], "/tts", timeout, payload)
            info = _wav_info(audio)
            temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                temporary.write_bytes(audio)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        return {**receipt, "status": "generated", **info}
    except (RuntimeError, OSError) as error:
        return {**receipt, "status": "failed", "detail": str(error)}
