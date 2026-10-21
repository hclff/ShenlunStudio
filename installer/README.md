# Inno Setup 安装包

使用 Inno Setup 6 编译：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\installer\build-installer.ps1
```

默认输出为 `E:\ShenlunStudio\ShenlunStudio-0.9.5-Setup.exe`。可通过 `-OutputDir` 指定其他输出目录。安装源为同级 `source\dist\ShenlunStudio`，请先运行 `source\scripts\build_release.py`。

安装器按当前用户安装到 `%LOCALAPPDATA%\Programs\ShenlunStudio`，不会修改应用源码或界面。卸载时会先给出不可恢复警告；确认后删除安装目录、`%LOCALAPPDATA%\ShenlunStudio` 中的全部本地数据，以及该软件写入 Windows 凭据管理器的模型 API 密钥。只有位于这两个清理目录范围外的独立报告和备份才会保留；自选备份目录若仍在安装目录或默认数据目录之内，也会随卸载删除。安装标识和卸载确认、凭据及目录清理行为沿用旧版。
