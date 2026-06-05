"""
二期数据质检引擎 — 7 条规则的自动检查
"""
import re
from dataclasses import dataclass, field


@dataclass
class QAIssue:
    """单个质检问题"""
    rule: str           # 违反的规则编号
    severity: str       # 🔴严重 / 🟡中等 / 🟢轻微
    description: str    # 问题描述
    suggestion: str     # 修改建议


@dataclass
class QARecordResult:
    """单条记录的质检结果"""
    row_index: int      # 行号
    title: str          # 题目摘要（前30字）
    issues: list = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.issues) == 0

    @property
    def has_critical(self) -> bool:
        return any(i.severity == "🔴" for i in self.issues)


@dataclass
class QAReport:
    """完整质检报告"""
    results: list = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed_count(self) -> int:
        return self.total - self.passed_count

    @property
    def critical_count(self) -> int:
        return sum(1 for r in self.results if r.has_critical)


class QAChecker:
    """二期数据质检器"""

    def __init__(self, field_mapping: dict = None):
        """
        field_mapping: 字段名映射，用于适配不同表格的列名
        默认映射：
        {
            "title": "题目",         # 题目内容字段
            "attachments": "附件内容", # 附件描述字段
            "output": "产物内容",     # 产物要求字段
            "task_type": "任务类型",   # 任务类型字段
            "checklist": "打分checklist" # 打分checklist字段
        }
        """
        self.field_mapping = field_mapping or {
            "title": "题目",
            "attachments": "附件内容",
            "output": "产物内容",
            "task_type": "任务类型",
            "checklist": "打分checklist",
        }

    def _get_field(self, record: dict, key: str) -> str:
        """根据映射获取字段值"""
        field_name = self.field_mapping.get(key, key)
        return record.get(field_name, "")

    def check_record(self, row_index: int, record: dict) -> QARecordResult:
        """对单条记录执行全部质检规则"""
        title = self._get_field(record, "title")
        attachments = self._get_field(record, "attachments")
        output = self._get_field(record, "output")
        task_type = self._get_field(record, "task_type")
        checklist = self._get_field(record, "checklist")

        result = QARecordResult(
            row_index=row_index,
            title=title[:30] + "..." if len(title) > 30 else title,
        )

        # 规则 1：场景真实性
        self._check_rule1(result, title)

        # 规则 2：附件描述是否精简
        self._check_rule2(result, title, attachments)

        # 规则 3：产物要求是否精简
        self._check_rule3(result, title, output)

        # 规则 4：题目是否赘述
        self._check_rule4(result, title)

        # 规则 5：自检要求
        self._check_rule5(result, title, output)

        # 规则 6：checklist 是否填写
        self._check_rule6(result, checklist)

        # 规则 7：是否包含 L1-3 层级
        self._check_rule7(result, title)

        return result

    def check_all(self, records: list) -> QAReport:
        """对所有记录执行质检"""
        report = QAReport()
        for i, record in enumerate(records, 1):
            result = self.check_record(i, record)
            report.results.append(result)
        return report

    # ──── 具体规则实现 ────

    def _check_rule1(self, result: QARecordResult, title: str):
        """规则 1：场景真实性 — 检测虚假/不合理场景"""
        # 虚假场景关键词
        fake_patterns = [
            r"后台数据",
            r"数据库直接",
            r"内部系统接口",
            r"API\s*接口",
            r"爬虫.*爬取",
            r"黑入",
            r"破解",
        ]
        # 不合理场景关键词
        unreasonable_patterns = [
            r"不使用.*搜索.*查",
            r"不用.*APP.*查询",
        ]

        for pattern in fake_patterns:
            if re.search(pattern, title, re.IGNORECASE):
                result.issues.append(QAIssue(
                    rule="规则1",
                    severity="🔴",
                    description="题目包含虚假/非真实场景描述",
                    suggestion="题目必须基于真实情况和场景出发，避免使用后台数据、API接口等非用户可操作的场景",
                ))
                return

        for pattern in unreasonable_patterns:
            if re.search(pattern, title):
                result.issues.append(QAIssue(
                    rule="规则1",
                    severity="🔴",
                    description="题目场景不合理（不使用正常工具却要求获取专业数据）",
                    suggestion="改为使用常见的APP或工具完成任务",
                ))
                return

    def _check_rule2(self, result: QARecordResult, title: str, attachments: str):
        """规则 2：附件描述是否过详细（二期核心规则）"""
        if not title:
            return

        # 检测题目中是否包含过详细的附件描述
        detail_patterns = [
            # 附件名称+格式
            r"附件[1-9一二三四五六七八九十]*[（(].*?[.].*?[)）]",
            # 附件详细内容描述（超过20字的括号内容）
            r"[（(].*?(?:包含|共|总计).*?[)）]",
            # 文件格式描述
            r"\.(?:xlsx|xls|csv|docx|doc|pdf|pptx|ppt)\b",
            # 具体时间/日期描述
            r"(?:20\d{2}年|20\d{2}[-/])\d{1,2}月",
            # 行数/数据量描述
            r"(?:共|总计|约)\s*\d+\s*(?:行|条|条数据|列)",
        ]

        issue_count = 0
        for pattern in detail_patterns:
            if re.search(pattern, title):
                issue_count += 1

        if issue_count >= 2:
            result.issues.append(QAIssue(
                rule="规则2",
                severity="🟡",
                description="题目中附件描述过于详细（包含文件名、格式、行数等）",
                suggestion="精简附件描述，如'现在有几个附件需要你进行关联'，让大模型自行判断如何使用附件",
            ))
        elif issue_count == 1:
            result.issues.append(QAIssue(
                rule="规则2",
                severity="🟡",
                description="题目中可能包含过多附件细节信息",
                suggestion="考虑精简附件描述，附件题目、格式和时间可不体现在题目中",
            ))

    def _check_rule3(self, result: QARecordResult, title: str, output: str):
        """规则 3：产物要求是否精简（二期核心规则）"""
        if not title:
            return

        # 检测次级要求关键词
        secondary_patterns = [
            r"数据来源",
            r"配色",
            r"字体.*(?:大小|颜色|类型)",
            r"页边距",
            r"行距",
            r"字数.*(?:不少于|不少于|控制在)",
            r"(?:必须|一定|需要).*?(?:注明|标注|列出).*?来源",
        ]

        secondary_count = 0
        for pattern in secondary_patterns:
            if re.search(pattern, title):
                secondary_count += 1

        if secondary_count >= 2:
            result.issues.append(QAIssue(
                rule="规则3",
                severity="🟡",
                description="题目中包含过多次级产物要求（如数据来源、配色、字体等）",
                suggestion="精简产物要求，保留核心需求，次级要求（配色、数据来源等）可移除或更开放",
            ))

        # 检测是否要求过于具体（不够开放）
        rigid_patterns = [
            r"(?:必须|一定|严格按照).*?格式",
            r"(?:不得|不能|不可以).*?(?:修改|更改|调整)",
        ]
        for pattern in rigid_patterns:
            if re.search(pattern, title):
                result.issues.append(QAIssue(
                    rule="规则3",
                    severity="🟢",
                    description="产物要求可能过于刚性",
                    suggestion="考虑更开放的要求方式，如'我想要一份报告'，不对格式做严格要求",
                ))
                break

    def _check_rule4(self, result: QARecordResult, title: str):
        """规则 4：题目是否赘述"""
        if not title:
            return

        # 检测重复表达
        if len(title) > 500:
            result.issues.append(QAIssue(
                rule="规则4",
                severity="🟢",
                description=f"题目过长（{len(title)}字），可能包含赘述",
                suggestion="题目阐述要精简，不赘述",
            ))

        # 检测重复句式
        sentences = re.split(r"[。！？\n]", title)
        if len(sentences) > 10:
            result.issues.append(QAIssue(
                rule="规则4",
                severity="🟢",
                description="题目包含较多句子，可能存在赘述",
                suggestion="精简题目，合并重复表达",
            ))

    def _check_rule5(self, result: QARecordResult, title: str, output: str):
        """规则 5：自检要求是否合理"""
        if not title:
            return

        # 检测自检相关关键词
        self_check_patterns = [
            r"自检",
            r"自行检查",
            r"检查.*?(?:是否|有没有)",
            r"验证.*?(?:是否|正确性)",
        ]

        has_self_check = any(re.search(p, title) for p in self_check_patterns)

        if has_self_check:
            # 检查是否有明确的产物要求（有明确要求才适合自检）
            clear_output = self._get_field(None, "output")  # 这里用 output
            if not output or len(output) < 20:
                result.issues.append(QAIssue(
                    rule="规则5",
                    severity="🟢",
                    description="题目包含自检要求，但产物要求不够明确",
                    suggestion="模型自检不强制要求。如要求自检，需确保产物格式/内容要求十分明确",
                ))

    def _check_rule6(self, result: QARecordResult, checklist: str):
        """规则 6：checklist 是否填写"""
        if not checklist or checklist.strip() == "":
            result.issues.append(QAIssue(
                rule="规则6",
                severity="🔴",
                description="打分checklist未填写（必填项）",
                suggestion="需列出模型应达成的核心需求，规则需客观可评判",
            ))

    def _check_rule7(self, result: QARecordResult, title: str):
        """规则 7：题目中不应包含 L1-3 层级任务"""
        if not title:
            return

        level_patterns = [
            r"L[123]",
            r"层级[123一二三]",
            r"任务层级",
            r"任务级别.*[123一二三]",
        ]

        for pattern in level_patterns:
            if re.search(pattern, title, re.IGNORECASE):
                result.issues.append(QAIssue(
                    rule="规则7",
                    severity="🟡",
                    description="题目中包含了L1-3层级任务描述",
                    suggestion="L1-3层级任务应在任务类型列选择，不用写在题目中",
                ))
                return


