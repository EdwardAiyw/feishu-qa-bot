"""
飞书质检机器人 Webhook 服务
接收飞书事件 → 读取表格 → 执行质检 → 返回报告
"""
import json
import logging
import hashlib
from flask import Flask, request, jsonify
from feishu_client import FeishuClient
from qa_checker import QAChecker, format_report
import config

# ──── 日志配置 ────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ──── 初始化 ────
app = Flask(__name__)
feishu = FeishuClient()
checker = QAChecker()


def verify_signature(timestamp: str, nonce: str, body: str, signature: str) -> bool:
    """验证飞书事件签名"""
    # 飞书开放平台的 Encrypt Key（如果配置了）
    encrypt_key = ""  # 如果在开放平台配置了 Encrypt Key，填在这里
    if not encrypt_key:
        return True  # 未配置则跳过验证

    content = timestamp + nonce + encrypt_key + body
    return hashlib.sha256(content.encode()).hexdigest() == signature


@app.route("/webhook/event", methods=["POST"])
def webhook_event():
    """飞书事件回调入口"""
    data = request.get_json(force=True)

    # ── 飞书 URL 验证（首次配置时飞书会发送验证请求）──
    if "challenge" in data:
        logger.info("收到飞书 URL 验证请求")
        return jsonify({"challenge": data["challenge"]})

    # ── 签名验证 ──
    header = data.get("header", {})
    token = header.get("token", "")
    # 如果配置了 Verification Token，可以在这里校验

    # ── 事件处理 ──
    event = data.get("event", {})
    event_type = header.get("event_type", "")

    if event_type == "im.message.receive_v1":
        handle_message_event(event)

    return jsonify({"code": 0})


def handle_message_event(event: dict):
    """处理收到的消息事件"""
    message = event.get("message", {})
    sender = event.get("sender", {})

    msg_type = message.get("message_type", "")
    chat_type = message.get("chat_type", "")
    message_id = message.get("message_id", "")

    # 只处理文本消息
    if msg_type != "text":
        feishu.reply_message(message_id, "text", json.dumps({
            "text": "请发送飞书多维表格的链接来进行质检哦～"
        }))
        return

    # 解析消息内容
    try:
        content = json.loads(message.get("content", "{}"))
        text = content.get("text", "")
    except json.JSONDecodeError:
        text = ""

    logger.info(f"收到消息: {text[:100]}")

    # 查找表格链接
    table_info = feishu.parse_bitable_url(text)
    if not table_info:
        feishu.reply_message(message_id, "text", json.dumps({
            "text": "没有找到多维表格链接 😅\n请发送飞书多维表格的链接，例如：\nhttps://xxx.feishu.cn/base/xxxxx"
        }))
        return

    # 开始质检
    try:
        do_quality_check(message_id, table_info)
    except Exception as e:
        logger.error(f"质检失败: {e}", exc_info=True)
        feishu.reply_message(message_id, "text", json.dumps({
            "text": f"质检过程中出错：{str(e)}\n请检查链接是否正确，或联系管理员。"
        }))


