"""Run source or packaged executable smoke with workspace-contained artifacts."""
import argparse
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--exe', type=Path)
    parser.add_argument('--output-dir', type=Path, default=Path('build-tmp/ui-smoke'))
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    command = [str(args.exe.resolve())] if args.exe else [sys.executable, str(project / 'run_app.py')]
    command += ['--data-dir', str(output / 'data'), '--smoke-test', '--screenshot', str(output / 'desktop.png'), '--smoke-report', str(output / 'report.json')]
    env = {**os.environ, 'QT_QPA_PLATFORM':os.environ.get('QT_QPA_PLATFORM', 'offscreen')}
    return subprocess.run(command, cwd=project, env=env, timeout=60, check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
