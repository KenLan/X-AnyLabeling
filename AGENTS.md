# X-AnyLabeling 定制版（Passonbio）项目规约

本仓库基于上游 [CVHub520/X-AnyLabeling](https://github.com/CVHub520/X-AnyLabeling) v4.3.2 定制。
本文件记录本定制版必须长期遵守的项目级规则，所有会话（人类与 AI）在进行任何改动前都应知悉。

## 1. 阿里云 OSS 使用规约（最高优先级）

- **路径前缀规则**：本程序在 OSS 上使用的**所有**对象路径一律以
  `X-AnyLabeling/` 前缀开头，无例外。新增任何 OSS 对象（配置、模型、其他资源）
  时必须遵守；`anylabeling/services/cloud_config.py` 中的 `assert_oss_key()`
  与 `tools/oss_config_tool.py` 都内置了该前缀校验。
- Bucket：`singlechip-orders`；Endpoint：`oss-cn-guangzhou.aliyuncs.com`。
- **AccessKey 分级**（两类凭据都**严禁写入任何源码、文档、提交、日志**；
  仓库受 GitHub Push Protection 保护，提交密钥会被直接拒绝）：
  - 只读 Key：仅存在于本地 `oss_ro.env`（已 gitignore）。加载顺序：
    环境变量 `XANYLABELING_OSS_KEY_ID` / `XANYLABELING_OSS_KEY_SECRET`
    → 打包时注入 exe 的内置副本 → 程序目录下的 `oss_ro.env`（人工覆盖）。
    打包分发包前必须确认本机仓库根有 `oss_ro.env`（spec 会自动注入）。
  - 读写 Key：仅存在于本地 `.oss_rw.env`（已 gitignore），供
    `tools/oss_config_tool.py` 上传配置使用。
  - 若任一 Key 意外泄露，立即在阿里云控制台作废并更换。

## 2. 配置文件体系（云端绝对权威）

- 权威配置：OSS 对象 `X-AnyLabeling/config.xanylabelingrc`。
- 程序启动时用只读 Key 下载并**原子覆盖**程序目录下的本地副本
  `<程序目录>/config.xanylabelingrc`（打包后 = exe 所在目录；源码运行 = 仓库根）。
- 下载失败（离线/超时）时沿用本地副本，不阻塞启动。
- **程序不读写用户主目录的 `~/.xanylabelingrc`**（已废弃该行为）。
- 运行期的界面修改会写入本地副本，但下次启动即被云端覆盖；
  要固化修改须用 `python tools/oss_config_tool.py upload` 推回 OSS。

## 3. 构建与分发

- Windows 正式产物为 GPU(CUDA12) onedir 版，构建方式见仓库 README/脚本；
  exe 依赖同目录的版本号文件夹（PyInstaller contents_directory）。
- 打包机无需安装 CUDA：NVIDIA cu12 运行库经 pip 装入 venv，
  由 `packaging/pyinstaller/specs/x-anylabeling-win-gpu.spec` 全量收集进 exe 目录。

## 4. 通用

- 提交信息沿用上游风格（gitmoji + conventional commits），身份使用
  `KenLan <20080532+KenLan@users.noreply.github.com>`（仓库本地 git config）。
- 上游同步：`git fetch upstream && git merge upstream/main`，冲突时以功能为准。
