"""Bounded, local question/answer file intake, separate from corpus ingestion."""
from dataclasses import dataclass,field
import copy
import io
import json
from pathlib import Path
import re
import tempfile
import zipfile

from PySide6.QtCore import QByteArray,QBuffer,QIODevice,QSize,Qt
from PySide6.QtGui import QImage,QPainter

from . import ocr
from .domain import RUBRICS,finite_number,validate_rubric
from .model import CancelledError,check_cancel

MAX_FILE_BYTES=25*1024*1024
MAX_TEXT=250000
MAX_BATCH_PAGES=80
MAX_BATCH_IMAGE_BYTES=96*1024*1024
SUPPORTED_SUFFIXES={'.txt','.md','.json','.pdf','.docx','.png','.jpg','.jpeg','.bmp','.tif','.tiff','.webp'}
FILE_FILTER='题目文件 (*.txt *.md *.json *.pdf *.docx *.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp)'


@dataclass
class InputPage:
    source:str
    number:int
    text:str
    method:str
    preview_png:bytes=b''
    warnings:list[str]=field(default_factory=list)
    fields:dict=field(default_factory=dict)
    source_image:bytes=b''
    ocr_info:dict=field(default_factory=dict)


@dataclass
class ImportBatch:
    pages:list[InputPage]=field(default_factory=list)
    errors:list[str]=field(default_factory=list)
    attempted_pages:int=0


def _notify(progress,message):
    if progress:progress(message)


def _text_bytes(data):
    encodings=['utf-16'] if data.startswith((b'\xff\xfe',b'\xfe\xff')) else ['utf-8-sig','gb18030']
    for encoding in encodings:
        try:text=data.decode(encoding)
        except UnicodeDecodeError:continue
        if any(ord(c)<32 and c not in '\n\r\t\f' for c in text):
            raise ValueError('文件含二进制控制字符，不能作为文字导入。')
        return text.replace('\r\n','\n').replace('\r','\n').strip()
    raise ValueError('无法识别文字编码，请另存为UTF-8文字文件。')


def _png(image,max_edge=2560):
    if image.isNull():raise ValueError('无法读取图片。')
    if max_edge and max(image.width(),image.height())>max_edge:
        image=image.scaled(QSize(max_edge,max_edge),aspectMode=Qt.AspectRatioMode.KeepAspectRatio,
                           mode=Qt.TransformationMode.SmoothTransformation)
    if image.hasAlphaChannel():
        background=QImage(image.size(),QImage.Format.Format_RGB32);background.fill(Qt.GlobalColor.white)
        painter=QPainter(background);painter.drawImage(0,0,image);painter.end()
        image=background
    else:image=image.convertToFormat(QImage.Format.Format_RGB32)
    data=QByteArray();buffer=QBuffer(data);buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer,'PNG'):raise ValueError('图片转换失败。')
    buffer.close()
    return bytes(data)


def _ocr_page(data,source,number,progress,cancel,ocr_options=None,retain_failed=False):
    from . import image_ocr
    check_cancel(cancel)
    _notify(progress,f'本机识别：{Path(source).name} 第{number}页…')
    options={'mode':'standard',**(ocr_options or {})}
    try:result=image_ocr.recognize_image(data,progress=progress,cancel=cancel,**options)
    except (ValueError,OSError) as exc:
        check_cancel(cancel)
        if not retain_failed:raise
        original=image_ocr.normalize_image(data)
        preview=image_ocr.preview_png(original);check_cancel(cancel)
        return InputPage(source,number,'','本机OCR失败（待重试）',preview,
                         ['本机识别失败：'+str(exc)[:400]+'；原图保留，可手动修订或选择AI识别。'],
                         source_image=original,ocr_info={'failed':True,**options})
    check_cancel(cancel)
    warnings=list(result['warnings'])
    language=result.get('language','未知')
    if not language.startswith('zh'):
        warnings.append('当前识别语言为'+language+'；中文材料可能不完整，请在Windows设置中添加中文OCR语言组件。')
    if not result['text'].strip():warnings.append('未识别出文字，可对照原图手动补入，或取消勾选此页。')
    label='本机增强OCR' if options['mode']=='enhanced' else '本机OCR'
    return InputPage(source,number,result['text'],label+'（'+language+'）',result['preview_png'],warnings,
                     source_image=result['source_image'],ocr_info=result['info'])


