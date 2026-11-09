"""Dub and conform the Tyrol episode. Only the api_v2 process needs the TTS dependencies."""
import argparse
from array import array
from contextlib import contextmanager
import csv
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import wave

from gpt_sovits import backend_status

ROOT = Path(__file__).resolve().parent
RATE = 48000


def read_json(name):
    return json.loads((ROOT / name).read_text('utf-8-sig'))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def media_tool(key):
    exe = shutil.which(os.environ.get(key.upper(), key))
    require(exe, f'{key} is required. Put it on PATH or set {key.upper()}.')
    return exe


def audio_pcm(ffmpeg, source):
    return subprocess.check_output([ffmpeg, '-nostdin', '-v', 'error', '-i', str(source),
                                    '-map', '0:a:0', '-ac', '1', '-ar', str(RATE), '-f', 's16le', '-'])


def shorten_pauses(samples, pcm, start, end, excess):
    # Work only inside sustained quiet intervals. Keep both transitions and at least 180 ms per breath.
    spans = []
    quiet_start = None
    step = RATE // 100
    for pos in range(start, end, step):
        block = samples[pos:min(pos + step, end)]
        quiet = max(block) <= 165 and min(block) >= -165
        if quiet and quiet_start is None:
            quiet_start = pos
        if not quiet and quiet_start is not None:
            if quiet_start > start and pos - quiet_start >= RATE * 0.28:
                spans.append((quiet_start, pos))
            quiet_start = None
    spare = [b-a-round(RATE*0.18) for a,b in spans]
    budget = sum(spare)
    if not budget:
        return pcm[2*start:2*end], 0
    fraction = min(1.0, excess / budget)
    pieces = []
    previous = start
    removed = 0
    for (a,b), available in zip(spans, spare):
        cut = int(available*fraction)
        left = (a+b-cut)//2
        pieces.append(pcm[2*previous:2*left])
        previous = left+cut
        removed += cut
    pieces.append(pcm[2*previous:2*end])
    return b''.join(pieces), removed


def fit_voice(ffmpeg, pcm, capacity):
    samples = array('h')
    samples.frombytes(pcm)
    if sys.byteorder == 'big':
        samples.byteswap()
    require(any(samples), 'Silent or empty source WAV')
    if len(samples) <= capacity:
        return pcm, 1.0, 0, 0
    # On overruns only, remove digitally quiet edges while leaving a 60 ms breath margin.
    threshold = 80
    first = next((i for i, v in enumerate(samples) if abs(v) > threshold), None)
    require(first is not None, 'Source level too low to fit automatically')
    last = len(samples) - next(i for i, v in enumerate(reversed(samples)) if abs(v) > threshold)
    start, end = max(0, first - 2880), min(len(samples), last + 2880)
    removed = len(samples) - (end - start)
    trimmed = pcm[start*2:end*2]
    pauses = 0
    if end-start > capacity*1.08:
        trimmed, pauses = shorten_pauses(samples, pcm, start, end, end-start-capacity)
    if len(trimmed) <= capacity * 2:
        return trimmed, 1.0, removed, pauses
    # atempo uses analysis windows. Preserve its output including the end of the sentence.
    ratio = len(trimmed) / (2 * capacity)
    for _ in range(4):
        steps = max(1, math.ceil(math.log2(ratio)))
        filters = ','.join([f'atempo={ratio ** (1 / steps):.9f}'] * steps)
        fitted = subprocess.check_output([ffmpeg, '-v', 'error', '-f', 's16le', '-ar', str(RATE),
                                         '-ac', '1', '-i', '-', '-af', filters, '-f', 's16le', '-'], input=trimmed)
        require(fitted, 'Tempo filter returned an empty WAV')
        if len(fitted) <= 2 * capacity:
            return fitted, ratio, removed, pauses
        ratio *= len(fitted) / (capacity * 2) * 1.012
    raise ValueError('Could not fit full sentence in its slot. Shorten pauses in the original WAV and rerun mix.')


def probe(ffprobe, file):
    return json.loads(subprocess.check_output([ffprobe, '-v', 'error', '-show_streams', '-show_chapters',
                                               '-of', 'json', str(file)]))


