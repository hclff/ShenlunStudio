"""Set the application version in every file that carries it.

Version strings live in the package, the packaging metadata, the installer
project and its validator. Editing them by hand has produced mismatched releases
before, so this script rewrites all of them from one argument and leaves line
endings untouched.

    python scripts/bump_version.py 0.9.6
    python scripts/bump_version.py 1.0.0 --dry-run
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# (path relative to the repository root, pattern with one version group, template)
TARGETS = (
    ('source/shenlun/__init__.py', r"""__version__ = ['"]([0-9][0-9.]*)['"]""", '__version__ = "{version}"'),
    ('source/pyproject.toml', r'(?m)^version = "([0-9][0-9.]*)"$', 'version = "{version}"'),
    ('source/README.md', r'(?m)^# 申论研习 ([0-9][0-9.]*)$', '# 申论研习 {version}'),
    ('installer/ShenlunStudio.iss', r'#define MyAppVersion "([0-9][0-9.]*)"',
     '#define MyAppVersion "{version}"'),
    ('installer/ShenlunStudio.iss', r'(?m)^VersionInfoVersion=([0-9][0-9.]*)$',
     'VersionInfoVersion={quad}'),
    ('installer/ShenlunStudio.iss', r'#define MySetupBaseName "ShenlunStudio-([0-9][0-9.]*)-Setup"',
     '#define MySetupBaseName "ShenlunStudio-{version}-Setup"'),
    ('installer/build-installer.ps1', r"\$setupName = 'ShenlunStudio-([0-9][0-9.]*)-Setup\.exe'",
     "$setupName = 'ShenlunStudio-{version}-Setup.exe'"),
)


def quad(version):
    parts = [int(part) for part in version.split('.')]
    while len(parts) < 4:
        parts.append(0)
    return '.'.join(str(part) for part in parts[:4])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('version', help='new version, for example 1.0.0')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    version = args.version.strip()
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise SystemExit('版本号应为 X.Y.Z：' + version)

    changed = 0
    for relative, pattern, template in TARGETS:
        path = ROOT / relative
        if not path.is_file():
            raise SystemExit('缺少文件：' + relative)
        original = path.read_bytes()
        text = original.decode('utf-8')
        replacement = template.format(version=version, quad=quad(version))
        updated, count = re.subn(pattern, replacement, text, count=1)
        if not count:
            raise SystemExit('在 ' + relative + ' 中未找到版本占位：' + pattern)
        if updated == text:
            print('未变化', relative)
            continue
        changed += 1
        print(('将修改 ' if args.dry_run else '已修改 ') + relative + ' -> ' + version)
        if not args.dry_run:
            path.write_bytes(updated.encode('utf-8'))
    print('版本 ' + version + '，涉及 ' + str(changed) + ' 处文件。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
