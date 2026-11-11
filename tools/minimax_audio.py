"""One MiniMax HTTP TTS take, with persistent Windows user-env credentials."""
import argparse
from datetime import datetime, timezone
from getpass import getpass
import json
import os
from pathlib import Path
import sys

import requests


ENV_KEY = 'MINIMAX_API_KEY'


def load_key():
    key = os.environ.get(ENV_KEY)
    if not key and sys.platform == 'win32':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as env:
                key = winreg.QueryValueEx(env, ENV_KEY)[0]
        except FileNotFoundError:
            pass
    return key


def save_key(key):
    if sys.platform != 'win32':
        raise RuntimeError('--save-key requires Windows user environment storage')
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, 'Environment') as env:
        winreg.SetValueEx(env, ENV_KEY, 0, winreg.REG_SZ, key)
    os.environ[ENV_KEY] = key
    # Notify Explorer/new terminals; an already-running parent cannot inherit it.
    import ctypes
    result = ctypes.c_size_t()
    ctypes.windll.user32.SendMessageTimeoutW(
        0xffff, 0x001A, 0, 'Environment', 2, 1000, ctypes.byref(result))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def decode_audio(result):
    status = result.get('base_resp') or {}
    if status.get('status_code') != 0:
        raise RuntimeError(f"MiniMax {status.get('status_code')}: {status.get('status_msg', 'missing status')}")
    data = result.get('data') or {}
    if data.get('status') != 2 or not data.get('audio'):
        raise RuntimeError('MiniMax response did not contain completed audio')
    audio = bytes.fromhex(data['audio'])
    if not audio:
        raise RuntimeError('MiniMax returned empty audio')
    return audio


def synthesize(key, body, output, site):
    endpoint = f'https://api.minimax.{site}/v1/t2a_v2'
    meta = output.with_suffix('.json')
    if meta.exists():
        old = json.loads(meta.read_text(encoding='utf-8'))
        if (old.get('state') == 'succeeded' and old.get('request') == body
                and old.get('endpoint') == endpoint and output.exists()):
            print('REUSE', output)
            return old
        raise RuntimeError(f'Existing take is different or incomplete; inspect {meta} before another paid request')
    if output.exists():
        raise RuntimeError('Output exists without its receipt; refusing to overwrite')
    output.parent.mkdir(parents=True, exist_ok=True)
    record = {'state': 'outcome_unknown', 'endpoint': endpoint, 'request': body,
              'started_utc': datetime.now(timezone.utc).isoformat()}
    write_json(meta, record)
    try:
        response = requests.post(endpoint, json=body,
                                 headers={'Authorization': 'Bearer ' + key},
                                 timeout=(15, 180), allow_redirects=False)
    except requests.RequestException:
        raise RuntimeError('Request interrupted; outcome unknown. No automatic paid retry.') from None
    record['http_status'] = response.status_code
    try:
        result = response.json()
    except ValueError:
        write_json(meta, record)
        raise RuntimeError(f'HTTP {response.status_code}: non-JSON response; outcome unknown') from None
    record['response'] = {k: result[k] for k in ('base_resp', 'trace_id', 'extra_info') if k in result}
    subtitle_file = (result.get('data') or {}).get('subtitle_file')
    if subtitle_file:
        record['response']['data'] = {'subtitle_file': subtitle_file}
    if isinstance(record['response'].get('base_resp'), dict):
        status = record['response']['base_resp']
        status['status_msg'] = str(status.get('status_msg', '')).replace(key, '[redacted]')
    try:
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}: {record['response'].get('base_resp', {})}")
        audio = decode_audio(result)
    except (RuntimeError, ValueError) as exc:
        # A declared provider failure is distinct from a missing/invalid audio result.
        if (result.get('base_resp') or {}).get('status_code') not in (None, 0):
            record['state'] = 'failed'
        write_json(meta, record)
        raise RuntimeError(str(exc).replace(key, '[redacted]')) from None
    output.write_bytes(audio)
    record.update(state='succeeded', audio_bytes=len(audio),
                  finished_utc=datetime.now(timezone.utc).isoformat())
    write_json(meta, record)
    print(json.dumps({'output': str(output), 'extra_info': result.get('extra_info'),
                      'trace_id': result.get('trace_id')}, ensure_ascii=False))
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--text-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model', default='speech-2.6-turbo')
    parser.add_argument('--voice', default='Chinese (Mandarin)_Wise_Women')
    parser.add_argument('--speed', type=float, default=1.0)
    parser.add_argument('--pitch', type=int, default=0)
    parser.add_argument('--emotion', choices=['happy', 'sad', 'angry', 'fearful', 'disgusted',
                                            'surprised', 'calm', 'fluent', 'whisper'])
    parser.add_argument('--site', choices=['io', 'cn'], default='io')
    parser.add_argument('--prompt-key', action='store_true')
    parser.add_argument('--save-key', action='store_true')
    args = parser.parse_args()
    text = args.text_file.read_text(encoding='utf-8-sig').strip()
    if not text or len(text) >= 10000:
        parser.error('Text must contain 1–9999 characters')
    if args.output.suffix.lower() != '.mp3':
        parser.error('Output must be .mp3')
    if not .5 <= args.speed <= 2 or not -12 <= args.pitch <= 12:
        parser.error('speed must be 0.5–2 and pitch must be -12–12')
    if args.save_key and not args.prompt_key:
        parser.error('--save-key requires --prompt-key')
    key = getpass('MiniMax API key (hidden): ').strip() if args.prompt_key else load_key()
    if not key:
        parser.error('Set MINIMAX_API_KEY or use --prompt-key')
    if args.save_key:
        save_key(key)
        print('Saved MINIMAX_API_KEY in Windows user environment (value hidden).', flush=True)
    voice = {'voice_id': args.voice, 'speed': args.speed, 'vol': 1, 'pitch': args.pitch}
    if args.emotion:
        voice['emotion'] = args.emotion
    body = {'model': args.model, 'text': text, 'stream': False, 'language_boost': 'Chinese',
            'output_format': 'hex', 'voice_setting': voice,
            'audio_setting': {'sample_rate': 32000, 'bitrate': 128000, 'format': 'mp3', 'channel': 1}}
    synthesize(key, body, args.output.resolve(), args.site)


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as exc:
        raise SystemExit(str(exc))
