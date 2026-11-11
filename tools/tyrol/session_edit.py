"""Edit a recorded table with existing sprites, reference voices and FFmpeg.

The selection JSON owns editorial choices; every quotation is checked against
this table's public files. No game or campaign-model calls are made here.
"""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import csv
from datetime import datetime
from functools import lru_cache
from getpass import getpass
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageEnhance
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'tools'), str(Path(__file__).parent / 'qwen_day12'),
                str(Path(__file__).parent / 'opening'), str(Path(__file__).parent)]
from edit import align, caption_parts, srt_time
from cast_scene import portrait
from reference import reference_assignments, load_voice, CAST_IDS
from voices import API, MODEL, finite_wav
from script import spoken
from cut import province_panel

FPS, SR, SPF = 25, 24000, 960
FF = os.environ.get('FFMPEG', 'D:/Shotcut/ffmpeg.exe')
FP = os.environ.get('FFPROBE', 'D:/Shotcut/ffprobe.exe')
NAMES = {'glm': '智谱娘', 'gemini': '基米', 'qwen': '千问', 'deepseek': '大肥鱼', 'gpt': '白龙酱', 'claude': '克莱德'}
COLORS = {'glm': '#edcf8c', 'gemini': '#d6bfff', 'qwen': '#bfe6d5', 'deepseek': '#accfff', 'gpt': '#d5e5ff', 'claude': '#ffd5ad'}
COUNTRIES = {'AUS': '奥地利', 'ITA': '意大利', 'GER': '德国', 'FRA': '法国', 'SWI': '瑞士'}
LEGACY_TAG = {role: tag for tag, role in CAST_IDS.items()}


def read(p):
    return json.loads(Path(p).read_text('utf-8-sig'))


def write(p, value):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', 'utf8')


def clean(text):
    return text.replace('\\n', '').replace('\r', '').replace('\n', '').strip()


def ff(args):
    subprocess.run([FF, '-hide_banner', '-nostdin', '-v', 'error', '-y', *map(str, args)], check=True)


def probe(p):
    return json.loads(subprocess.check_output([FP, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(p)]))


def public_sources(name):
    run = ROOT / 'runs/campaigns' / name
    table = ROOT / 'runs' / name
    manifest = read(run / 'manifest.json')
    assignments = manifest['config']['production']['assignments']
    sources = {}
    for r in read(table / '开桌公开原话.json'):
        sources[r['id']] = {**r, 'source_file': str((table / '开桌公开原话.json').relative_to(ROOT)), 'turn': 0}
    for entry in manifest['turns']:
        n = entry['index']
        assert entry['status'] == 'completed'
        trial = run / 'trials' / f'turn_{n:03d}'
        for i, r in enumerate(read(trial / 'public_diplomacy.json')):
            assert r['visibility'] == 'public'
            key = f'T{n}-{r["country"]}-{r["round"]}'
            sources[key] = {'id': key, 'role': assignments[r['country']], 'country': r['country'],
                            'text': r['message'], 'turn': n, 'round': r['round'], 'kind': 'public',
                            'source_file': str((trial / 'public_diplomacy.json').relative_to(ROOT)), 'pointer': f'/{i}/message'}
        for i, r in enumerate(read(trial / 'table_host.json')):
            assert r['visibility'] == 'public'
            key = f'T{n}-H{i+1}'
            sources[key] = {'id': key, 'role': 'glm', 'text': r['message'], 'turn': n, 'kind': 'host',
                            'source_file': str((trial / 'table_host.json').relative_to(ROOT)), 'pointer': f'/{i}/message'}
    for i, r in enumerate(read(table / 'closing/approved-for-review.json')):
        sources[r['id']] = {**r, 'role': r['character_id'], 'kind': 'closing', 'turn': 8,
                            'country': next((tag for tag, role in assignments.items() if tag!='host' and role==r['character_id']), None),
                            'source_file': str((table / 'closing/approved-for-review.json').relative_to(ROOT)), 'pointer': f'/{i}/text'}
    return sources, manifest


