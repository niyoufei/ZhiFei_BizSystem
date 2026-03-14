# `bid.niyoufei.com` 部署说明

本文按当前已知信息编写：

- 域名：`bid.niyoufei.com`
- 目标服务器 IP：`199.180.118.204`
- 检查时间：`2026-03-13`

## 1. 当前阻塞点

当前 `bid.niyoufei.com` 解析结果不是 `199.180.118.204`，而是 `198.18.0.27`。

同时，`http://bid.niyoufei.com` 返回了 Cloudflare `521`，这表示：

- Cloudflare 已经接管了这个域名入口
- 但 Cloudflare 现在无法连到你的源站

所以当前问题不在应用代码本身，而在以下任一环节：

1. DNS A 记录没有改到 `199.180.118.204`
2. 虽然 DNS 已经挂到 Cloudflare，但 Cloudflare 的源站配置不是 `199.180.118.204`
3. 源站机器上没有监听 `80/443`
4. 源站防火墙没有放通 `80/443`
5. 反向代理（Caddy/Nginx）没有启动

## 2. 推荐部署结构

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

## 3. 服务器上要做的事

假设服务器是 Ubuntu / Debian：

### 3.1 上传项目

把仓库上传到：

```text
/opt/zhifei/ZhiFei_BizSystem
```

### 3.2 安装 Python 依赖

```bash
cd /opt/zhifei/ZhiFei_BizSystem
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 3.3 安装 systemd 服务

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

然后执行：

```bash
sudo systemctl daemon-reload
sudo systemctl enable zhifei-bid
sudo systemctl start zhifei-bid
sudo systemctl status zhifei-bid
```

### 3.4 安装 Caddy

复制下面这个文件：

- 源文件：`deploy/caddy/Caddyfile.bid.niyoufei.com`
- 目标文件：`/etc/caddy/Caddyfile`

然后执行：

```bash
sudo systemctl restart caddy
sudo systemctl status caddy
```

## 4. DNS 应该怎么改

你现在必须确认 Cloudflare / DNS 平台里的 `A` 记录最终指向：

```text
199.180.118.204
```

如果仍然指向别的地址，域名一定不通。

建议检查：

- `Type`: `A`
- `Name`: `bid`
- `Content`: `199.180.118.204`

如果你使用 Cloudflare：

- 先确保源站上 `Caddy` 已正常运行
- 再决定是否开启橙云代理
- `SSL/TLS` 模式建议使用 `Full (strict)`

## 5. 521 的直接诊断方法

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

## 6. 最终应达到的状态

成功后应满足：

1. `curl -I http://127.0.0.1:8000/` 返回 `200`
2. `systemctl status zhifei-bid` 正常
3. `systemctl status caddy` 正常
4. `https://bid.niyoufei.com` 可直接打开系统

## 7. 当前仓库新增的部署文件

- `deploy/caddy/Caddyfile.bid.niyoufei.com`
- `deploy/systemd/zhifei-bid.service`

如果后续你要我继续，我下一步可以直接按这套配置继续收口成：

- 一份服务器初始化命令清单
- 一份完整的上线核验清单
- 一份 Cloudflare 后台应如何填写的具体配置单
