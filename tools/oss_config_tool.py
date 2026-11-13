#!/usr/bin/env python
"""OSS 配置管理工具（开发期使用，正式程序不包含读写 Key）。

用法（需在仓库虚拟环境中运行）::

    python tools/oss_config_tool.py upload [本地文件]   # 上传配置到 OSS（读写 Key）
    python tools/oss_config_tool.py fetch [输出文件]    # 用只读 Key 下载（生产行为演示）
    python tools/oss_config_tool.py show                # 打印远端配置内容（只读 Key）

读写凭据来源（二选一，均**不进入源码库**）：
  1. 环境变量 ``OSS_AK_ID`` / ``OSS_AK_SECRET``
  2. 仓库根目录 ``.oss_rw.env`` 文件（KEY=VALUE 每行一条，已被 .gitignore 忽略）

项目规约：本程序使用的所有 OSS 对象路径一律以 ``X-AnyLabeling/`` 开头。
"""

import argparse
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from anylabeling.services import cloud_config  # noqa: E402

ENV_FILE = os.path.join(REPO_ROOT, ".oss_rw.env")


def load_rw_credentials():
    """读取读写 AccessKey：环境变量优先，其次 .oss_rw.env。"""
    key_id = os.environ.get("OSS_AK_ID")
    secret = os.environ.get("OSS_AK_SECRET")
    if key_id and secret:
        return key_id, secret

    values = cloud_config.parse_env_file(ENV_FILE)
    key_id = values.get("OSS_AK_ID")
    secret = values.get("OSS_AK_SECRET")
    if not (key_id and secret):
        sys.exit(
            "错误：未找到读写 AccessKey。请设置环境变量 OSS_AK_ID / "
            f"OSS_AK_SECRET，或在 {ENV_FILE} 中配置（KEY=VALUE）。"
        )
    return key_id, secret


def make_bucket(key_id, secret):
    import oss2

    auth = oss2.Auth(key_id, secret)
    return oss2.Bucket(
        auth,
        cloud_config.OSS_ENDPOINT,
        cloud_config.OSS_BUCKET,
        connect_timeout=10,
    )


def cmd_upload(args):
    local_file = args.file or os.path.join(
        REPO_ROOT, "config.xanylabelingrc"
    )
    if not os.path.isfile(local_file):
        sys.exit(f"错误：文件不存在: {local_file}")
    if os.path.getsize(local_file) == 0:
        sys.exit("错误：文件为空，拒绝上传。")

    cloud_config.assert_oss_key(cloud_config.OSS_CONFIG_KEY)
    key_id, secret = load_rw_credentials()
    bucket = make_bucket(key_id, secret)
    result = bucket.put_object_from_file(
        cloud_config.OSS_CONFIG_KEY, local_file
    )
    size_kb = os.path.getsize(local_file) / 1024
    print(
        f"✅ 已上传: {local_file}\n"
        f"   → oss://{cloud_config.OSS_BUCKET}/{cloud_config.OSS_CONFIG_KEY}\n"
        f"   大小: {size_kb:.1f} KB  ETag: {result.etag}"
    )


def cmd_fetch(args):
    cloud_config.assert_oss_key(cloud_config.OSS_CONFIG_KEY)
    key_id, secret = cloud_config.get_readonly_credentials()
    bucket = make_bucket(key_id, secret)
    if args.out:
        bucket.get_object_to_file(cloud_config.OSS_CONFIG_KEY, args.out)
        print(f"✅ 已下载到: {args.out}")
    else:
        result = bucket.get_object(cloud_config.OSS_CONFIG_KEY)
        sys.stdout.write(result.read().decode("utf-8"))


def cmd_show(args):
    cmd_fetch(argparse.Namespace(out=None))


def main():
    parser = argparse.ArgumentParser(
        description="X-AnyLabeling OSS 配置管理工具（开发期）"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_upload = sub.add_parser("upload", help="上传配置到 OSS（需读写 Key）")
    p_upload.add_argument(
        "file", nargs="?", default=None, help="本地配置文件（默认仓库根 config.xanylabelingrc）"
    )
    p_upload.set_defaults(func=cmd_upload)
    p_fetch = sub.add_parser("fetch", help="下载配置（只读 Key，生产行为）")
    p_fetch.add_argument("out", nargs="?", default=None, help="输出文件（缺省打印到终端）")
    p_fetch.set_defaults(func=cmd_fetch)
    p_show = sub.add_parser("show", help="打印远端配置内容（只读 Key）")
    p_show.set_defaults(func=cmd_show)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
