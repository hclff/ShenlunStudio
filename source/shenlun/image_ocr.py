"""Local, bounded image preparation; thumbnails never replace the retained source."""
import io
import math
from pathlib import Path
import tempfile

from PIL import Image,ImageFilter,ImageOps

from . import ocr
from .model import check_cancel

MAX_BYTES=25*1024*1024
MAX_PIXELS=24000000
MAX_SEGMENTS=12


def _clean(image):
    if image.width*image.height>MAX_PIXELS:raise ValueError('图片像素超过2400万，请先缩小或裁剪。')
    image=ImageOps.exif_transpose(image)
    if image.mode in ('RGBA','LA') or 'transparency' in image.info:
        rgba=image.convert('RGBA');background=Image.new('RGBA',rgba.size,'white')
        background.alpha_composite(rgba);image=background.convert('RGB')
    clean=Image.new('RGB',image.size,'white');clean.paste(image)
    return clean


def _decode(data):
    if not isinstance(data,bytes) or not 0<len(data)<=MAX_BYTES:raise ValueError('图片不存在或超过25MB。')
    try:
        with Image.open(io.BytesIO(data),formats=['PNG','JPEG','BMP','TIFF','WEBP']) as image:
            return _clean(image)
    except (Image.DecompressionBombError,OSError,SyntaxError) as exc:
        raise ValueError('图片无法解码或像素过大，请重新导出。') from exc


def _encoded(image,format='PNG',**kwargs):
    stream=io.BytesIO();image.save(stream,format,**kwargs);return stream.getvalue()


def _reference(image):
    data=_encoded(image)
    if len(data)>MAX_BYTES:data=_encoded(image,'JPEG',quality=90)
    if len(data)>MAX_BYTES:data=_encoded(image,'JPEG',quality=80)
    if len(data)>MAX_BYTES:raise ValueError('转换后的图片仍超过25MB，请先裁剪。')
    return data


def normalize_image(data):return _reference(_decode(data))


def normalize_pillow_image(image):return _reference(_clean(image))


def _transformed(image,rotation=0,crop=None):
    if type(rotation) is not int or rotation not in (0,90,180,270):raise ValueError('图片旋转只支持0/90/180/270度。')
    if rotation:image=image.rotate(-rotation,expand=True,fillcolor='white')
    if crop is not None:
        if (not isinstance(crop,(list,tuple)) or len(crop)!=4 or
                any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in crop)):
            raise ValueError('裁剪范围须为0—1的有效矩形。')
        left,top,right,bottom=crop
        if left>=right or top>=bottom:raise ValueError('裁剪范围不能为空。')
        box=(round(left*image.width),round(top*image.height),round(right*image.width),round(bottom*image.height))
        if box[0]>=box[2] or box[1]>=box[3]:raise ValueError('裁剪区域过小，请重新选择。')
        image=image.crop(box)
    return image


def transform_image(data,rotation=0,crop=None):return _reference(_transformed(_decode(data),rotation,crop))


def preview_png(data,max_edge=2560):
    if type(max_edge) is not int or not 64<=max_edge<=2560:raise ValueError('预览尺寸无效。')
    image=_decode(data);image.thumbnail((max_edge,max_edge),Image.Resampling.LANCZOS)
    return _encoded(image)


