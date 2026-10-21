"""Image fidelity, local OCR bounds, cancellation and reversible rescue."""
import importlib
import importlib.util
import io
import threading

from PIL import Image
import pytest

from shenlun.model import CancelledError


def module():
    assert importlib.util.find_spec('shenlun.image_ocr'), 'bounded image enhancement module required'
    return importlib.import_module('shenlun.image_ocr')


def png(image):
    stream=io.BytesIO();image.save(stream,'PNG');return stream.getvalue()


def test_normalize_keeps_pixels_orientation_white_alpha_and_strips_metadata():
    m=module();image=Image.new('RGBA',(40,20),(255,0,0,0));image.putpixel((1,1),(0,0,0,255))
    result=Image.open(io.BytesIO(m.normalize_image(png(image))))
    assert result.size==(40,20) and result.mode=='RGB' and result.getpixel((0,0))==(255,255,255)
    photo=Image.new('RGB',(40,20),'white');exif=Image.Exif();exif[274]=6;exif[270]='private-camera-metadata'
    stream=io.BytesIO();photo.save(stream,'JPEG',exif=exif)
    clean=Image.open(io.BytesIO(m.normalize_image(stream.getvalue())))
    assert clean.size==(20,40) and not clean.getexif() and not clean.info.get('exif')


def test_rotation_then_normalized_crop_matches_visible_region_and_never_modifies_source():
    m=module();image=Image.new('RGB',(80,40),'white')
    for x in range(40):
        for y in range(40):image.putpixel((x,y),(255,0,0))
    original=png(image)
    transformed=Image.open(io.BytesIO(m.transform_image(original,rotation=90,crop=[0,0,1,.5])))
    assert transformed.size==(40,40) and transformed.getpixel((20,20))==(255,0,0)
    assert Image.open(io.BytesIO(original)).size==(80,40)
    for crop in ([0,0,0,1],[-.1,0,1,1],[0,0,True,1],[0,0,float('nan'),1]):
        with pytest.raises(ValueError):m.transform_image(original,crop=crop)
    with pytest.raises(ValueError):m.transform_image(original,rotation=45)


def test_oversize_or_non_image_is_rejected_before_ocr():
    m=module()
    with pytest.raises(ValueError):m.normalize_image(b'not an image')
    with pytest.raises(ValueError):m.normalize_image(b'x'*(25*1024*1024+1))
    too_large=png(Image.new('1',(6000,5000),1))
    with pytest.raises(ValueError,match='像素'):m.normalize_image(too_large)


def test_enhanced_long_strip_retains_glyph_scale_and_full_source_with_bounded_tiles(monkeypatch):
    m=module();from shenlun import ocr
    calls=[]
    def recognize(path,**kwargs):
        with Image.open(path) as image:calls.append(image.size)
        return {'text':f'分段{len(calls)}','language':'zh-Hans-CN'}
    monkeypatch.setattr(ocr,'recognize_png',recognize)
    original=png(Image.new('RGB',(600,6500),'white'))
    result=m.recognize_image(original,mode='enhanced')
    assert len(calls)>=3 and max(height for _,height in calls)<=2400
    assert min(width for width,_ in calls)>=600
    assert all(f'分段{i}' in result['text'] for i in range(1,len(calls)+1))
    assert Image.open(io.BytesIO(result['source_image'])).size==(600,6500)
    assert max(Image.open(io.BytesIO(result['preview_png'])).size)<=2560
    assert result['info']['segments']==len(calls)
    calls.clear();m.recognize_image(original,mode='standard')
    assert len(calls)==1 and max(calls[0])<=2560


def test_two_columns_run_left_then_right_and_keep_boundaries(monkeypatch):
    m=module();from shenlun import ocr
    calls=[]
    def recognize(path,**kwargs):
        calls.append(Image.open(path).size)
        return {'text':'左栏' if len(calls)==1 else '右栏','language':'zh-Hans-CN'}
    monkeypatch.setattr(ocr,'recognize_png',recognize)
    result=m.recognize_image(png(Image.new('RGB',(1800,1000),'white')),columns=2)
    assert len(calls)==2 and result['text'].index('左栏')<result['text'].index('右栏')
    assert result['info']['columns']==2


def test_clean_glyphs_keep_scale_and_filters_only_retry_empty_result(monkeypatch):
    m=module();from shenlun import ocr
    sizes=[]
    def recognize(path,**kwargs):
        with Image.open(path) as image:sizes.append(image.size)
        return {'text':'' if len(sizes)==1 else '重试文字','language':'zh-Hans-CN'}
    monkeypatch.setattr(ocr,'recognize_png',recognize)
    result=m.recognize_image(png(Image.new('RGB',(1000,700),'white')))
    assert sizes[0]==(1000,700) and len(sizes)==2 and result['text']=='重试文字'
    assert '空结果后对比度、锐化重试' in result['info']['operations']