def read_image_bytes(data,source='剪贴板图片',progress=None,cancel=None,ocr_options=None,retain_failed=False):
    check_cancel(cancel)
    if not isinstance(data,bytes) or len(data)>MAX_FILE_BYTES:raise ValueError('图片数据无效或超过25MB。')
    from PIL import Image
    with Image.open(io.BytesIO(data),formats=['PNG','JPEG','BMP','TIFF','WEBP']) as image:
        png=_pillow_png(image)
    page=_ocr_page(png,source,1,progress,cancel,ocr_options,retain_failed)
    check_cancel(cancel)
    return ImportBatch([page])


def _pillow_png(image):
    from .image_ocr import normalize_pillow_image
    return normalize_pillow_image(image)


def _images(path,progress,cancel,max_pages=40,max_text=MAX_TEXT,ocr_options=None,retain_failed=False,max_image_bytes=MAX_BATCH_IMAGE_BYTES):
    from PIL import Image
    batch=ImportBatch()
    with Image.open(path,formats=['PNG','JPEG','BMP','TIFF','WEBP']) as image:
        count=getattr(image,'n_frames',1)
        if count>40:raise ValueError('单个图片文件超过40帧/页，请拆分后导入。')
        if count>max_pages:batch.errors.append(f'{path.name}：本次最多80页，后续图片页未读取，请分批。')
        characters=0;image_bytes=0
        for index in range(min(count,max_pages)):
            check_cancel(cancel)
            batch.attempted_pages+=1
            try:
                image.seek(index)
                page=_ocr_page(_pillow_png(image),str(path),index+1,progress,cancel,ocr_options,retain_failed)
                size=len(page.source_image)+len(page.preview_png)
                if image_bytes+size>max_image_bytes:
                    batch.errors.append(f'{path.name}：原图缓存超过96MB，本页及后续页未加入，请分批。');break
                if characters+len(page.text)>max_text:
                    batch.errors.append(f'{path.name}：本次最多25万字符，此页及后续页未加入，请分批。');break
                batch.pages.append(page);characters+=len(page.text);image_bytes+=size
            except CancelledError:raise
            except (ValueError,OSError) as exc:batch.errors.append(f'{path.name} 第{index+1}页：{exc}')
    return batch


def _page_numbers(spec,count):
    if not spec.strip():
        if count>40:raise ValueError('PDF超过40页，请指定页码范围，每次最多40页。')
        return list(range(count))
    selected=[]
    for part in spec.replace('，',',').split(','):
        match=re.fullmatch(r'\s*(\d+)(?:\s*-\s*(\d+))?\s*',part)
        if not match:raise ValueError('页码格式应为1-3,5。')
        start,end=int(match[1]),int(match[2] or match[1])
        if start<1 or end<start or end>count:raise ValueError(f'页码越界或顺序错误；此PDF共{count}页。')
        for number in range(start-1,end):
            if number not in selected:selected.append(number)
            if len(selected)>40:raise ValueError('每次最多选择40页PDF。')
    return selected


