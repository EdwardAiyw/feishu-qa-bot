# 飞书质检机器人 - 云服务器部署指南

## 一、选择云服务器

### 推荐方案（免费/低成本）

| 方案 | 价格 | 特点 |
|------|------|------|
| **阿里云** | 新用户免费3个月 | 国内访问快，飞书推荐 |
| **腾讯云** | 新用户免费3个月 | 国内访问快 |
| **华为云** | 新用户免费3个月 | 国内访问快 |
| **Railway** | 免费500小时/月 | 国外平台，无需备案 |
| **Render** | 免费版可用 | 国外平台，无需备案 |
| **Fly.io** | 免费版可用 | 国外平台，无需备案 |

### 推荐配置
- **CPU**: 1核
- **内存**: 1GB
- **硬盘**: 20GB
- **系统**: Ubuntu 22.04 LTS
- **带宽**: 1Mbps 足够

---

## 二、部署步骤（以阿里云为例）

### 第 1 步：购买服务器

1. 打开 https://www.aliyun.com
2. 注册/登录账号
3. 搜索「云服务器 ECS」
4. 选择：
   - 地区：华东1（杭州）或华东2（上海）
   - 系统：Ubuntu 22.04 LTS
   - 配置：1核1G（够用了）
5. 购买后会给你一个**公网IP**，类似：`47.xxx.xxx.xxx`

### 第 2 步：连接服务器

Windows 用户推荐使用：
- **MobaXterm**（免费）：https://mobaxterm.mobatek.net
- **PuTTY**：https://www.putty.org
- **Windows Terminal** + SSH

连接命令：
```bash
ssh root@你的公网IP
```

### 第 3 步：安装环境

```bash
# 更新系统
apt update && apt upgrade -y

# 安装 Python 3.11
apt install -y python3.11 python3.11-venv python3-pip git

# 创建项目目录
mkdir -p /opt/feishu-qa-bot
cd /opt/feishu-qa-bot
```

### 第 4 步：上传代码

方式一：使用 git（推荐）
```bash
cd /opt
git clone https://github.com/你的用户名/feishu-qa-bot.git
cd feishu-qa-bot
```

方式二：使用 scp 上传
```bash
# 在本地 Windows 执行
scp -r C:\Users\edawr\feishu-qa-bot root@你的公网IP:/opt/feishu-qa-bot
```

方式三：使用 MobaXterm 的文件管理器直接拖拽上传

### 第 5 步：安装依赖

```bash
cd /opt/feishu-qa-bot

# 创建虚拟环境
python3.11 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 第 6 步：配置环境变量

```bash
# 创建 .env 文件
cat > .env << 'EOF'
FEISHU_APP_ID=cli_aaa88cb40ea11bc3
FEISHU_APP_SECRET=Rhx0AxYTxpWFTbo8qUltxgCIkQlMoluG
PORT=5000
DEBUG=false
EOF
```

### 第 7 步：测试运行

```bash
# 激活虚拟环境
source venv/bin/activate

# 启动服务
python server.py
```

如果看到 `🤖 飞书质检机器人启动` 说明成功了。

按 `Ctrl+C` 停止，接下来配置后台运行。

### 第 8 步：配置后台运行（systemd）

```bash
# 创建 systemd 服务文件
cat > /etc/systemd/system/feishu-qa-bot.service << 'EOF'
[Unit]
Description=Feishu QA Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/feishu-qa-bot
Environment=PATH=/opt/feishu-qa-bot/venv/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/opt/feishu-qa-bot/venv/bin/python server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 启用并启动服务
systemctl daemon-reload
systemctl enable feishu-qa-bot
systemctl start feishu-qa-bot

# 查看状态
systemctl status feishu-qa-bot

# 查看日志
journalctl -u feishu-qa-bot -f
```

### 第 9 步：配置 Nginx 反向代理（可选但推荐）

```bash
# 安装 Nginx
apt install -y nginx