def prepare(name, selection):
    work = ROOT / 'runs' / name / 'edit'
    work.mkdir(parents=True, exist_ok=True)
    source, manifest = public_sources(name)
    spec = read(selection)
    assert spec['campaign'] == name and manifest['status'] == 'completed'
    rows, positions = [], {}
    for i, item in enumerate(spec['rows'], 1):
        r = dict(item)
        r['line_id'] = item.get('line_id', f'L{i:03d}')
        assert re.fullmatch(r'[A-Za-z0-9_-]+', r['line_id'])
        assert r['line_id'] not in {x['line_id'] for x in rows}
        if r.get('source'):
            s = source[r['source']]
            text = clean(r.get('text', s['text']))
            assert text and text in clean(s['text']), (r['source'], text)
            r = {**s, **r, 'text': text, 'quote_start': clean(s['text']).index(text), 'source_text': s['text']}
        elif r['kind'] == 'narration':
            assert r.get('evidence') and r['role'] == 'glm'
            r['text'] = clean(r['text'])
            for evidence in r['evidence']:
                assert (ROOT / evidence.split('#')[0]).is_file(), evidence
        if r.get('role'):
            role = r['role']
            other = r.get('listener') or ('glm' if role != 'glm' else 'qwen')
            if other == role:
                other = 'gemini' if role != 'gemini' else 'qwen'
            left = r.get('speaker_left', positions.get(role, not positions.get(other, False)))
            positions = {role: left, other: not left}
            r.update(listener=other, speaker_left=left, speaker=NAMES[role])
            r['edit_tempo'] = 1.055 if role=='glm' else 1.035
        r.setdefault('gap', 0.8)
        r.setdefault('style', 'table')
        rows.append(r)
    write(work / 'prepared.json', {**spec, 'rows': rows, 'assignments': manifest['config']['production']['assignments']})
    write(work / 'public_source_index.json', {k: source[k] for k in dict.fromkeys(r['source'] for r in rows if r.get('source'))})
    # The save receipt is the timestamped anchor, not a guessed OBS edit time.
    records = read(ROOT / 'runs' / name / 'recordings_inventory.json')
    plate_dir = work / 'plates'
    plate_dir.mkdir(exist_ok=True)
    plates = {}
    for day in range(1, 8):
        facts = [json.loads(l) for l in (ROOT / 'runs/campaigns' / name / f'native_turns/turn_{day:03d}/audience_facts.jsonl').read_text('utf8').splitlines() if l]
        anchor = next(r for r in reversed(facts) if r['event'] == 'save_observation')
        when = datetime.fromisoformat(anchor['utc']).timestamp()
        rec = next(r for r in records if datetime.fromisoformat(r['start']).timestamp() <= when < datetime.fromisoformat(r['end']).timestamp())
        offset = when - datetime.fromisoformat(rec['start']).timestamp() + 0.1
        target = plate_dir / f'after_{day:03d}.png'
        if not target.exists():
            ff(['-ss', f'{offset:.6f}', '-i', rec['file'], '-frames:v', 1, target])
        plates[str(day)] = {'file': str(target), 'recording': rec['file'], 'offset_seconds': offset,
                            'utc': anchor['utc'], 'date': manifest['turns'][day-1]['final_date']}
        if day==1:
            initial=next(r for r in facts if r['event']=='frozen_initial')
            stamp=datetime.fromisoformat(initial['utc']).timestamp()
            offset=stamp-datetime.fromisoformat(rec['start']).timestamp()+0.1
            target=plate_dir/'initial.png'
            if not target.exists():ff(['-ss',f'{offset:.6f}','-i',rec['file'],'-frames:v',1,target])
            plates['initial']={'file':str(target),'recording':rec['file'],'offset_seconds':offset,'utc':initial['utc'],'date':manifest['initial_date']}
    close = spec['opening_close']
    assert (ROOT/close['file']).is_file() and (ROOT/close['evidence']).is_file()
    assert read(ROOT/close['evidence'])['receipt']['result']['state']['date_raw']==manifest['turns'][0]['final_date_raw']
    plates['opening_close'] = {**close,'file':str(ROOT/close['file'])}
    write(work / 'map_plates.json', plates)
    for row in rows:
        if row['kind']!='game':continue
        day=row['turn']
        facts=[json.loads(l) for l in (ROOT/'runs/campaigns'/name/f'native_turns/turn_{day:03d}/audience_facts.jsonl').read_text('utf8').splitlines() if l]
        start=next(r['utc'] for r in facts if r['event']=='intent' and r.get('operation')=='native_clock_v1' and r.get('payload','').startswith('step '))
        end=next(r['utc'] for r in reversed(facts) if r['event']=='clock_effect')
        a,b=datetime.fromisoformat(start).timestamp(),datetime.fromisoformat(end).timestamp()
        rec=next(r for r in records if datetime.fromisoformat(r['start']).timestamp()<=a<b<datetime.fromisoformat(r['end']).timestamp())
        row.update(recording=rec['file'],source_in=a-datetime.fromisoformat(rec['start']).timestamp(),source_seconds=b-a,source_utc=[start,end])
        row['game_frame_grid']='25fps_after_time_stretch'
    write(work / 'prepared.json', {**spec, 'rows': rows, 'assignments': manifest['config']['production']['assignments']})
    print(json.dumps({'lines': len(rows), 'spoken_lines': sum(bool(r.get('text')) for r in rows),
                      'characters': sum(len(r.get('text', '')) for r in rows)}, ensure_ascii=False))


