"""测试飞书表格写入权限"""
import requests
import json

# 获取 token
resp = requests.post('https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal', json={
    'app_id': 'cli_aaa88cb40ea11bc3',
    'app_secret': 'Rhx0AxYTxpWFTbo8qUltxgCIkQlMoluG'
})
token = resp.json()['tenant_access_token']
print(f'Token: {token[:20]}...')

headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}

# 测试读取
app_token = 'HbVxbeTdJabbFiszEH4czSTdnfh'
table_id = 'tblCjV6tM6sX6Jz3'

resp = requests.get(f'https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records?page_size=1', headers=headers)
data = resp.json()
print(f'读取: code={data.get("code")}, msg={data.get("msg","")}')

if data.get('code') == 0:
    record_id = data['data']['items'][0]['record_id']
    print(f'Record ID: {record_id}')
    
    # 测试创建字段
    resp2 = requests.post(f'https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields', 
        headers=headers, 
        json={'field_name': '是否通过', 'type': 1})
    data2 = resp2.json()
    print(f'创建字段: code={data2.get("code")}, msg={data2.get("msg","")}')
    
    # 测试更新记录
    resp3 = requests.put(f'https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}',
        headers=headers,
        json={'fields': {'是否通过': '✅通过'}})
    data3 = resp3.json()
    print(f'更新记录: code={data3.get("code")}, msg={data3.get("msg","")}')
