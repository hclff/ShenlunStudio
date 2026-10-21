"""Offline frozen verification of enhanced OCR and a synthetic loopback image API."""
import base64
import io
import json
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
import threading
import uuid


def probe_ocr(work,screenshot=None):
    from PIL import Image
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont,QImage,QPainter
    from .image_ocr import recognize_image,transform_image
    from .intake import _png,read_inputs
    from .model import DEFAULT_CONFIG
    from .service import AppService
    from .storage import Store
    from .vision_ocr import estimate_image
    from .ui.widgets import ensure_fonts
    from PySide6.QtWidgets import QApplication
    from .ui.image_tools import ImageToolsDialog,CandidateDialog

    work=Path(work)/('ocr-probe-'+uuid.uuid4().hex);work.mkdir(parents=True,exist_ok=True)
    ensure_fonts()
    image=QImage(1000,6200,QImage.Format.Format_RGB32);image.fill(Qt.GlobalColor.white)
    painter=QPainter(image);painter.setPen(Qt.GlobalColor.black)
    font=QFont('Microsoft YaHei');font.setPixelSize(30);painter.setFont(font)
    for index in range(35):painter.drawText(30,70+index*170,f'第{index+1}项：社区保留人工窗口，提供电话服务。')
    painter.drawText(30,6100,'末页核验：长图分段识别完成。');painter.end()
    data=_png(image,max_edge=None);path=work/'long.png';path.write_bytes(data)
    batch=read_inputs([path],ocr_options={'mode':'enhanced'},retain_failed=True)
    if batch.errors or len(batch.pages)!=1:raise RuntimeError('增强长图读取失败：'+str(batch.errors))
    page=batch.pages[0]
    with Image.open(io.BytesIO(page.source_image)) as source:source_size=source.size
    with Image.open(io.BytesIO(page.preview_png)) as preview:preview_size=preview.size
    if source_size!=(1000,6200) or max(preview_size)>2560 or page.ocr_info['segments']<3 or '末页核验' not in page.text:
        raise RuntimeError('增强长图丢失源图或末段文字')
    if screenshot:
        tools=ImageToolsDialog(page.source_image,crop=(0,.85,1,1))
        candidate=CandidateDialog('人工修订：保留人工窗口和电话服务。',
                                  {'text':page.text,'warnings':['发布验收：右侧为本机OCR候选；仅演示核对，不写入练习。']},'当前合成长图整页')
        try:
            for name,dialog in (('ocr-region',tools),('ocr-candidate',candidate)):
                dialog.show();QApplication.processEvents()
                target=screenshot.with_name(screenshot.stem+'-'+name+'.png')
                if not dialog.grab().save(str(target)):raise OSError('无法保存图片识别界面截图')
                dialog.reject()
        finally:tools.reject();candidate.reject()

    # Rotation precedes crop; fake API receives the upper half of a clockwise-
    # rotated red/green source, which must contain the red half only.
    source=Image.new('RGB',(800,400),'green');source.paste('red',(0,0,400,400))
    stream=io.BytesIO();source.save(stream,'PNG')
    region=transform_image(stream.getvalue(),rotation=90,crop=[0,0,1,.5])
    captured=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            length=int(self.headers.get('Content-Length',0))
            if not 0<length<=6*1024*1024:self.send_error(413);return
            body=json.loads(self.rfile.read(length));content=body['messages'][1]['content']
            image_items=[item for item in content if item['type']=='image_url']
            encoded=image_items[0]['image_url']['url'].split(',',1)[1]
            with Image.open(io.BytesIO(base64.b64decode(encoded))) as selected:
                captured.append({'size':list(selected.size),'pixel':list(selected.convert('RGB').getpixel((100,100))),
                                 'metadata':bool(selected.getexif()),'images':len(image_items),
                                 'authorized':self.headers.get('Authorization')=='Bearer release-fixture-key'})
            response={'model':'loopback-image-fixture','choices':[{'finish_reason':'stop',
                'message':{'content':json.dumps({'text':'仅选中区域（测试接口返回）','uncertainties':['合成接口，无语义质量结论']},ensure_ascii=False)}}],
                'usage':{'prompt_tokens':12,'completion_tokens':8,'total_tokens':20}}
            response=json.dumps(response,ensure_ascii=False).encode('utf8')
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(response)));self.end_headers();self.wfile.write(response)
    server=HTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.05},daemon=True);thread.start()
    try:
        config={**DEFAULT_CONFIG,'base_url':f'http://127.0.0.1:{server.server_port}/v1','model':'loopback-image-fixture','timeout':5}
        estimate=estimate_image(region,config)
        if captured:raise RuntimeError('估算阶段出现图像请求')
        store=Store(work/'synthetic-usage.db')
        result=AppService(store,config,'release-fixture-key').recognize_image(region)
        if len(captured)!=1 or captured[0]!={'size':[400,400],'pixel':[255,0,0],'metadata':False,'images':1,'authorized':True}:
            raise RuntimeError('当前区域图像传输失败：'+str(captured))
        if result['text']!='仅选中区域（测试接口返回）' or store.list_reviews() or store.usage_summary()['total_tokens']!=20:
            raise RuntimeError('图像候选/用量隔离异常')
    finally:server.shutdown();server.server_close();thread.join(2)
    return {'ok':True,'native_segments':page.ocr_info['segments'],'retained_source':list(source_size),
            'preview':list(preview_size),'last_line_present':True,'region_rotation_crop':captured[0],
            'loopback_image_calls':len(captured),'estimated_total_tokens':estimate['estimated_total_tokens'],
            'recorded_tokens':result['usage']['total_tokens'],'cloud_model_calls':0,'assessment_writes':0}
