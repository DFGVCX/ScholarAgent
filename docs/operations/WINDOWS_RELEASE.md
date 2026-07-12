# ScholarAgent Windows 安装版

## 用户安装

1. 双击 `ScholarAgent-Setup-<version>.exe`。
2. 保持默认安装位置并完成安装。
3. 安装器会启动 ScholarAgent；以后可使用桌面或开始菜单快捷方式。
4. 登录页在桌面模式下会自动填写本地账号。模型供应商与密钥在“个人中心”配置。

用户不需要安装 Python、Node.js、MySQL、Redis 或 Docker。应用数据、知识库、批注、模型配置和日志保存在 `%LOCALAPPDATA%\ScholarAgent`，卸载程序默认不会删除这些用户数据。

开始菜单中的“停止 ScholarAgent”用于停止后台服务。重复点击 ScholarAgent 快捷方式不会重复启动服务，而是直接打开现有工作台。

## 构建环境

- Windows 10/11 x64
- Python 3.12 虚拟环境 `.venv`
- Node.js 20 或更高版本
- Inno Setup 6

安装构建工具：

```powershell
winget install JRSoftware.InnoSetup
.\.venv\Scripts\python.exe -m pip install pyinstaller pyinstaller-hooks-contrib
```

构建并执行完整验收：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows_release.ps1 -Version 0.2.0
```

脚本会依次执行前端构建、Python 测试、PyInstaller 打包、打包程序健康检查和 Inno Setup 编译。最终文件位于：

```text
release/output/ScholarAgent-Setup-0.2.0.exe
```

## 去本地化约束

- 运行数据使用 `%LOCALAPPDATA%\ScholarAgent`，不依赖源码目录或开发者绝对路径。
- 桌面版使用内置 SQLite、ChromaDB 和进程内任务执行，不要求外部 MySQL/Redis。
- MCP 工具默认在进程内注册，机构访问由同一安装包中的 Browser Worker 提供。
- 模型密钥不写入源码、安装器或 Git，只能由最终用户在界面中配置。
- 桌面版只初始化一个本地租户，不包含 Acme、开发者账号或开发机数据库。
- Browser Worker 优先复用用户已安装的 Microsoft Edge；学校登录、验证码和机构授权仍由用户本人完成。

## 发布校验

发布前至少确认：

```powershell
Get-FileHash release\output\ScholarAgent-Setup-0.2.0.exe -Algorithm SHA256
git diff main...test-release --check
```

正式对外发布建议增加 Windows Authenticode 代码签名。未签名安装器可能触发 Microsoft Defender SmartScreen 提示，这不影响本地功能，但会影响用户信任和企业分发。
