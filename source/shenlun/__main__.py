"""Identical source and frozen application entry, including offline smoke mode."""
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path


def bundled_seed():
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    return root / 'assets' / 'seed.db'


def bundled_article_methods():
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    return root / 'assets' / 'builtin_article_methods.json'


def create_store(data_dir, seed=None, method_catalog=None):
    from .storage import Store
    directory = Path(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / 'studio.db'
    existed = target.exists()
    source = Path(seed) if seed is not None else bundled_seed()
    if not target.exists() and source.is_file():
        # SQLite backup includes any live WAL and never touches a prior user DB.
        with sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True) as original:
            with sqlite3.connect(target) as destination:
                original.backup(destination)
    store = Store(target)
    catalog = Path(method_catalog) if method_catalog is not None else bundled_article_methods() if seed is None else None
    if catalog is not None:
        from .builtin_articles import install_bundled_article_methods
        install_bundled_article_methods(store, catalog, source, backup_existing=existed)
    return store


def probe_intake(window, paths, screenshot=None):
    """Offline release check, confined by main() to an explicit smoke data directory."""
    from PySide6.QtWidgets import QApplication
    from .intake import read_inputs
    from .ui.intake import IntakePreviewDialog, TARGETS
    before = {key:getattr(window,key+'_edit').toPlainText() for _,key in TARGETS}
    batch = read_inputs(paths)
    dialog = IntakePreviewDialog(batch,window)
    dialog.show()
    QApplication.processEvents()
    if screenshot:
        screenshot.parent.mkdir(parents=True,exist_ok=True)
        if not dialog.grab().save(str(screenshot)):
            raise OSError('无法保存导入预览截图')
    dialog.reject()
    after = {key:getattr(window,key+'_edit').toPlainText() for _,key in TARGETS}
    return {'pages':len(batch.pages), 'errors':batch.errors,
            'methods':[page.method for page in batch.pages],
            'text':'\n'.join(page.text for page in batch.pages),
            'cancel_preserved_editors':before==after}


def probe_workbench(window,screenshot):
    """Render every real page at normal/minimum size; restore the active view."""
    from PySide6.QtWidgets import QApplication
    original_size=window.size();original_page=window.navigation.currentRow()
    result={'pages':[],'minimum_pages':[]}
    try:
        for key,size in (('pages',(1360,900)),('minimum_pages',(1050,720))):
            window.resize(*size)
            for index in range(window.pages.count()):
                window.navigation.setCurrentRow(index);QApplication.processEvents()
                target=screenshot.with_name(screenshot.stem+'-'+key+'-'+str(index+1)+'.png')
                target.parent.mkdir(parents=True,exist_ok=True)
                if not window.grab().save(str(target)):raise OSError('无法保存工作区截图')
                result[key].append({'page':index,'width':window.width(),'height':window.height(),'screenshot':target.name})
    finally:
        window.resize(original_size);window.navigation.setCurrentRow(original_page);QApplication.processEvents()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='申论研习桌面应用')
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--smoke-test', action='store_true')
    parser.add_argument('--screenshot', type=Path)
    parser.add_argument('--smoke-report', type=Path)
    parser.add_argument('--smoke-import', type=Path, nargs='+')
    args = parser.parse_args(argv)
    if args.smoke_test and args.data_dir is None:
        parser.error('--smoke-test 必须显式指定 --data-dir，以保护已有用户数据')
    if args.smoke_import and not args.smoke_test:
        parser.error('--smoke-import 只能与 --smoke-test 和显式 --data-dir 一起使用')
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication
    from .ui import MainWindow
    data_dir = args.data_dir or Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local'))) / 'ShenlunStudio'
    store = create_store(data_dir)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName('申论研习')
    app.setOrganizationName('ShenlunStudio')
    app.setFont(QFont('Microsoft YaHei UI', 10))
    window = MainWindow(store)
    window.show()
    smoke_state = {'ok':False}
    if args.smoke_test:
        def capture():
            try:
                if args.screenshot:
                    args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                    if not window.grab().save(str(args.screenshot)):
                        raise OSError('无法保存界面截图')
                report = {'ok':True, 'title':window.windowTitle(), 'visible':window.isVisible(),
                    'width':window.width(), 'height':window.height(), 'pages':window.pages.count(),
                    'stats':store.stats(), 'reviews':len(store.list_reviews()), 'workers':len(window.workers),
                    'data_dir':str(data_dir.resolve()), 'screenshot':str(args.screenshot.resolve()) if args.screenshot else None,
                    'model_configured':bool(window.config.get('base_url') and window.config.get('model'))}
                from .service import AppService
                builtin_cards = [card for card in store.list_cards() if card.get('builtin_catalog')]
                for card in builtin_cards:
                    AppService(store, {}, '')._validate_card(card)
                report['builtin_articles'] = {
                    'cards':len(builtin_cards), 'refs':sum(len(card['refs']) for card in builtin_cards),
                    'enabled':sum(bool(card['enabled']) for card in builtin_cards),
                    'article_tab_is_default':window.methods_tabs.currentIndex()==1,
                    'all_evidence_valid':True}
                if args.smoke_import:
                    preview = args.screenshot.with_name(args.screenshot.stem+'-intake.png') if args.screenshot else None
                    report['intake'] = probe_intake(window,args.smoke_import,preview)
                    if report['intake']['errors'] or not report['intake']['pages'] or not report['intake']['cancel_preserved_editors']:
                        raise RuntimeError('导入烟雾检查失败：'+str(report['intake']))
                if args.screenshot:
                    report['workbench']=probe_workbench(window,args.screenshot)
                from .release_probe import probe_practice
                report['practice']=probe_practice(data_dir.parent)
                from .ocr_release_probe import probe_ocr
                report['image_ocr']=probe_ocr(data_dir.parent,args.screenshot)
                from .evaluation_release_probe import probe_evaluation
                report['evaluation']=probe_evaluation(data_dir.parent,args.screenshot)
                from .teaching_release_probe import probe_teaching
                report['teaching']=probe_teaching(data_dir.parent,args.screenshot)
                from .method_library_release_probe import probe_method_library
                report['method_library']=probe_method_library(data_dir.parent,args.screenshot)
                from .method_workflow_release_probe import probe_method_workflow
                report['method_workflow']=probe_method_workflow(data_dir.parent,args.screenshot)
                from .practice_deletion_release_probe import probe_practice_deletion
                report['practice_deletion']=probe_practice_deletion(data_dir.parent,args.screenshot)
                from .learning_release_probe import probe_learning
                report['learning']=probe_learning(data_dir.parent,args.screenshot)
                from .reliability_release_probe import probe_reliability
                report['reliability']=probe_reliability(data_dir.parent,args.screenshot)
                from .workflow_release_probe import probe_workflow
                report['workflow']=probe_workflow(data_dir.parent,args.screenshot)
                smoke_state['ok'] = True
            except Exception as exc:
                report = {'ok':False, 'error':str(exc)}
            if args.smoke_report:
                args.smoke_report.parent.mkdir(parents=True, exist_ok=True)
                args.smoke_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(report, ensure_ascii=False), flush=True)
            window.wait_for_backup()
            window.close()
            app.quit()
        QTimer.singleShot(600, capture)
    result = app.exec()
    return (0 if smoke_state['ok'] else 1) if args.smoke_test else result


if __name__ == '__main__':
    raise SystemExit(main())
