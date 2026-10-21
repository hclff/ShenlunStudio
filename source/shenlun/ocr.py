"""Local Windows OCR: trusted WinRT helper, data-only stdin, cancellable child."""
import base64
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from .model import check_cancel


_SCRIPT=r'''
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
    $null = [Windows.Storage.FileAccessMode, Windows.Storage, ContentType=WindowsRuntime]
    $null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
    $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
    $null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
    $null = [Windows.Graphics.Imaging.BitmapPixelFormat, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
    $null = [Windows.Graphics.Imaging.BitmapAlphaMode, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
    $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
    $null = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType=WindowsRuntime]
    $null = [Windows.Globalization.Language, Windows.Globalization, ContentType=WindowsRuntime]
    $script:asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1
    } | Select-Object -First 1)
    function Await-Result($Operation, $ResultType) {
        $method = $script:asTask.MakeGenericMethod($ResultType)
        $task = $method.Invoke($null, @($Operation))
        $task.Wait()
        return $task.Result
    }
    $payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('zh-Hans-CN'))
    if ($null -eq $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
    if ($null -eq $engine) { throw 'No OCR language is installed. Add a Chinese OCR language in Windows Settings.' }
    $file = Await-Result ([Windows.Storage.StorageFile]::GetFileFromPathAsync([string]$payload.path)) ([Windows.Storage.StorageFile])
    $stream = Await-Result ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    try {
        $decoder = Await-Result ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Await-Result ($decoder.GetSoftwareBitmapAsync([Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8, [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied)) ([Windows.Graphics.Imaging.SoftwareBitmap])
        try {
            if ($bitmap.PixelWidth -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension -or $bitmap.PixelHeight -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension) {
                throw 'Image dimensions exceed the Windows OCR limit.'
            }
            $result = Await-Result ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
            $lines = @($result.Lines | ForEach-Object { $_.Text })
            $details = @()
            foreach ($line in $result.Lines) {
                $left=[double]::PositiveInfinity; $top=[double]::PositiveInfinity; $right=0.0; $bottom=0.0
                foreach ($word in $line.Words) {
                    $r=$word.BoundingRect
                    $left=[Math]::Min($left,$r.X); $top=[Math]::Min($top,$r.Y)
                    $right=[Math]::Max($right,$r.X+$r.Width); $bottom=[Math]::Max($bottom,$r.Y+$r.Height)
                }
                if (-not [double]::IsInfinity($left)) {
                    $details += @{text=[string]$line.Text;x=$left;y=$top;width=($right-$left);height=($bottom-$top)}
                }
            }
            $angle=$null
            if ($null -ne $result.TextAngle) {
                try { $angle=[double]$result.TextAngle } catch {
                    try { $angle=[double]$result.TextAngle.Value } catch { $angle=$null }
                }
            }
            $output = @{ ok=$true; text=($lines -join "`n"); language=$engine.RecognizerLanguage.LanguageTag;lines=$details;angle=$angle }
        } finally { $bitmap.Dispose() }
    } finally { $stream.Dispose() }
    [Console]::Out.WriteLine(($output | ConvertTo-Json -Compress -Depth 4))
} catch {
    $failure = $_.Exception
    $messages = @()
    while ($null -ne $failure) {
        $messages += $failure.Message
        $failure = $failure.InnerException
    }
    [Console]::Out.WriteLine((@{ok=$false;error=($messages -join ' / ')} | ConvertTo-Json -Compress -Depth 3))
    exit 1
}
'''


def run_hidden_json(command,payload,cancel=None,timeout=45):
    """Run a trusted command only; user-controlled values stay in JSON stdin."""
    check_cancel(cancel)
    encoded=json.dumps(payload,ensure_ascii=False).encode('utf8')
    process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                             creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    deadline=time.monotonic()+timeout
    first=True
    try:
        while True:
            check_cancel(cancel)
            if time.monotonic()>=deadline:
                raise ValueError('本机OCR响应超时，请缩小图片或减少页数后重试。')
            try:
                out,err=process.communicate(input=encoded if first else None,timeout=.1)
                break
            except subprocess.TimeoutExpired:
                first=False
        check_cancel(cancel)
        if len(out)>2_000_000 or len(err)>200_000:
            raise ValueError('本机OCR响应过长，请缩小本次导入。')
        try:
            value=json.loads(out.decode('utf-8-sig').strip())
        except (UnicodeDecodeError,json.JSONDecodeError):
            raise ValueError('本机OCR未返回可用结果，请检查Windows OCR语言组件。') from None
        if not isinstance(value,dict) or process.returncode!=0 or value.get('ok') is False:
            message=str(value.get('error',''))[:350] if isinstance(value,dict) else ''
            raise ValueError('本机OCR不可用，请检查Windows中文OCR语言组件。'+(' '+message if message else ''))
        return value
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def recognize_png(path,cancel=None,timeout=45):
    if sys.platform!='win32':
        raise ValueError('本机OCR需要Windows 10/11及OCR语言组件。')
    path=Path(path).resolve()
    if not path.is_file() or path.stat().st_size>25*1024*1024:
        raise ValueError('OCR图片不存在或超过25MB。')
    powershell=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32'/'WindowsPowerShell'/'v1.0'/'powershell.exe'
    if not powershell.is_file():
        raise ValueError('未找到Windows PowerShell，本机OCR不可用。')
    script=base64.b64encode(_SCRIPT.encode('utf-16-le')).decode('ascii')
    value=run_hidden_json([str(powershell),'-NoLogo','-NoProfile','-NonInteractive','-EncodedCommand',script],
                          {'path':str(path)},cancel,timeout)
    text=value.get('text')
    if not isinstance(text,str):
        raise ValueError('OCR文字结果格式不正确。')
    result={'text':_clean_text(text),'language':str(value.get('language','未知'))}
    angle=value.get('angle')
    result['angle']=angle if type(angle) in (int,float) and math.isfinite(angle) and -180<=angle<=180 else None
    result['lines']=[]
    for line in value.get('lines',[]) or []:
        if not isinstance(line,dict) or not isinstance(line.get('text'),str):continue
        if not all(type(line.get(key)) in (int,float) and math.isfinite(line[key]) and line[key]>=0 for key in ('x','y','width','height')):continue
        result['lines'].append({**{key:line[key] for key in ('x','y','width','height')},'text':_clean_text(line['text'])})
    return result


def _clean_text(text):
    # Windows OCR separates Chinese words by spaces; retain Latin word spacing.
    text=re.sub(r'(?<=[\u3400-\u9fff]) +(?=[\u3400-\u9fff])','',text)
    text=re.sub(r' +(?=[，。！？；：、）】])','',text)
    text=re.sub(r'(?<=[（【]) +','',text)
    return text.strip()