def format_report(report: QAReport) -> str:
    """将质检报告格式化为飞书消息文本"""
    lines = []
    lines.append("📊 二期数据质检报告\n")
    lines.append("📋 总览")
    lines.append(f"- 检查总数：{report.total} 条")
    lines.append(f"- ✅ 通过：{report.passed_count} 条")
    lines.append(f"- ⚠️ 有问题：{report.failed_count} 条")

    # 按严重程度分组
    critical = [r for r in report.results if r.has_critical]
    medium = [r for r in report.results if not r.has_critical and any(i.severity == "🟡" for i in r.issues)]
    light = [r for r in report.results if not r.has_critical and not any(i.severity == "🟡" for i in r.issues) and not r.passed]

    if critical:
        lines.append(f"\n🔴 严重问题（{len(critical)} 条）")
        lines.append("━" * 20)
        for i, r in enumerate(critical, 1):
            lines.append(f"{i}. 第{r.row_index}题：「{r.title}」")
            for issue in r.issues:
                lines.append(f"   ❌ {issue.rule}-{issue.description}")
                lines.append(f"   💡 {issue.suggestion}")

    if medium:
        lines.append(f"\n🟡 中等问题（{len(medium)} 条）")
        lines.append("━" * 20)
        for i, r in enumerate(medium, 1):
            lines.append(f"{i}. 第{r.row_index}题：「{r.title}」")
            for issue in r.issues:
                if issue.severity == "🟡":
                    lines.append(f"   ⚠️ {issue.rule}-{issue.description}")
                    lines.append(f"   💡 {issue.suggestion}")

    if light:
        lines.append(f"\n🟢 轻微问题（{len(light)} 条）")
        lines.append("━" * 20)
        for i, r in enumerate(light, 1):
            lines.append(f"{i}. 第{r.row_index}题：「{r.title}」")
            for issue in r.issues:
                lines.append(f"   ℹ️ {issue.rule}-{issue.description}")

    if report.failed_count == 0:
        lines.append("\n🎉 所有数据均通过质检！")

    lines.append("\n---")
    lines.append("🤖 由质检机器人自动生成")

    return "\n".join(lines)
