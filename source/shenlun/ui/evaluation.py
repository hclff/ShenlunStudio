"""Local evaluation collections and consented, cancellable isolated batch runs."""
from collections import Counter
from copy import deepcopy
import json
import math
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QSpinBox, QSplitter,
    QTabWidget, QVBoxLayout, QWidget)

from ..evaluation import (ORIGINS, PARTITIONS, blind_eval_rows, blind_eval_html,
    case_submission, plan_evaluation, run_evaluation, evaluation_report, evaluation_report_html)
from ..practice import score_value
from .widgets import button, row
from .workers import Job
from .method_comparison import MethodComparisonUiMixin

RUN_STATUS={'pending':'待运行','running':'运行中','completed':'已完成','failed':'失败','cancelled':'已取消'}


def readonly(text=''):
    edit = QPlainTextEdit(); edit.setReadOnly(True); edit.setPlainText(text)
    return edit


def choice(mapping):
    combo = QComboBox()
    for key, label in mapping.items(): combo.addItem(label, key)
    return combo


class JsonPreviewDialog(QDialog):
    """Complete read-only preview. Closing this dialog performs no persistence."""
    def __init__(self, records, filename, parent=None, labels=False):
        super().__init__(parent); self.setWindowTitle('核对人工评分导入' if labels else '核对评测作答导入')
        self.resize(850, 600); layout = QVBoxLayout(self)
        notice = QLabel(f'文件：{filename}\n共 {len(records)} 条；确认后整批校验并新增。任何一条失败均不写入。' +
            ('\n空白人工分会跳过；独立性为评阅者声明。' if labels else '\n外部人工已确认要点不会导入为可信状态。来源未确认及摘录不可冒充完整真题答卷。'))
        notice.setTextFormat(Qt.TextFormat.PlainText); notice.setWordWrap(True); layout.addWidget(notice)
        self.preview = readonly(json.dumps(records, ensure_ascii=False, indent=2)); layout.addWidget(self.preview, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('确认整批导入')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)


class CaseDialog(QDialog):
    """Structured local entry; no changes reach Store until the parent accepts it."""
    def __init__(self, submission=None, parent=None):
        super().__init__(parent); self.setWindowTitle('新增评测作答 · 本机核对')
        self.resize(850, 670); layout = QVBoxLayout(self)
        notice = QLabel('保存为独立评测快照，不改动练习编辑区。请如实注明来源；人工分数另行独立记录。')
        notice.setWordWrap(True); layout.addWidget(notice)
        value = deepcopy(submission or {})
        self._base_submission = deepcopy(value)
        form = QFormLayout(); self.origin = choice(ORIGINS); self.origin.setCurrentIndex(self.origin.findData('unknown'))
        self.source = QLineEdit(); self.source.setMaxLength(3000); self.source.setPlaceholderText('原始公开链接、作者自述、授权情况；合成样本请明确注明')
        self.partition = choice(PARTITIONS); self.complete = QCheckBox('原答为完整作答，且已核对题干与全部材料')
        form.addRow('来源类别', self.origin); form.addRow('来源说明 / URL', self.source)
        form.addRow('题目分组', self.partition); form.addRow(self.complete)
        controls = QWidget(); controls_form = QFormLayout(controls); controls_form.setContentsMargins(0,0,0,0)
        self.question_type = QComboBox(); self.question_type.addItems(['归纳概括','综合分析','提出对策','应用写作','文章写作'])
        self.question_type.setCurrentText(value.get('question_type','归纳概括'))
        self.maximum = QDoubleSpinBox(); self.maximum.setRange(.01,1000); self.maximum.setValue(value.get('max_score',20))
        self.word_limit = QSpinBox(); self.word_limit.setRange(0,100000); self.word_limit.setValue(value.get('word_limit',0))
        controls_form.addRow('题型',self.question_type); controls_form.addRow('满分',self.maximum); controls_form.addRow('字数上限',self.word_limit)
        form.addRow(controls); layout.addLayout(form)
        tabs = QTabWidget(); self.fields = {}
        for key, label in (('question','题干'),('materials','全部材料'),('answer','完整原答 / 摘录'),('reference','参考答案（可空）')):
            edit = QPlainTextEdit(); edit.setPlainText(value.get(key,'')); self.fields[key] = edit; tabs.addTab(edit,label)
        layout.addWidget(tabs,1)
        self.error = QLabel(); self.error.setWordWrap(True); self.error.setTextFormat(Qt.TextFormat.PlainText); layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('保存为评测快照')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def record(self):
        raw = {**deepcopy(self._base_submission), **{key: edit.toPlainText() for key, edit in self.fields.items()}}
        raw.update(question_type=self.question_type.currentText(),max_score=self.maximum.value(),word_limit=self.word_limit.value())
        return dict(submission=case_submission(raw),origin=self.origin.currentData(),source=self.source.text(),
                    partition=self.partition.currentData(),answer_complete=self.complete.isChecked())

    def accept(self):
        try:
            self.record()
        except ValueError as exc:
            self.error.setText(str(exc)); return
        super().accept()