def _pdf(path,progress,cancel,force_ocr,page_spec,max_pages=40,max_text=MAX_TEXT,ocr_options=None,retain_failed=False,max_image_bytes=MAX_BATCH_IMAGE_BYTES):
    from pypdf import PdfReader
    from PySide6.QtPdf import QPdfDocument
    reader=PdfReader(path)
    if reader.is_encrypted:raise ValueError('PDF已加密，请先解密后导入。')
    indices=_page_numbers(page_spec,len(reader.pages))
    renderer=QPdfDocument()
    error=renderer.load(str(path))
    if error!=QPdfDocument.Error.None_:raise ValueError('PDF无法加载，可能已损坏或不受支持。')
    batch=ImportBatch()
    if len(indices)>max_pages:batch.errors.append(f'{path.name}：本次最多80页，后续PDF页未读取，请分批。')
    characters=0;image_bytes=0
    try:
        for index in indices[:max_pages]:
            check_cancel(cancel)
            batch.attempted_pages+=1
            _notify(progress,f'读取PDF：{path.name} 第{index+1}/{len(reader.pages)}页…')
            try:
                size=renderer.pagePointSize(index)
                if size.width()<=0 or size.height()<=0:raise ValueError('PDF页面尺寸无效。')
                enhanced=bool(ocr_options and ocr_options.get('mode')=='enhanced')
                scale=min(4 if enhanced else 2.5,(3600 if enhanced else 2400)/max(size.width(),size.height()))
                image=renderer.render(index,QSize(max(1,round(size.width()*scale)),max(1,round(size.height()*scale))))
                data=_png(image,max_edge=None)
                text=(reader.pages[index].extract_text() or '').replace('\r\n','\n').strip()
                if force_ocr or len(re.sub(r'\s','',text))<10:
                    page=_ocr_page(data,str(path),index+1,progress,cancel,ocr_options,retain_failed)
                else:
                    page=InputPage(str(path),index+1,text,'PDF文字',_png(image),
                        ['已读取PDF文字层；若只有标题、水印或部分内容，请改用“强制OCR”并对照原页核对。'],source_image=data)
                retained_size=len(page.source_image)+len(page.preview_png)
                if image_bytes+retained_size>max_image_bytes:
                    batch.errors.append(f'{path.name}：原图缓存超过96MB，本页及后续页未加入，请分批。');break
                if characters+len(page.text)>max_text:
                    batch.errors.append(f'{path.name}：本次最多25万字符，此页及后续页未加入，请分批。');break
                batch.pages.append(page);characters+=len(page.text);image_bytes+=retained_size
            except CancelledError:raise
            except (ValueError,OSError) as exc:batch.errors.append(f'{path.name} 第{index+1}页：{exc}')
    finally:renderer.close()
    return batch


def _docx(path):
    from docx import Document
    from docx.text.paragraph import Paragraph
    with zipfile.ZipFile(path) as archive:
        info=archive.infolist()
        if len(info)>5000 or sum(item.file_size for item in info)>100*1024*1024:
            raise ValueError('Word解压内容过大，请简化文档。')
        if any(item.flag_bits&1 for item in info):raise ValueError('Word文档已加密。')
    document=Document(path)
    def blocks(container):
        for block in container.iter_inner_content():
            if isinstance(block,Paragraph):yield block.text
            else:
                for row in block.rows:
                    seen=set();cells=[]
                    for cell in row.cells:
                        if cell._tc in seen:continue
                        seen.add(cell._tc);cells.append(' / '.join(blocks(cell)))
                    yield '\t'.join(cells)
    text='\n'.join(blocks(document)).strip()
    warnings=[]
    if document.inline_shapes:
        warnings.append('Word含嵌入图片；本次只读取文字和表格，图片内文字请另存为图片/PDF后OCR。')
    if not text:raise ValueError('Word未发现正文；图片试卷请另存为图片或PDF后导入。')
    return ImportBatch([InputPage(str(path),1,text,'Word',warnings=warnings)])


def _question_fields(value):
    result={}
    for key in ('question','materials','answer','reference','exam','year','category'):
        if key in value:
            text=str(value[key]) if key in ('year','category') and isinstance(value[key],(int,float)) else value[key]
            if not isinstance(text,str):raise ValueError(f'{key}须为文字。')
            result[key]=text.replace('\r\n','\n').replace('\r','\n')
    if 'question_type' in value:
        if value['question_type'] not in RUBRICS:raise ValueError('题型不受支持，请在表单中手动选择。')
        result['question_type']=value['question_type']
    if 'max_score' in value:
        score=finite_number(value['max_score'],'满分',1,1000)
        if int(score)!=score:raise ValueError('结构化满分须为整数，以匹配当前表单。')
        result['max_score']=int(score)
    if 'word_limit' in value:
        number=value['word_limit']
        if type(number) is not int or not 0<=number<=10000:raise ValueError('字数上限须为0—10000的整数。')
        result['word_limit']=number
    if 'rubric' in value:
        result['rubric']=[{key:row[key] for key in ('id','name','weight','description') if key in row}
                          for row in validate_rubric(value['rubric'])]
    if 'rubric_selection' in value:
        from .rubrics import validate_selection
        result['rubric_selection']=validate_selection(value['rubric_selection'])
    return copy.deepcopy(result)


