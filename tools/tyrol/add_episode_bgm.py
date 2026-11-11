"""Add an ordered, repeating BGM playlist to the v3.5 remainder."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

import numpy as np

import session_edit as edit
from add_opening_bgm import mix, write_wave, SR

ROOT=edit.ROOT
BASE=ROOT/'runs/tyrol_new_table_20261110/v3_5_complete'
WORK=ROOT/'runs/tyrol_new_table_20261110/v3_6_playlist_bgm'
OUT=ROOT/'exports/tyrol_new_table_20261110/正片/v3.6后半段轮播配乐'


def playlist(sources,frames):
    tracks=[]
    for source in sources:
        raw=subprocess.check_output([edit.FF,'-v','error','-i',str(source),'-map','0:a:0','-vn',
            '-af','loudnorm=I=-19:TP=-9:LRA=11,volume=-13dB,aresample=48000,aformat=channel_layouts=stereo',
            '-ac','2','-ar',str(SR),'-f','f32le','-'])
        tracks.append(np.frombuffer(raw,'<f4').reshape(-1,2).copy())
    crossfade=2*SR
    if any(len(t)<crossfade*2 for t in tracks):raise ValueError('A music track is too short for a 2-second crossfade')
    bed=np.zeros((frames,2),dtype='float32')
    entries=[];position=0;index=0
    while position<frames:
        source=tracks[index%len(tracks)]
        count=min(len(source),frames-position)
        part=source[:count].copy()
        if position:
            fade_count=min(crossfade,count)
            fade=np.linspace(0,1,fade_count,dtype='float32')[:,None]
            bed[position:position+fade_count]*=1-fade
            part[:fade_count]*=fade
        bed[position:position+count]+=part
        entries.append({'source':str(sources[index%len(tracks)]),'playlist_index':index%len(tracks),
                        'cycle':index//len(tracks)+1,'local_start_seconds':position/SR,
                        'local_end_seconds':(position+count)/SR,'source_seconds':len(source)/SR})
        if position+count==frames:break
        position+=len(source)-crossfade;index+=1
    fade_in=round(.8*SR);fade_out=2*SR
    bed[:fade_in]*=np.linspace(0,1,fade_in,dtype='float32')[:,None]
    bed[-fade_out:]*=np.linspace(1,0,fade_out,dtype='float32')[:,None]
    return bed,entries


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--music',type=Path,nargs='+',required=True,help='Tracks in the requested playback order')
    args=parser.parse_args()
    for p in args.music:
        if not p.is_file():parser.error('Music file not found: '+str(p))
    for folder in (WORK,OUT,WORK/'source'):folder.mkdir(parents=True,exist_ok=True)
    delivery=edit.read(ROOT/'exports/tyrol_new_table_20261110/正片/v3.5全片重配与回合转场/交付.json')
    source_video=Path(delivery['episode']);timeline=edit.read(source_video.with_suffix('.timeline.json'))
    offset=round(delivery['opening_seconds']*SR)
    frames=round(timeline['frames']/timeline['fps']*SR)-offset
    archive=[]
    for source in args.music:
        target=WORK/'source'/source.name
        if target.resolve()!=source.resolve():shutil.copy2(source,target)
        archive.append(target)
    bed,entries=playlist(archive,frames)
    write_wave(WORK/'playlist_stem.wav',bed)
    metrics=mix(BASE/'full.mixed.wav',WORK/'full.mixed.wav',bed,offset,
                np.zeros((0,2),dtype='float32'),0)
    target=OUT/'这份初稿，下轮一定·四曲轮播配乐版.mp4'
    edit.ff(['-i',source_video,'-i',WORK/'full.mixed.wav','-map','0:v:0','-map','1:a:0',
             '-map_metadata','0','-map_chapters','0','-c:v','copy','-c:a','aac','-b:a','192k',
             '-metadata:s:a:0','language=zho','-movflags','+faststart',target])
    for suffix in ('.srt','.timeline.json'):shutil.copy2(source_video.with_suffix(suffix),target.with_suffix(suffix))
    for entry in entries:
        entry['start_seconds']=delivery['opening_seconds']+entry['local_start_seconds']
        entry['end_seconds']=delivery['opening_seconds']+entry['local_end_seconds']
    report={'video':str(target),'source_video':str(source_video),'playlist_order':[str(p) for p in args.music],
            'music_start_seconds':offset/SR,'music_end_seconds':(offset+frames)/SR,
            'crossfade_seconds':2,'fade_in_seconds':.8,'fade_out_seconds':2,
            'music_normalization':'loudnorm I=-19 TP=-9 LRA=11, then -13dB; nominal target -32 LUFS',
            'voice_gain':1,'video_reencoded':False,'opening_pcm_preserved':True,'playlist':entries,**metrics}
    edit.write(WORK/'mix_report.json',report);edit.write(OUT/'配乐交付.json',report)
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