def test_geometry_drops_only_overlap_rows_not_legitimate_repeated_lines(monkeypatch):
    m=module();from shenlun import ocr
    calls=[]
    def recognize(path,**kwargs):
        calls.append(1)
        with Image.open(path) as image:height=image.height
        return {'text':'首行\n相同合法行\n末行','language':'zh-Hans-CN','lines':[
            {'text':'首行','x':10,'y':0,'width':200,'height':10},
            {'text':'相同合法行','x':10,'y':height//2,'width':200,'height':10},
            {'text':'末行','x':10,'y':height-10,'width':200,'height':10}]}
    monkeypatch.setattr(ocr,'recognize_png',recognize)
    result=m.recognize_image(png(Image.new('RGB',(1000,4500),'white')))
    assert result['text'].count('相同合法行')==len(calls)
    assert result['text'].count('首行')==1 and result['text'].count('末行')==1


def test_mild_detected_angle_gets_at_most_one_retry(monkeypatch):
    m=module();from shenlun import ocr
    calls=[]
    def recognize(*args,**kwargs):
        calls.append(1)
        return {'text':'首轮' if len(calls)==1 else '纠偏结果','language':'zh-Hans-CN','angle':3.0}
    monkeypatch.setattr(ocr,'recognize_png',recognize)
    result=m.recognize_image(png(Image.new('RGB',(1200,800),'white')))
    assert len(calls)==2 and result['text']=='纠偏结果'
    assert result['info']['deskew_angle']==3.0


def test_failed_optional_deskew_keeps_useful_first_pass(monkeypatch):
    m=module();from shenlun import ocr
    calls=[]
    def recognize(*args,**kwargs):
        calls.append(1)
        if len(calls)>1:raise ValueError('纠偏识别不可用')
        return {'text':'应保留的首轮文字','language':'zh-Hans-CN','angle':3.0}
    monkeypatch.setattr(ocr,'recognize_png',recognize)
    result=m.recognize_image(png(Image.new('RGB',(1200,800),'white')))
    assert len(calls)==2 and result['text']=='应保留的首轮文字' and result['warnings']


def test_cancelled_local_failure_never_becomes_rescue_success(monkeypatch):
    module();from shenlun import intake,ocr
    event=threading.Event()
    def failed(*args,**kwargs):event.set();raise ValueError('组件不可用')
    monkeypatch.setattr(ocr,'recognize_png',failed)
    with pytest.raises(CancelledError):
        intake.read_image_bytes(png(Image.new('RGB',(400,300),'white')),retain_failed=True,cancel=event)


def test_cancel_between_tiles_never_returns_partial_text(monkeypatch):
    m=module();from shenlun import ocr
    event=threading.Event()
    def recognize(*args,**kwargs):event.set();return {'text':'不应成功','language':'zh-Hans-CN'}
    monkeypatch.setattr(ocr,'recognize_png',recognize)
    with pytest.raises(CancelledError):m.recognize_image(png(Image.new('RGB',(600,6000),'white')),cancel=event)


def test_failed_local_page_can_be_retained_for_manual_or_ai_rescue(monkeypatch,tmp_path):
    module();from shenlun import intake,ocr
    def failed(*args,**kwargs):raise ValueError('组件不可用')
    monkeypatch.setattr(ocr,'recognize_png',failed)
    source=tmp_path/'page.png';source.write_bytes(png(Image.new('RGB',(400,300),'white')))
    legacy=intake.read_inputs([source]);assert not legacy.pages and legacy.errors
    retained=intake.read_inputs([source],ocr_options={'mode':'enhanced'},retain_failed=True)
    assert len(retained.pages)==1 and not retained.pages[0].text
    assert retained.pages[0].source_image and retained.pages[0].preview_png and retained.pages[0].warnings


def test_intake_keeps_full_source_separate_from_thumbnail(monkeypatch,tmp_path):
    module();from shenlun import intake,ocr
    monkeypatch.setattr(ocr,'recognize_png',lambda *args,**kwargs:{'text':'实际文字','language':'zh-Hans-CN'})
    source=tmp_path/'long.png';source.write_bytes(png(Image.new('RGB',(600,5000),'white')))
    page=intake.read_inputs([source],ocr_options={'mode':'enhanced'}) .pages[0]
    assert Image.open(io.BytesIO(page.source_image)).height==5000
    assert max(Image.open(io.BytesIO(page.preview_png)).size)<=2560
    assert page.ocr_info['segments']>=3


def test_retained_image_cache_limit_applies_across_files(monkeypatch,tmp_path):
    module();from shenlun import intake
    monkeypatch.setattr(intake,'MAX_BATCH_IMAGE_BYTES',450)
    def page(data,source,number,*args,**kwargs):
        return intake.InputPage(source,number,'文字','本机OCR',b'p'*100,source_image=b's'*100)
    monkeypatch.setattr(intake,'_ocr_page',page)
    paths=[]
    for index in range(3):
        path=tmp_path/f'{index}.png';path.write_bytes(png(Image.new('RGB',(10,10),'white')));paths.append(path)
    batch=intake.read_inputs(paths,retain_failed=True)
    assert len(batch.pages)==2 and sum(len(p.source_image)+len(p.preview_png) for p in batch.pages)==400
    assert batch.errors and '96MB' in batch.errors[-1]
