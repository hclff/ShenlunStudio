"""Build a complete Windows portable folder, preserving dynamically linked Qt."""
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
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
               '--onedir', '--name', NAME, '--distpath', str(ROOT/'dist'),
               '--workpath', str(ROOT/'build'), '--specpath', str(ROOT/'build'),
               '--paths', str(ROOT), '--icon',str(app_icon_path),'--version-file',str(version_file),'--add-data', str(seed)+os.pathsep+'assets',
               '--add-data',str(ROOT/'assets'/'ui')+os.pathsep+'assets/ui',
               '--add-data',str(article_methods)+os.pathsep+'assets',
               '--hidden-import', 'keyring.backends.Windows', '--collect-submodules', 'keyring.backends']
    for module in ('PyQt5','PyQt6','PySide2','numpy','pandas','scipy','matplotlib','tkinter',
                   'IPython','notebook','sympy','torch','cv2','pytest'):
        command += ['--exclude-module', module]
    command.append(str(ROOT/'run_app.py'))
    log = ROOT / 'build-tmp' / 'pyinstaller.log'
    print('Building Windows folder; log: '+str(log), flush=True)
    with log.open('w', encoding='utf8') as output:
        subprocess.run(command, cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT, check=True)
    folder = ROOT/'dist'/NAME
    # Read-only installation directories must not require SQLite WAL sidecars.
    with sqlite3.connect(folder/'_internal'/'assets'/'seed.db') as bundled_db:
        bundled_db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        bundled_db.execute('PRAGMA journal_mode=DELETE')
    bundled_db.close()
    shutil.copy2(ROOT/'README.md', folder/'使用说明.md')
    shutil.copy2(ROOT/'assets'/'community_teaching_sources.md', folder/'GitHub社区方法来源与许可.md')
    article_handbook=ROOT.parent/'output'/'文章写法研习_2026-10-17'/'方法手册.md'
    if article_handbook.is_file():shutil.copy2(article_handbook,folder/'内置文章方法手册.md')
    teaching_sources=ROOT.parent/'方法研究'/'名师方法_2026-10-16'
    if teaching_sources.is_dir():
        shutil.copytree(teaching_sources,folder/'名师方法来源与边界',dirs_exist_ok=True)
    public_sources=ROOT.parent/'评测'/'真实样本_v0.7'
    if public_sources.is_dir():
        shutil.copytree(public_sources,folder/'公开作答来源索引',dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    evidence=folder/'验证资料'
    evidence.mkdir(exist_ok=True)
    for filename in (f'tests-v{__version__}.xml',f'ocr-benchmark-v{__version__}.json'):
        if (ROOT/'validation'/filename).is_file():shutil.copy2(ROOT/'validation'/filename,evidence/filename)
    source = folder/'source'
    for dirname in ('shenlun','scripts','tests'):
        shutil.copytree(ROOT/dirname, source/dirname, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for filename in ('run_app.py','pyproject.toml','requirements-build.txt','CONTRACTS.md','README.md'):
        shutil.copy2(ROOT/filename, source/filename)
    shutil.copytree(ROOT/'assets'/'ui',source/'assets'/'ui',dirs_exist_ok=True)
    shutil.copy2(article_methods,source/'assets'/'builtin_article_methods.json')
    shutil.copy2(ROOT/'assets'/'community_teaching_sources.md',source/'assets'/'community_teaching_sources.md')
    shutil.copytree(ROOT/'assets'/'community-licenses',source/'assets'/'community-licenses',dirs_exist_ok=True)
    licenses = folder/'THIRD_PARTY_LICENSES'
    licenses.mkdir(exist_ok=True)
    shutil.copytree(ROOT/'assets'/'community-licenses',licenses/'community-methods',dirs_exist_ok=True)
    packages = ('PyInstaller','PySide6','shiboken6','lxml','pypdf','keyring','pywin32-ctypes','jaraco.classes',
                'jaraco.context','jaraco.functools','more-itertools','packaging','python-docx','typing_extensions','Pillow')
    versions = {}
    for package in packages:
        try:
            dist = importlib.metadata.distribution(package)
        except importlib.metadata.PackageNotFoundError:
            continue
        versions[package] = dist.version
        target = licenses/package
        target.mkdir(exist_ok=True)
        (target/'METADATA.txt').write_text(dist.read_text('METADATA') or '', encoding='utf8')
        for item in dist.files or ():
            if any('license' in part.lower() or 'copying' in part.lower() or 'notice' in part.lower() for part in item.parts):
                path = Path(dist.locate_file(item))
                if path.is_file():
                    destination = target/Path(*item.parts[-3:])
                    destination.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(path,destination)
    # Conda distributions keep license texts outside the Python dist-info.
    cache = Path(sys.base_prefix)/'pkgs'
    if cache.is_dir():
        for pattern in ('pyside6-*','qtbase-*','qtsvg-*','qtwebengine-*','lxml-*','python-[0-9]*','libxml2-*','libxslt-*','openssl-*'):
            for package in cache.glob(pattern):
                path = package/'info'/'licenses'
                if path.is_dir():
                    shutil.copytree(path,licenses/('conda-'+package.name),dirs_exist_ok=True)
    (licenses/'README.txt').write_text(
        'This distribution includes Python, Qt/PySide6/Shiboken, lxml, pypdf, python-docx, Pillow and keyring.\n'
        'Qt/PySide6 are dynamically linked and distributed under LGPLv3 terms; see bundled license.\n'
        'Corresponding upstream source: https://download.qt.io/official_releases/QtForPython/pyside6/\n'
        'Qt source: https://download.qt.io/official_releases/qt/6.9/6.9.2/\n'
        'The adjacent source directory contains this application source and build instructions.\n'
        'Libraries remain separate and replaceable. Reverse engineering for debugging modifications is permitted.\n'
        'Publication article content retains its original publisher rights.\n',encoding='utf8')
    (folder/'build-info.json').write_text(json.dumps({'app':__version__,'python':sys.version,'dependencies':versions},indent=2),encoding='utf8')
    print('Built: '+str(folder/ (NAME+'.exe')), flush=True)


if __name__ == '__main__':
    main()
