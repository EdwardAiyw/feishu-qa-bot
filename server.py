"""
飞书质检机器人 Webhook 服务
接收飞书事件 → 读取表格 → 执行质检 → 返回报告
"""
import os
import json
import logging
import hashlib
import time
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
    """自动检测字段映射 — 动态适配任意表格结构
    
    匹配策略（按优先级）：
    1. 精确前缀匹配（如"题目"开头）
    2. 关键词包含匹配（如包含"checklist"）
    3. 模糊匹配（如"产物"相关列）
    
    找不到的字段不映射，对应规则自动跳过。
    """
    mapping = {}

    # 优先级1：精确匹配
    for name in field_names:
        ns = name.strip()
        # 题目列：以"题目"开头，排除"题目领域"
        if ns.startswith("题目") and "领域" not in ns:
            mapping["title"] = name
            break

    # 如果没找到"题目"开头的，尝试模糊匹配
    if "title" not in mapping:
        for name in field_names:
            ns = name.strip().lower()
            if "题目" in ns and "领域" not in ns and "类型" not in ns:
                mapping["title"] = name
                break

    # 附件内容
    for name in field_names:
        ns = name.strip()
        if "附件" in ns and ("内容" in ns or "总结" in ns):
            mapping["attachments"] = name
            break

    # 产物内容
    for name in field_names:
        ns = name.strip()
        if "产物" in ns and ("内容" in ns or "总结" in ns):
            mapping["output"] = name
            break

    # 任务类型
    for name in field_names:
        ns = name.strip()
        if "任务类型" in ns:
            mapping["task_type"] = name
            break

    # 打分checklist
    for name in field_names:
        nl = name.strip().lower()
        if "checklist" in nl or ("打分" in name and ("checklist" in name or "check" in nl)):
            mapping["checklist"] = name
            break
    # 兜底：如果没找到checklist，尝试"评分"相关
    if "checklist" not in mapping:
        for name in field_names:
            if "评分" in name and ("标准" in name or "规则" in name or "check" in name.lower()):
                mapping["checklist"] = name
                break

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
        # 支持 limit 参数限制读取量（避免大表格超时）
        limit = data.get("limit", 0)
        if limit > 0:
            records = feishu.read_records(app_token, table_id, page_size=min(limit, 100))
            records = records[:limit]
        else:
            records = feishu.read_records(app_token, table_id)

        # 解析并过滤空行
        parsed_records = []
        valid_record_ids = []
        field_names = [f["name"] for f in fields]
        checker.field_mapping = auto_detect_fields(field_names)

        for r in records:
            parsed = feishu.get_record_values(r, fields)
            title = checker._get_field(parsed, "title")
            if title and title.strip():  # 跳过题目为空的行
                parsed_records.append(parsed)
                valid_record_ids.append(r.get("record_id", ""))

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
                record_id = valid_record_ids[i] if i < len(valid_record_ids) else ""
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
    """单条记录质检 — 支持 body / query param / path"""
    return _do_single_check()


@app.route("/qa/<uid>", methods=["POST"])
def qa_by_uid(uid):
    """最简接口：uid 在 URL 路径中（支持 UID 列值或 record_id）"""
    return _do_single_check(path_uid=uid)


@app.route("/rec/<record_id>", methods=["POST"])
def qa_by_record_id(record_id):
    """最简接口：record_id 在 URL 路径中"""
    return _do_single_check(path_record_id=record_id)


def _do_single_check(path_uid=None, path_record_id=None):
    data = request.get_json(force=True) if request.is_json else {}
    url = data.get("url", "") or request.args.get("url", "") or "https://my.feishu.cn/base/HbVxbeTdJabbFiszEH4czSTdnfh"
    record_id = path_record_id or data.get("record_id", "") or request.args.get("record_id", "")
    uid = path_uid or data.get("uid") or request.args.get("uid")
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




