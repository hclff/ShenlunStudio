"""Build a complete Windows portable folder, preserving dynamically linked Qt.

PyInstaller writes its raw output into a scratch directory; ``stage_release``
then assembles ``dist/ShenlunStudio`` from an explicit whitelist, so the
installer never receives application source, tests or build leftovers.
"""
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from shenlun import __version__
NAME = 'ShenlunStudio'


def main():
    seed = ROOT / 'assets' / 'seed.db'
    if not seed.is_file():
        raise SystemExit('缺少 assets/seed.db')
    article_methods = ROOT / 'assets' / 'builtin_article_methods.json'
    if not article_methods.is_file():
        raise SystemExit('缺少内置文章方法 assets/builtin_article_methods.json')
    with sqlite3.connect(seed) as db:
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise SystemExit('语料数据库完整性检查失败')
        db.execute('PRAGMA journal_mode=DELETE')
    db.close()
    temp = ROOT / 'build-tmp'
    temp.mkdir(exist_ok=True)
    scratch = temp / 'pyi-dist'
    if scratch.exists():
        shutil.rmtree(scratch)
    env = {**os.environ, 'TEMP':str(temp), 'TMP':str(temp)}
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication
    from shenlun.ui.icons import app_icon
    app=QApplication.instance() or QApplication([])
    app_icon_path=temp/'ShenlunStudio.ico'
    if not app_icon().pixmap(256,256).save(str(app_icon_path),'ICO'):
        raise SystemExit('应用图标生成失败')
    version_numbers=tuple(int(part) for part in __version__.split('.'))+(0,)
    version_file=temp/'windows-version.txt'
    version_file.write_text(
        'VSVersionInfo(ffi=FixedFileInfo(filevers='+repr(version_numbers)+',prodvers='+repr(version_numbers)+
        ',mask=0x3f,flags=0,OS=0x40004,fileType=0x1,subtype=0,date=(0,0)),kids=['
        'StringFileInfo([StringTable("080404B0",['
        'StringStruct("CompanyName","申论研习"),StringStruct("FileDescription","申论研习学习工作台"),'
        'StringStruct("FileVersion",'+repr(__version__)+'),StringStruct("ProductVersion",'+repr(__version__)+'),'
        'StringStruct("ProductName","申论研习"),StringStruct("InternalName","ShenlunStudio"),'
        'StringStruct("OriginalFilename","ShenlunStudio.exe")])]),'
        'VarFileInfo([VarStruct("Translation",[2052,1200])])])',encoding='utf8')
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed',
               '--onedir', '--name', NAME, '--distpath', str(scratch),
               '--workpath', str(ROOT/'build'), '--specpath', str(ROOT/'build'),
               '--paths', str(ROOT), '--icon',str(app_icon_path),'--version-file',str(version_file),'--add-data', str(seed)+os.pathsep+'assets',
               '--add-data',str(ROOT/'assets'/'ui')+os.pathsep+'assets/ui',
               '--add-data',str(article_methods)+os.pathsep+'assets',
               '--hidden-import', 'keyring.backends.Windows', '--collect-submodules', 'keyring.backends',
               # Migration steps are imported by name at runtime; without this the
               # frozen application starts without them and fails on first open.
               '--collect-submodules', 'shenlun.migrations']
    for module in ('PyQt5','PyQt6','PySide2','numpy','pandas','scipy','matplotlib','tkinter',
                   'IPython','notebook','sympy','torch','cv2','pytest'):
        command += ['--exclude-module', module]
    command.append(str(ROOT/'run_app.py'))
    log = ROOT / 'build-tmp' / 'pyinstaller.log'
    print('Building Windows folder; log: '+str(log), flush=True)
    with log.open('w', encoding='utf8') as output:
        subprocess.run(command, cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT, check=True)
    dist = ROOT / 'dist'
    if dist.exists():
        shutil.rmtree(dist)
    from stage_release import stage
    folder = stage(scratch / NAME, dist / NAME, root=ROOT, version=__version__)
    print('Built: ' + str(folder / (NAME + '.exe')), flush=True)


if __name__ == '__main__':
    main()