def _enhance(image,operations):
    image=ImageOps.grayscale(image)
    corners=[image.getpixel((x,y)) for x in (0,image.width//2,image.width-1) for y in (0,image.height-1)]
    if sum(corners)/len(corners)<95:
        image=ImageOps.invert(image);operations.append('深色背景反转（需核对）')
    operations.append('灰度适配（保留字形尺寸）')
    if image.width>2560:
        image=image.resize((2560,max(1,round(image.height*2560/image.width))),Image.Resampling.LANCZOS)
        operations.append('宽度适配本机引擎')
    return image


def _fallback(image,upscale):
    """Only retry an empty result; unconditional filtering worsens clean glyphs."""
    image=ImageOps.autocontrast(image,cutoff=.5)
    image=image.filter(ImageFilter.UnsharpMask(radius=1,percent=120,threshold=3))
    if upscale and max(image.size)<1600:
        factor=min(2,1600/max(image.size))
        image=image.resize((max(1,round(image.width*factor)),max(1,round(image.height*factor))),Image.Resampling.LANCZOS)
    return image


def _tile_text(result,top,owner_top,owner_bottom,tiled):
    if not tiled:return result['text']
    lines=result.get('lines')
    if not isinstance(lines,list) or not lines:return result['text']
    valid=[]
    for line in lines:
        if not isinstance(line,dict) or not isinstance(line.get('text'),str):continue
        y,height=line.get('y'),line.get('height')
        if type(y) not in (int,float) or type(height) not in (int,float) or not math.isfinite(y+height) or height<0:continue
        valid.append((line,top+y+height/2))
    if not valid:return result['text']
    return '\n'.join(line['text'] for line,center in valid if owner_top<=center<owner_bottom)


def recognize_image(data,mode='enhanced',rotation=0,crop=None,columns=1,progress=None,cancel=None):
    check_cancel(cancel)
    if mode not in ('standard','enhanced'):raise ValueError('请选择标准或增强识别。')
    if type(columns) is not int or columns not in (1,2):raise ValueError('栏数只支持1或2。')
    original=normalize_image(data);image=_transformed(_decode(original),rotation,crop)
    operations=[];warnings=[]
    if rotation:operations.append('顺时针旋转'+str(rotation)+'度')
    if crop is not None:operations.append('仅识别所选区域')
    if columns==2:operations.append('双栏：先左后右')
    images=[image] if columns==1 else [image.crop((0,0,image.width//2,image.height)),image.crop((image.width//2,0,image.width,image.height))]
    prepared=[]
    for part in images:
        if not part.width or not part.height:raise ValueError('图片太小，不能分栏。')
        if mode=='enhanced':part=_enhance(part,operations)
        else:
            part.thumbnail((2560,2560),Image.Resampling.LANCZOS)
        prepared.append(part)
    counts=[max(1,math.ceil(part.height/2280)) if mode=='enhanced' else 1 for part in prepared]
    if sum(counts)>MAX_SEGMENTS:raise ValueError('图片过长，增强识别超过12段；请先裁剪分区识别。')
    if sum(counts)>columns:operations.append('保留字形尺寸分段识别')
    texts=[];languages=[];deskew=None;calls=0
    with tempfile.TemporaryDirectory(prefix='shenlun-enhance-') as directory:
        path=Path(directory)/'segment.png'
        for part,count in zip(prepared,counts):
            for n in range(count):
                check_cancel(cancel)
                owner_top=n*part.height//count;owner_bottom=(n+1)*part.height//count
                top=max(0,owner_top-40);bottom=min(part.height,owner_bottom+40)
                tile=part.crop((0,top,part.width,bottom))
                if progress:progress(f'本机{("增强" if mode=="enhanced" else "标准")}识别：第{calls+1}/{sum(counts)}段…')
                tile.save(path,'PNG');result=ocr.recognize_png(path,cancel=cancel);calls+=1
                check_cancel(cancel)
                angle=result.get('angle')
                if mode=='enhanced' and not result['text'].strip():
                    check_cancel(cancel)
                    _fallback(tile,sum(counts)==1).save(path,'PNG')
                    if progress:progress('未读到文字，对当前段进行一次对比度与锐化重试…')
                    try:
                        calls+=1;fallback=ocr.recognize_png(path,cancel=cancel);check_cancel(cancel)
                        result=fallback;operations.append('空结果后对比度、锐化重试')
                    except ValueError:warnings.append('增强重试不可用，保留首轮空结果与原图。')
                elif (mode=='enhanced' and sum(counts)==1 and type(angle) in (int,float)
                        and math.isfinite(angle) and .8<=abs(angle)<=12):
                    check_cancel(cancel)
                    rotated=tile.rotate(angle,expand=True,fillcolor=255)
                    rotated.thumbnail((2560,2560),Image.Resampling.LANCZOS);rotated.save(path,'PNG')
                    if progress:progress('按检测角度进行一次轻微纠偏重试…')
                    try:
                        calls+=1;corrected=ocr.recognize_png(path,cancel=cancel);check_cancel(cancel)
                        if corrected['text'].strip():
                            result=corrected;deskew=angle;operations.append('按检测角度轻微纠偏（需核对）')
                        else:warnings.append('纠偏未读到文字，保留首轮结果。')
                    except (ValueError,OSError):warnings.append('纠偏重试失败，保留首轮文字与原图。')
                texts.append(_tile_text(result,top,owner_top,owner_bottom,count>1))
                languages.append(str(result.get('language','未知')))
    check_cancel(cancel)
    language=next(iter(languages),'未知')
    if not language.startswith('zh'):warnings.append('当前本机OCR语言为'+language+'，中文识别可能不完整。')
    warnings.append('识别结果请对照原图核对数字、专名与手写内容；本机引擎不提供逐字可信度。')
    text='\n'.join(piece.strip() for piece in texts if piece.strip())
    if not text:warnings.append('未识别出文字，可旋转、裁剪后重试或选择AI识别，原图仍保留。')
    return {'text':text,'language':language,'source_image':original,'preview_png':preview_png(original),
            'warnings':warnings,'info':{'mode':mode,'rotation':rotation,'crop':list(crop) if crop is not None else None,
                                      'columns':columns,'segments':sum(counts),'calls':calls,
                                      'operations':list(dict.fromkeys(operations)),'deskew_angle':deskew}}