# 配置 Nginx
cat > /etc/nginx/sites-available/feishu-qa-bot << 'EOF'
server {
    listen 80;
    server_name 你的域名或IP;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
EOF

# 启用配置
ln -sf /etc/nginx/sites-available/feishu-qa-bot /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# 测试配置
nginx -t

# 重启 Nginx
systemctl restart nginx
```

### 第 10 步：配置防火墙

```bash
# 阿里云安全组需要放行端口
# 控制台 -> 云服务器 ECS -> 安全组 -> 添加规则

# 放行 80 端口（HTTP）
ufw allow 80/tcp

# 放行 443 端口（HTTPS，如果需要）
ufw allow 443/tcp

# 放行 5000 端口（如果不用 Nginx）
ufw allow 5000/tcp
```

---

## 三、更新代码

```bash
cd /opt/feishu-qa-bot
git pull  # 如果用 git

# 或者重新上传代码后：
source venv/bin/activate
pip install -r requirements.txt  # 如果有新依赖

# 重启服务
systemctl restart feishu-qa-bot
```

---

## 四、配置飞书自动化

部署完成后，你的服务地址就是：`http://你的公网IP`

### 更新飞书自动化配置

1. 打开多维表格 → 自动化
2. 编辑「运行质检」自动化
3. 更新 HTTP 请求配置：
   - URL：`http://你的公网IP/test/check`
   - 方法：`POST`
   - Header：`Content-Type: application/json`
   - Body：`{"url":"https://my.feishu.cn/base/HbVxbeTdJabbFiszEH4czSTdnfh","write_back":true}`
4. 保存

### 配置飞书机器人 Webhook

1. 登录飞书开放平台
2. 进入你的应用
3. 「事件与回调」→「事件配置」
4. 请求地址填：`http://你的公网IP/webhook/event`
5. 验证并保存

---

## 五、常见问题

### Q1: 服务启动失败？
```bash
# 查看错误日志
journalctl -u feishu-qa-bot -n 50
```

### Q2: 端口被占用？
```bash
# 查看端口占用
lsof -i :5000

# 杀掉进程
kill -9 PID
```

### Q3: 如何重启服务？
```bash
systemctl restart feishu-qa-bot
```

### Q4: 如何查看实时日志？
```bash
journalctl -u feishu-qa-bot -f
```

### Q5: 如何停止服务？
```bash
systemctl stop feishu-qa-bot
```

---

## 六、安全建议

1. **不要在代码中硬编码密钥** — 使用 .env 文件
2. **配置 HTTPS** — 使用 Let's Encrypt 免费证书
3. **限制 IP 访问** — 只允许飞书服务器 IP 访问
4. **定期更新系统** — `apt update && apt upgrade`
5. **使用强密码** — 服务器 root 密码

---

## 七、成本估算

| 项目 | 费用 |
|------|------|
| 云服务器（1核1G） | 免费~50元/月 |
| 域名（可选） | 50-100元/年 |
| SSL 证书 | 免费（Let's Encrypt） |
| **总计** | **0-50元/月** |

---

## 八、快速部署脚本

如果想一键部署，可以用以下脚本：

```bash
#!/bin/bash
# 一键部署脚本

set -e

echo "开始部署飞书质检机器人..."

# 安装依赖
apt update
apt install -y python3.11 python3.11-venv python3-pip git nginx

# 创建项目目录
mkdir -p /opt/feishu-qa-bot
cd /opt/feishu-qa-bot

# 克隆代码（替换为你的仓库地址）
git clone https://github.com/你的用户名/feishu-qa-bot.git .

# 创建虚拟环境
python3.11 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cat > .env << 'EOF'
FEISHU_APP_ID=cli_aaa88cb40ea11bc3
FEISHU_APP_SECRET=Rhx0AxYTxpWFTbo8qUltxgCIkQlMoluG
PORT=5000
DEBUG=false
EOF

# 配置 systemd
cat > /etc/systemd/system/feishu-qa-bot.service << 'EOF'
[Unit]
Description=Feishu QA Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/feishu-qa-bot
Environment=PATH=/opt/feishu-qa-bot/venv/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/opt/feishu-qa-bot/venv/bin/python server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 启动服务
systemctl daemon-reload
systemctl enable feishu-qa-bot
systemctl start feishu-qa-bot

# 配置 Nginx
cat > /etc/nginx/sites-available/feishu-qa-bot << 'EOF'
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
EOF

ln -sf /etc/nginx/sites-available/feishu-qa-bot /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
systemctl restart nginx

echo "========================================="
echo "部署完成！"
echo "服务地址: http://$(curl -s ifconfig.me)"
echo "========================================="
```

---

## 九、总结

1. 购买云服务器（阿里云/腾讯云）
2. 上传代码
3. 配置 systemd 后台运行
4. 配置 Nginx 反向代理
5. 更新飞书自动化配置
6. 完成！

部署完成后，飞书表格里的按钮就能正常使用了！🎉
