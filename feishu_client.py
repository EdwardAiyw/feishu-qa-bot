"""
飞书 API 客户端 — 封装认证和表格读写
"""
import re
import time
import requests
import config


class FeishuClient:
    """飞书 Open API 客户端"""

    def __init__(self):
        self.base_url = config.FEISHU_BASE_URL
        self.app_id = config.FEISHU_APP_ID
        self.app_secret = config.FEISHU_APP_SECRET
        self._token = None
        self._token_expires = 0

    def get_tenant_access_token(self) -> str:
        """获取 tenant_access_token（自动缓存）"""
        if self._token and time.time() < self._token_expires:
            return self._token

        url = f"{self.base_url}/auth/v3/tenant_access_token/internal"
        resp = requests.post(url, json={
            "app_id": self.app_id,
            "app_secret": self.app_secret,
        })
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"获取 token 失败: {data}")

        self._token = data["tenant_access_token"]
        self._token_expires = time.time() + data.get("expire", 7200) - 300
        return self._token

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.get_tenant_access_token()}",
            "Content-Type": "application/json",
        }

    def parse_bitable_url(self, url: str) -> dict | None:
        """
        解析飞书多维表格链接
        示例: https://xxx.feishu.cn/base/KyIwbVsHfa5RIPsf0vLcwUyOntg
        返回: {"app_token": "KyIwbVsHfa5RIPsf0vLcwUyOntg"}
        """
        pattern = r"feishu\.cn/base/([a-zA-Z0-9]+)"
        match = re.search(pattern, url)
        if match:
            return {"app_token": match.group(1)}

        # 也支持 table 链接
        pattern2 = r"feishu\.cn/base/([a-zA-Z0-9]+)\?.*table=([a-zA-Z0-9]+)"
        match2 = re.search(pattern2, url)
        if match2:
            return {
                "app_token": match2.group(1),
                "table_id": match2.group(2),
            }
        return None

    def list_tables(self, app_token: str) -> list:
        """获取多维表格中的所有数据表"""
        url = f"{self.base_url}/bitable/v1/apps/{app_token}/tables"
        resp = requests.get(url, headers=self._headers())
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"获取表格列表失败: {data}")

        tables = data.get("data", {}).get("items", [])
        return [{"table_id": t["table_id"], "name": t.get("name", "")} for t in tables]

    def read_record(self, app_token: str, table_id: str, record_id: str) -> dict:
        """读取单条记录"""
        url = f"{self.base_url}/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"
        resp = requests.get(url, headers=self._headers())
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"读取记录失败: {data}")
        return data.get("data", {}).get("record", {})

    def read_records(self, app_token: str, table_id: str, page_size: int = 100) -> list:
        """读取多维表格的所有记录"""
        url = f"{self.base_url}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
        all_records = []
        page_token = None

        while True:
            params = {"page_size": page_size}
            if page_token:
                params["page_token"] = page_token

            resp = requests.get(url, headers=self._headers(), params=params)
            data = resp.json()

            if data.get("code") != 0:
                raise Exception(f"读取记录失败: {data}")

            items = data.get("data", {}).get("items", [])
            all_records.extend(items)

            if not data.get("data", {}).get("has_more", False):
                break
            page_token = data["data"].get("page_token")

        return all_records

    def read_fields(self, app_token: str, table_id: str) -> list:
        """获取表格的字段（列）定义"""
        url = f"{self.base_url}/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
        resp = requests.get(url, headers=self._headers())
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"获取字段失败: {data}")

        fields = data.get("data", {}).get("items", [])
        return [{"field_id": f["field_id"], "name": f.get("field_name", ""), "type": f.get("type", 0)} for f in fields]

    def get_record_values(self, record: dict, fields: list) -> dict:
        """从记录中提取字段值，返回 {字段名: 值} 的字典"""
        result = {}
        field_id_to_name = {f["field_id"]: f["name"] for f in fields}
        field_names_set = {f["name"] for f in fields}

        for key, value in record.get("fields", {}).items():
            # 兼容两种情况：
            # 1. key 是 field_id（正常多维表格）
            # 2. key 已经是 field_name（从电子表格转换来的多维表格）
            if key in field_names_set:
                field_name = key
            else:
                field_name = field_id_to_name.get(key, key)

            # 处理不同类型的字段值
            if isinstance(value, list):
                # 多选、人员等字段
                text_parts = []
                for item in value:
                    if isinstance(item, dict):
                        text_parts.append(item.get("text", str(item)))
                    else:
                        text_parts.append(str(item))
                result[field_name] = ", ".join(text_parts)
            elif isinstance(value, dict):
                result[field_name] = value.get("text", str(value))
            else:
                result[field_name] = str(value) if value else ""

        return result

    def send_message(self, receive_id: str, msg_type: str, content: str, receive_id_type: str = "open_id"):
        """发送消息给用户"""
        url = f"{self.base_url}/im/v1/messages"
        params = {"receive_id_type": receive_id_type}
        body = {
            "receive_id": receive_id,
            "msg_type": msg_type,
            "content": content,
        }
        resp = requests.post(url, headers=self._headers(), params=params, json=body)
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"发送消息失败: {data}")
        return data

    def reply_message(self, message_id: str, msg_type: str, content: str):
        """回复消息"""
        url = f"{self.base_url}/im/v1/messages/{message_id}/reply"
        body = {
            "msg_type": msg_type,
            "content": content,
        }
        resp = requests.post(url, headers=self._headers(), json=body)
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"回复消息失败: {data}")
        return data

    # ──── 电子表格（Sheet）方法 ────

    def parse_sheet_url(self, url: str) -> dict | None:
        """
        解析飞书电子表格链接或 wiki 链接
        支持格式:
          - https://xxx.feishu.cn/sheets/I1uFsT6AXhmmHjtj14ScydZXnqc
          - https://xxx.feishu.cn/wiki/GRrdw2gnPivxzLk8HZkcgMTRnPh (wiki 中的 sheet)
        返回: {"spreadsheet_token": "xxx", "sheet_id": "yyy" | None}
        """
        import re
        # Direct sheet URL
        pattern = r"feishu\.cn/sheets/([a-zA-Z0-9]+)"
        match = re.search(pattern, url)
        if match:
            return {"spreadsheet_token": match.group(1)}

        # Wiki URL — need to resolve to sheet token
        pattern2 = r"feishu\.cn/wiki/([a-zA-Z0-9]+)"
        match2 = re.search(pattern2, url)
        if match2:
            wiki_token = match2.group(1)
            node = self.get_wiki_node(wiki_token)
            if node and node.get("obj_type") == "sheet":
                return {"spreadsheet_token": node["obj_token"], "wiki_token": wiki_token}
            return None
        return None

    def get_wiki_node(self, wiki_token: str) -> dict | None:
        """获取 wiki 节点信息"""
        url = f"{self.base_url}/wiki/v2/spaces/get_node"
        resp = requests.get(url, headers=self._headers(), params={"token": wiki_token})
        data = resp.json()
        if data.get("code") != 0:
            return None
        return data.get("data", {}).get("node", {})

    def get_sheet_meta(self, spreadsheet_token: str) -> dict:
        """获取电子表格元信息（含所有 sheet 列表）"""
        url = f"{self.base_url}/sheets/v2/spreadsheets/{spreadsheet_token}/metainfo"
        resp = requests.get(url, headers=self._headers())
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"获取表格元信息失败: {data}")
        return data.get("data", {})

    def read_sheet_values(self, spreadsheet_token: str, range_str: str) -> list:
        """
        读取电子表格单元格数据
        range_str 格式: "sheetId!A1:Z100" 或 "sheetId!A:Z"
        返回: [[cell1, cell2, ...], [cell1, cell2, ...], ...]
        """
        url = f"{self.base_url}/sheets/v2/spreadsheets/{spreadsheet_token}/values/{range_str}"
        resp = requests.get(url, headers=self._headers(), params={"valueRenderOption": "ToString"})
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"读取表格数据失败: {data}")
        return data.get("data", {}).get("valueRange", {}).get("values", [])

    def write_sheet_values(self, spreadsheet_token: str, range_str: str, values: list) -> dict:
        """
        写入电子表格单元格数据
        values: [[cell1, cell2], [cell1, cell2], ...]
        """
        url = f"{self.base_url}/sheets/v2/spreadsheets/{spreadsheet_token}/values"
        body = {
            "valueRange": {
                "range": range_str,
                "values": values,
            }
        }
        resp = requests.put(url, headers=self._headers(), json=body)
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"写入表格数据失败: {data}")
        return data

    @staticmethod
    def col_index_to_letter(index: int) -> str:
        """列索引(0-based)转字母: 0→A, 25→Z, 26→AA"""
        result = ""
        while index >= 0:
            result = chr(65 + index % 26) + result
            index = index // 26 - 1
        return result

    # ──── 多维表格写入方法 ────

    def create_field(self, app_token: str, table_id: str, field_name: str, field_type: int = 1) -> dict:
        """
        在表格中创建新字段（列）
        field_type: 1=文本, 2=数字, 3=单选, 4=多选, 5=日期, 7=复选框, 11=人员, 15=链接
        """
        url = f"{self.base_url}/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
        body = {
            "field_name": field_name,
            "type": field_type,
        }
        resp = requests.post(url, headers=self._headers(), json=body)
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"创建字段失败: {data}")
        return data.get("data", {}).get("field", {})

    def find_field(self, app_token: str, table_id: str, field_name: str) -> dict | None:
        """查找指定名称的字段"""
        fields = self.read_fields(app_token, table_id)
        for f in fields:
            if f["name"] == field_name:
                return f
        return None

    def ensure_field(self, app_token: str, table_id: str, field_name: str, field_type: int = 1) -> dict:
        """确保字段存在，不存在则创建"""
        existing = self.find_field(app_token, table_id, field_name)
        if existing:
            return existing
        return self.create_field(app_token, table_id, field_name, field_type)

    def update_record(self, app_token: str, table_id: str, record_id: str, fields: dict) -> dict:
        """
        更新单条记录的字段值
        fields: {"字段名": "值", ...}
        """
        url = f"{self.base_url}/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"
        body = {"fields": fields}
        resp = requests.put(url, headers=self._headers(), json=body)
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"更新记录失败: {data}")
        return data

    def batch_update_records(self, app_token: str, table_id: str, records: list) -> dict:
        """
        批量更新记录
        records: [{"record_id": "xxx", "fields": {"字段名": "值"}}, ...]
        最多一次 500 条
        """
        url = f"{self.base_url}/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_update"
        body = {"records": records}
        resp = requests.post(url, headers=self._headers(), json=body)
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"批量更新失败: {data}")
        return data
