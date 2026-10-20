# rsync portable

在 GitHub Actions 中组装 Windows x64 便携版 rsync。组件来自 Cygwin 官方镜像，不使用 cwRsync 成品、不在本地构建，不制作单 EXE 包装器。

## 构建与下载

1. 将此项目放入自己的 GitHub 仓库，工作流文件需要存在于默认分支。
2. 打开 **Actions → 打包 Windows rsync → Run workflow**，选择分支后运行。
3. 等待两个任务成功，在该次运行页面的 **Artifacts** 下载 `rsync-portable-windows-x64`。
4. 解压下载的外层压缩包，再解压其中的 `rsync-portable-windows-x64.zip`，即可使用 `rsync-portable` 文件夹。

不需要设置 Secrets，不需要本机安装 Cygwin 或 Python。工作流只接受手动触发，不自动发布 Release。最终下载产物保留 30 天；中间产物 `assembled-packages` 只保留 1 天，不能作为验证成功的依据。下载 Actions 产物需要登录 GitHub；过期后可重新运行。

最终产物包含：

| 文件 | 内容 |
| --- | --- |
| `rsync-portable-windows-x64.zip` | rsync、SSH、实际导入的 DLL、上游许可和文档、组件清单 |
| `corresponding-sources.zip` | 对应的 Cygwin 原始源码包（含上游打包材料）、签名索引及本次打包模板 |
| `SHA256SUMS` | 上述两个 ZIP 的 SHA256 |

## 使用

将整个 `rsync-portable` 文件夹放在任意位置，不要只复制其中的 EXE。在 PowerShell 中进入该目录：

```powershell
.\rsync.exe --version
.\ssh.exe -V
.\rsync.exe -av /cygdrive/c/source/ /cygdrive/d/backup/
```

远程同步显式指定附带的 SSH，避免 PATH 中其他版本影响行为：

```powershell
.\rsync.exe -av -e './ssh.exe' /cygdrive/c/source/ fake_user@fake_info:/backup/
```

上面的远程示例需要在便携包目录下运行。其他工作目录请用 `-e` 指定 `ssh.exe` 的实际路径（含空格时需正确引用）。Windows 绝对路径使用 `/cygdrive/c/...`；相对路径按调用者当前目录解析。保留正常的主机密钥校验，不预置私钥、密码、服务器地址或用户 SSH 配置。用户自己的密钥与配置需要自行准备。

## 打包方式与边界

- Ubuntu runner 下载 Cygwin 的稳定版本索引，验证官方签名及固定公钥指纹，再按索引 SHA512 校验每个二进制包和源码包；任何校验或依赖解析失败都会终止。
- 每次运行选择当时的上游稳定版本，不承诺不同日期产物相同。`components.lock.json` 记录本次实际组件、版本、归档路径、上游 SHA512 和文件 SHA256；源码包保存签名索引供审计。镜像撤包时需要重新运行以获取新版本，不会静默跳过组件。
- 递归收集 rsync 和 SSH 的 PE 导入依赖，仅打包实际使用的 DLL。保留所选包提供的文档及许可，程序和 DLL 位于同一个文件夹。
- Windows runner 校验 ZIP，并在含空格的目录中执行 `rsync --version` 和 `ssh -V`。这只能验证基本启动；SSH 远程同步、rsync daemon、中文路径、权限映射和实际传输需要在目标环境另行验收。
- 不包含 SSH 服务、ssh-agent、ssh-keygen、完整 Cygwin 环境、安装器或系统配置。动态加载的可选加密模块、硬件密钥、Kerberos 等完整 OpenSSH 扩展功能不在首版保证范围内。
- 运行库支持范围以上游 Cygwin 为准；首版只面向 Windows 10/11 x64。

## 公开仓库与许可

模板仅包含公开下载地址和通用占位示例。工作流不读取个人目录、SSH 文件、机器环境转储或仓库 Secrets；上传路径仅限脚本生成的 `dist/*`。`debug/`、`dist/` 已被 Git 忽略，不应提交本地缓存或下载的组件。

本仓库脚本与工作流采用 MIT 许可；这个许可不覆盖所打包的上游组件。上游各自许可与文档保留于便携 ZIP 的 `licenses-and-docs/`，对应源码单独提供。分发二进制时，应同时提供本次匹配的源码 ZIP 和相关许可，不要仅分发 EXE 或仅提供会过期的源码下载入口。需要长期公开分发时，请将两个 ZIP 与校验文件一并保存到长期下载位置。

参考：[Cygwin 安装与签名验证](https://cygwin.com/install.html)、[Cygwin 分发条款](https://cygwin.com/licensing.html)、[GitHub Artifact 上传与保留设置](https://github.com/actions/upload-artifact)。