def verify_movie(info, meta, dubbed):
    video, = [s for s in info['streams'] if s['codec_type'] == 'video']
    require(int(video['nb_frames']) == meta['frames'] and Fraction(video['avg_frame_rate']) == meta['fps'],
            'Unexpected frame count or frame rate')
    require(Fraction(video['time_base']) * int(video['duration_ts']) == Fraction(meta['frames'], meta['fps']),
            'Unexpected picture duration')
    require(len(info['chapters']) == len(meta['chapters']), 'Chapter count changed')
    for chapter, expected in zip(info['chapters'], meta['chapters']):
        require(chapter['tags']['title'] == expected['name'] and all(
            Fraction(chapter['time_base']) * int(chapter[key]) == Fraction(expected[key+'_frame'], meta['fps'])
            for key in ('start','end')), 'Chapter title or time changed')
    audio = [s for s in info['streams'] if s['codec_type'] == 'audio']
    if dubbed:
        require(len(audio) == 1 and int(audio[0]['sample_rate']) == RATE, 'Missing dialogue track')
        s = audio[0]
        require(abs(Fraction(s['time_base']) * int(s['duration_ts']) - Fraction(meta['frames'], meta['fps'])) <= Fraction(1,1000),
                'Dialogue duration does not match picture')
    else:
        require(not audio, 'Expected a silent picture master')


def timeline(meta):
    rows = [json.loads(s) for s in (ROOT / 'voice_lines.jsonl').read_text('utf-8-sig').splitlines() if s.strip()]
    require(len(rows) == meta['line_count'] and len({r['wav_name'] for r in rows}) == len(rows), 'Invalid line list')
    end = 0
    for i, row in enumerate(rows, 1):
        require(row['sequence'] == i and end <= row['start_frame'] < row['end_frame'] <= meta['frames']
                and Path(row['wav_name']).name == row['wav_name'], f'Invalid cue {i}')
        end = row['end_frame']
    return rows


def mix(meta, rows, wav_dir, out, ffmpeg, ffprobe):
    names = [r['wav_name'] for r in rows if not (wav_dir / r['wav_name']).is_file()]
    require(not names, 'Missing WAVs: ' + ', '.join(names))
    per_frame, remainder = divmod(RATE, meta['fps'])
    require(not remainder, 'Frame rate is incompatible with the PCM clock')
    with tempfile.TemporaryDirectory(prefix='mix-',dir=out) as stage:
        stage = Path(stage)
        dialogue, report, movie = stage/'dialogue.wav', stage/'timing.csv', stage/'tyrol-voiced.mp4'
        with wave.open(str(dialogue),'wb') as track, report.open('w',newline='',encoding='utf-8-sig') as fp:
            track.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
            writer = csv.writer(fp)
            writer.writerow(['sequence','role','wav','start_tc','end_tc','slot_ms','source_ms','edge_removed_ms',
                             'pause_removed_ms','atempo','fitted_ms','start_sample','end_sample_exclusive','review'])
            cursor = 0
            for row in rows:
                start, stop = row['start_frame'] * per_frame, row['end_frame'] * per_frame
                pcm = audio_pcm(ffmpeg, wav_dir / row['wav_name'])
                try:
                    fitted, speed, edges, pauses = fit_voice(ffmpeg, pcm, stop-start)
                except (ValueError, subprocess.CalledProcessError) as error:
                    raise ValueError(f"{row['wav_name']}: {error}") from error
                track.writeframesraw(bytes((start-cursor)*2))
                track.writeframesraw(fitted)
                cursor = start + len(fitted)//2
                writer.writerow([row['sequence'],row['character_id'],row['wav_name'],row['start_timecode'],row['end_timecode'],
                                 row['duration_ms'],round(len(pcm)*500/RATE),round(edges*1000/RATE),round(pauses*1000/RATE),round(speed,4),
                                 round(len(fitted)*500/RATE),start,cursor,'复听语速' if speed > 1.25 else ''])
            track.writeframesraw(bytes((meta['frames'] * per_frame - cursor)*2))
        subprocess.run([ffmpeg,'-nostdin','-v','error','-i',str(ROOT/meta['master']),'-i',str(dialogue),
                        '-map','0:v:0','-map','1:a:0','-map_metadata','0','-map_chapters','0','-c:v','copy',
                        '-c:a','aac','-b:a','192k','-metadata:s:a:0','language=zho','-movflags','+faststart',
                        str(movie)],check=True)
        verify_movie(probe(ffprobe, movie), meta, True)
        for file in [dialogue,report,movie]:
            file.replace(out/file.name)
    print(f"Done: {out/'tyrol-voiced.mp4'}",flush=True)


