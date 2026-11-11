"""Mix the supplied BGM after a short TV beep, ending at the entrance transition."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import wave

import numpy as np

import session_edit as edit

ROOT=edit.ROOT
BASE=ROOT/'runs/tyrol_new_table_20261110/v3_3_signal_break'
VIDEO=ROOT/'exports/tyrol_new_table_20261110/正片/v3.3无信号转场'
WORK=ROOT/'runs/tyrol_new_table_20261110/v3_4_bgm'
OUT=ROOT/'exports/tyrol_new_table_20261110/正片/v3.4配乐版'
SR=48000


def write_wave(path,samples):
    with wave.open(str(path),'wb') as output:
        output.setparams((2,2,SR,0,'NONE','not compressed'))
        output.writeframes(np.rint(samples*32767).astype('<i2').tobytes())


def music_loop(source,frames):
    raw=subprocess.check_output([edit.FF,'-v','error','-i',str(source),'-map','0:a:0','-vn',
        '-af','loudnorm=I=-19:TP=-9:LRA=11,volume=-13dB,aresample=48000,aformat=channel_layouts=stereo',
        '-ac','2','-ar',str(SR),'-f','f32le','-'])
    original=np.frombuffer(raw,'<f4').reshape(-1,2).copy()
    crossfade=2*SR
    if len(original)<crossfade*2:raise ValueError('Music is too short for the selected loop crossfade')
    loop=original.copy();passes=1
    fade=np.linspace(0,1,crossfade,dtype='float32')[:,None]
    while len(loop)<frames:
        joined=loop[-crossfade:]*(1-fade)+original[:crossfade]*fade
        loop=np.concatenate([loop[:-crossfade],joined,original[crossfade:]])
        passes+=1
    bed=loop[:frames].copy()
    fade_in=round(.55*SR);fade_out=round(1.2*SR)
    bed[:fade_in]*=np.linspace(0,1,fade_in,dtype='float32')[:,None]
    bed[-fade_out:]*=np.linspace(1,0,fade_out,dtype='float32')[:,None]
    return bed,{'source_decoded_seconds':len(original)/SR,'passes':passes,'loop_crossfade_seconds':2,
                'target_lufs':-32,'target_true_peak_dbfs':-22,'fade_in_seconds':.55,'fade_out_seconds':1.2}


def mix(source,target,bed,bed_start,tone,tone_start):
    peak=0.;position=0
    with wave.open(str(source),'rb') as original,wave.open(str(target),'wb') as output:
        assert (original.getnchannels(),original.getsampwidth(),original.getframerate())==(2,2,SR)
        output.setparams(original.getparams())
        total=original.getnframes()
        while data:=original.readframes(SR):
            pcm=np.frombuffer(data,'<i2').reshape(-1,2)
            signal=pcm.astype('float32')/32768
            for stem,start in [(bed,bed_start),(tone,tone_start)]:
                a=max(position,start);b=min(position+len(pcm),start+len(stem))
                if a<b:signal[a-position:b-position]+=stem[a-start:b-start]
            peak=max(peak,float(np.max(np.abs(signal))))
            if peak>=.999:raise RuntimeError('Mix would clip; lower the added layer before encoding')
            # Preserve the original PCM exactly where no new layer is present.
            mixed=np.rint(signal*32768).astype('<i2')
            output.writeframesraw(mixed.tobytes());position+=len(pcm)
    assert position==total
    return {'samples':total,'seconds':total/SR,'sample_peak_dbfs':20*np.log10(max(peak,1e-12))}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--music',required=True,type=Path)
    args=parser.parse_args()
    if not args.music.is_file():parser.error('Supplied music file does not exist')
    WORK.mkdir(parents=True,exist_ok=True);OUT.mkdir(parents=True,exist_ok=True)
    archive=WORK/'source'/args.music.name;archive.parent.mkdir(exist_ok=True)
    if archive.resolve()!=args.music.resolve():shutil.copy2(args.music,archive)
    timeline=edit.read(BASE/'opening_timeline.json')
    first=next(r for r in timeline['rows'] if r.get('signal_break'))
    tone_start=round((first['start_frame']+first['signal_break']['a'])/25*SR)
    tone_frames=round(.32*SR)
    bed_start=tone_start+tone_frames
    bed_end=round(next(r['start_frame'] for r in timeline['rows'] if r['kind']=='transition')/25*SR)
    bed,music_info=music_loop(archive,bed_end-bed_start)
    time=np.arange(tone_frames)/SR
    mono=.1*np.sin(2*np.pi*1000*time)
    edge=round(.012*SR)
    mono[:edge]*=np.linspace(0,1,edge);mono[-edge:]*=np.linspace(1,0,edge)
    tone=np.repeat(mono[:,None],2,axis=1).astype('float32')
    write_wave(WORK/'bgm_stem.wav',bed);write_wave(WORK/'tv_beep.wav',tone)
    deliver=[]
    for prefix,name in [('opening','开场与六人入场·MiniMax2.8.mp4'),
                        ('full','这份初稿，下轮一定·开场接入版（后段原配音）.mp4')]:
        audio=WORK/(prefix+'.mixed.wav')
        metrics=mix(BASE/(prefix+'.normalized.wav'),audio,bed,bed_start,tone,tone_start)
        target=OUT/('开场与六人入场·配乐版.mp4' if prefix=='opening' else '这份初稿，下轮一定·开场配乐版（后段原配音）.mp4')
        edit.ff(['-i',VIDEO/name,'-i',audio,'-map','0:v:0','-map','1:a:0',
                 '-map_metadata','0','-map_chapters','0','-c:v','copy','-c:a','aac','-b:a','192k',
                 '-metadata:s:a:0','language=zho','-movflags','+faststart',target])
        for suffix in ['.srt','.timeline.json']:
            shutil.copy2((VIDEO/name).with_suffix(suffix),target.with_suffix(suffix))
        deliver.append({'video':str(target),**metrics})
    report={'music_source':str(archive),'original_user_file':str(args.music.resolve()),
            'beep_start_seconds':tone_start/SR,'beep_end_seconds':bed_start/SR,
            'beep_frequency_hz':1000,'beep_peak_dbfs':-20,
            'music_start_seconds':bed_start/SR,'music_end_seconds':bed_end/SR,
            'music_ends_at':'选手入场转场开始，后续自我介绍不铺这首BGM',
            'music_level':music_info,'voice_gain':1,'video_reencoded':False,'outputs':deliver}
    edit.write(WORK/'mix_report.json',report);edit.write(OUT/'配乐交付.json',report)
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
