# `bid.niyoufei.com` 部署说明

本文按当前要上线的配置编写：

- 域名：`bid.niyoufei.com`
- 目标服务器 IP：`199.180.118.204`
- DNS 记录：`A bid -> 199.180.118.204`
- Cloudflare：`Proxied`
- 检查时间：`2026-03-23`

## 1. 推荐部署结构

推荐使用：

- 应用：`app.main`
- 应用监听：`127.0.0.1:8000`
- 反向代理：`Caddy`
- 对外域名：`https://bid.niyoufei.com`

这样做的好处：

- Python 应用不直接暴露公网
- Caddy 负责 HTTPS 和域名接入
- 外部只访问 `80/443`
- 应用仍然只在本机回环地址上监听

## 2. 服务器上要做的事

假设服务器是 Ubuntu / Debian：

### 2.1 上传项目

把仓库上传到：

```text
/opt/zhifei/ZhiFei_BizSystem
```

### 2.2 安装 Python 依赖

```bash
cd /opt/zhifei/ZhiFei_BizSystem
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 2.3 安装 systemd 服务与环境变量

复制下面这个文件到系统目录：

- 源文件：`deploy/systemd/zhifei-bid.service`
- 目标文件：`/etc/systemd/system/zhifei-bid.service`

同时建议先复制环境变量样板：

- 源文件：`deploy/systemd/zhifei-bid.env.example`
- 目标文件：`/etc/zhifei-bid.env`

这份样板已经包含本轮新增的生产安全开关：

- `ZHIFEI_PRODUCTION_MODE=1`
- `ZHIFEI_ALLOWED_HOSTS=bid.niyoufei.com`
- `ZHIFEI_MAX_UPLOAD_MB=64`
- `ZHIFEI_REQUIRE_API_KEYS=1`

当前 `zhifei-bid.service` 已经配置为从 `/etc/zhifei-bid.env` 读取运行参数，所以：

- 修改域名、端口、上传限制时，优先改 `/etc/zhifei-bid.env`
- 改完后执行 `systemctl daemon-reload && systemctl restart zhifei-bid`

然后执行：

```bash
sudo systemctl daemon-reload
sudo systemctl enable zhifei-bid
sudo systemctl start zhifei-bid
sudo systemctl status zhifei-bid
```

### 2.4 安装 Caddy

复制下面这个文件：

- 源文件：`deploy/caddy/Caddyfile.bid.niyoufei.com`
- 目标文件：`/etc/caddy/Caddyfile`

然后执行：

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl restart caddy
sudo systemctl status caddy
```

### 2.5 防火墙与云安全组

至少放通：

- `80/tcp`
- `443/tcp`

如果服务器启用了 `ufw`，可执行：

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw status
```

## 3. Cloudflare / DNS 应该怎么填

你截图里的目标配置就是这套系统当前应使用的域名入口：

建议检查：

- `Type`: `A`
- `Name`: `bid`
- `Content`: `199.180.118.204`
- `Proxy status`: `Proxied`
- `TTL`: `Auto`

如果你使用 Cloudflare：

- 先确保源站上 `Caddy` 已正常运行，再开启橙云代理
- `SSL/TLS` 模式建议使用 `Full (strict)`
- 若 Cloudflare 仍报 `521`，先回服务器检查 `caddy` 和防火墙，不要先改应用代码

## 4. 局部核验命令

在服务器上执行：

```bash
curl -I http://127.0.0.1:8000/
curl -I http://127.0.0.1
curl -I https://bid.niyoufei.com
```

判断规则：

- 如果 `127.0.0.1:8000` 不通：应用没起来
- 如果 `127.0.0.1:8000` 通，但 `127.0.0.1` 不通：Caddy 没起来
- 如果本机都通，但外部域名还是 `521`：防火墙或 Cloudflare 源站配置有问题

## 5. 最终应达到的状态

成功后应满足：

1. `curl -I http://127.0.0.1:8000/` 返回 `200`
2. `systemctl status zhifei-bid` 正常
3. `systemctl status caddy` 正常
4. `https://bid.niyoufei.com` 可直接打开系统

## 6. 当前仓库中的域名部署文件

- `deploy/caddy/Caddyfile.bid.niyoufei.com`
- `deploy/systemd/zhifei-bid.service`
- `deploy/systemd/zhifei-bid.env.example`

如果后续你要我继续，我下一步可以直接按这套配置继续收口成：

- 一份服务器初始化命令清单
- 一份完整的上线核验清单
- 一份 Cloudflare 后台应如何填写的具体配置单