def do_quality_check(message_id: str, table_info: dict):
    """执行质检流程并写回结果到表格"""
    app_token = table_info["app_token"]

    # 1. 获取表格列表
    logger.info(f"读取表格: {app_token}")
    tables = feishu.list_tables(app_token)

    if not tables:
        raise Exception("多维表格中没有找到数据表")

    # 使用指定的 table 或第一个表
    table_id = table_info.get("table_id") or tables[0]["table_id"]
    table_name = next((t["name"] for t in tables if t["table_id"] == table_id), tables[0]["name"])

    logger.info(f"使用数据表: {table_name} ({table_id})")

    # 2. 读取字段定义
    fields = feishu.read_fields(app_token, table_id)
    logger.info(f"字段: {[f['name'] for f in fields]}")

    # 3. 确保「是否通过」和「质检备注」字段存在
    pass_field = feishu.ensure_field(app_token, table_id, "是否通过", 1)  # 文本
    note_field = feishu.ensure_field(app_token, table_id, "质检备注", 1)   # 文本
    logger.info(f"质检字段就绪: 是否通过={pass_field.get('field_id')}, 质检备注={note_field.get('field_id')}")

    # 4. 读取所有记录
    records = feishu.read_records(app_token, table_id)
    logger.info(f"读取到 {len(records)} 条记录")

    if not records:
        feishu.reply_message(message_id, "text", json.dumps({
            "text": f"表格「{table_name}」中没有数据～"
        }))
        return

    # 5. 转换为可读格式
    parsed_records = []
    for record in records:
        parsed = feishu.get_record_values(record, fields)
        parsed_records.append(parsed)

    # 6. 自动适配字段映射
    field_names = [f["name"] for f in fields]
    mapping = auto_detect_fields(field_names)
    logger.info(f"字段映射: {mapping}")

    checker.field_mapping = mapping

    # 7. 执行质检
    report = checker.check_all(parsed_records)
    logger.info(f"质检完成: {report.passed_count}/{report.total} 通过")

    # 8. 写回结果到表格
    update_records = []
    for i, result in enumerate(report.results):
        record_id = records[i].get("record_id", "")
        if not record_id:
            continue

        # 是否通过
        pass_status = "✅通过" if result.passed else "❌不通过"

        # 质检备注：列出所有问题
        notes = []
        for issue in result.issues:
            severity_icon = issue.severity
            notes.append(f"{severity_icon} {issue.rule}: {issue.description}\n💡 {issue.suggestion}")
        note_text = "\n\n".join(notes) if notes else "无问题"

        update_records.append({
            "record_id": record_id,
            "fields": {
                "是否通过": pass_status,
                "质检备注": note_text,
            }
        })

    # 批量写回（每批最多 500 条）
    batch_size = 500
    for j in range(0, len(update_records), batch_size):
        batch = update_records[j:j + batch_size]
        feishu.batch_update_records(app_token, table_id, batch)
        logger.info(f"写回第 {j+1}-{j+len(batch)} 条结果")

    # 9. 格式化报告并回复
    report_text = format_report(report)
    report_text += f"\n\n✅ 已将质检结果写入表格「{table_name}」的「是否通过」和「质检备注」列"
    feishu.reply_message(message_id, "text", json.dumps({"text": report_text}))


def auto_detect_fields(field_names: list) -> dict:
    """自动检测字段映射"""
    mapping = {}

    for name in field_names:
        name_lower = name.lower()

        if "题目" in name_lower or "question" in name_lower or "prompt" in name_lower:
            mapping["title"] = name
        elif "附件" in name_lower or "attachment" in name_lower:
            mapping["attachments"] = name
        elif "产物" in name_lower or "output" in name_lower or "输出" in name_lower:
            mapping["output"] = name
        elif "任务类型" in name_lower or "task" in name_lower:
            mapping["task_type"] = name
        elif "checklist" in name_lower or "打分" in name_lower or "评分" in name_lower:
            mapping["checklist"] = name

    return mapping


# ──── 健康检查 ────
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "feishu-qa-bot"})


