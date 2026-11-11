"""Add the approved strategy/supply sentence; reuse every other approved take."""
import copy
from pathlib import Path
import sys

import requests

import session_edit as edit
from integrate_v3_opening import pcm_read

sys.path.insert(0,str(edit.ROOT/'tools'))
from minimax_audio import load_key,synthesize

ROOT=edit.ROOT
WORK=ROOT/'runs/tyrol_new_table_20261110/v3_2_pacing'
OUT=ROOT/'exports/tyrol_new_table_20261110/声音试听/v3_2_pacing'
SCRIPT_OUT=ROOT/'exports/tyrol_new_table_20261110/配音稿/v3.2'
ADDED='不过这游戏打起来可真不简单，既要考虑战略，又要盯紧供给。'


def main():
    for folder in (WORK,OUT,SCRIPT_OUT):folder.mkdir(parents=True,exist_ok=True)
    script=edit.read(ROOT/'exports/tyrol_new_table_20261110/配音稿/v3.1/Gemini-v3.1_完整纯配音稿.json')
    source={r['id']:r for r in script['segments']}
    row=source['L004-K024']
    row['selected_text']=ADDED+row['selected_text']
    row['spoken_text']=ADDED+row['spoken_text']
    row['user_revision']='v3.2: prepend strategy/supply explanation'
    script.update(version='v3.2',pacing='Five editorial pauses and an entrance transition; voice rate unchanged',
                  audition={'run':str(WORK),'changed_takes':['L004-A03'],
                            'parent_audio_run':'runs/tyrol_new_table_20261110/v3_1_revision'})
    edit.write(SCRIPT_OUT/'Gemini-v3.2_完整纯配音稿.json',script)
    (SCRIPT_OUT/'Gemini-v3.2_完整纯配音稿.txt').write_text('\n\n'.join(
        f'[{r["id"]}｜{r["role"]}]\n{r["selected_text"]}' for r in script['segments'] if not r['deleted'])+'\n',encoding='utf-8-sig')
    chunks=edit.read(ROOT/'runs/tyrol_new_table_20261110/v3_1_revision/synthesis_chunks.json')
    chunk=next(r for r in chunks if r['id']=='L004-A03')
    chunk.update(text=''.join(source[k]['selected_text'] for k in chunk['source_ids']),
                 spoken_text=''.join(source[k]['spoken_text'] for k in chunk['source_ids']))
    edit.write(WORK/'synthesis_chunks.json',chunks)
    report=edit.read(ROOT/'runs/tyrol_new_table_20261110/v3_1_revision/selected_audio_validation.json')
    original=next(r for r in report['segments'] if r['id']=='L004-A03')
    request=copy.deepcopy(edit.read(ROOT/'exports/tyrol_new_table_20261110/声音试听/v3_opening_28turbo_4cps/L004-A03.json')['request'])
    request.update(text=chunk['spoken_text'],subtitle_enable=True,subtitle_type='word')
    audio=OUT/'L004-A03.mp3'
    receipt=synthesize(load_key(),request,audio,'io')
    subtitle=audio.with_suffix('.subtitles.json')
    if not subtitle.exists():
        url=receipt['response'].get('data',{}).get('subtitle_file','')
        if not url.startswith('https://'):raise RuntimeError('Native subtitle URL missing')
        response=requests.get(url,timeout=(15,45));response.raise_for_status();edit.write(subtitle,response.json())
    pcm=pcm_read(audio)
    original.update(selected_audio=str(audio),selected_take='v3.2',native_subtitles=str(subtitle),
                    duration_seconds=len(pcm)/edit.SR,decoded=True)
    original.update(hanzi=sum('\u4e00'<=c<='\u9fff' for c in request['text']))
    original['hanzi_per_second']=original['hanzi']/original['duration_seconds']
    original['usage_characters']=receipt['response']['extra_info']['usage_characters']
    report.update(new_tts_calls=1,new_tts_usage_characters=original['usage_characters'],
                  changed_takes=['L004-A03'],speed_correction_candidates=[],
                  parent_audio_report='runs/tyrol_new_table_20261110/v3_1_revision/selected_audio_validation.json')
    edit.write(WORK/'selected_audio_validation.json',report)
    print('READY',original['duration_seconds'],original['hanzi_per_second'],flush=True)


if __name__=='__main__':main()
