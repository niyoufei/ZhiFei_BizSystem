# 青天评标系统 Windows 保密版安装与使用

本文对应当前仓库新增的 Windows 保密版运行模式。目标不是“把 Python 项目手工搬到 Windows 上跑”，而是产出一个可安装、可双击启动、默认启用保密控制的桌面程序。

## 1. 交付物

- 构建入口：`app/windows_desktop.py`
- PyInstaller 规格：`packaging/windows/QingtianBidSecure.spec`
- Inno Setup 安装脚本：`packaging/windows/QingtianBidSecure.iss`
- 一键构建脚本：`scripts/build_windows_secure.ps1`

构建成功后会得到两类产物：

- `dist/QingtianBidSecure/`
  说明：可直接分发的桌面程序目录
- `dist/installer/QingtianBidSecureSetup.exe`
  说明：可安装到 Windows 电脑的安装包

## 2. 安全设计

保密版默认启用以下控制：

- 本地回环监听：仅绑定 `127.0.0.1`，不对局域网暴露
- 当前用户加密：`data/` 下 JSON、缓存、上传资料使用 Windows DPAPI 按当前登录用户加密
- 自动迁移：首次进入保密模式时，会把现有明文数据重写为加密数据
- 禁止导出：禁用 Markdown 导出、下载和前端复制按钮
- 本机目录隔离：数据默认落到 `%LOCALAPPDATA%\\QingtianBidSystem\\data`

需要明确的边界：

- 纯软件无法绝对阻止截图、拍照、人工转录
- 同一台电脑、同一 Windows 用户在程序运行时仍可查看系统内结果
- 如果要进一步限制截屏、外发、U 盘复制，需要叠加企业 DLP、终端管控或域策略

## 3. Windows 构建

在 Windows 构建机上打开 PowerShell，进入项目根目录后执行：

```powershell
.\scripts\build_windows_secure.ps1
```

脚本会依次完成：

1. 安装运行依赖
2. 安装 `PyInstaller`
3. 生成 `dist/QingtianBidSecure/`
4. 若本机安装了 Inno Setup，则继续生成安装包 `dist/installer/QingtianBidSecureSetup.exe`

如果 PowerShell 默认禁止脚本执行，可先执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

## 4. 最终安装

在目标 Windows 电脑上：

1. 运行 `QingtianBidSecureSetup.exe`
2. 按向导完成安装
3. 双击桌面或开始菜单中的“青天评标系统保密版”
4. 程序会自动启动本地服务并打开浏览器

默认访问地址是：

```text
http://127.0.0.1:8000/
```

如果 `8000` 已占用，桌面入口会自动尝试后续少量端口。

## 5. 运行后的数据位置

保密版不再把核心数据默认写回安装目录，而是写入：

```text
%LOCALAPPDATA%\QingtianBidSystem\data
```

主要包含：

- 项目、评分、历史、画像、补丁等 JSON
- 缓存文件
- 上传资料文件

这些文件在保密模式下都是加密后的二进制，不是明文 JSON/原始资料。

## 6. 使用注意

- 保密版会禁用 Markdown 导出、下载分析包、证据追溯下载、体检/画像下载、复制导出按钮
- 若需要对外交换材料，请使用非保密版环境，或由管理员在受控流程中导出
- 若要备份数据，请备份 `%LOCALAPPDATA%\QingtianBidSystem\` 整个目录
- 数据复制到其他电脑后，默认不能直接解密读取

## 7. 故障排查

| 现象 | 处理 |
|------|------|
| 安装后无法启动 | 查看 `%LOCALAPPDATA%\QingtianBidSystem\logs\desktop.log` |
| 浏览器未自动打开 | 手动访问 `http://127.0.0.1:8000/`，若端口占用则查看日志中的实际端口 |
| 构建时缺少 `pyinstaller` | 重新执行 `.\scripts\build_windows_secure.ps1` |
| 没生成安装包 | 说明构建机未安装 Inno Setup，先安装 Inno Setup 6 后重跑 |
| 旧数据仍是明文 | 启动保密版一次，系统会对 `data/` 内旧文件做迁移加密 |
