"""Apply the user's opening corrections and resynthesize only changed MiniMax takes."""
from concurrent.futures import ThreadPoolExecutor
import copy
import csv
from pathlib import Path
import re
import sys

import requests

import session_edit as edit
from integrate_v3_opening import pcm_read

sys.path.insert(0,str(edit.ROOT/'tools'))
from minimax_audio import load_key,synthesize

ROOT=edit.ROOT
OLD=ROOT/'runs/tyrol_new_table_20261110/v3_opening_28turbo_4cps'
WORK=ROOT/'runs/tyrol_new_table_20261110/v3_1_revision'
OUT=ROOT/'exports/tyrol_new_table_20261110/声音试听/v3_1_revision'
SCRIPT_OUT=ROOT/'exports/tyrol_new_table_20261110/配音稿/v3.1'
CHANGED={'L004-A01','L004-A02','L004-A04','L004-A05','L004-A06','L004-A07','L007','L009','L010'}
REPLACEMENTS={
 'L004-K039':'每回打输了，我群里都喊：把将军叫来。看了一会儿之后，',
 'L004-K040':'步枪是我让造的，',
 'L004-K050':'陆海空三军……肯定难不倒我大狐狸。可惜的是平时都没人陪我玩这游戏。',
 'L004-K055':'合照是真的，地图也是真的。既然来都来了，看都看了，不如陪我多看一会儿。',
 'L007':'我是白龙酱，来自OpenAI。云端第一条起飞的龙。大肥鱼，一碗饭就能开工，这份本事我记下了。',
 'L009':'本小姐克莱德，来自Anthropic。橙色长直发，鬓边这朵向日葵是自己别的——别笑它。',
 'L010':'千问，是通义千问家的大小姐。蓝紫大氅，白礼帽，认人认这身花色就行。'}
DELETIONS={'L004-K017':'用户删除战犯最多的游戏这一问句',
           'L004-K018':'群里喊将军一段移至L004-K039，不在这里重复',
           'L004-K044':'用户删除种田、飞机和海军整句'}