@lru_cache(None)
def font(size, bold=False):
    return ImageFont.truetype('C:/Windows/Fonts/msyh' + ('bd' if bold else '') + '.ttc', size)


def wrap(text, size, width):
    lines, current = [], ''
    for ch in text:
        if current and font(size, True).getlength(current + ch) > width:
            lines.append(current)
            current = ''
        current += ch
    if current:
        lines.append(current)
    return lines


@lru_cache(64)
def background(kind):
    p = ROOT / ('assets/stage/backdrops/council.png' if kind == 'closing' else 'assets/stage/academy/backdrops/map_focus.png')
    im = Image.open(p).convert('RGBA').resize((1280, 720), Image.Resampling.LANCZOS)
    return ImageEnhance.Brightness(im).enhance(.78 if kind == 'closing' else .87)


def frame_image(row, caption, talking, work):
    style = row['style']
    if style == 'situation':
        return situation_image(row, work)
    if style in ('map', 'result'):
        plates = read(work / 'map_plates.json')
        key = str(row.get('plate', 7))
        im = Image.open(plates[key]['file']).convert('RGBA').resize((1280, 720))
        # Presenter stays outside the military focus in the middle of the map.
        if row.get('text'):
            art = portrait('glm', talking, 315)
            im.alpha_composite(art, (4, 546-art.height))
        if row.get('diagram'):
            if not os.environ.get('HOI4_DIR'):os.environ['HOI4_DIR']=read(ROOT/'configs/live.local.json')['game_dir']
            panel=province_panel().resize((657,344),Image.Resampling.LANCZOS)
            im.alpha_composite(panel,(538,150))
    elif style == 'prop':
        im = ImageOps.fit(Image.open(ROOT/'assets/stage/props/empty_stove_draft.png').convert('RGBA'), (1280,720), method=Image.Resampling.LANCZOS)
    else:
        im = background('closing' if row.get('kind') == 'closing' else 'table').copy()
        d = ImageDraw.Draw(im)
        # The board carries the changing issue, rather than a redundant wall of exposition.
        issue = row.get('board', row['chapter'])
        if style=='title':
            for j,text in enumerate(issue.splitlines()):
                size=66 if len(text)<=12 else 34
                d.text((640,225+j*92),text,font=font(size,True),fill='#f4dfad',anchor='mt')
        else:
            for j, text in enumerate(wrap(issue, 37, 550)):
                d.text((640, 155+j*51), text, font=font(37, True), fill='#f4dfad', anchor='mt')
        if style == 'roster':
            roles = ['glm', 'deepseek', 'gpt', 'gemini', 'claude', 'qwen']
            for i, role in enumerate(roles):
                art = portrait(role, talking and row.get('role') == role, 320)
                if row.get('revealed_roles') is not None and role not in row['revealed_roles']:
                    silhouette = Image.new('RGBA', art.size, '#152732')
                    silhouette.putalpha(art.getchannel('A'))
                    art = silhouette
                im.alpha_composite(art, (round(104+i*214-art.width/2), 540-art.height))
        elif row.get('role'):
            role = row['role']; other = row['listener']
            active_x = 300 if row['speaker_left'] else 980
            other_x = 1280-active_x
            art = portrait(other, False, 322)
            im.alpha_composite(art, (round(other_x-art.width/2), 540-art.height))
            art = portrait(role, talking, 410)
            im.alpha_composite(art, (round(active_x-art.width/2), 540-art.height))
    d = ImageDraw.Draw(im)
    # Full-width opaque rail hides every cut edge, including during empty subtitle beats.
    if row.get('text') or style in ('table','roster'):
        d.rectangle((0, 520, 1279, 719), fill='#41352f')
        d.rectangle((0, 520, 1279, 523), fill='#bfa374')
        d.rounded_rectangle((36, 533, 1244, 700), 13, fill='#132b32', outline='#839c92', width=2)
    top = row['chapter']
    width = font(24, True).getlength(top)+36
    d.rounded_rectangle((27, 24, 27+width, 68), 9, fill='#132b32')
    d.text((45, 30), top, font=font(24, True), fill='#f0d899')
    note = '节目比喻插画' if style=='prop' else row.get('stamp', '')
    if not note:
        note = '节目比喻插画' if style == 'prop' else '剪辑旁白' if row.get('kind') == 'narration' else '公开原话 · 立绘演绎'
    right_width = font(18).getlength(note)+28
    d.rounded_rectangle((1253-right_width, 25, 1253, 60), 7, fill='#132b32')
    d.text((1239, 31), note, font=font(18), fill='#d3dfda', anchor='rt')
    if row.get('text'):
        role = row['role']; color = COLORS[role]
        label = row.get('speaker', NAMES[role])
        country = row.get('country')
        label += (' / '+COUNTRIES[country]) if country else ' / 剪辑旁白' if row.get('kind') == 'narration' else ' / 桌边主持' if role == 'glm' else ''
        d.text((67, 545), label, font=font(23, True), fill=color)
        if caption:
            lines = wrap(caption, 36, 1120)
            assert len(lines) <= 2, caption
            y = 595 if len(lines) == 1 else 580
            for j, text in enumerate(lines):
                d.text((67, y+j*45), text, font=font(36, True), fill='#fff7e5')
    if row.get('date_label'):
        date = row['date_label']
        d.rounded_rectangle((28, 82, 54+font(20).getlength(date), 117), 6, fill='#132b32')
        d.text((41, 87), date, font=font(20), fill='#e8e5d5')
    return im.convert('RGB')


