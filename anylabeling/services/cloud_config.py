"""OSS 云端配置同步（阿里云 OSS）。

⚠️ 项目规约（必须遵守）：本程序在 OSS 上使用的所有对象路径一律以
``X-AnyLabeling/`` 前缀开头。详见仓库根目录 ``AGENTS.md``。

工作方式：
- 程序启动时用**只读** AccessKey 从 OSS 下载权威配置
  ``X-AnyLabeling/config.xanylabelingrc``，原子替换程序目录下的本地副本
  ``<程序目录>/config.xanylabelingrc``（云端绝对权威）。
- 下载失败（离线/超时/对象不存在）时沿用本地副本，绝不阻塞启动。
- 程序不读写用户主目录的 ``~/.xanylabelingrc``。

只读凭据加载顺序（**任何凭据都不进入源码库**，仓库受 GitHub Push
Protection 保护，提交密钥会被拒绝）：
1. 环境变量 ``XANYLABELING_OSS_KEY_ID`` / ``XANYLABELING_OSS_KEY_SECRET``
2. 打包内置的 ``oss_ro.env``（构建时由 PyInstaller spec 从仓库根的
   同名文件注入，仅存在于正式程序内）
3. 程序目录下的 ``oss_ro.env``（人工覆盖用）
"""

import logging
import os
import sys

from anylabeling.views.labeling.logger import logger

OSS_ENDPOINT = "https://oss-cn-guangzhou.aliyuncs.com"
OSS_BUCKET = "singlechip-orders"
OSS_PATH_PREFIX = "X-AnyLabeling/"
OSS_CONFIG_KEY = "X-AnyLabeling/config.xanylabelingrc"

RO_ENV_FILE_NAME = "oss_ro.env"
RO_KEY_ENV_VARS = ("XANYLABELING_OSS_KEY_ID", "XANYLABELING_OSS_KEY_SECRET")
RO_KEY_FILE_KEYS = ("OSS_RO_AK_ID", "OSS_RO_AK_SECRET")

DOWNLOAD_TIMEOUT_SECONDS = 5.0


def assert_oss_key(key: str) -> None:
    """校验对象路径符合项目规约：必须以 ``X-AnyLabeling/`` 开头。"""
    if not isinstance(key, str) or not key.startswith(OSS_PATH_PREFIX):
        raise ValueError(
            f"OSS 对象路径必须以 '{OSS_PATH_PREFIX}' 开头（项目规约），"
            f"当前值: {key!r}"
        )


def parse_env_file(path):
    """解析 KEY=VALUE 形式的环境文件，返回 dict（文件不存在返回空）。"""
    values = {}
    if not path or not os.path.isfile(path):
        return values
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip()
    return values


def _readonly_env_candidates():
    """只读凭据文件的候选位置（按优先级）。"""
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:  # 打包后：构建时注入的 oss_ro.env
        candidates.append(os.path.join(meipass, RO_ENV_FILE_NAME))
    from anylabeling.config import get_program_dir

    candidates.append(os.path.join(get_program_dir(), RO_ENV_FILE_NAME))
    return candidates


def get_readonly_credentials() -> "tuple[str, str]":
    """返回只读 AccessKey：环境变量优先，其次内置/程序目录的 oss_ro.env。"""
    key_id = os.environ.get(RO_KEY_ENV_VARS[0])
    secret = os.environ.get(RO_KEY_ENV_VARS[1])
    if key_id and secret:
        return key_id, secret

    for path in _readonly_env_candidates():
        values = parse_env_file(path)
        key_id = values.get(RO_KEY_FILE_KEYS[0])
        secret = values.get(RO_KEY_FILE_KEYS[1])
        if key_id and secret:
            return key_id, secret

    logger.warning(
        "未找到 OSS 只读凭据：请设置环境变量 "
        f"{RO_KEY_ENV_VARS[0]} / {RO_KEY_ENV_VARS[1]}，"
        f"或提供 {RO_ENV_FILE_NAME} 文件"
    )
    return "", ""


def _get_program_config_path():
    # 延迟导入：保证本模块可被 CLI 子命令/工具轻量加载。
    from anylabeling.config import get_program_config_path

    return get_program_config_path()


def download_config(timeout: float = DOWNLOAD_TIMEOUT_SECONDS):
    """从 OSS 下载权威配置并原子替换本地副本。

    Returns:
        (成功标志, 说明文本)。任何异常都不抛出，保证启动安全。
    """
    assert_oss_key(OSS_CONFIG_KEY)
    target_path = _get_program_config_path()
    tmp_path = target_path + ".tmp"
    key_id, secret = get_readonly_credentials()
    try:
        import oss2  # 延迟导入：未打包场景/离线时不应影响其他功能

        auth = oss2.Auth(key_id, secret)
        bucket = oss2.Bucket(
            auth,
            OSS_ENDPOINT,
            OSS_BUCKET,
            connect_timeout=timeout,
        )
        bucket.get_object_to_file(OSS_CONFIG_KEY, tmp_path)
        if os.path.getsize(tmp_path) == 0:
            raise RuntimeError("下载的配置文件为空")
        os.replace(tmp_path, target_path)  # 原子替换，避免半包损坏
        return True, target_path
    except Exception as e:  # noqa: BLE001 —— 下载失败必须兜底，不能阻塞启动
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        logger.warning(
            f"☁️ 云端配置下载失败，将使用本地副本: {type(e).__name__}: {e}"
        )
        return False, str(e)


def sync_config(timeout: float = DOWNLOAD_TIMEOUT_SECONDS) -> str:
    """启动入口：尝试云端同步，无论成败都返回本地配置文件路径。"""
    ok, info = download_config(timeout=timeout)
    if ok:
        logger.info(f"☁️ 已从 OSS 同步云端配置: {OSS_CONFIG_KEY}")
    else:
        logger.info("☁️ 沿用程序目录下的本地配置副本")
    return _get_program_config_path()
