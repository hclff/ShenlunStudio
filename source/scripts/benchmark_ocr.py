"""Paired native WinRT OCR on reproducible synthetic Chinese print, never cloud."""
import argparse
import io
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PIL import Image,ImageDraw,ImageFont
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from shenlun import image_ocr,ocr
from shenlun.intake import _png


LINES=['给定材料：社区保留人工窗口和电话服务。',
       '第一项：帮助老年群众预约，提供上门办理。',
       '第二项：完善公共设施，解决居民实际困难。',
       '第三项：听取群众意见，协调资源改进服务。',
       '要求：概括主要做法，内容准确、条理清晰。',
       '数字核对：2026年，共有35人参加，分为5组。']


def encode(image,format='PNG',**kwargs):
    stream=io.BytesIO();image.save(stream,format,**kwargs);return stream.getvalue()


def sample(lines=LINES,size=(1200,850),font_size=36,bg='white',ink='black',gap=115):
    image=Image.new('RGB',size,bg);draw=ImageDraw.Draw(image)
    font=ImageFont.truetype(str(Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/'msyh.ttc'),font_size)
    for n,line in enumerate(lines):draw.text((45,45+n*gap),line,font=font,fill=ink)
    return image,'\n'.join(lines)


def fixtures():
    clean,expected=sample()
    yield '清晰印刷',clean,expected,{}
    light,expected=sample(font_size=24,bg=(235,235,235),ink=(185,185,185))
    yield '小字浅灰',light,expected,{}
    dark,expected=sample(bg=(30,30,30),ink=(225,225,225))
    yield '深色背景',dark,expected,{}
    yield '倾斜5度',clean.rotate(-5,expand=True,fillcolor='white'),expected,{}
    yield '侧转90度',clean.rotate(-90,expand=True,fillcolor='white'),expected,{'rotation':270}
    lines=[f'第{n+1}项：社区保留人工窗口，为群众提供电话服务。' for n in range(70)]
    long,expected=sample(lines,(1000,14000),28,gap=195)
    yield '长截图70行',long,expected,{}
    left,left_expected=sample(['左栏'+line for line in LINES],(1050,1000),30,gap=140)
    right,right_expected=sample(['右栏'+line for line in LINES],(1050,1000),30,gap=140)
    two=Image.new('RGB',(2100,1000),'white');two.paste(left);two.paste(right,(1050,0))
    yield '双栏先左后右',two,left_expected+'\n'+right_expected,{'columns':2}
    compressed=Image.open(io.BytesIO(encode(clean,'JPEG',quality=25))).copy()
    yield 'JPEG低质量',compressed,'\n'.join(LINES),{}


def character_errors(reference,text):
    """Levenshtein; remove only whitespace, retain numbers and punctuation errors."""
    a=re.sub(r'\s','',reference);b=re.sub(r'\s','',text)
    previous=list(range(len(b)+1))
    for i,ca in enumerate(a,1):
        current=[i]
        for j,cb in enumerate(b,1):current.append(min(current[-1]+1,previous[j]+1,previous[j-1]+(ca!=cb)))
        previous=current
    errors=previous[-1]
    return {'reference_chars':len(a),'recognized_chars':len(b),'edits':errors,'cer':round(errors/max(1,len(a)),6)}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=ROOT/'tasks/ocr-v0.6/benchmark')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    app=QApplication.instance() or QApplication([])
    rows=[]
    for name,image,expected,options in fixtures():
        data=encode(image);(args.output/(name+'.png')).write_bytes(data)
        baseline=args.output/'baseline.png';baseline.write_bytes(_png(QImage.fromData(data)))
        row={'case':name,'dimensions':list(image.size),'expected':expected,'enhanced_options':options}
        for method in ('baseline2560','enhanced'):
            started=time.monotonic()
            try:
                result=ocr.recognize_png(baseline) if method=='baseline2560' else image_ocr.recognize_image(data,**options)
                row[method]={**character_errors(expected,result['text']),'text':result['text'],
                             'info':result.get('info',{}),'angle':result.get('angle')}
            except Exception as exc:row[method]={'error':str(exc)}
            row[method]['seconds']=round(time.monotonic()-started,3)
        rows.append(row)
        (args.output/'results.json').write_text(json.dumps({'scope':'Synthetic printed Chinese; local WinRT only; manual rotation and column selection where noted; not handwriting or AI accuracy',
                                                          'cases':rows},ensure_ascii=False,indent=2),encoding='utf8')
        print(name+': '+json.dumps({key:{k:v for k,v in row[key].items() if k not in ('text','info')} for key in ('baseline2560','enhanced')},ensure_ascii=False),flush=True)
    if any('error' in row[key] for row in rows for key in ('baseline2560','enhanced')):raise SystemExit(1)


if __name__=='__main__':main()
