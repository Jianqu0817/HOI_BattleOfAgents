from concurrent.futures import ThreadPoolExecutor
from getpass import getpass
import base64
import hashlib
import io
import json
import os
import wave
from urllib.parse import urlparse
import requests
from script import WORK, rows, voices, spoken

MODEL='qwen-audio-3.1-tts-flash'
API='https://maas.qianwenaiapi.com/api/v1/services/audio/tts/SpeechSynthesizer'

def finite_wav(data):
    with wave.open(io.BytesIO(data)) as wav:
        if (wav.getnchannels(),wav.getsampwidth(),wav.getframerate())!=(1,2,24000):
            raise ValueError('Unexpected audio format')
        pcm=wav.readframes(len(data)//2)
    if len(pcm)<4800 or len(pcm)%2:
        raise ValueError('Empty or incomplete WAV')
    dest=io.BytesIO()
    with wave.open(dest,'wb') as result:
        result.setparams((1,2,24000,0,'NONE','not compressed'))
        result.writeframes(pcm)
    return dest.getvalue(),len(pcm)/48000

def main():
    key=os.getenv('DASHSCOPE_API_KEY') or getpass('Qwen API key: ')
    dest=WORK/'takes'
    dest.mkdir(parents=True,exist_ok=True)
    def take(item):
        i,r=item
        settings=voices[r['tag']]
        instruction=settings[3]
        if r['tag']=='GLM':
            instruction+=('给观众解说时亲切清楚，数字逐位读。' if r['kind']=='观众解说' else '这句是对同桌玩家发问，俏皮一点，把问题抛给对方。')
        body={'model':MODEL,'input':{'text':spoken(r['text']),'voice':settings[2],'format':'wav','sample_rate':24000,
                                    'instruction':instruction,'rate':0.94}}
        identity=hashlib.sha256(json.dumps(body,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        audio=dest/f'{i:02d}.wav'
        meta=audio.with_suffix('.json')
        if audio.exists() and meta.exists() and json.loads(meta.read_text('utf8'))['identity']==identity:
            return
        resp=requests.post(API,json=body,headers={'Authorization':'Bearer '+key},timeout=(15,120),allow_redirects=False)
        response=resp.json()
        if resp.status_code!=200 or 'output' not in response:
            raise RuntimeError(f'{i}: HTTP {resp.status_code}, '+str(response.get('code'))+' '+str(response.get('message',''))[:300])
        result=response['output']['audio']
        url=result.get('url') or ''
        if url.startswith('data:'):
            data=base64.b64decode(url.partition(',')[2],validate=True)
        elif result.get('data'):
            data=base64.b64decode(result['data'],validate=True)
        else:
            parts=urlparse(url)
            if parts.scheme=='http':
                url=parts._replace(scheme='https').geturl()
            elif parts.scheme!='https':
                raise ValueError(f'Unexpected output audio fields {list(result)}; URL scheme {parts.scheme[:10]}')
            download=requests.get(url,timeout=(15,90))
            download.raise_for_status()
            data=download.content
        data,seconds=finite_wav(data)
        temp=audio.with_suffix('.part')
        temp.write_bytes(data)
        temp.replace(audio)
        meta.write_text(json.dumps(dict(identity=identity,model=MODEL,voice=settings[2],request_id=response.get('request_id'),seconds=seconds,
                                         usage=response.get('usage',{})),ensure_ascii=False,indent=2),'utf8')
        print(i,r['tag'],round(seconds,2),flush=True)
    items=list(enumerate(rows,1))
    take(items[0])
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(take,items[1:]))

if __name__=='__main__':
    main()