class EvaluationDialog(MethodComparisonUiMixin,QDialog):
    def __init__(self, parent, current_submission=None):
        super().__init__(parent)
        self.host = parent; self.store = parent.store
        self.config = deepcopy(parent.config)
        self.current_submission = deepcopy(current_submission)
        self._jobs = set(); self._close_pending = False; self._busy = False
        self._label_version = 0; self._run_id = None; self._reviewing = False
        self._label_case_id = None; self._label_baseline = ('','','',False)
        self._label_drafts = {}
        self.setWindowTitle('小规模评测 · 作答、盲评与重复评分')
        self.resize(1000, 680); self.setMinimumSize(900, 600)
        layout = QVBoxLayout(self)
        notice = QLabel('本机评测与普通训练记录分开。公开作者自答、GPT 对照及机构示范分别统计；缺少独立人工评分时不计算准确性。小样本尚未校准。')
        notice.setWordWrap(True); layout.addWidget(notice)
        self.set_combo = QComboBox(); self.create_button = button('新建评测集', self.create_set)
        layout.addLayout(row(QLabel('评测集'),self.set_combo,self.create_button))
        self.tabs = QTabWidget(); layout.addWidget(self.tabs,1)
        self.build_cases(); self.build_labels(); self.build_runs(); self.build_method_comparisons()
        self.message = QLabel('尚无评测集时，请先新建，再添加或导入有来源的作答。')
        self.message.setTextFormat(Qt.TextFormat.PlainText); self.message.setWordWrap(True); layout.addWidget(self.message)
        self.close_button = button('关闭',self.reject); layout.addLayout(row(self.close_button))
        self.set_combo.currentIndexChanged.connect(self.refresh_cases)
        self.refresh_sets()
        self.host.installEventFilter(self)

    def build_cases(self):
        page = QWidget(); layout = QVBoxLayout(page); split = QSplitter()
        self.case_list = QListWidget(); self.case_list.setMinimumWidth(230); self.case_list.setWordWrap(True)
        self.case_detail = readonly('先添加作答。原答、材料与来源会完整显示在这里。')
        split.addWidget(self.case_list); split.addWidget(self.case_detail); split.setSizes([300,650]); layout.addWidget(split,1)
        self.current_button = button('添加当前原答…',self.add_current)
        self.manual_button = button('手动粘贴作答…',self.add_manual)
        self.import_button = button('预览导入 JSON…',lambda:self.import_file())
        layout.addLayout(row(self.current_button,self.manual_button,self.import_button))
        self.partition_combo = choice(PARTITIONS); self.partition_button = button('将整题分入此组',self.set_partition)
        layout.addLayout(row(QLabel('同一题的全部作答一起分组'),self.partition_combo,self.partition_button))
        self.case_counts = QLabel(); self.case_counts.setWordWrap(True); layout.addWidget(self.case_counts)
        self.case_list.currentItemChanged.connect(self.show_case)
        self.tabs.addTab(page,'作答与来源')

    def build_labels(self):
        page = QWidget(); layout = QVBoxLayout(page)
        self.label_case = QLabel('先在“作答与来源”选择作答。'); self.label_case.setTextFormat(Qt.TextFormat.PlainText)
        self.label_case.setWordWrap(True); layout.addWidget(self.label_case)
        form = QFormLayout(); self.label_score = QLineEdit(); self.label_score.setPlaceholderText('留空表示尚未评分；0 为有效分数')
        self.label_score.setMaxLength(40); self.label_reviewer = QLineEdit(); self.label_reviewer.setMaxLength(100)
        self.label_reason = QPlainTextEdit(); self.label_reason.setPlaceholderText('记录评分理由及反馈来源')
        self.label_reason.setMaximumHeight(100)
        self.label_independent = QCheckBox('我声明该评分在未查看 AI 分数及预设排序时独立完成（默认未勾选）')
        form.addRow('人工分数',self.label_score); form.addRow('评阅人',self.label_reviewer); form.addRow('理由 / 来源',self.label_reason)
        layout.addLayout(form); layout.addWidget(self.label_independent)
        self.save_label_button = button('保存人工评分新版本',self.save_label)
        self.reload_label_button = button('重新载入人工评分…',self.reload_label)
        layout.addLayout(row(self.save_label_button,self.reload_label_button))
        self.label_history = readonly('尚无人工评分。'); layout.addWidget(self.label_history,1)
        draft_notice=QLabel('未保存输入在本窗口内按作答保留；切题或切换评测集不会丢失，也不会自动写入人工评分。')
        draft_notice.setWordWrap(True);layout.addWidget(draft_notice)
        self.blind_json_button = button('导出盲评 JSON…',lambda:self.export_blind())
        self.blind_html_button = button('导出盲评 HTML…',lambda:self.export_blind(html=True))
        self.import_labels_button = button('预览导入人工评分…',lambda:self.import_file(labels=True))
        layout.addLayout(row(self.blind_json_button,self.blind_html_button,self.import_labels_button))
        self.tabs.addTab(page,'独立人工评分')

    def build_runs(self):
        page = QWidget(); layout = QVBoxLayout(page)
        self.repeats = QSpinBox(); self.repeats.setRange(1,5); self.repeats.setValue(3)
        self.run_button = button('估算并运行勾选作答…',self.start_run,True)
        self.cancel_button = button('取消本轮',self.cancel_run); self.cancel_button.hide()
        layout.addLayout(row(QLabel('同一答案重复次数'),self.repeats,self.run_button,self.cancel_button))
        self.run_list = QListWidget(); self.run_list.setMaximumHeight(105); layout.addWidget(self.run_list)
        self.run_detail = readonly('尚无轮次。勾选完整作答后，可先估算，再明确确认上传。'); layout.addWidget(self.run_detail,1)
        self.report_json_button = button('导出本轮报告 JSON…',lambda:self.export_report())
        self.report_html_button = button('导出本轮报告 HTML…',lambda:self.export_report(html=True))
        self.refresh_button = button('刷新已保存结果',self.refresh_runs)
        layout.addLayout(row(self.report_json_button,self.report_html_button,self.refresh_button))
        self.snapshot_button = button('检查完整冻结快照…', self.inspect_run)
        layout.addWidget(self.snapshot_button)
        self.run_list.currentItemChanged.connect(self.show_run)
        self.tabs.addTab(page,'重复评分与报告')

    def set_id(self): return self.set_combo.currentData()

    def current_case(self):
        item = self.case_list.currentItem()
        return deepcopy(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def say(self, text): self.message.setText(str(text))

    def refresh_sets(self, selected=None):
        self.set_combo.blockSignals(True); self.set_combo.clear()
        for dataset in self.store.list_eval_sets(): self.set_combo.addItem(dataset['name']+f'（{dataset["case_count"]} 份）',dataset['id'])
        if selected: self.set_combo.setCurrentIndex(max(0,self.set_combo.findData(selected)))
        self.set_combo.blockSignals(False); self.refresh_cases()

    def refresh_cases(self, *args):
        if self._busy: return
        self.refresh_method_comparisons()
        previous = self.current_case(); previous_id = previous['id'] if previous else None
        self.case_list.blockSignals(True); self.case_list.clear()
        cases = self.store.list_eval_cases(self.set_id()) if self.set_id() else []
        for case in cases:
            item = QListWidgetItem(case['submission']['question'][:45]+'\n'+ORIGINS[case['origin']]+' · '+PARTITIONS[case['partition']]+
                                   (' · 完整' if case['answer_complete'] else ' · 摘录'))
            item.setData(Qt.ItemDataRole.UserRole,case); item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if case['answer_complete'] and case['submission']['mode']=='full' else Qt.CheckState.Unchecked)
            self.case_list.addItem(item)
            if case['id']==previous_id:self.case_list.setCurrentItem(item)
        if self.case_list.currentRow()<0:self.case_list.setCurrentRow(0)
        self.case_list.blockSignals(False); self.show_case(self.case_list.currentItem())
        counts=Counter(case['origin'] for case in cases)
        self.case_counts.setText(f'实际已导入 {len(cases)} / 100 份；'+('；'.join(ORIGINS[key]+f' {count} 份' for key,count in counts.items()) or '尚无作答。')+
            '\n短摘录、索引、缺少题干材料或原答的条目不能运行完整评分。无独立人工标签时不产生 MAE。')
        self.refresh_runs()

    def show_case(self, item, previous=None):
        case = self.current_case()
        if not case:
            self.case_detail.setPlainText('尚无作答。请添加有来源的答卷或预览导入 JSON。')
        else:
            sub=case['submission']
            text=f'{ORIGINS[case["origin"]]}\n来源：{case["source"]}\n分组：{PARTITIONS[case["partition"]]}\n原答：'+('已标记为完整' if case['answer_complete'] else '摘录')+'\n题干与材料：'+('齐全' if sub['mode']=='full' else '缺失')
            text+='\n来源附注：'+json.dumps(case['source_metadata'],ensure_ascii=False,indent=2)
            for key,label in (('question','题干'),('materials','全部给定材料'),('answer','原始作答'),('reference','参考答案')):
                text+='\n\n'+label+'\n'+sub[key]
            text+='\n\n作答 ID：'+case['id']+'\n内容指纹：'+case['content_hash']
            self.case_detail.setPlainText(text); self.partition_combo.setCurrentIndex(self.partition_combo.findData(case['partition']))
        self.load_label()

    def label_form(self):
        return (self.label_score.text(),self.label_reviewer.text(),self.label_reason.toPlainText(),self.label_independent.isChecked())

    def retain_label_draft(self):
        if self._label_case_id is None:return
        form=self.label_form()
        if form!=self._label_baseline:
            self._label_drafts[self._label_case_id]={'form':form,'baseline':self._label_baseline,'version':self._label_version}
        else:self._label_drafts.pop(self._label_case_id,None)

    def load_label(self,*,discard=False):
        if discard:self._label_drafts.pop(self._label_case_id,None)
        else:self.retain_label_draft()
        case=self.current_case(); label=self.store.get_eval_label(case['id']) if case else None
        self._label_case_id=case['id'] if case else None
        self._label_version=label['version'] if label else 0
        # A fresh persisted version requires a fresh independence declaration.
        # A session draft preserves both that declaration and its original conflict version.
        form=(str(label['score']),label['reviewer'],label['reason'],False) if label else ('','','',False)
        self._label_baseline=form
        draft=self._label_drafts.get(self._label_case_id)
        if draft:
            form=draft['form'];self._label_baseline=draft['baseline'];self._label_version=draft['version']
        self.label_case.setText(('当前作答：'+case['id']+f' · 满分 {case["submission"]["max_score"]} · 人工版本 v{self._label_version}'+
            (' · 本机未保存输入' if draft else '')) if case else '尚未选择作答。')
        self.label_score.setText(form[0]);self.label_reviewer.setText(form[1]);self.label_reason.setPlainText(form[2])
        self.label_independent.setChecked(form[3])
        history=self.store.list_eval_labels(case['id']) if case else []
        self.label_history.setPlainText(json.dumps(history,ensure_ascii=False,indent=2) if history else '尚无人工评分；空白不等于零分。')

    def reload_label(self):
        if self._busy:return
        if QMessageBox.question(self,'重新载入人工评分','将替换尚未保存的人工评分输入，是否继续？',
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,defaultButton=QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes:self.load_label(discard=True)

    def create_set(self):
        if self._busy:return
        name,ok=QInputDialog.getText(self,'新建本机评测集','名称（最多100字）')
        if not ok:return
        try:
            dataset=self.store.create_eval_set(name);self.refresh_sets(dataset['id']);self.say('已新建本机评测集。')
        except Exception as exc:self.say(exc)

    def add_current(self):
        if self._busy:return
        try:
            self.edit_case(self.current_submission if self.current_submission is not None else self.host.raw_submission())
        except Exception as exc:self.say(str(exc)+' 当前练习内容保留。')

    def add_manual(self):self.edit_case({})

    def edit_case(self,submission):
        if self._busy:return
        if not self.set_id():self.say('请先新建评测集。');return
        dialog=CaseDialog(submission,self)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        try:
            self.store.add_eval_case(self.set_id(),**dialog.record());self.refresh_sets(self.set_id());self.say('已新增独立评测快照。')
        except Exception as exc:self.say(exc)

    def set_partition(self):
        if self._busy:return
        case=self.current_case()
        if not case:return
        try:
            self.store.set_eval_partition(self.set_id(),case['question_fingerprint'],self.partition_combo.currentData())
            self.refresh_cases();self.say('已将同题全部作答分入同一组。')
        except Exception as exc:self.say(exc)

    def save_label(self):
        if self._busy:return
        try:
            case=self.current_case()
            if not case:raise ValueError('请先选择作答。')
            text=self.label_score.text().strip()
            if not text:raise ValueError('尚未填写人工分数；空白不会保存为零。')
            score=float(text)
            if not math.isfinite(score):raise ValueError('人工分数须为有限数值。')
            label=self.store.save_eval_label(case['id'],score,reason=self.label_reason.toPlainText(),reviewer=self.label_reviewer.text(),
                independent=self.label_independent.isChecked(),expected_version=self._label_version)
            self.load_label(discard=True);self.say(f'已保存人工评分 v{label["version"]}；独立性为自行声明。')
        except Exception as exc:self.say(str(exc)+' 当前输入保留。')

    def import_file(self,path=None,*,labels=False):
        if self._busy or self._reviewing:return
        if not self.set_id():self.say('请先新建评测集。');return
        if not path:
            path,_=QFileDialog.getOpenFileName(self,'导入人工评分' if labels else '导入评测作答','','JSON (*.json)')
        if not path:return
        try:
            file=Path(path)
            if file.stat().st_size>25*1024*1024:raise ValueError('文件超过25 MB。')
            raw=file.read_bytes()
            if len(raw)>25*1024*1024:raise ValueError('文件超过25 MB。')
            records=json.loads(raw.decode('utf-8-sig'))
            if isinstance(records,dict):records=records.get('labels' if labels else 'cases')
            if not isinstance(records,list) or not 1<=len(records)<=100:raise ValueError('文件须包含1—100条作答或人工评分记录。仅来源索引不能作为完整答卷导入。')
            for record in records:
                if not isinstance(record,dict):raise ValueError('每条记录须为对象。')
                if not labels:
                    case_submission(record.get('submission',{}))
                    if record.get('origin','unknown') not in ORIGINS:raise ValueError('来源类别不受支持。')
                    if record.get('partition','development') not in PARTITIONS:raise ValueError('分组不受支持。')
                    if type(record.get('answer_complete',False)) is not bool:raise ValueError('完整性须为明确的布尔值。')
            target=self.set_id(); self._reviewing=True
            preview=JsonPreviewDialog(records,file.name,self,labels=labels)
            if preview.exec()!=QDialog.DialogCode.Accepted or self._close_pending:return
            if labels:
                result=self.store.import_eval_labels(target,deepcopy(records));message=f'已导入 {result["imported"]} 条，跳过空白 {result["skipped"]} 条。'
            else:
                added=self.store.import_eval_cases(target,deepcopy(records));message=f'已导入 {len(added)} 份作答。'
            self.refresh_sets(target);self.say(message)
        except Exception as exc:self.say(str(exc)+'\n导入未完成，现有作答保持不变。')
        finally:self._reviewing=False

    def export_blind(self,path=None,*,html=False):
        if self._busy:return
        try:
            if not self.set_id():raise ValueError('请先新建评测集并添加作答。')
            rows=blind_eval_rows(self.store,self.set_id())
            if not rows:raise ValueError('尚无可导出的作答。')
            if not path:path,_=QFileDialog.getSaveFileName(self,'导出独立盲评','blind.'+('html' if html else 'json'),'HTML (*.html)' if html else 'JSON (*.json)')
            if not path:return
            Path(path).write_text(blind_eval_html(rows) if html else json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf8')
            self.say('已导出不含 AI 分数、来源类别与预设排序的盲评文件。')
        except Exception as exc:self.say(exc)

    def selected_case_ids(self):
        return [self.case_list.item(i).data(Qt.ItemDataRole.UserRole)['id'] for i in range(self.case_list.count())
                if self.case_list.item(i).checkState()==Qt.CheckState.Checked]

    def start_run(self):
        if self._jobs or self._busy or self._close_pending or self._reviewing:return
        if getattr(self.host,'workers',set()):self.say('请先完成或取消其他运行中的任务。');return
        try:
            identifiers=self.selected_case_ids();repeats=self.repeats.value();target=self.set_id()
            config=deepcopy(self.host.config)
            # This operation is pure: no credential reads and no service transport.
            plan=plan_evaluation(self.store,target,identifiers,repeats,config)
            text=(f'接口：{plan["config"]["base_url"]}\n模型：{plan["config"]["model"]}\n'
                f'作答 {len(identifiers)} 份，每份重复 {repeats} 次，共 {plan["calls"]} 次模型评分调用。\n'
                f'预计总上限 {plan["estimated_total_tokens"]} token；任务预算 {plan["config"]["session_token_budget"]}。\n\n'
                '所选作答的完整题干、材料、原答及评分依据将发送给上述服务，可能产生费用。\n'
                '取消会停止后续调用；服务端已收到的请求仍可能处理并计费。不会生成改写。\n'
                +plan['notice']+'\n\n是否明确上传并开始此次评测？')
            if QMessageBox.question(self,'确认上传与费用 · 批量评测',text,
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
                defaultButton=QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
            if self._close_pending:return
            # Only the explicit Yes above authorizes credential construction.
            service=self.host.get_service(require_config=True)
            run=self.store.create_eval_run(target,identifiers,repeats,config,expected_plan_hash=plan['plan_hash'])
            self._run_id=run['id'];self.refresh_runs(selected=run['id'])
            job=Job(lambda progress,cancel:run_evaluation(self.store,service,run['id'],progress=progress,cancel=cancel),self)
            self._jobs.add(job);self.host.workers.add(job)
            job.progress.connect(self.job_progress);job.failed.connect(self.job_failed)
            job.finished.connect(lambda:self.job_finished(job))
            self.set_busy(True);self.tabs.setCurrentIndex(2);self.say('正在运行独立评测…');job.start()
        except Exception as exc:self.say(exc)

    def set_busy(self,busy):
        self._busy=busy
        controls=(self.set_combo,self.create_button,self.case_list,self.current_button,self.manual_button,self.import_button,
            self.partition_combo,self.partition_button,self.label_score,self.label_reviewer,self.label_reason,self.label_independent,
            self.save_label_button,self.reload_label_button,self.blind_json_button,self.blind_html_button,self.import_labels_button,
            self.repeats,self.run_button,self.run_list,self.report_json_button,self.report_html_button,self.refresh_button,self.snapshot_button)
        for control in controls:control.setEnabled(not busy)
        self.set_method_comparison_busy(busy)
        self.cancel_button.setVisible(busy);self.cancel_button.setEnabled(busy)

    def job_progress(self,message):
        if not self._close_pending:self.say(str(message)[:1000])

    def job_failed(self,message):
        if not self._close_pending and not any(job.cancel_event.is_set() for job in self._jobs):self.say(message)

    def job_finished(self,job):
        cancelled=job.cancel_event.is_set();self._jobs.discard(job);self.host.workers.discard(job)
        job.deleteLater();self.set_busy(False)
        # Always refresh persisted outcomes, including completed units before cancel.
        self.refresh_runs(selected=self._run_id)
        self.refresh_method_comparisons()
        if self._close_pending or getattr(self.host,'_closing',False):
            super().reject()
            if getattr(self.host,'_closing',False) and not self.host.workers:QTimer.singleShot(0,self.host.close)
            return
        self.say('已取消；已完成单元保留，迟到结果未记成功。已发送请求仍可能计费。' if cancelled else '本轮处理结束，请查看已保存的逐次状态与分母。')

    def cancel_run(self):
        for job in self._jobs:job.cancel()
        self.comparison_cancel_button.setEnabled(False)
        self.cancel_button.setEnabled(False);self.say('正在取消… 等待当前请求结束，随后停止后续调用。')

    def refresh_runs(self,selected=None):
        old=self.run_list.currentItem();selected=selected or (old.data(Qt.ItemDataRole.UserRole) if old else None)
        self.run_list.blockSignals(True);self.run_list.clear()
        for run in self.store.list_eval_runs(self.set_id()) if self.set_id() else []:
            item=QListWidgetItem(f'{run["created_at"][:19]} · {RUN_STATUS.get(run["status"],run["status"])} · {run["calls"]} 次 · {run["config"]["model"]}')
            item.setData(Qt.ItemDataRole.UserRole,run['id']);self.run_list.addItem(item)
            if run['id']==selected:self.run_list.setCurrentItem(item)
        if self.run_list.currentRow()<0:self.run_list.setCurrentRow(0)
        self.run_list.blockSignals(False);self.show_run(self.run_list.currentItem())

    def show_run(self,item,previous=None):
        if not item:self.run_detail.setPlainText('尚无评测轮次。先选择完整作答，再估算并明确确认上传。');return
        try:
            report=evaluation_report(self.store,item.data(Qt.ItemDataRole.UserRole));summary=report['summary']
            text=(f'状态：{RUN_STATUS.get(summary["status"],summary["status"])}\n成功 {summary["successes"]} / 计划 {summary["requested_calls"]} 次；'
                f'失败 {summary["errors"]}；取消 {summary["cancelled"]}；未执行 {summary["pending"]}。\n'
                f'符合条件的独立人工对照：{summary["human_pairs"]} 份原答（重复调用不增加人工样本数）。\n'
                f'百分制 MAE：{summary["mae_percent"] if summary["mae_percent"] is not None else "样本不足 / 多评分条件分组"}；'
                f'平均偏差：{summary["bias_percent"] if summary["bias_percent"] is not None else "样本不足 / 多评分条件分组"}。\n'
                +summary['notice'])
            text+='\n\n来源分母\n'+'\n'.join(ORIGINS.get(origin,origin)+f'：{count} 份' for origin,count in summary['origin_counts'].items())
            text+='\n\n同条件重复评分'
            for group in summary['repeat_groups']:
                text+=f'\n原答 {group["case_id"]}：同条件重复 {group["n"]} 次，分差 {group["spread"]:g}；条件 {group["version"]}'
            if not summary['repeat_groups']:text+='\n尚无至少两次成功且评分条件相同的重复组。'
            text+='\n\n人工对照按评分条件分组'
            for group in summary['metrics_groups']:
                text+=f'\n条件 {group["version"]}：{group["human_pairs"]} 份原答；MAE {group["mae_percent"]:g}，偏差 {group["bias_percent"]:g}（百分制）'
            if not summary['metrics_groups']:text+='\n尚无符合来源、独立性及内容绑定条件的人工对照。'
            text+='\n\n逐次结果'
            for result in report['results']:
                text+='\n原答 '+result['case_id']+f' · 第 {result["repeat"]} 次：'
                if result['status']=='success':
                    review=result.get('review') or {};maximum=review.get('assessment',{}).get('max_score','?')
                    score=score_value(review);formatted=f'{score:g}' if score is not None else '无有效总分'
                    text+='成功，'+formatted+f' / {maximum:g}' if isinstance(maximum,(int,float)) else '成功，'+formatted+' / '+str(maximum)
                    text+='；模型 '+str(review.get('model','未记录'))
                else:text+=('已取消' if result['status']=='cancelled' else '失败')+'；'+result.get('error','')
            self.run_detail.setPlainText(text)
        except Exception as exc:self.run_detail.setPlainText(str(exc))

    def inspect_run(self):
        if self._busy:return
        item=self.run_list.currentItem()
        if not item:self.say('请先选择已保存的评测轮次。');return
        try:
            report=evaluation_report(self.store,item.data(Qt.ItemDataRole.UserRole))
            dialog=QDialog(self);dialog.setWindowTitle('完整冻结快照 · 只读');dialog.resize(850,600)
            layout=QVBoxLayout(dialog);layout.addWidget(readonly(json.dumps(report,ensure_ascii=False,indent=2)),1)
            layout.addWidget(button('关闭',dialog.reject));dialog.exec()
        except Exception as exc:self.say(exc)

    def export_report(self,path=None,*,html=False):
        if self._busy:return
        item=self.run_list.currentItem()
        if not item:self.say('请先选择已保存的评测轮次。');return
        try:
            report=evaluation_report(self.store,item.data(Qt.ItemDataRole.UserRole))
            if not path:path,_=QFileDialog.getSaveFileName(self,'导出可复现评测报告','evaluation.'+('html' if html else 'json'),'HTML (*.html)' if html else 'JSON (*.json)')
            if not path:return
            Path(path).write_text(evaluation_report_html(report) if html else json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
            self.say('已导出本轮快照、标签版本、逐次结果及统计。')
        except Exception as exc:self.say(exc)

    def confirm_discard_labels(self):
        self.retain_label_draft()
        if not self._label_drafts:return True
        if QMessageBox.question(self,'丢弃未保存的人工评分输入？',
            f'本窗口有 {len(self._label_drafts)} 份作答的人工评分输入尚未保存。关闭后将丢弃这些本机会话草稿。\n'
            '选择“否”可返回检查并明确保存；不会自动创建人工评分标签。是否明确丢弃并关闭？',
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
            defaultButton=QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return False
        self._label_drafts.clear();self._label_baseline=self.label_form()
        return True

    def eventFilter(self,watched,event):
        if watched is self.host and event.type()==QEvent.Type.Close and not self._close_pending:
            if not self.confirm_discard_labels():
                event.ignore();return True
        return super().eventFilter(watched,event)

    def reject(self):
        if not self._close_pending and not self.confirm_discard_labels():return
        self._close_pending=True
        if self._jobs:self.cancel_run();return
        super().reject()

    def closeEvent(self,event):
        self.reject()
        if self._close_pending and not self._jobs:event.accept()
        else:event.ignore()
