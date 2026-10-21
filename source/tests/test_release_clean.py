"""The installable folder may only contain the whitelisted release entries."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from stage_release import assert_release_clean, stage


def clean_release(tmp_path):
    folder = tmp_path / 'ShenlunStudio'
    (folder / '_internal' / 'assets').mkdir(parents=True)
    (folder / 'THIRD_PARTY_LICENSES').mkdir()
    (folder / 'THIRD_PARTY_LICENSES' / 'README.txt').write_text('licenses\n', encoding='utf8')
    (folder / 'ShenlunStudio.exe').write_bytes(b'MZ')
    (folder / '_internal' / 'assets' / 'seed.db').write_bytes(b'bundled corpus')
    (folder / '使用说明.md').write_text('使用说明\n', encoding='utf8')
    (folder / 'GitHub社区方法来源与许可.md').write_text('许可\n', encoding='utf8')
    (folder / 'build-info.json').write_text('{}', encoding='utf8')
    return folder


def source_root(tmp_path):
    root = tmp_path / 'source'
    (root / 'shenlun').mkdir(parents=True)
    (root / 'shenlun' / '__init__.py').write_text("__version__='9.9.9'\n", encoding='utf8')
    (root / 'shenlun' / 'storage.py').write_text('VALUE = 1\n', encoding='utf8')
    (root / 'run_app.py').write_text('print(1)\n', encoding='utf8')
    (root / 'assets').mkdir()
    (root / 'assets' / 'community_teaching_sources.md').write_text('许可\n', encoding='utf8')
    (root / 'README.md').write_text('使用说明\n', encoding='utf8')
    return root


def raw_build(tmp_path):
    raw = tmp_path / 'pyi-dist' / 'ShenlunStudio'
    (raw / '_internal' / 'assets').mkdir(parents=True)
    (raw / 'ShenlunStudio.exe').write_bytes(b'MZ')
    with sqlite3.connect(raw / '_internal' / 'assets' / 'seed.db') as conn:
        # The staged seed is opened as a real Store, so it needs the real shape.
        conn.execute('CREATE TABLE articles(id TEXT PRIMARY KEY,title TEXT NOT NULL,'
                     'journal TEXT NOT NULL,kind TEXT NOT NULL,status TEXT NOT NULL,'
                     'fingerprint TEXT NOT NULL,publication TEXT NOT NULL,'
                     'search_text TEXT NOT NULL,data TEXT NOT NULL)')
    return raw


def test_clean_release_reports_counts_and_passes(tmp_path):
    evidence = assert_release_clean(clean_release(tmp_path))
    assert evidence['clean'] is True
    assert evidence['python_files'] == 0
    assert evidence['files'] == 6


def test_planted_module_is_rejected(tmp_path):
    folder = clean_release(tmp_path)
    (folder / 'source').mkdir()
    (folder / 'source' / 'storage.py').write_text('secret = 1\n', encoding='utf8')
    with pytest.raises(RuntimeError) as error:
        assert_release_clean(folder)
    assert 'source/' in str(error.value)


def test_bytecode_anywhere_is_rejected(tmp_path):
    folder = clean_release(tmp_path)
    (folder / '_internal' / 'leftover.pyc').write_bytes(b'\0')
    with pytest.raises(RuntimeError) as error:
        assert_release_clean(folder)
    assert 'leftover.pyc' in str(error.value)


def test_test_and_audit_directories_are_rejected(tmp_path):
    folder = clean_release(tmp_path)
    (folder / 'validation-artifacts').mkdir()
    (folder / 'validation-artifacts' / 'shot.png').write_bytes(b'png')
    with pytest.raises(RuntimeError) as error:
        assert_release_clean(folder)
    assert 'validation-artifacts/' in str(error.value)


def test_only_the_bundled_corpus_may_ship(tmp_path):
    folder = clean_release(tmp_path)
    (folder / '验证资料').mkdir()
    (folder / '验证资料' / 'probe.db').write_bytes(b'data')
    with pytest.raises(RuntimeError) as error:
        assert_release_clean(folder)
    assert 'probe.db' in str(error.value)


def test_missing_required_entry_is_rejected(tmp_path):
    folder = clean_release(tmp_path)
    (folder / 'build-info.json').unlink()
    with pytest.raises(RuntimeError) as error:
        assert_release_clean(folder)
    assert 'build-info.json' in str(error.value)


def test_stage_drops_raw_leftovers_and_records_source_hashes(tmp_path):
    root, raw = source_root(tmp_path), raw_build(tmp_path)
    (raw / 'source').mkdir()
    (raw / 'source' / 'leaked.py').write_text('VALUE = 2\n', encoding='utf8')
    (raw / 'validation-artifacts').mkdir()
    (raw / 'validation-artifacts' / 'old.png').write_bytes(b'png')
    dest = stage(raw, tmp_path / 'dist' / 'ShenlunStudio', root=root, version='9.9.9')
    assert (dest / 'ShenlunStudio.exe').is_file()
    assert not (dest / 'source').exists()
    assert not (dest / 'validation-artifacts').exists()
    assert (dest / '使用说明.md').is_file()
    info = json.loads((dest / 'build-info.json').read_text(encoding='utf8'))
    assert info['app'] == '9.9.9'
    assert info['contains_application_source'] is False
    assert set(info['source_sha256']) == {'shenlun/__init__.py', 'shenlun/storage.py', 'run_app.py'}
    assert any('内置文章方法手册.md' in item for item in info['missing_optional_assets'])
    # The shipped manifest must not disclose build-machine paths.
    assert all('/' not in item and '\\' not in item for item in info['missing_optional_assets'])
    assert_release_clean(dest)


def test_stage_rejects_missing_required_documentation(tmp_path):
    root, raw = source_root(tmp_path), raw_build(tmp_path)
    (root / 'README.md').unlink()
    with pytest.raises(SystemExit) as error:
        stage(raw, tmp_path / 'dist' / 'ShenlunStudio', root=root, version='9.9.9')
    assert '使用说明.md' in str(error.value)


def test_stage_requires_packaged_executable(tmp_path):
    root = source_root(tmp_path)
    empty = tmp_path / 'pyi-dist' / 'ShenlunStudio'
    empty.mkdir(parents=True)
    with pytest.raises(SystemExit) as error:
        stage(empty, tmp_path / 'dist' / 'ShenlunStudio', root=root, version='9.9.9')
    assert 'ShenlunStudio.exe' in str(error.value)