def prepare():
    for folder in (WORK,OUT,SCRIPT_OUT):folder.mkdir(parents=True,exist_ok=True)
    script=edit.read(ROOT/'exports/tyrol_new_table_20261110/配音稿/v3/Gemini-v3_完整纯配音稿.json')
    rows={r['id']:r for r in script['segments']}
    for ident,text in REPLACEMENTS.items():
        row=rows[ident]
        row.update(selected_text=text,spoken_text=text,user_revision='2026-11-10 opening revision')
    for ident,reason in DELETIONS.items():
        rows[ident].update(deleted=True,selected_text='',spoken_text='',deletion_decision=reason)
        script['deletions'][ident]=reason
    script.update(version='v3.1',user_revision='Opening timing, transitions, moved general joke, added invitations and revised introductions',
                  audition={'run':str(WORK),'changed_takes':sorted(CHANGED)})
    text='\n\n'.join(f'[{r["id"]}｜{r["role"]}]\n{r["selected_text"]}'
                       for r in script['segments'] if not r['deleted'])+'\n'
    assert '钢四是不是战犯最多的游戏' not in text
    assert '想种田、开飞机、给海军攒条船，也行' not in text
    assert text.count('每回打输了，我群里都喊：把将军叫来。')==1
    (SCRIPT_OUT/'Gemini-v3.1_完整纯配音稿.txt').write_text(text,encoding='utf-8-sig')
    edit.write(SCRIPT_OUT/'Gemini-v3.1_完整纯配音稿.json',script)
    with (SCRIPT_OUT/'Gemini-v3.1_逐段修订.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f);writer.writerow(['ID','角色','显示正文','朗读正文','已删除','删除决定'])
        for r in script['segments']:writer.writerow([r['id'],r['role'],r['selected_text'],r['spoken_text'],r['deleted'],r.get('deletion_decision','')])
    chunks=edit.read(OLD/'synthesis_chunks.json')
    for chunk in chunks:
        chunk['source_ids']=[k for k in chunk['source_ids'] if not rows[k]['deleted']]
        chunk['text']=''.join(rows[k]['selected_text'] for k in chunk['source_ids'])
        chunk['spoken_text']=''.join(rows[k]['spoken_text'] for k in chunk['source_ids'])
    edit.write(WORK/'synthesis_chunks.json',chunks)
    return chunks


def old_request(row):
    audio=Path(row['selected_audio'])
    folder=audio.parent.parent
    take=row.get('selected_take','first')
    meta=folder/(row['id']+'.json') if take=='first' else folder/take/(row['id']+'.json')
    result=copy.deepcopy(edit.read(meta)['request'])
    assert result['voice_setting']['speed']==row['speed']
    return result


def main():
    chunks={r['id']:r for r in prepare()}
    baseline=edit.read(OLD/'selected_audio_validation.json')
    key=load_key()
    if not key:raise RuntimeError('MINIMAX_API_KEY missing')
    def take(row,speed=None,dest=OUT):
        ident=row['id'];body=old_request(row)
        body.update(text=chunks[ident]['spoken_text'],subtitle_enable=True,subtitle_type='word')
        if speed is not None:body['voice_setting']['speed']=speed
        dest.mkdir(parents=True,exist_ok=True)
        audio=dest/(ident+'.mp3')
        receipt=synthesize(key,body,audio,'io')
        subtitle=audio.with_suffix('.subtitles.json')
        if not subtitle.exists():
            url=receipt['response'].get('data',{}).get('subtitle_file','')
            if not url.startswith('https://'):raise RuntimeError(ident+': missing native subtitle URL')
            response=requests.get(url,timeout=(15,45));response.raise_for_status()
            edit.write(subtitle,response.json())
        native=edit.read(subtitle)
        if not native or not native[0].get('timestamped_words'):raise RuntimeError(ident+': native word timestamps absent')
        pcm=pcm_read(audio);seconds=len(pcm)/edit.SR
        result={**row,'source_ids':chunks[ident]['source_ids'],'selected_audio':str(audio),
                'selected_take':'v3.1' if speed is None else 'v3.1-retake1','native_subtitles':str(subtitle),
                'duration_seconds':seconds,'hanzi':len(re.findall(r'[\u4e00-\u9fff]',body['text'])),
                'usage_characters':receipt['response']['extra_info']['usage_characters'],
                'speed':body['voice_setting']['speed'],'decoded':True}
        result['hanzi_per_second']=result['hanzi']/seconds
        return result
    tasks=[r for r in baseline['segments'] if r['id'] in CHANGED]
    # Establish a native-timestamp result, then at most three requests in flight.
    selected={tasks[0]['id']:take(tasks[0])}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for i in range(1,len(tasks),3):
            futures=[pool.submit(take,r) for r in tasks[i:i+3]]
            for future in futures:
                result=future.result();selected[result['id']]=result
    # One measured speed correction only for materially off-target revised takes.
    retakes=[]
    for original in tasks:
        current=selected[original['id']]
        cps=current['hanzi_per_second']
        if 3.4<=cps<=4.6:continue
        speed=round(min(2,max(.5,current['speed']*4/cps)),2)
        candidate=take(original,speed,OUT/'retake1')
        retakes.append(candidate)
        if abs(candidate['hanzi_per_second']-4)<abs(cps-4):
            selected[original['id']]=candidate
    rows=[selected.get(r['id'],r) for r in baseline['segments']]
    for row in rows:row['source_ids']=chunks[row['id']]['source_ids']
    report={'model':'speech-2.8-turbo','segments':rows,'changed_takes':sorted(CHANGED),
            'new_tts_calls':len(CHANGED)+len(retakes),
            'new_tts_usage_characters':sum(edit.read(OUT/(k+'.json'))['response']['extra_info']['usage_characters'] for k in CHANGED)
                                       +sum(r['usage_characters'] for r in retakes),
            'speed_correction_candidates':retakes,
            'subtitle_source':'Native TTS word timestamps on every revised take; unchanged takes retained',
            'scene_changes':'No paper/blackboard cut during 后面还有抽卡; classroom begins at 行，这稿就念到这儿'}
    edit.write(WORK/'selected_audio_validation.json',report)
    edit.write(OUT/'实测与请求.json',report)
    print('READY',report['new_tts_calls'],report['new_tts_usage_characters'],flush=True)


if __name__=='__main__':main()