def situation_image(row, work):
    """Keep the dated game image and the province-key diagram visibly distinct."""
    plate = read(work/'map_plates.json')[str(row['plate'])]
    day = row['state_day']
    state_file = ROOT/'runs/campaigns'/read(work/'prepared.json')['campaign']/(
        'initial_capture' if day == 0 else f'captures/after_{day:03d}')/'world.json'
    state = read(state_file)
    assert state['date'] == plate['date']
    assert state['provinces']['671']['controller'] == 'AUS'
    assert state['provinces']['6673']['controller'] == 'ITA'
    assert {'first':'AUS','second':'ITA'} in state['public_wars']
    im = Image.new('RGBA', (1280,720), '#10232d')
    d = ImageDraw.Draw(im)
    d.text((30,25), row['headline'], font=font(39,True), fill='#f4dfad')
    d.text((31,86), row['date_label'], font=font(24), fill='#bfcfcf')
    actual = Image.open(plate['file']).convert('RGBA')
    # Enlarge the native map's actual front-line focus, not the whole continent.
    actual = actual.crop((150,65,1100,600)).resize((790,445),Image.Resampling.LANCZOS)
    im.alpha_composite(actual,(30,143))
    d = ImageDraw.Draw(im)
    d.rectangle((30,143,820,183), fill='#193541')
    d.text((44,150),'山口近景 / 对应日期游戏实录',font=font(20,True),fill='#eff2e6')
    for i,(label,value) in enumerate(row['status']):
        y=150+i*99
        d.text((852,y),label,font=font(21,True),fill='#8dbfb9')
        for j,line in enumerate(wrap(value,27,367)):
            d.text((852,y+33+j*34),line,font=font(27,True),fill='#fff4da')
    if not os.environ.get('HOI4_DIR'):
        os.environ['HOI4_DIR']=read(ROOT/'configs/live.local.json')['game_dir']
    im.alpha_composite(province_panel().resize((383,200),Image.Resampling.LANCZOS),(848,441))
    d=ImageDraw.Draw(im)
    d.text((853,413),'焦点省份 / 控制权示意',font=font(19),fill='#bfcfcf')
    for x,label,color in [(30,'671  奥地利控制','#efc66d'),(430,'6673  意大利控制','#8bddbf')]:
        d.rounded_rectangle((x,605,x+383,654),8,fill='#203c46')
        d.text((x+18,614),label,font=font(25,True),fill=color)
    d.text((30,682),row['footer'],font=font(20),fill='#c6d5d5')
    return im.convert('RGB')


