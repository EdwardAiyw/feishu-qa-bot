"""通过 API 给应用添加表格写入权限"""
import requests
import json

# 获取 token
resp = requests.post('https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal', json={
    'app_id': 'cli_aaa88cb40ea11bc3',
    'app_secret': 'Rhx0AxYTxpWFTbo8qUltxgCIkQlMoluG'
})
token = resp.json()['tenant_access_token']
print(f'Token OK')

headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
app_token = 'HbVxbeTdJabbFiszEH4czSTdnfh'

# 方法1: 添加应用为表格成员
print('\n--- 方法1: 添加成员 ---')
resp1 = requests.post(
    f'https://open.feishu.cn/open-apis/drive/v1/permissions/{app_token}/members?type=bitable',
    headers=headers,
    json={
        'member_type': 'chat',
        'member_id': 'cli_aaa88cb40ea11bc3',
        'perm': 'full_access'
    }
)
print(f'Code: {resp1.json().get("code")}, Msg: {resp1.json().get("msg","")}')

# 方法2: 使用 open_id
print('\n--- 方法2: 使用 app_open_id ---')
resp2 = requests.post(
    f'https://open.feishu.cn/open-apis/drive/v1/permissions/{app_token}/members?type=bitable',
    headers=headers,
    json={
        'member_type': 'userid',
        'member_id': 'cli_aaa88cb40ea11bc3',
        'perm': 'full_access'
    }
)
print(f'Code: {resp2.json().get("code")}, Msg: {resp2.json().get("msg","")}')

# 方法3: 使用 apptype
print('\n--- 方法3: 使用 apptype ---')
resp3 = requests.post(
    f'https://open.feishu.cn/open-apis/drive/v1/permissions/{app_token}/members?type=bitable',
    headers=headers,
    json={
        'member_type': 'app',
        'member_id': 'cli_aaa88cb40ea11bc3',
        'perm': 'full_access'
    }
)
print(f'Code: {resp3.json().get("code")}, Msg: {resp3.json().get("msg","")}')

# 方法4: 列出当前权限
print('\n--- 当前权限列表 ---')
resp4 = requests.get(
    f'https://open.feishu.cn/open-apis/drive/v1/permissions/{app_token}/members?type=bitable',
    headers=headers
)
print(json.dumps(resp4.json(), indent=2, ensure_ascii=False)[:500])