@contextmanager
def api(cfg,out):
    home = os.environ.get('GPT_SOVITS_HOME')
    if not home:
        status = backend_status(cfg)
        require(status['status'] == 'available',
                'Start GPT-SoVITS api_v2 with access to this directory, or set GPT_SOVITS_HOME to its installation. ' + str(status))
        yield
        return
    # Start a private API with a job-local config. Never change a running service on its default port.
    home = Path(home).expanduser().resolve()
    require((home/'api_v2.py').is_file(), 'GPT_SOVITS_HOME/api_v2.py is missing')
    import socket
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0))
        port = listener.getsockname()[1]
    cfg['base_url'] = f'http://127.0.0.1:{port}'
    voice = cfg['voices']['glm']
    device = os.environ.get('TTS_DEVICE','cuda')
    custom = {'device':device,'is_half':device != 'cpu','version':'v2',
              'bert_base_path':'GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large',
              'cnhuhbert_base_path':'GPT_SoVITS/pretrained_models/chinese-hubert-base',
              't2s_weights_path':str(ROOT/voice['gpt_weights']),'vits_weights_path':str(ROOT/voice['sovits_weights'])}
    with tempfile.TemporaryDirectory(prefix='api-',dir=out) as temp, (out/'api.log').open('w',encoding='utf-8') as log:
        path = Path(temp)/'inference.yaml'
        path.write_text(json.dumps({'custom':custom}), 'utf-8')
        env_python = home/('.venv/Scripts/python.exe' if os.name=='nt' else '.venv/bin/python')
        python = os.environ.get('GPT_SOVITS_PYTHON') or (str(env_python) if env_python.is_file() else sys.executable)
        process = subprocess.Popen([python,'api_v2.py','-a','127.0.0.1','-p',str(port),'-c',str(path)],
                                   cwd=home,stdout=log,stderr=subprocess.STDOUT)
        try:
            for _ in range(180):
                require(process.poll() is None, f'api_v2 exited. See {out/"api.log"}')
                if backend_status(cfg)['status'] == 'available':
                    break
                time.sleep(2)
            else:
                raise RuntimeError(f'api_v2 startup timed out. See {out/"api.log"}')
            yield
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['all','mix','check'],default='all',nargs='?')
    parser.add_argument('--out',type=Path,default=ROOT/'output')
    parser.add_argument('--wav-dir',type=Path,help='Directory containing the named WAVs (default output/wav)')
    args = parser.parse_args()
    out = args.out.resolve()
    if args.mode == 'check':
        for line in (ROOT/'SHA256SUMS').read_text('utf-8').splitlines():
            expected, name = line.split('  ',1)
            sha = hashlib.sha256()
            with (ROOT/name).open('rb') as f:
                for block in iter(lambda:f.read(4*1024*1024),b''):
                    sha.update(block)
            require(sha.hexdigest()==expected, f'SHA-256 mismatch: {name}')
    ffmpeg, ffprobe = media_tool('ffmpeg'), media_tool('ffprobe')
    meta = read_json('manifest.json')
    rows = timeline(meta)
    verify_movie(probe(ffprobe,ROOT/meta['master']),meta,False)
    if args.mode == 'check':
        print(f"OK: checksums, {len(rows)} lines, {meta['frames']} frames, {len(meta['chapters'])} chapters")
        return
    out.mkdir(exist_ok=True,parents=True)
    wav_dir = (args.wav_dir or out/'wav').resolve()
    if args.mode == 'all':
        cfg = read_json('tts.cast.json')
        cfg['base_url'] = os.environ.get('TTS_URL',cfg['base_url'])
        for voice in cfg['voices'].values():
            for field in ('gpt_weights','sovits_weights','ref_audio_path'):
                voice[field] = str(ROOT/voice[field])
            voice['aux_ref_audio_paths'] = [str(ROOT/p) for p in voice['aux_ref_audio_paths']]
        with api(cfg,out), tempfile.TemporaryDirectory(dir=out) as temp:
            config = Path(temp)/'cast.json'
            config.write_text(json.dumps(cfg,ensure_ascii=False),'utf-8')
            subprocess.run([sys.executable,str(ROOT/'batch_dub.py'),'--config',str(config),'--out',str(wav_dir)],check=True)
    mix(meta,rows,wav_dir,out,ffmpeg,ffprobe)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