@app.route("/debug/raw", methods=["POST"])
def debug_raw():
    """临时调试：读取第一条记录的原始数据"""
    import requests as req
    data = request.get_json(force=True) if request.is_json else {}
    url = data.get("url", "")
    table_info = feishu.parse_bitable_url(url)
    if not table_info:
        return jsonify({"error": "invalid url"}), 400
    try:
        app_token = table_info["app_token"]
        table_id = table_info.get("table_id", "")
        if not table_id:
            tables = feishu.list_tables(app_token)
            table_id = tables[0]["table_id"]
        token = feishu.get_tenant_access_token()
        api_url = f"{feishu.base_url}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
        resp = req.get(api_url, headers=feishu._headers(), params={"page_size": 1}, timeout=10)
        d = resp.json()
        items = d.get("data", {}).get("items", [])
        if items:
            r = items[0]
            return jsonify({
                "code": d.get("code"),
                "record_id": r.get("record_id"),
                "fields_count": len(r.get("fields", {})),
                "sample_keys": list(r.get("fields", {}).keys())[:5],
                "uid_value": r.get("fields", {}).get("UID", "MISSING"),
            })
        return jsonify({"code": d.get("code"), "items": 0, "raw": str(d)[:300]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ──── 表格发现（查看实际字段和映射）────
@app.route("/discover", methods=["POST"])
def discover_table():
    """发现表格结构：显示所有字段和自动映射结果"""
    data = request.get_json(force=True) if request.is_json else {}
    url = data.get("url", "")

    # 判断是 bitable 还是 sheet
    sheet_info = feishu.parse_sheet_url(url)
    bitable_info = feishu.parse_bitable_url(url)

    if bitable_info:
        app_token = bitable_info["app_token"]
        try:
            tables = feishu.list_tables(app_token)
            all_results = []
            for t in tables:
                tid = t["table_id"]
                tname = t["name"]
                fields = feishu.read_fields(app_token, tid)
                field_names = [f["name"] for f in fields]
                mapping = auto_detect_fields(field_names)
                # 只保留前30个字段名（截断太长的）
                preview = [n[:40] for n in field_names[:20]]
                all_results.append({
                    "table_id": tid,
                    "table_name": tname,
                    "field_count": len(field_names),
                    "fields_preview": preview,
                    "mapping": {k: v[:40] for k, v in mapping.items()},
                    "mapped_rules": list(mapping.keys()),
                })
            return jsonify({"type": "bitable", "tables": all_results})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    elif sheet_info:
        spreadsheet_token = sheet_info["spreadsheet_token"]
        try:
            meta = feishu.get_sheet_meta(spreadsheet_token)
            sheets = meta.get("sheets", [])
            all_results = []
            for s in sheets:
                sid = s["sheetId"]
                sname = s["title"]
                headers = feishu.read_sheet_values(spreadsheet_token, f"{sid}!A1:Z1")
                if headers:
                    field_names = [h for h in headers[0] if h]
                    mapping = auto_detect_fields(field_names)
                    all_results.append({
                        "sheet_id": sid,
                        "sheet_name": sname,
                        "field_count": len(field_names),
                        "fields_preview": [n[:40] for n in field_names[:20]],
                        "mapping": {k: v[:40] for k, v in mapping.items()},
                        "mapped_rules": list(mapping.keys()),
                    })
            return jsonify({"type": "sheet", "sheets": all_results})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    else:
        return jsonify({"error": "无法解析链接"}), 400

# ──── 电子表格质检 ────

# 这个电子表格的列映射（根据表头自动检测）
SHEET_FIELD_MAP = {
    "title": "B",        # 题目
    "attachments": "L",   # 附件内容
    "output": "N",        # 产物内容
    "checklist": "P",     # 打分checklist
    "qa_result": "R",     # 内部质检（写入）
    "qa_note": "S",       # 原因（写入）
}


def detect_sheet_field_map(headers: list) -> dict:
    """根据表头自动检测列映射"""
    mapping = {}
    for i, h in enumerate(headers):
        if not h:
            continue
        h_str = str(h).strip()
        h_lower = h_str.lower()
        col = feishu.col_index_to_letter(i)

        # 题目列：表头以"题目"开头（排除"题目领域"等）
        if h_str.startswith("题目") and "领域" not in h_str:
            mapping["title"] = col
        elif "附件内容" in h_str or ("附件" in h_str and "总结" in h_str):
            mapping["attachments"] = col
        elif "产物内容" in h_str or ("产物" in h_str and "总结" in h_str):
            mapping["output"] = col
        elif "checklist" in h_lower or ("打分" in h_str and "checklist" in h_str):
            mapping["checklist"] = col
        elif h_str == "内部质检":
            mapping["qa_result"] = col
        elif h_str == "原因":
            mapping["qa_note"] = col

    return mapping


@app.route("/check/sheet", methods=["POST"])
def check_sheet():
    """
    电子表格质检
    Body: {
        "url": "飞书表格/wiki链接",
        "row": 2,              # 可选，指定行号（不传则检查所有数据行）
        "sheet_id": "ff061b"   # 可选，不传则用第一个 sheet
    }
    """
    data = request.get_json(force=True) if request.is_json else {}
    url = data.get("url", "")
    target_row = data.get("row")
    target_sheet_id = data.get("sheet_id")

    if not url:
        return jsonify({"error": "缺少 url 参数"}), 400

    # 1. 解析 URL
    sheet_info = feishu.parse_sheet_url(url)
    if not sheet_info:
        return jsonify({"error": "无法解析表格链接，请确认是飞书电子表格或 wiki 链接"}), 400

    spreadsheet_token = sheet_info["spreadsheet_token"]

    try:
        # 2. 获取 sheet 元信息
        meta = feishu.get_sheet_meta(spreadsheet_token)
        sheets = meta.get("sheets", [])
        if not sheets:
            return jsonify({"error": "表格中没有找到工作表"}), 400

        sheet = sheets[0]
        if target_sheet_id:
            sheet = next((s for s in sheets if s["sheetId"] == target_sheet_id), sheets[0])

        sheet_id = sheet["sheetId"]
        sheet_name = sheet["title"]
        col_count = sheet.get("columnCount", 26)
        row_count = sheet.get("rowCount", 100)

        # 3. 读取表头
        last_col = feishu.col_index_to_letter(min(col_count, 26) - 1)
        header_range = f"{sheet_id}!A1:{last_col}1"
        header_rows = feishu.read_sheet_values(spreadsheet_token, header_range)
        if not header_rows:
            return jsonify({"error": "无法读取表头"}), 400

        headers = header_rows[0]
        field_map = detect_sheet_field_map(headers)
        logger.info(f"Sheet 字段映射: {field_map}")

        # 检查必要字段
        if "title" not in field_map:
            return jsonify({"error": "未找到「题目」列，请检查表头"}), 400

        # 确定写入列
        qa_result_col = field_map.get("qa_result", "R")
        qa_note_col = field_map.get("qa_note", "S")

        # 4. 读取数据行
        data_range = f"{sheet_id}!A2:{last_col}{row_count}"
        all_rows = feishu.read_sheet_values(spreadsheet_token, data_range)
        logger.info(f"读取到 {len(all_rows)} 行数据")

        if not all_rows:
            return jsonify({"error": "表格中没有数据"}), 400

        # 5. 转换为 checker 可用的格式
        def get_cell(row, col_letter):
            idx = ord(col_letter) - 65
            if idx < len(row):
                val = row[idx]
                return str(val) if val else ""
            return ""

        def row_to_record(row):
            return {
                "题目": get_cell(row, field_map.get("title", "B")),
                "附件内容": get_cell(row, field_map.get("attachments", "L")),
                "产物内容": get_cell(row, field_map.get("output", "N")),
                "打分checklist": get_cell(row, field_map.get("checklist", "P")),
            }

        # 6. 执行质检
        results_to_write = []

        if target_row:
            # 单行质检
            row_idx = int(target_row) - 2  # 0-based, skip header
            if row_idx < 0 or row_idx >= len(all_rows):
                return jsonify({"error": f"行号 {target_row} 超出范围（数据行 2-{len(all_rows)+1}）"}), 400

            record = row_to_record(all_rows[row_idx])
            result = checker.check_record(target_row, record)
            pass_status = "✅通过" if result.passed else "❌不通过"
            notes = [f"{iss.severity} {iss.rule}: {iss.description}\n💡 {iss.suggestion}" for iss in result.issues]
            note_text = "\n\n".join(notes) if notes else "无问题"

            actual_row = target_row
            results_to_write.append({
                "range": f"{sheet_id}!{qa_result_col}{actual_row}:{qa_note_col}{actual_row}",
                "values": [[pass_status, note_text]],
            })
        else:
            # 全量质检
            for i, row in enumerate(all_rows):
                if not any(row):  # 跳过空行
                    continue
                record = row_to_record(row)
                result = checker.check_record(i + 2, record)
                pass_status = "✅通过" if result.passed else "❌不通过"
                notes = [f"{iss.severity} {iss.rule}: {iss.description}\n💡 {iss.suggestion}" for iss in result.issues]
                note_text = "\n\n".join(notes) if notes else "无问题"

                actual_row = i + 2
                results_to_write.append({
                    "range": f"{sheet_id}!{qa_result_col}{actual_row}:{qa_note_col}{actual_row}",
                    "values": [[pass_status, note_text]],
                })

        # 7. 批量写回
        for item in results_to_write:
            feishu.write_sheet_values(spreadsheet_token, item["range"], item["values"])
            time.sleep(0.1)  # 避免 rate limit

        logger.info(f"写回 {len(results_to_write)} 行质检结果")

        # 8. 生成报告
        report_lines = [f"📊 电子表格质检报告", f"📋 工作表：{sheet_name}"]
        report_lines.append(f"- 检查行数：{len(results_to_write)}")
        report_lines.append(f"- 写入列：{qa_result_col}（内部质检）、{qa_note_col}（原因）")

        return jsonify({
            "total": len(results_to_write),
            "sheet_name": sheet_name,
            "qa_result_col": qa_result_col,
            "qa_note_col": qa_note_col,
            "report": "\n".join(report_lines),
        })

    except Exception as e:
        logger.error(f"电子表格质检失败: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ──── 启动 ────
if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("🤖 飞书质检机器人启动")
    logger.info(f"   端口: {config.PORT}")
    logger.info(f"   Webhook: http://localhost:{config.PORT}/webhook/event")
    logger.info("=" * 50)
    app.run(host="0.0.0.0", port=config.PORT, debug=config.DEBUG)