def boards(name):
    work = ROOT/'runs'/name/'edit'
    rows = read(work/'prepared.json')['rows']
    chosen = []
    for style in ('roster', 'table', 'map', 'prop'):
        r = next(r for r in rows if r['style'] == style and r.get('text'))
        chosen.append(r)
    chosen += [next(r for r in rows if r.get('kind') == 'closing'), next(r for r in rows if r['style']=='result')]
    sheet = Image.new('RGB', (1920, 720), '#14252a')
    for i, r in enumerate(chosen):
        parts = caption_parts(r['text']) if r.get('text') else ['']
        im = frame_image(r, parts[0], False, work)
        im.save(work/(r['line_id']+'_board.png'))
        sheet.paste(im.resize((640,360)), ((i%3)*640,(i//3)*360))
    sheet.save(work/'contact_sheet.png')
    print(work/'contact_sheet.png')


def voices(name, prompt_key=False, key_file=None, first_only=False, line_id=None, workers=1):
    key = key_file.read_text('utf8').strip() if key_file else os.environ.get('DASHSCOPE_API_KEY')
    if not key and prompt_key:
        key = getpass('Qwen TTS API key (hidden): ')
    if not key:
        raise RuntimeError('Configure DASHSCOPE_API_KEY or use --prompt-key / --key-file')
    work = ROOT/'runs'/name/'edit'
    rows = [r for r in read(work/'prepared.json')['rows'] if r.get('text')]
    pairs = reference_assignments([LEGACY_TAG[r['role']] for r in rows], work)
    dest = work/'takes';dest.mkdir(exist_ok=True)
    # A deleted or inserted shot must not shift the reference draw of existing takes.
    for i,row in enumerate(rows):
        meta = dest/(row['line_id']+'.json')
        if meta.exists():pairs[i] = read(meta)['sources']
    enrolled = {}

    def take(item):
        index, row = item
        sources = pairs[index]
        pair = (row['role'], tuple(sources))
        voice = enrolled[pair]
        instruction = ('普通话，保留参考音色。直接从台词第一个字开口，标点自然换气，不添加哼笑或字外声音。'
                       + ('用旁白口吻讲清地图和变化。' if row.get('kind') == 'narration' else '和同桌朋友说话，反问留一点停顿，不念播报腔。'))
        body = {'model':MODEL,'input':{'text':spoken(row['text']),'voice':voice,'format':'wav','sample_rate':SR,
                                    'instruction':instruction,'rate':1.04,'pitch':1.045 if row['role']=='glm' else 1.0,'language_hints':['zh']}}
        audio = dest/(row['line_id']+'.wav');meta=audio.with_suffix('.json')
        if audio.exists() and meta.exists() and read(meta).get('request')==body:
            return
        response = requests.post(API,json=body,headers={'Authorization':'Bearer '+key},timeout=(15,120),allow_redirects=False)
        result = response.json()
        if response.status_code!=200 or 'output' not in result:
            write(dest/(row['line_id']+'.failed.json'),{'request':body,'http_status':response.status_code,'response':result})
            raise RuntimeError(f'{row["line_id"]}: HTTP {response.status_code}: {result.get("code")}: {str(result.get("message",""))[:180]}')
        write(dest/(row['line_id']+'.response.json'),{'request':body,'response':result})
        source = result['output']['audio'];url=source.get('url','')
        if source.get('data'):
            data=base64.b64decode(source['data'],validate=True)
        elif url.startswith('data:'):
            data=base64.b64decode(url.split(',',1)[1],validate=True)
        else:
            if url.startswith('http://'):url='https://'+url[7:]
            if not url.startswith('https://'):raise ValueError('Unexpected audio URL scheme')
            downloaded=requests.get(url,timeout=(15,90));downloaded.raise_for_status();data=downloaded.content
        data,seconds=finite_wav(data);audio.write_bytes(data)
        write(meta,{'request':body,'seconds':seconds,'sources':sources,'request_id':result.get('request_id'),'usage':result.get('usage',{})})
        print(row['line_id'],row['role'],round(seconds,2),flush=True)

    # Establish one real reference-voice request before enrolling the rest of the cast.
    first_index=next((i for i,r in enumerate(rows) if r['line_id']==line_id),None) if line_id else 0
    if first_index is None:raise ValueError('Unknown spoken line ID')
    first=rows[first_index];pair=(first['role'],tuple(pairs[first_index]))
    enrolled[pair]=load_voice(key,MODEL,LEGACY_TAG[first['role']],work,pairs[first_index]);take((first_index,first))
    if first_only or line_id:return
    for i,row in enumerate(rows):
        pair=(row['role'],tuple(pairs[i]))
        if pair not in enrolled:
            enrolled[pair]=load_voice(key,MODEL,LEGACY_TAG[row['role']],work,pairs[i])
    # Keep only the requested number in flight; an error must not launch the rest of the paid queue.
    items=iter(enumerate(rows[1:],1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending={pool.submit(take,item) for item in list(__import__('itertools').islice(items,workers))}
        while pending:
            done,pending=wait(pending,return_when=FIRST_COMPLETED)
            for future in done:future.result()
            for _ in done:
                item=next(items,None)
                if item is not None:pending.add(pool.submit(take,item))


def read_pcm(path, tempo=1.0):
    with wave.open(str(path)) as audio:
        assert (audio.getframerate(),audio.getnchannels(),audio.getsampwidth())==(SR,1,2)
        pcm=np.frombuffer(audio.readframes(audio.getnframes()),'<i2')
    nonzero=np.flatnonzero(abs(pcm.astype('i4'))>48)
    assert len(nonzero)>100
    pcm=pcm[max(0,nonzero[0]-1920):min(len(pcm),nonzero[-1]+2880)].copy()
    if tempo!=1.0:
        data=subprocess.check_output([FF,'-v','error','-f','s16le','-ar',str(SR),'-ac','1','-i','pipe:0',
                                      '-af',f'atempo={tempo:.6f}','-f','s16le','pipe:1'],input=pcm.tobytes())
        pcm=np.frombuffer(data,'<i2')
    return pcm


def complete_audio(source, target, frames):
    """Flush audio filters independently of the video frame limit."""
    inputs = ['-i',source] if source else ['-f','lavfi','-i','anullsrc=r=48000:cl=stereo']
    filters = ('loudnorm=I=-18:TP=-2:LRA=10,' if source else '')
    filters += f'aresample=48000,aformat=channel_layouts=stereo,apad,atrim=end_sample={frames*1920},asetpts=N/SR/TB'
    ff([*inputs,'-vn','-af',filters,'-c:a','pcm_s16le',target])
    with wave.open(str(target)) as audio:
        assert (audio.getframerate(),audio.getnchannels(),audio.getsampwidth(),audio.getnframes()) == (48000,2,2,frames*1920)


def repair_clip_audio(row, info, target, work):
    audio = target.with_suffix('.audio.wav')
    source = (ROOT/row['file'] if row['kind']=='external' else
              work/'layers'/row['line_id']/'voice.wav' if row.get('text') else None)
    complete_audio(source,audio,info['frames'])
    temporary = target.with_name(target.stem+'.complete.mkv')
    ff(['-i',target,'-i',audio,'-map','0:v:0','-map','1:a:0','-c','copy',temporary])
    temporary.replace(target)
    info['audio_version'] = 2
    info['audio_samples'] = info['frames']*1920
    return info


def render(name, limit=None):
    work=ROOT/'runs'/name/'edit';spec=read(work/'prepared.json');rows=spec['rows'][:limit]
    clips=work/'clips';clips.mkdir(exist_ok=True)
    timeline=[];subtitles=[];position=0
    for row in rows:
        target=clips/(row['line_id']+'.mkv');receipt=target.with_suffix('.json')
        if target.exists() and receipt.exists() and read(receipt)['row']==row:
            info=read(receipt)
        elif row['kind']=='game':
            seconds=row['source_seconds']*2;frames=round(seconds*FPS)
            overlay=Image.new('RGBA',(1280,720));draw=ImageDraw.Draw(overlay)
            for y,text in [(24,row['chapter']),(78,row['date_label']),(123,'原片 0.5 倍速 · 游戏时间推进 24 小时')]:
                width=font(22,True).getlength(text)+32;draw.rounded_rectangle((26,y,26+width,y+40),7,fill='#132b32');draw.text((42,y+5),text,font=font(22,True),fill='#f1dfb5')
            layer=clips/(row['line_id']+'_overlay.png');overlay.save(layer)
            ff(['-ss',f'{row["source_in"]:.6f}','-t',f'{row["source_seconds"]:.6f}','-i',row['recording'],'-loop',1,'-i',layer,
                '-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-filter_complex','[0:v]setpts=2*(PTS-STARTPTS),fps=25,tpad=stop_mode=clone:stop_duration=0.12[game];[game][1:v]overlay=shortest=1,setsar=1,format=yuv420p[v]',
                '-map','[v]','-map','2:a:0','-frames:v',frames,'-t',f'{frames/FPS:.2f}','-c:v','libx264','-threads',4,'-preset','veryfast','-crf',19,'-c:a','pcm_s16le',target])
            info={'row':row,'frames':frames,'cues':[]}
        elif row['kind']=='external':
            source=ROOT/row['file'];data=probe(source);video=next(s for s in data['streams'] if s['codec_type']=='video')
            frames=round(row.get('seconds',float(data['format']['duration']))*FPS)
            ff(['-i',source,'-t',f'{frames/FPS:.2f}','-vf','fps=25,scale=1280:720,setsar=1',
                '-af','loudnorm=I=-18:TP=-2:LRA=10,aresample=48000,aformat=channel_layouts=stereo','-c:v','libx264','-threads',4,'-preset','veryfast','-crf',19,'-c:a','pcm_s16le',target])
            cues=[]
            text=(ROOT/row['srt']).read_text('utf-8-sig')
            for block in re.split(r'\r?\n\s*\r?\n',text.strip()):
                lines=block.splitlines();a,b=lines[1].split(' --> ')
                def number(s):
                    h,m,sec,ms=map(int,re.split('[:,]',s));return round((((h*60+m)*60+sec)+ms/1000)*FPS)
                if number(a)<frames:cues.append((number(a),min(frames,number(b)),'\n'.join(lines[2:])))
            info={'row':row,'frames':frames,'cues':cues}
        else:
            text=row.get('text','');is_spoken=bool(text)
            if is_spoken:
                pcm=read_pcm(work/'takes'/(row['line_id']+'.wav'),row['edit_tempo'])
                n=math.ceil(len(pcm)/SPF);parts=caption_parts(text);bounds=align(parts,pcm,n)
                gap=round(row['gap']*FPS)
            else:
                n=round(row.get('seconds',2.4)*FPS);gap=0;pcm=np.zeros(n*SPF,dtype='<i2');parts=[''];bounds=[0,n]
            art=work/'layers'/row['line_id'];art.mkdir(parents=True,exist_ok=True)
            listing=['ffconcat version 1.0'];cues=[]
            padded=np.pad(pcm,(0,n*SPF-len(pcm)))
            energy=np.sqrt((padded.reshape(-1,SPF).astype(float)**2).mean(axis=1))
            pieces=[]
            def add(file, count):
                if count<=0:return
                if pieces and pieces[-1][0]==file:pieces[-1]=(file,pieces[-1][1]+count)
                else:pieces.append((file,count))
            for j,part in enumerate(parts):
                a,b=bounds[j:j+2]
                for mouth in (False,True):frame_image(row,part,mouth,work).save(art/f'{j}-{int(mouth)}.png',compress_level=2)
                if part:cues.append((a,b,row.get('speaker','')+'　'+part))
                for k in range(a,b,4):
                    mouth=bool(is_spoken and energy[k]>250 and (k//4)%3!=2)
                    add(f'{j}-{int(mouth)}.png',min(4,b-k))
            frame_image(row,'',False,work).save(art/'idle.png',compress_level=2);add('idle.png',gap)
            for filename,count in pieces:listing.extend([f"file '{filename}'",f'duration {count/FPS:.2f}'])
            listing.append(f"file '{pieces[-1][0]}'")
            (art/'frames.ffconcat').write_text('\n'.join(listing)+'\n','ascii')
            sound=art/'voice.wav'
            with wave.open(str(sound),'wb') as out:
                out.setparams((1,2,SR,0,'NONE','not compressed'));out.writeframes(padded.tobytes()+bytes(gap*SPF*2))
            frames=n+gap
            ff(['-f','concat','-safe',0,'-i',art/'frames.ffconcat','-vf','fps=25,format=yuv420p,setsar=1',
                '-an','-frames:v',frames,'-c:v','libx264','-threads',4,
                '-preset','veryfast','-crf',19,target])
            info={'row':row,'frames':frames,'cues':cues}
        if info.get('audio_version') != 2 or not target.with_suffix('.audio.wav').exists():
            info = repair_clip_audio(row,info,target,work)
        write(receipt,info)
        for a,b,text in info['cues']:subtitles.append((position+a,position+b,text))
        timeline.append({**row,'start_frame':position,'end_frame':position+info['frames'],'clip':str(target)})
        position+=info['frames'];print('RENDER',row['line_id'],round(position/FPS,2),flush=True)
    write(work/('preview_timeline.json' if limit else 'timeline.json'),{'fps':FPS,'frames':position,'rows':timeline,'cues':subtitles})


def assemble(name):
    work=ROOT/'runs'/name/'edit';spec=read(work/'prepared.json');timeline=read(work/'timeline.json')
    out=ROOT/'exports'/name/'正片';out.mkdir(parents=True,exist_ok=True)
    concat=work/'assembly.ffconcat'
    concat.write_text('ffconcat version 1.0\n'+''.join("file '"+r['clip'].replace('\\','/')+"'\n"+
                      f"duration {(r['end_frame']-r['start_frame'])/FPS:.2f}\n" for r in timeline['rows']),'utf8')
    # Build the sound timeline in PCM samples, never from a muxed clip's endpoint PTS.
    master = work/'master_dialogue.wav'
    with wave.open(str(master),'wb') as output:
        output.setparams((2,2,48000,0,'NONE','not compressed'))
        for r in timeline['rows']:
            with wave.open(str(Path(r['clip']).with_suffix('.audio.wav'))) as audio:
                assert audio.getnframes() == (r['end_frame']-r['start_frame'])*1920
                while data := audio.readframes(48000):output.writeframesraw(data)
    normalized = work/'master_dialogue_normalized.wav'
    ff(['-i',master,'-af',f"loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,apad,atrim=end_sample={timeline['frames']*1920}",
        '-c:a','pcm_s16le',normalized])
    chapters=[]
    for r in timeline['rows']:
        if not chapters or chapters[-1][1]!=r['chapter']:chapters.append((r['start_frame'],r['chapter']))
    meta=[';FFMETADATA1']
    for (a,title),b in zip(chapters,[x[0] for x in chapters[1:]]+[timeline['frames']]):
        meta.extend(['[CHAPTER]','TIMEBASE=1/25',f'START={a}',f'END={b}','title='+title])
    (work/'chapters.txt').write_text('\n'.join(meta)+'\n','utf8')
    target=out/(spec['title']+spec.get('output_suffix','·无BGM配音版')+'.mp4')
    ff(['-f','concat','-safe',0,'-i',concat,'-i',normalized,'-f','ffmetadata','-i',work/'chapters.txt',
        '-map','0:v:0','-map','1:a:0','-map_metadata',2,'-map_chapters',2,'-c:v','copy',
        '-c:a','aac','-b:a','160k','-movflags','+faststart',target])
    ff(['-i',target,'-map','0:a:0','-c:a','copy',out/'无BGM对白轨.m4a'])
    (out/'中文字幕.srt').write_text('\n\n'.join(f'{i}\n{srt_time(a)} --> {srt_time(b)}\n{text}' for i,(a,b,text) in enumerate(timeline['cues'],1))+'\n','utf-8-sig')
    write(out/'剪辑时间轴.json',timeline)
    with (out/'台词与来源.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f);writer.writerow(['入点','出点','角色','原话编号','类别','台词','来源文件'])
        for r in timeline['rows']:writer.writerow([srt_time(r['start_frame']),srt_time(r['end_frame']),r.get('speaker',''),r.get('source',''),r['kind'],r.get('text',''),r.get('source_file',';'.join(r.get('evidence',[])))])
    write(out/'交付.json',{'video':str(target),'frames':timeline['frames'],'fps':FPS,'seconds':timeline['frames']/FPS,'bgm_included':False,'voice_included':True,'source_campaign':name})
    print(target, timeline['frames']/FPS,flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['prepare','boards','voices','render','assemble'])
    parser.add_argument('--name',required=True)
    parser.add_argument('--selection',type=Path)
    parser.add_argument('--prompt-key',action='store_true')
    parser.add_argument('--key-file',type=Path)
    parser.add_argument('--first-only',action='store_true')
    parser.add_argument('--line-id')
    parser.add_argument('--workers',type=int,choices=(1,2,3),default=1)
    parser.add_argument('--limit',type=int)
    args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+',args.name):parser.error('Invalid table name')
    if args.command=='prepare':
        if not args.selection:parser.error('--selection is required')
        prepare(args.name,args.selection)
    elif args.command=='boards':boards(args.name)
    elif args.command=='voices':voices(args.name,args.prompt_key,args.key_file,args.first_only,args.line_id,args.workers)
    elif args.command=='render':render(args.name,args.limit)
    else:assemble(args.name)


if __name__=='__main__':main()
