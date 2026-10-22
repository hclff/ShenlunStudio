"""Native workbench shell with explicit task feedback and stable page routing."""
import sys
from pathlib import Path

from PySide6.QtCore import QSize,QTimer,Qt
from PySide6.QtGui import QAction,QKeySequence
from PySide6.QtWidgets import (QDialog,QDialogButtonBox,QHBoxLayout,QLabel,QListWidget,
    QListWidgetItem,QMainWindow,QProgressBar,QStackedWidget,QTextBrowser,QVBoxLayout,QWidget)

from .. import __version__
from ..service import AppService
from ..settings import load_config
from .assessment import AssessmentMixin
from .library import LibraryMixin
from .preferences import PreferencesMixin
from .backups import BackupMixin
from .practice import PracticeMixin
from .practice_deletion import PracticeDeletionMixin
from .widgets import STYLE,badge,button,ensure_fonts
from .icons import app_icon,icon
from .workers import Job
from .task_feedback import TaskFeedbackMixin


def guide_path():
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))
    return root / 'assets' / '使用说明.md'


class MainWindow(QMainWindow,TaskFeedbackMixin,PracticeDeletionMixin,PracticeMixin,AssessmentMixin,LibraryMixin,PreferencesMixin,BackupMixin):
    PAGE_NAMES=['开始批改','文章资料库','写作方法库','训练记录','设置','我的题库']

    def __init__(self,store,service_factory=None):
        super().__init__()
        ensure_fonts()
        self.store=store
        self.service_factory=service_factory or (lambda config,key:AppService(store,config,key))
        self.config=load_config(store)
        self.current_review=None
        self.workers=set()
        self._closing=False
        self.task_state='idle'
        self.setWindowTitle('申论研习 · 学习工作台')
        self.setWindowIcon(app_icon())
        self.resize(1360,900);self.setMinimumSize(1050,720)
        self.setStyleSheet(STYLE)
        root=QWidget();self.setCentralWidget(root)
        root_layout=QHBoxLayout(root);root_layout.setContentsMargins(0,0,0,0);root_layout.setSpacing(0)
        sidebar=QWidget();sidebar.setObjectName('sidebar');sidebar.setFixedWidth(218)
        side=QVBoxLayout(sidebar);side.setContentsMargins(14,24,14,16);side.setSpacing(10)
        brand_row=QHBoxLayout();brand_row.setContentsMargins(9,0,0,0);brand_row.setSpacing(10)
        mark=QLabel();mark.setPixmap(app_icon().pixmap(36,36));brand_row.addWidget(mark)
        brand_text=QVBoxLayout();brand_text.setSpacing(2)
        brand=QLabel('申论研习');brand.setObjectName('brand');brand_text.addWidget(brand)
        tagline=QLabel('读懂材料 · 写清道理');tagline.setObjectName('brand_tagline');brand_text.addWidget(tagline)
        brand_row.addLayout(brand_text);brand_row.addStretch();side.addLayout(brand_row)
        section=QLabel('研习空间');section.setObjectName('nav_section');side.addWidget(section)
        self.navigation=QListWidget();self.navigation.setObjectName('navigation');self.navigation.setIconSize(QSize(20,20))
        nav_icons=['edit','library','methods','history','settings','library']
        for i,name in enumerate(self.PAGE_NAMES):
            item=QListWidgetItem(icon(nav_icons[i],'#bdd2e4',22),name)
            item.setToolTip(name+'  Alt+'+str(i+1));self.navigation.addItem(item)
        side.addWidget(self.navigation,1)
        model_panel=QWidget();model_panel.setObjectName('sidebar_model')
        model_layout=QVBoxLayout(model_panel);model_layout.setContentsMargins(12,12,12,12);model_layout.setSpacing(9)
        self.connection_label=QLabel();self.connection_label.setObjectName('connection_label')
        self.connection_label.setTextFormat(Qt.TextFormat.PlainText);self.connection_label.setWordWrap(True)
        model_layout.addWidget(self.connection_label)
        setup=button('模型设置  →',lambda:self.navigation.setCurrentRow(4));setup.setProperty('role','quiet')
        setup.setStyleSheet('QPushButton{color:#80d3c5;background:transparent;border:0;text-align:left;padding:2px 0;}')
        model_layout.addWidget(setup);side.addWidget(model_panel)
        help_button=button('使用指南',self.show_help,name='sidebar_help');help_button.setIcon(icon('help','#a8bfd3',18));side.addWidget(help_button)
        footer=QLabel('本地研习  ·  v'+__version__);footer.setObjectName('sidebar_note');footer.setContentsMargins(12,0,0,0);side.addWidget(footer)
        root_layout.addWidget(sidebar)
        main=QWidget();main_layout=QVBoxLayout(main);main_layout.setContentsMargins(0,0,0,0);main_layout.setSpacing(0)
        top=QWidget();top.setObjectName('topbar');top.setFixedHeight(50)
        top_layout=QHBoxLayout(top);top_layout.setContentsMargins(26,0,24,0)
        self.breadcrumb=QLabel();self.breadcrumb.setObjectName('breadcrumb');top_layout.addWidget(self.breadcrumb);top_layout.addStretch()
        self.model_badge=badge('模型待配置','warning');top_layout.addWidget(self.model_badge)
        local=badge('资料保存在本机');top_layout.addWidget(local)
        guide=button('帮助',self.show_help);guide.setProperty('role','quiet');guide.setIcon(icon('help',size=16));top_layout.addWidget(guide)
        main_layout.addWidget(top)
        self.pages=QStackedWidget()
        for factory in (self.build_assessment,self.build_corpus,self.build_methods,self.build_history,self.build_settings,self.build_practice):
            self.pages.addWidget(factory())
        main_layout.addWidget(self.pages,1)
        main_layout.addWidget(self.build_task_feedback())
        self.busy=QProgressBar();self.busy.setTextVisible(False);self.busy.setRange(0,0);self.busy.setFixedHeight(3);self.busy.hide();main_layout.addWidget(self.busy)
        status=QWidget();status.setObjectName('statusbar');status.setMinimumHeight(40)
        status_layout=QHBoxLayout(status);status_layout.setContentsMargins(24,5,18,5);status_layout.setSpacing(10)
        self.status_symbol=QLabel('●');self.status_symbol.setObjectName('status_symbol');status_layout.addWidget(self.status_symbol)
        self.status_label=QLabel('准备就绪');self.status_label.setObjectName('status_label')
        self.status_label.setTextFormat(Qt.TextFormat.PlainText);self.status_label.setWordWrap(True);self.status_label.setMaximumHeight(48)
        status_layout.addWidget(self.status_label,1)
        self.global_cancel=button('取消当前任务',self.cancel_jobs);self.global_cancel.hide();status_layout.addWidget(self.global_cancel)
        main_layout.addWidget(status);root_layout.addWidget(main,1)
        self.navigation.currentRowChanged.connect(self.navigate_page)
        self.navigation.setCurrentRow(0)
        self.build_menus()
        # Evaluation jobs also join workers directly; keep method controls locked
        # for those jobs as well as jobs launched by start_job. Poll only the
        # worker set here: the refresh routines scan large lists, so running them
        # ten times a second made every click feel sluggish.
        self._last_busy_state=None
        self.teaching_busy_timer=QTimer(self)
        self.teaching_busy_timer.setInterval(100)
        self.teaching_busy_timer.timeout.connect(self._sync_busy_state)
        self.teaching_busy_timer.start()
        self.initialize_workspace()
        self.refresh_connection_label();self.refresh_corpus();self.refresh_methods();self.refresh_history();self.refresh_calibration();self.refresh_usage();self.refresh_questions()
        self.initialize_backup_schedule()

    def navigate_page(self,index):
        if 0<=index<self.pages.count():
            if hasattr(self,'autosave_timer') and not self.flush_workspace():
                self.navigation.blockSignals(True);self.navigation.setCurrentRow(self.pages.currentIndex());self.navigation.blockSignals(False)
                return
            self.pages.setCurrentIndex(index)
            self.breadcrumb.setText('研习工作台  /  '+self.PAGE_NAMES[index])

    def build_menus(self):
        practice=self.menuBar().addMenu('练习')
        self.import_action=QAction(icon('import'),'导入题目 / 材料…',self)
        self.import_action.setShortcut(QKeySequence('Ctrl+O'));self.import_action.triggered.connect(lambda:self.load_input_file('materials'))
        practice.addAction(self.import_action)
        self.save_action=QAction(icon('save'),'保存当前草稿',self)
        self.save_action.setShortcut(QKeySequence('Ctrl+S'));self.save_action.triggered.connect(lambda:self.save_draft());practice.addAction(self.save_action)
        practice.addAction('未完成任务…').triggered.connect(self.show_assessment_recovery)
        practice.addSeparator();quit_action=practice.addAction('退出');quit_action.triggered.connect(self.close)
        view=self.menuBar().addMenu('视图')
        for i,name in enumerate(self.PAGE_NAMES):
            action=QAction(name,self);action.setShortcut(QKeySequence('Alt+'+str(i+1)))
            action.triggered.connect(lambda checked=False,index=i:self.navigation.setCurrentRow(index));view.addAction(action)
            if i==4:self.settings_action=action
        help_menu=self.menuBar().addMenu('帮助')
        help_action=help_menu.addAction('使用指南');help_action.setShortcut(QKeySequence('F1'));help_action.triggered.connect(self.show_help)
        help_menu.addAction('关于申论研习').triggered.connect(self.show_about)

    def show_help(self):
        guide = guide_path()
        try:
            text = guide.read_text(encoding='utf-8')
        except OSError:
            text = ('# 使用指南暂不可用\n\n'
                    '未找到随软件分发的《使用说明.md》。\n\n'
                    '可先按以下步骤使用：\n\n'
                    '1. 在「设置」保存模型接口、模型名和密钥。\n'
                    '2. 在「训练批改」导入题目、材料和原答，核对后开始批改。\n'
                    '3. 在「训练记录」查看评分依据、修改稿和人工评分。\n'
                    '4. 「文章资料库」和「写作方法库」分别用于阅读积累与有据方法。\n'
                    '5. 「我的题库」保存题目和草稿。\n\n'
                    '快捷操作：Ctrl+O 导入，Ctrl+S 保存草稿，Alt+1—6 切换工作区，F1 使用指南。')
        self.markdown_dialog('使用指南', 'help_dialog', text)

    def show_about(self):
        self.information_dialog('关于申论研习','about_dialog',
            '<h2>申论研习</h2><p>学习工作台　v'+__version__+'</p><p>读懂材料 · 写清道理</p>'
            '<p>本地文章资料、题目识别、训练批改与有据可查的复盘。资料保存在本机；仅在主动批改或提炼时调用所配置的模型服务。</p>'
            '<p>API密钥由系统凭据库或当前会话保存。内置文章保留原出版方权利，依赖说明及许可证见软件目录。</p>')

    def information_dialog(self,title,name,html):
        dialog=QDialog(self);dialog.setWindowTitle(title);dialog.setObjectName(name);dialog.resize(650,570)
        layout=QVBoxLayout(dialog);layout.setContentsMargins(20,20,20,16)
        browser=QTextBrowser();browser.setObjectName(name+'_browser');browser.setOpenExternalLinks(False)
        browser.setHtml(html);layout.addWidget(browser,1)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);buttons.button(QDialogButtonBox.StandardButton.Close).setText('关闭')
        buttons.rejected.connect(dialog.reject);layout.addWidget(buttons);dialog.exec()

    def markdown_dialog(self,title,name,markdown):
        dialog=QDialog(self);dialog.setWindowTitle(title);dialog.setObjectName(name);dialog.resize(820,680)
        layout=QVBoxLayout(dialog);layout.setContentsMargins(20,20,20,16)
        browser=QTextBrowser();browser.setObjectName(name+'_browser');browser.setOpenExternalLinks(False)
        browser.document().setDefaultStyleSheet(
            'h1{font-size:24px;color:#152d45;margin:0 0 10px;}'
            'h2{font-size:18px;color:#1b3a52;margin:20px 0 8px;}'
            'h3{font-size:15px;color:#24536b;margin:14px 0 6px;}'
            'p,li{line-height:1.65;}'
            'code{background:#f1f5f9;}'
            'blockquote{color:#64748b;margin-left:0;padding-left:10px;}')
        browser.setMarkdown(markdown);layout.addWidget(browser,1)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);buttons.button(QDialogButtonBox.StandardButton.Close).setText('关闭')
        buttons.rejected.connect(dialog.reject);layout.addWidget(buttons);dialog.exec()

    def refresh_connection_label(self):
        if self.config.get('base_url') and self.config.get('model'):
            self.connection_label.setText('●  模型已配置\n' + self.config['model'] + '\n仅在调用时连接')
            self.model_badge.setText('模型已配置');self.model_badge.setProperty('tone','success')
        else:
            self.connection_label.setText('●  本地模式\n批改前请配置模型')
            self.model_badge.setText('模型待配置');self.model_badge.setProperty('tone','warning')
        self.model_badge.style().unpolish(self.model_badge);self.model_badge.style().polish(self.model_badge)

    def notify(self, message):
        self.status_label.setText(str(message)[:1200])
        self.status_label.setToolTip(str(message))
        self.set_task_state('busy' if self.workers else 'idle')

    def set_task_state(self,state):
        self.task_state=state
        self.status_symbol.setText({'busy':'◷','success':'✓','error':'!','cancelled':'○'}.get(state,'●'))
        for label in (self.status_label,self.status_symbol):
            label.setProperty('tone',state);label.style().unpolish(label);label.style().polish(label)

    def job_failed(self,job,message):
        job.failure_message=str(message)
        if job.cancel_event.is_set(): return
        error = getattr(job, 'error', None)
        self.remember_output_limit(getattr(job,'config_snapshot',self.config),
            getattr(error,'parameter',None), getattr(error,'upper_limit',None))
        retry = getattr(job,'retry_callback',None) if not getattr(job,'result_received',False) else None
        self.show_task_failure(getattr(job,'last_stage',job.task_title), message, retry)

    def job_result(self,job,callback,result):
        if self._closing or job.cancel_event.is_set():return
        job.result_received=True
        before=self.status_label.text()
        try:
            callback(result)
            if before==self.status_label.text():
                self.notify(job.task_title+'完成。')
            if not self.workers:self.set_task_state('success')
        except Exception as exc:
            self.job_failed(job,str(exc) or type(exc).__name__)

    def _sync_busy_state(self):
        busy=bool(self.workers)
        if busy==self._last_busy_state:return
        self._last_busy_state=busy
        self.refresh_teaching_busy()
        self.refresh_practice_delete_actions()

    def start_job(self, title, operation, on_result, *, retry=None, domains=None):
        if self.workers:
            self.notify('已有任务正在运行，请完成或取消后再开始。')
            return None
        job = Job(operation, self)
        job.task_title=title
        job.last_stage=title
        job.result_received=False
        job.retry_callback=retry
        # None keeps the historical "refresh everything" behaviour for any
        # caller that has not declared what it changes.
        job.domains=None if domains is None else tuple(domains)
        job.config_snapshot=dict(self.config)
        job.failure_message=''
        self.clear_task_feedback()
        self.workers.add(job)
        self._sync_busy_state()
        job.progress.connect(lambda message:self.job_progress(job,message))
        job.failed.connect(lambda message:self.job_failed(job,message))
        job.result.connect(lambda result:self.job_result(job,on_result,result))
        job.finished.connect(lambda: self.finish_job(job))
        self.assess_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.busy.show()
        self.global_cancel.setEnabled(True)
        self.global_cancel.show()
        self.notify(title + '…')
        job.start()
        return job

    def finish_job(self, job):
        self.workers.discard(job)
        self._sync_busy_state()
        cancelled = job.cancel_event.is_set()
        job.deleteLater()
        self.assess_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.busy.hide()
        self.global_cancel.hide()
        self.retry_task_button.setEnabled(self._retry_task is not None and not cancelled)
        # A cancelled extraction/import may already have durable checkpoints, so
        # declarations are honoured for cancellations as well.
        self.refresh_domains(getattr(job, 'domains', None))
        if cancelled:
            self.notify('任务已取消；此前已保存的记录和导入进度仍在本机。')
            self.set_task_state('cancelled')
        elif job.failure_message or not self.failure_panel.isHidden():
            self.notify(self.failure_summary.text())
            self.set_task_state('error')
        else:
            self.set_task_state('success')
        if self._closing and not self.workers:
            QTimer.singleShot(0, self.close)

    DOMAIN_REFRESHES = {'corpus': 'refresh_corpus_fresh', 'methods': 'refresh_methods',
                        'history': 'refresh_history', 'usage': 'refresh_usage',
                        'questions': 'refresh_questions', 'calibration': 'refresh_calibration'}

    def refresh_domains(self, domains=None):
        """Refresh only what a finished job could have changed.

        ``None`` means the caller did not declare a scope, which keeps the
        previous refresh-everything behaviour instead of risking stale views.
        """
        names = tuple(self.DOMAIN_REFRESHES) if domains is None else tuple(domains)
        for name in names:
            refresh = getattr(self, self.DOMAIN_REFRESHES[name], None)
            if refresh is not None:
                refresh()

    def cancel_jobs(self):
        for worker in self.workers:
            worker.cancel()
        self.notify('正在取消，请等待当前处理结束。')
        self.cancel_button.setEnabled(False)
        self.global_cancel.setEnabled(False)

    def closeEvent(self, event):
        if not self.workers and hasattr(self,'teaching_panel') and not self.teaching_panel.confirm_discard():
            self._closing=False
            event.ignore()
            return
        if hasattr(self,'autosave_timer') and not self.flush_workspace():
            self._closing=False
            event.ignore()
            return
        if self.workers:
            self._closing = True
            self.cancel_jobs()
            event.ignore()
        else:
            self._closing=True
            self.wait_for_backup()
            event.accept()
