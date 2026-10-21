"""Assemble the installable release folder from an explicit whitelist.

PyInstaller writes its raw output to a scratch directory; this module copies only
the entries named below into a freshly recreated ``dist/ShenlunStudio``. Nothing
else can reach the installer, so application source, tests, build leftovers and
audit artifacts stay out by construction rather than by deletion.

``assert_release_clean`` re-checks a finished folder and is shared by the build
script, the release verifier and the packaging tests.
"""
import hashlib
import importlib.metadata
import json
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shenlun.storage import upgrade_article_columns

NAME = 'ShenlunStudio'
EXE = NAME + '.exe'

# Directories that must never appear at the top of an installed release.
FORBIDDEN_DIRS = ('source', 'tests', 'scripts', 'validation', 'validation-artifacts',
                  'build', 'build-tmp', '.git', '__pycache__')
# Textual or build-only files that would expose source code.
FORBIDDEN_SUFFIXES = ('.py', '.pyc', '.pyo', '.spec', '.pdb', '.pyi')
# Databases are user data or probe residue; only the bundled corpus may ship.
ALLOWED_DATABASES = ('_internal/assets/seed.db',)

LICENSE_PACKAGES = ('PyInstaller', 'PySide6', 'shiboken6', 'lxml', 'pypdf', 'keyring',
                    'pywin32-ctypes', 'jaraco.classes', 'jaraco.context',
                    'jaraco.functools', 'more-itertools', 'packaging', 'python-docx',
                    'typing_extensions', 'Pillow')

LICENSE_NOTICE = (
    'This distribution includes Python, Qt/PySide6/Shiboken, lxml, pypdf, python-docx, '
    'Pillow and keyring.\n'
    'Qt/PySide6 are dynamically linked and distributed under LGPLv3 terms; see bundled license.\n'
    'Corresponding upstream source: https://download.qt.io/official_releases/QtForPython/pyside6/\n'
    'Qt source: https://download.qt.io/official_releases/qt/6.9/6.9.2/\n'
    'This package does not contain the application source code; the executable bundles '
    'compiled bytecode only.\n'
    'Libraries remain separate and replaceable. Reverse engineering for debugging '
    'modifications is permitted.\n'
    'Publication article content retains its original publisher rights.\n'
)


def required_paths():
    """Entries every installable release must contain."""
    return ('ShenlunStudio.exe', '_internal', 'THIRD_PARTY_LICENSES/README.txt',
            '使用说明.md', 'GitHub社区方法来源与许可.md', 'build-info.json')


