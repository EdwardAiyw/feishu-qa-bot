"""
二期数据质检引擎 — 从 rules.yaml 读取规则配置
修改 rules.yaml 后重新部署即可生效，无需改代码
"""
import re
import os
import yaml
from dataclasses import dataclass, field


def load_rules() -> dict:
    """加载规则配置文件"""
    rules_path = os.path.join(os.path.dirname(__file__), "rules.yaml")
    with open(rules_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# 全局规则配置
RULES = load_rules()


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
    """二期数据质检器 — 从 rules.yaml 读取规则"""

    def __init__(self, field_mapping: dict = None):
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

        # 规则 1：场景真实性 🔴
        self._check_rule1(result, title)

        # 规则 2：附件描述是否精简 🟡
        self._check_rule2(result, title)

        # 规则 3：产物要求是否精简 🟡
        self._check_rule3(result, title)

        # 规则 4：题目是否赘述 🟢
        self._check_rule4(result, title)

        # 规则 5：自检要求 🟢
        self._check_rule5(result, title, output)

        # 规则 6：checklist 是否填写 🔴
        self._check_rule6(result, checklist)

        # 规则 7：是否包含 L1-3 层级 🟡
        self._check_rule7(result, title)

        return result

    def check_all(self, records: list) -> QAReport:
        """对所有记录执行质检"""
        report = QAReport()
        for i, record in enumerate(records, 1):
            result = self.check_record(i, record)
            report.results.append(result)
        return report

    # ──── 规则 1：场景真实性 🔴 ────

    def _check_rule1(self, result: QARecordResult, title: str):
        """规则 1：场景真实性 — 检测虚假/非真实场景"""
        if not title:
            return

        rule = RULES.get("rule1", {})
        severity = rule.get("severity", "🔴")

        for pattern in rule.get("critical_keywords", []):
            if re.search(pattern, title, re.IGNORECASE):
                result.issues.append(QAIssue(
                    rule="规则1",
                    severity=severity,
                    description="题目包含虚假/非真实场景描述",
                    suggestion="题目必须基于真实情况和场景出发，避免使用后台数据、API接口等非用户可操作的场景",
                ))
                return

        for pattern in rule.get("unreasonable_patterns", []):
            if re.search(pattern, title):
                result.issues.append(QAIssue(
                    rule="规则1",
                    severity=severity,
                    description="题目场景不合理（限制正常工具却要求专业数据）",
                    suggestion="改为使用常见的APP或工具完成任务",
                ))
                return

    # ──── 规则 2：附件描述是否精简 🟡 ────

    def _check_rule2(self, result: QARecordResult, title: str):
        """规则 2：附件描述是否过详细（二期核心规则）"""
        if not title:
            return

        rule = RULES.get("rule2", {})
        severity = rule.get("severity", "🟡")
        min_hits = RULES.get("thresholds", {}).get("rule2_min_hits", 2)

        issue_count = 0
        for pattern in rule.get("patterns", []):
            if re.search(pattern, title):
                issue_count += 1

        if issue_count >= min_hits:
            result.issues.append(QAIssue(
                rule="规则2",
                severity=severity,
                description="题目中附件描述过于详细（包含文件名、格式、行数等）",
                suggestion="精简附件描述，如'现在有几个附件需要你进行关联'，让大模型自行判断如何使用附件",
            ))
        elif issue_count == 1:
            result.issues.append(QAIssue(
                rule="规则2",
                severity=severity,
                description="题目中可能包含过多附件细节信息",
                suggestion="考虑精简附件描述，附件题目、格式和时间可不体现在题目中",
            ))

    # ──── 规则 3：产物要求是否精简 🟡 ────

    def _check_rule3(self, result: QARecordResult, title: str):
        """规则 3：产物要求是否精简（二期核心规则）"""
        if not title:
            return

        rule = RULES.get("rule3", {})
        severity = rule.get("severity", "🟡")
        min_hits = RULES.get("thresholds", {}).get("rule3_min_hits", 2)

        secondary_count = 0
        for pattern in rule.get("secondary_keywords", []):
            if re.search(pattern, title):
                secondary_count += 1

        if secondary_count >= min_hits:
            result.issues.append(QAIssue(
                rule="规则3",
                severity=severity,
                description="题目中包含过多次级产物要求（如数据来源、配色、字体等）",
                suggestion="精简产物要求，保留核心需求，次级要求（配色、数据来源等）可移除或更开放",
            ))
        elif secondary_count == 1:
            result.issues.append(QAIssue(
                rule="规则3",
                severity=severity,
                description="题目中可能包含次级产物要求",
                suggestion="考虑精简产物要求，只有最核心的要求才需要体现",
            ))

        for pattern in rule.get("rigid_patterns", []):
            if re.search(pattern, title):
                result.issues.append(QAIssue(
                    rule="规则3",
                    severity="🟢",
                    description="产物要求可能过于刚性",
                    suggestion="考虑更开放的要求方式，如'我想要一份报告'，不对格式做严格要求",
                ))
                break

    # ──── 规则 4：题目是否赘述 🟢 ────

    def _check_rule4(self, result: QARecordResult, title: str):
        """规则 4：题目是否赘述"""
        if not title:
            return

        rule = RULES.get("rule4", {})
        severity = rule.get("severity", "🟢")
        max_chars = RULES.get("thresholds", {}).get("rule4_max_chars", 500)
        max_sentences = RULES.get("thresholds", {}).get("rule4_max_sentences", 10)

        if len(title) > max_chars:
            result.issues.append(QAIssue(
                rule="规则4",
                severity=severity,
                description=f"题目过长（{len(title)}字），可能包含赘述",
                suggestion="题目阐述要精简，不赘述",
            ))

        sentences = re.split(r"[。！？\n]", title)
        if len(sentences) > max_sentences:
            result.issues.append(QAIssue(
                rule="规则4",
                severity=severity,
                description="题目包含较多句子，可能存在赘述",
                suggestion="精简题目，合并重复表达",
            ))

    # ──── 规则 5：自检要求 🟢 ────

    def _check_rule5(self, result: QARecordResult, title: str, output: str):
        """规则 5：自检要求是否合理"""
        if not title:
            return

        rule = RULES.get("rule5", {})
        severity = rule.get("severity", "🟢")
        output_min = RULES.get("thresholds", {}).get("rule5_output_min_chars", 20)

        has_self_check = any(
            re.search(p, title) for p in rule.get("self_check_keywords", [])
        )

        if has_self_check:
            if not output or len(output) < output_min:
                result.issues.append(QAIssue(
                    rule="规则5",
                    severity=severity,
                    description="题目包含自检要求，但产物要求不够明确",
                    suggestion="模型自检不强制要求。如要求自检，需确保产物格式/内容要求十分明确",
                ))

    # ──── 规则 6：checklist 是否填写 🔴 ────

    def _check_rule6(self, result: QARecordResult, checklist: str):
        """规则 6：checklist 是否填写"""
        rule = RULES.get("rule6", {})
        min_chars = RULES.get("thresholds", {}).get("rule6_min_chars", 10)

        if not checklist or checklist.strip() == "":
            result.issues.append(QAIssue(
                rule="规则6",
                severity=rule.get("severity_empty", "🔴"),
                description="打分checklist未填写（必填项）",
                suggestion="需列出模型应达成的核心需求，规则需客观可评判",
            ))
        elif len(checklist.strip()) < min_chars:
            result.issues.append(QAIssue(
                rule="规则6",
                severity=rule.get("severity_short", "🟡"),
                description="打分checklist内容过短",
                suggestion="checklist应列出具体的评判标准，确保客观可评判",
            ))

    # ──── 规则 7：是否包含 L1-3 层级 🟡 ────

    def _check_rule7(self, result: QARecordResult, title: str):
        """规则 7：题目中不应包含 L1-3 层级任务"""
        if not title:
            return

        rule = RULES.get("rule7", {})
        severity = rule.get("severity", "🟡")

        for pattern in rule.get("level_patterns", []):
            if re.search(pattern, title, re.IGNORECASE):
                result.issues.append(QAIssue(
                    rule="规则7",
                    severity=severity,
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