# ──── 手动触发（测试用）───
@app.route("/test/check", methods=["POST"])
def test_check():
    """手动触发质检（测试用）"""
    data = request.get_json(force=True)
    url = data.get("url", "")
    write_back = data.get("write_back", False)  # 是否写回表格

    table_info = feishu.parse_bitable_url(url)
    if not table_info:
        return jsonify({"error": "无效的表格链接"}), 400

    try:
        app_token = table_info["app_token"]
        tables = feishu.list_tables(app_token)
        table_id = table_info.get("table_id") or tables[0]["table_id"]
        table_name = next((t["name"] for t in tables if t["table_id"] == table_id), tables[0]["name"])

        fields = feishu.read_fields(app_token, table_id)
        records = feishu.read_records(app_token, table_id)

        parsed_records = [feishu.get_record_values(r, fields) for r in records]

        field_names = [f["name"] for f in fields]
        checker.field_mapping = auto_detect_fields(field_names)

        report = checker.check_all(parsed_records)
        report_text = format_report(report)

        # 写回表格
        written = False
        if write_back and records:
            # 确保字段存在
            feishu.ensure_field(app_token, table_id, "是否通过", 1)
            feishu.ensure_field(app_token, table_id, "质检备注", 1)

            update_records = []
            for i, result in enumerate(report.results):
                record_id = records[i].get("record_id", "")
                if not record_id:
                    continue

                pass_status = "✅通过" if result.passed else "❌不通过"
                notes = []
                for issue in result.issues:
                    notes.append(f"{issue.severity} {issue.rule}: {issue.description}\n💡 {issue.suggestion}")
                note_text = "\n\n".join(notes) if notes else "无问题"

                update_records.append({
                    "record_id": record_id,
                    "fields": {
                        "是否通过": pass_status,
                        "质检备注": note_text,
                    }
                })

            # 批量写回
            batch_size = 500
            for j in range(0, len(update_records), batch_size):
                batch = update_records[j:j + batch_size]
                feishu.batch_update_records(app_token, table_id, batch)

            written = True
            report_text += f"\n\n✅ 已将质检结果写入表格「{table_name}」的「是否通过」和「质检备注」列"

        return jsonify({
            "total": report.total,
            "passed": report.passed_count,
            "failed": report.failed_count,
            "written": written,
            "report": report_text,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ──── 调试接口（查看收到的数据）────
@app.route("/debug", methods=["POST"])
def debug():
    """调试用：返回收到的请求体"""
    data = request.get_json(force=True)
    logger.info(f"调试收到: {data}")
    return jsonify({"received": data})


# ──── 单条记录质检（飞书自动化按钮用）────
@app.route("/check/record", methods=["POST"])
def check_single_record():
    """
    单条记录质检 — 飞书自动化按钮点击时调用
    请求体：
    {
        "url": "飞书表格链接",
        "uid": 1,           # UID 列的值（行号）
        "write_back": true
    }
    或：
    {
        "url": "飞书表格链接",
        "record_id": "recXXX",
        "write_back": true
    }
    """
    data = request.get_json(force=True)
    url = data.get("url", "")
    record_id = data.get("record_id", "")
    uid = data.get("uid", None)
    write_back = data.get("write_back", True)

    table_info = feishu.parse_bitable_url(url)
    if not table_info:
        return jsonify({"error": "无效的表格链接"}), 400

    try:
        app_token = table_info["app_token"]
        tables = feishu.list_tables(app_token)
        table_id = table_info.get("table_id") or tables[0]["table_id"]
        table_name = next((t["name"] for t in tables if t["table_id"] == table_id), tables[0]["name"])

        # 读取字段定义
        fields = feishu.read_fields(app_token, table_id)
        field_names = [f["name"] for f in fields]
        checker.field_mapping = auto_detect_fields(field_names)

        # 获取记录
        if uid is not None:
            # 通过 UID 查找记录
            all_records = feishu.read_records(app_token, table_id)
            target_record = None
            for r in all_records:
                parsed = feishu.get_record_values(r, fields)
                if parsed.get("UID", "") == str(uid):
                    target_record = r
                    break
            if not target_record:
                return jsonify({"error": f"未找到 UID={uid} 的记录"}), 404
            record = target_record
        elif record_id:
            record = feishu.read_record(app_token, table_id, record_id)
        else:
            return jsonify({"error": "缺少 uid 或 record_id"}), 400

        parsed = feishu.get_record_values(record, fields)

        # 执行质检
        result = checker.check_record(1, parsed)

        # 生成单条报告
        if result.passed:
            report_text = f"✅ 质检通过！\n「{result.title}」"
        else:
            lines = [f"⚠️ 质检未通过", f"「{result.title}」", ""]
            for issue in result.issues:
                lines.append(f"{issue.severity} {issue.rule}: {issue.description}")
                lines.append(f"💡 {issue.suggestion}")
            report_text = "\n".join(lines)

        # 写回表格
        actual_record_id = record.get("record_id", record_id)
        written = False
        if write_back and actual_record_id:
            feishu.ensure_field(app_token, table_id, "是否通过", 1)
            feishu.ensure_field(app_token, table_id, "质检备注", 1)

            pass_status = "✅通过" if result.passed else "❌不通过"
            notes = []
            for issue in result.issues:
                notes.append(f"{issue.severity} {issue.rule}: {issue.description}\n💡 {issue.suggestion}")
            note_text = "\n\n".join(notes) if notes else "无问题"

            feishu.update_record(app_token, table_id, actual_record_id, {
                "是否通过": pass_status,
                "质检备注": note_text,
            })
            written = True
            report_text += "\n\n✅ 已将结果写入表格"

        return jsonify({
            "passed": result.passed,
            "issues": len(result.issues),
            "written": written,
            "report": report_text,
        })
    except Exception as e:
        logger.error(f"单条质检失败: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ──── 启动 ────
if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("🤖 飞书质检机器人启动")
    logger.info(f"   端口: {config.PORT}")
    logger.info(f"   Webhook: http://localhost:{config.PORT}/webhook/event")
    logger.info("=" * 50)
    app.run(host="0.0.0.0", port=config.PORT, debug=config.DEBUG)