def _hash_file(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _is_license_text(item):
    """License documents only: some packages keep a ``licenses/`` code module."""
    parts = [part.lower() for part in item.parts]
    if '__pycache__' in parts:
        return False
    if not any('license' in part or 'copying' in part or 'notice' in part for part in parts):
        return False
    return Path(item).suffix.lower() not in ('.py', '.pyc', '.pyo', '.pyd', '.pyi', '.so', '.dll')


def _copy_entry(source, target, absent, label):
    if source.is_dir():
        shutil.copytree(source, target, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo'))
        return True
    if source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return True
    # Keep the shipped manifest free of build-machine paths; show it on console only.
    absent.append(label)
    print('缺少发布素材：' + label + '（' + str(source) + '）', flush=True)
    return False


def source_hashes(root):
    """Content hashes of the application modules this build came from."""
    files = sorted((root / 'shenlun').rglob('*.py')) + [root / 'run_app.py']
    return {path.relative_to(root).as_posix(): _hash_file(path)
            for path in files if path.is_file()}


def collect_licenses(folder, root):
    """Copy third-party licences; licenses are documentation, never source."""
    licenses = folder / 'THIRD_PARTY_LICENSES'
    licenses.mkdir(parents=True, exist_ok=True)
    community = root / 'assets' / 'community-licenses'
    if community.is_dir():
        shutil.copytree(community, licenses / 'community-methods', dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo'))
    versions = {}
    for package in LICENSE_PACKAGES:
        try:
            distribution = importlib.metadata.distribution(package)
        except importlib.metadata.PackageNotFoundError:
            continue
        versions[package] = distribution.version
        target = licenses / package
        target.mkdir(exist_ok=True)
        (target / 'METADATA.txt').write_text(distribution.read_text('METADATA') or '',
                                             encoding='utf8')
        for item in distribution.files or ():
            if not _is_license_text(item):
                continue
            path = Path(distribution.locate_file(item))
            if path.is_file():
                destination = target / Path(*item.parts[-3:])
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
    # Conda distributions keep license texts outside the Python dist-info.
    cache = Path(sys.base_prefix) / 'pkgs'
    if cache.is_dir():
        for pattern in ('pyside6-*', 'qtbase-*', 'qtsvg-*', 'qtwebengine-*', 'lxml-*',
                        'python-[0-9]*', 'libxml2-*', 'libxslt-*', 'openssl-*'):
            for package in cache.glob(pattern):
                path = package / 'info' / 'licenses'
                if path.is_dir():
                    shutil.copytree(path, licenses / ('conda-' + package.name),
                                    dirs_exist_ok=True)
    (licenses / 'README.txt').write_text(LICENSE_NOTICE, encoding='utf8')
    return versions


def write_build_info(folder, *, version, dependencies, missing, root):
    """Record provenance without shipping the source itself."""
    info = {
        'app': version,
        'python': sys.version,
        'pyside6': dependencies.get('PySide6'),
        'pyinstaller': dependencies.get('PyInstaller'),
        'dependencies': dependencies,
        'source_sha256': source_hashes(root),
        'build_command': ('python -m PyInstaller --onedir --windowed '
                          '--distpath <scratch> --add-data assets/seed.db:assets '
                          '--add-data assets/ui:assets/ui run_app.py'),
        'missing_optional_assets': list(missing),
        'contains_application_source': False,
    }
    (folder / 'build-info.json').write_text(json.dumps(info, ensure_ascii=False, indent=2),
                                            encoding='utf8')
    return info


def assert_release_clean(folder):
    """Return evidence for a clean release folder, or raise with every offender."""
    folder = Path(folder)
    if not (folder / EXE).is_file():
        raise RuntimeError('发行目录缺少 ' + EXE + '：' + str(folder))
    missing = [name for name in required_paths() if not (folder / name).exists()]
    if missing:
        raise RuntimeError('发行目录缺少必需内容：' + '、'.join(missing))
    problems = []
    for name in FORBIDDEN_DIRS:
        if (folder / name).exists():
            problems.append(name + '/')
    source_files = []
    databases = []
    for path in folder.rglob('*'):
        if not path.is_file():
            continue
        relative = path.relative_to(folder).as_posix()
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            source_files.append(relative)
        if path.suffix.lower() in ('.db', '.sqlite', '.sqlite3'):
            if relative not in ALLOWED_DATABASES:
                databases.append(relative)
    if source_files:
        problems.append('源码/字节码文件：' + '、'.join(source_files[:10]))
    if databases:
        problems.append('非种子数据库：' + '、'.join(databases[:10]))
    if problems:
        raise RuntimeError('发行目录包含不应交付的内容：' + '；'.join(problems))
    files = [path for path in folder.rglob('*') if path.is_file()]
    return {'clean': True, 'files': len(files),
            'bytes': sum(path.stat().st_size for path in files),
            'python_files': 0, 'forbidden_dirs': 0}


def stage(raw_folder, dest, root, version):
    """Rebuild ``dest`` from the whitelist and the raw PyInstaller output."""
    raw_folder, dest, root = Path(raw_folder), Path(dest), Path(root)
    for name in (EXE, '_internal'):
        if not (raw_folder / name).exists():
            raise SystemExit('PyInstaller 输出缺少 ' + name + '：' + str(raw_folder))
    missing = []
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    shutil.copy2(raw_folder / EXE, dest / EXE)
    shutil.copytree(raw_folder / '_internal', dest / '_internal', dirs_exist_ok=True)
    # Read-only installation directories must not require SQLite WAL sidecars.
    bundled = sqlite3.connect(dest / '_internal' / 'assets' / 'seed.db')
    try:
        bundled.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        bundled.execute('PRAGMA journal_mode=DELETE')
        # Bake the derived article columns so a fresh install does not pay the
        # one-time upgrade while the first window is opening.
        upgrade_article_columns(bundled)
        bundled.commit()
    finally:
        bundled.close()
    # Required user-facing documentation: a missing entry aborts the build.
    _copy_entry(root / 'README.md', dest / '使用说明.md', missing, '使用说明.md')
    _copy_entry(root / 'assets' / 'community_teaching_sources.md',
                dest / 'GitHub社区方法来源与许可.md', missing, 'GitHub社区方法来源与许可.md')
    if missing:
        raise SystemExit('缺少必需发布内容：' + '、'.join(missing))
    # Optional publisher material: reported when absent instead of failing silently.
    optional = []
    _copy_entry(root.parent / 'output' / '文章写法研习_2026-10-17' / '方法手册.md',
                dest / '内置文章方法手册.md', optional, '内置文章方法手册.md')
    _copy_entry(root.parent / '方法研究' / '名师方法_2026-10-16' / '名师方法来源与边界',
                dest / '名师方法来源与边界', optional, '名师方法来源与边界')
    _copy_entry(root.parent / '评测' / '真实样本_v0.7', dest / '公开作答来源索引',
                optional, '公开作答来源索引')
    evidence = dest / '验证资料'
    evidence.mkdir(exist_ok=True)
    for filename in ('tests-v' + version + '.xml', 'ocr-benchmark-v' + version + '.json'):
        if (root / 'validation' / filename).is_file():
            shutil.copy2(root / 'validation' / filename, evidence / filename)
    versions = collect_licenses(dest, root)
    write_build_info(dest, version=version, dependencies=versions, missing=optional, root=root)
    evidence_summary = assert_release_clean(dest)
    print('发行内容校验通过：' + json.dumps(evidence_summary, ensure_ascii=True), flush=True)
    return dest