def _json(path,text,max_pages=80):
    data=json.loads(text)
    candidates=data.get('questions',data.get('submission',data)) if isinstance(data,dict) else data
    records=candidates if isinstance(candidates,list) else [candidates]
    if not any(isinstance(row,dict) and ('question' in row or 'materials' in row or 'submission' in row) for row in records):
        return ImportBatch([InputPage(str(path),1,text,'JSON文本',warnings=['未识别为题目结构，按纯文字预览；请手动选择需要导入的内容。'])])
    if len(records)>80:raise ValueError('题库超过80条，请分批导入。')
    batch=ImportBatch(attempted_pages=min(len(records),max_pages))
    if len(records)>max_pages:batch.errors.append(f'{path.name}：本次最多80页/条，其余记录未读取，请分批。')
    for i,record in enumerate(records[:max_pages],1):
        try:
            if not isinstance(record,dict):raise ValueError('题目条目须为对象。')
            record=record.get('submission',record)
            if not isinstance(record,dict) or not ('question' in record or 'materials' in record):raise ValueError('条目缺少题干或材料字段。')
            fields=_question_fields(record)
            body='\n\n'.join(label+'\n'+fields[key] for key,label in [('question','【题干】'),('materials','【材料】'),('answer','【原答】'),('reference','【参考】')] if fields.get(key))
            if not body.strip():raise ValueError('题目没有可用文字。')
            batch.pages.append(InputPage(str(path),i,body,'题目JSON',fields=fields))
        except (ValueError,TypeError,OverflowError) as exc:batch.errors.append(f'{path.name} 第{i}条：{exc}')
    return batch


def read_inputs(paths,progress=None,cancel=None,force_ocr=False,page_spec='',ocr_options=None,retain_failed=False):
    check_cancel(cancel)
    paths=list(paths)
    if not paths or len(paths)>20:raise ValueError('每次请选择1—20个文件。')
    result=ImportBatch();characters=0;attempted=0;image_bytes=0
    for i,value in enumerate(paths,1):
        check_cancel(cancel)
        path=Path(value)
        if attempted>=MAX_BATCH_PAGES or characters>=MAX_TEXT:
            result.errors.append('本次已达到80页或25万字符，其余文件未读取，请分批。');break
        _notify(progress,f'读取文件 {i}/{len(paths)}：{path.name}')
        try:
            if not path.is_file():raise ValueError('文件不存在或不是普通文件。')
            if path.stat().st_size>MAX_FILE_BYTES:raise ValueError('单个文件不能超过25MB。')
            suffix=path.suffix.lower()
            if suffix=='.doc':raise ValueError('旧版.doc请先用Word/WPS另存为.docx或PDF。')
            if suffix not in SUPPORTED_SUFFIXES:raise ValueError('不支持此文件类型。')
            if suffix=='.pdf':batch=_pdf(path,progress,cancel,force_ocr,page_spec,
                                      max_pages=MAX_BATCH_PAGES-attempted,max_text=MAX_TEXT-characters,
                                      ocr_options=ocr_options,retain_failed=retain_failed,
                                      max_image_bytes=MAX_BATCH_IMAGE_BYTES-image_bytes)
            elif suffix=='.docx':batch=_docx(path)
            elif suffix in ('.txt','.md','.json'):
                text=_text_bytes(path.read_bytes())
                if not text:raise ValueError('文件没有正文。')
                batch=_json(path,text,max_pages=MAX_BATCH_PAGES-attempted) if suffix=='.json' else ImportBatch([InputPage(str(path),1,text,'文本')])
            else:batch=_images(path,progress,cancel,max_pages=MAX_BATCH_PAGES-attempted,max_text=MAX_TEXT-characters,
                              ocr_options=ocr_options,retain_failed=retain_failed,
                              max_image_bytes=MAX_BATCH_IMAGE_BYTES-image_bytes)
            attempted+=batch.attempted_pages or len(batch.pages)
            result.attempted_pages=attempted
            result.errors.extend(batch.errors)
            for page in batch.pages:
                check_cancel(cancel)
                retained_size=len(page.source_image)+len(page.preview_png)
                if image_bytes+retained_size>MAX_BATCH_IMAGE_BYTES:
                    result.errors.append(f'{path.name} 第{page.number}页未加入：原图缓存最多96MB，请分批。')
                    continue
                if len(result.pages)>=MAX_BATCH_PAGES or characters+len(page.text)>MAX_TEXT:
                    result.errors.append(f'{path.name} 第{page.number}页未加入：本次最多80页、25万字符，请拆分。')
                    continue
                result.pages.append(page);characters+=len(page.text);image_bytes+=retained_size
        except CancelledError:raise
        except Exception as exc:
            result.errors.append(path.name+'：'+str(exc)[:500])
    check_cancel(cancel)
    return result
