"""Compare introduction takes at a chosen Hanzi/s rate and voiced F0 targets.

Uses local Praat/Parselmouth PSOLA for pitch and FFmpeg atempo for duration.
No TTS requests, game actions, or changes to the full episode.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys
import wave

import numpy as np
from PIL import Image, ImageDraw

import session_edit as edit


def pcm_write(path, values):
    pcm = np.clip(np.rint(values*32768), -32768, 32767).astype('<i2')
    with wave.open(str(path), 'wb') as audio:
        audio.setparams((1, 2, edit.SR, 0, 'NONE', 'not compressed'))
        audio.writeframes(pcm.tobytes())


def pitch_stats(sound):
    pitch = sound.to_pitch_ac(time_step=.01, pitch_floor=70, pitch_ceiling=600)
    values = pitch.selected_array['frequency']
    values = values[values > 0]
    if len(values) < 20:
        raise ValueError('Too few voiced pitch frames')
    return dict(median_hz=float(np.median(values)), p10_hz=float(np.quantile(values,.1)),
                p90_hz=float(np.quantile(values,.9)), voiced_frames=len(values))


def raised_pitch(sound, target, call, allow_lower=False):
    measured = pitch_stats(sound)['median_hz']
    if measured >= target and not allow_lower:
        return sound, 1.0
    factor = target/measured
    manipulation = call(sound, 'To Manipulation', .01, 70, 600)
    tier = call(manipulation, 'Extract pitch tier')
    call(tier, 'Multiply frequencies', sound.xmin, sound.xmax, factor)
    call([tier,manipulation], 'Replace pitch tier')
    return call(manipulation, 'Get resynthesis (overlap-add)'), factor


def render_segment(row, work, label, rate, pitch, source_audio, voice_frames, original_frames, variant_title=None):
    frames = voice_frames+14  # 0.56 s unhurried boundary after each introduction.
    stem = row['line_id']+'_'+label
    overlay = Image.new('RGBA',(1280,720))
    draw = ImageDraw.Draw(overlay)
    draw.rounded_rectangle((800,22,1253,133),10,fill='#132b32',outline='#bfa374',width=2)
    draw.text((818,33),'A · 原版' if label=='A' else variant_title or 'B · 提速 / 音高试听',font=edit.font(27,True),fill='#f0d899')
    draw.text((818,78),f'{rate:.1f} 汉字/秒  ·  基频约 {pitch:.0f} Hz',font=edit.font(22),fill='#e8e5d5')
    layer = work/(stem+'.png');overlay.save(layer)
    video = work/(stem+'.video.mkv')
    filters = (f'[0:v]trim=end_frame={original_frames},setpts=(PTS-STARTPTS)*{voice_frames}/{original_frames},'
               'fps=25,tpad=stop_mode=clone:stop_duration=1[v];[v][1:v]overlay=0:0,format=yuv420p,setsar=1[out]')
    edit.ff(['-i',row['clip'],'-loop',1,'-i',layer,'-filter_complex',filters,
             '-map','[out]','-an','-frames:v',frames,'-c:v','libx264','-threads',4,'-preset','veryfast','-crf',19,video])
    audio = work/(stem+'.audio.wav')
    edit.complete_audio(source_audio,audio,frames)
    target = work/(stem+'.mkv')
    edit.ff(['-i',video,'-i',audio,'-map','0:v:0','-map','1:a:0','-c','copy',target])
    return dict(line_id=row['line_id'],variant=label,clip=str(target),audio=str(audio),frames=frames,voice_frames=voice_frames)


def assemble(segments, work, target):
    stem = target.stem
    listing = work/(stem+'.ffconcat')
    listing.write_text('ffconcat version 1.0\n'+''.join(
        "file '"+r['clip'].replace('\\','/')+"'\nduration "+f"{r['frames']/25:.2f}\n" for r in segments),'utf8')
    master = work/(stem+'.wav')
    with wave.open(str(master),'wb') as audio:
        audio.setparams((2,2,48000,0,'NONE','not compressed'))
        for r in segments:
            with wave.open(r['audio']) as part:
                assert part.getnframes() == r['frames']*1920
                audio.writeframesraw(part.readframes(part.getnframes()))
    edit.ff(['-f','concat','-safe',0,'-i',listing,'-i',master,'-map','0:v:0','-map','1:a:0',
             '-c:v','copy','-c:a','aac','-b:a','160k','-movflags','+faststart',target])
    edit.ff(['-i',target,'-map','0:a:0','-c:a','libmp3lame','-b:a','192k',target.with_suffix('.mp3')])
    return sum(r['frames'] for r in segments)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name',required=True)
    parser.add_argument('--target-hz',type=float,default=250)
    parser.add_argument('--extra-hanzi',type=float,default=2)
    parser.add_argument('--speech-rate',type=float,help='Absolute Hanzi/s target instead of an increment')
    parser.add_argument('--role-hz',action='append',default=[],metavar='ROLE=HZ',help='Exact role target, allowing downward shifts')
    parser.add_argument('--label',help='Separate audition directory; leaves previous exports intact')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+',args.name):parser.error('Invalid table name')
    if args.label and not re.fullmatch(r'[A-Za-z0-9_-]+',args.label):parser.error('Invalid preview label')
    if args.speech_rate is not None and args.speech_rate<=0:parser.error('Speech rate must be positive')
    overrides={}
    for value in args.role_hz:
        role,sep,hz=value.partition('=')
        if not sep or role not in edit.NAMES:parser.error('Use --role-hz ROLE=HZ with an existing role')
        try:overrides[role]=float(hz)
        except ValueError:parser.error('Invalid pitch target')
        if not 70<=overrides[role]<=600:parser.error('Pitch target outside supported analysis range')
    prior_work = edit.ROOT/'runs'/args.name/'voice_preview_250hz'
    work = edit.ROOT/'runs'/args.name/('voice_preview_'+args.label if args.label else 'voice_preview_250hz');work.mkdir(exist_ok=True)
    sys.path.insert(0,str(work/'deps' if (work/'deps').exists() else prior_work/'deps'))
    import parselmouth
    from parselmouth.praat import call
    source_work = edit.ROOT/'runs'/args.name/'edit'
    ids = ['L006','L007','L008','L009','L010']
    rows = [r for r in edit.read(source_work/'timeline.json')['rows'] if r['line_id'] in ids]
    assert len(rows)==5
    report=[];comparison=[];new_only=[]
    for row in rows:
        line=row['line_id'];old_frames=row['end_frame']-row['start_frame']-round(row['gap']*25)
        with wave.open(str(source_work/'layers'/line/'voice.wav')) as audio:
            pcm=np.frombuffer(audio.readframes(old_frames*edit.SPF),'<i2').astype(float)/32768
        sound=parselmouth.Sound(pcm,sampling_frequency=edit.SR)
        count=len(re.findall(r'[\u4e00-\u9fff]',row['text']))
        old_rate=count/(old_frames/25)
        requested_rate=args.speech_rate if args.speech_rate is not None else old_rate+args.extra_hanzi
        new_frames=round(count/requested_rate*25)
        factor=old_frames/new_frames
        original_path=work/(line+'_source.wav');pcm_write(original_path,pcm)
        before=pitch_stats(sound)
        explicit_target=overrides.get(row['role'])
        shifted,pitch_factor=raised_pitch(sound,explicit_target if explicit_target is not None else args.target_hz,
                                          call,allow_lower=explicit_target is not None)
        pitch_path=work/(line+'_pitch.wav');shifted.save(str(pitch_path),'WAV')
        fast=work/(line+'_fast.wav')
        edit.ff(['-i',pitch_path,'-af',f'atempo={factor:.9f},apad,atrim=end_sample={new_frames*edit.SPF}',
                 '-ar',edit.SR,'-ac',1,'-c:a','pcm_s16le',fast])
        after=pitch_stats(parselmouth.Sound(str(fast)))
        target=explicit_target if explicit_target is not None else max(args.target_hz,before['median_hz'])
        assert abs(after['median_hz']-target)<15,(line,before,after)
        new_rate=count/(new_frames/25)
        assert abs(new_rate-requested_rate)<.05
        a=render_segment(row,work,'A',old_rate,before['median_hz'],original_path,old_frames,old_frames)
        variant_title='B · 四字每秒 / 音高试听' if args.speech_rate==4 else None
        b=render_segment(row,work,'B',new_rate,after['median_hz'],fast,new_frames,old_frames,variant_title)
        comparison.extend([a,b]);new_only.append(b)
        report.append(dict(line_id=line,role=row['role'],speaker=row['speaker'],text=row['text'],hanzi=count,
                           before_rate=old_rate,after_rate=new_rate,before_pitch=before,after_pitch=after,
                           tempo_factor=factor,pitch_factor=pitch_factor,old_voice_seconds=old_frames/25,new_voice_seconds=new_frames/25))
        print(line,row['speaker'],f'{old_rate:.2f} -> {new_rate:.2f} Hanzi/s',
              f'{before["median_hz"]:.1f} -> {after["median_hz"]:.1f} Hz',flush=True)
    out=edit.ROOT/'exports'/args.name/'声音试听'
    if args.label:out=out/args.label
    out.mkdir(parents=True,exist_ok=True)
    compare_title='自我介绍·原版与四字每秒对比' if args.speech_rate==4 else '自我介绍·原版与提速升调对比'
    preview_title='自我介绍·四字每秒试听' if args.speech_rate==4 else '自我介绍·提速升调试听'
    compare_frames=assemble(comparison,work,out/(compare_title+'.mp4'))
    new_frames=assemble(new_only,work,out/(preview_title+'.mp4'))
    edit.write(work/'measurement.json',dict(method='Praat AC voiced F0 median; PSOLA pitch contour scaling; atempo; same original words',
         target_hz=args.target_hz,role_hz=overrides,speech_rate=args.speech_rate,extra_hanzi=args.extra_hanzi if args.speech_rate is None else None,
         preview_video=str(out/(preview_title+'.mp4')),comparison_video=str(out/(compare_title+'.mp4')),
         rate_definition='Chinese ideographs / utterance span, internal pauses and English duration included, inter-line gap excluded',
         parselmouth_version=parselmouth.__version__,lines=report,comparison_frames=compare_frames,preview_frames=new_frames,
         comparison_segments=comparison,preview_segments=new_only))
    edit.write(out/'实测参数.json',edit.read(work/'measurement.json'))
    print(out,flush=True)


if __name__=='__main__':main()
