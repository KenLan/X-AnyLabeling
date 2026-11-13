"""云配置同步（anylabeling/services/cloud_config.py）单元测试。

不访问真实 OSS：通过向 sys.modules 注入 fake ``oss2`` 模块模拟下载
成功/失败两种行为。
"""

import importlib
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

from anylabeling import config
from anylabeling.services import cloud_config

REPO_ROOT = Path(__file__).resolve().parents[2]


class _FakeAuth:
    def __init__(self, key_id, secret):
        self.key_id = key_id
        self.secret = secret


class _FakeBucket:
    """记录调用参数的 oss2.Bucket 替身。"""

    calls = []
    behavior = None  # 每个测试自行设置的函数 (key, path)

    def __init__(self, auth, endpoint, bucket_name, connect_timeout=None):
        self.auth = auth
        self.endpoint = endpoint
        self.bucket_name = bucket_name
        self.connect_timeout = connect_timeout
        _FakeBucket.calls.append(
            {
                "key_id": auth.key_id,
                "endpoint": endpoint,
                "bucket": bucket_name,
            }
        )

    def get_object_to_file(self, key, local_path):
        _FakeBucket.behavior(key, local_path)


def _install_fake_oss2():
    fake = types.ModuleType("oss2")
    fake.Auth = _FakeAuth
    fake.Bucket = _FakeBucket
    sys.modules["oss2"] = fake
    return fake


class TestAssertOssKey(unittest.TestCase):
    def test_accepts_prefixed_key(self):
        cloud_config.assert_oss_key("X-AnyLabeling/config.xanylabelingrc")

    def test_rejects_key_without_prefix(self):
        with self.assertRaises(ValueError):
            cloud_config.assert_oss_key("config.xanylabelingrc")
        with self.assertRaises(ValueError):
            cloud_config.assert_oss_key("other-app/config.yaml")
        with self.assertRaises(ValueError):
            cloud_config.assert_oss_key("")


class TestProgramDir(unittest.TestCase):
    def setUp(self):
        self._saved = config._program_directory

    def tearDown(self):
        config._program_directory = self._saved

    def test_source_run_defaults_to_repo_root(self):
        self.assertEqual(config.get_program_dir(), str(REPO_ROOT))
        self.assertEqual(
            config.get_program_config_path(),
            str(REPO_ROOT / "config.xanylabelingrc"),
        )

    def test_frozen_build_uses_executable_dir(self):
        exe_dir = str(Path(tempfile.gettempdir()) / "fake-dist")
        old_frozen = getattr(sys, "frozen", None)
        old_executable = sys.executable
        try:
            sys.frozen = True
            sys.executable = str(Path(exe_dir) / "X-AnyLabeling.exe")
            config._program_directory = None
            self.assertEqual(config.get_program_dir(), exe_dir)
        finally:
            if old_frozen is None:
                del sys.frozen
            else:
                sys.frozen = old_frozen
            sys.executable = old_executable

    def test_set_program_dir_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            config.set_program_dir(directory)
            self.assertEqual(config.get_program_dir(), str(Path(directory)))


class TestDownloadConfig(unittest.TestCase):
    def setUp(self):
        self._saved_program_dir = config._program_directory
        self._saved_oss2 = sys.modules.get("oss2")
        self._saved_env = {
            k: os.environ.get(k)
            for k in cloud_config.RO_KEY_ENV_VARS
        }
        # 固定凭据，隔离宿主机上的 oss_ro.env / 环境变量
        os.environ[cloud_config.RO_KEY_ENV_VARS[0]] = "test-key-id"
        os.environ[cloud_config.RO_KEY_ENV_VARS[1]] = "test-secret"
        _FakeBucket.calls = []
        _FakeBucket.behavior = None
        _install_fake_oss2()
        self._tmp = tempfile.TemporaryDirectory()
        config.set_program_dir(self._tmp.name)

    def tearDown(self):
        config._program_directory = self._saved_program_dir
        if self._saved_oss2 is None:
            sys.modules.pop("oss2", None)
        else:
            sys.modules["oss2"] = self._saved_oss2
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    def test_success_uses_readonly_credentials(self):
        def behavior(key, path):
            with open(path, "w", encoding="utf-8") as f:
                f.write("labels: [a]\n")

        _FakeBucket.behavior = behavior
        ok, _ = cloud_config.download_config()
        self.assertTrue(ok)
        self.assertEqual(_FakeBucket.calls[-1]["key_id"], "test-key-id")

    def test_success_replaces_local_copy_atomically(self):
        def behavior(key, path):
            self.assertTrue(path.endswith(".tmp"))
            with open(path, "w", encoding="utf-8") as f:
                f.write("labels: [culex_f, culex_m]\n")

        _FakeBucket.behavior = behavior
        ok, info = cloud_config.download_config()
        self.assertTrue(ok)
        target = Path(self._tmp.name) / "config.xanylabelingrc"
        self.assertEqual(
            target.read_text(encoding="utf-8"),
            "labels: [culex_f, culex_m]\n",
        )
        # 无残留 .tmp 文件
        self.assertFalse(Path(str(target) + ".tmp").exists())
        # 使用只读凭据与规约端点
        self.assertEqual(_FakeBucket.calls[-1]["bucket"], "singlechip-orders")

    def test_failure_keeps_existing_local_copy(self):
        target = Path(self._tmp.name) / "config.xanylabelingrc"
        target.write_text("keep-me: true\n", encoding="utf-8")

        def behavior(key, path):
            with open(path, "w", encoding="utf-8") as f:
                f.write("partial-download")
            raise RuntimeError("network unreachable")

        _FakeBucket.behavior = behavior
        ok, info = cloud_config.download_config()
        self.assertFalse(ok)
        self.assertEqual(target.read_text(encoding="utf-8"), "keep-me: true\n")
        self.assertFalse(Path(str(target) + ".tmp").exists())

    def test_empty_download_is_rejected(self):
        def behavior(key, path):
            with open(path, "w", encoding="utf-8") as f:
                f.write("")

        _FakeBucket.behavior = behavior
        ok, info = cloud_config.download_config()
        self.assertFalse(ok)
        self.assertFalse(
            (Path(self._tmp.name) / "config.xanylabelingrc").exists()
        )


class TestReadonlyCredentials(unittest.TestCase):
    def setUp(self):
        self._saved_program_dir = config._program_directory
        self._saved_env = {
            k: os.environ.get(k)
            for k in cloud_config.RO_KEY_ENV_VARS
        }
        for key in self._saved_env:
            os.environ.pop(key, None)
        self._tmp = tempfile.TemporaryDirectory()
        config.set_program_dir(self._tmp.name)

    def tearDown(self):
        config._program_directory = self._saved_program_dir
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    def test_env_vars_take_priority(self):
        os.environ[cloud_config.RO_KEY_ENV_VARS[0]] = "env-id"
        os.environ[cloud_config.RO_KEY_ENV_VARS[1]] = "env-secret"
        env_file = Path(self._tmp.name) / cloud_config.RO_ENV_FILE_NAME
        env_file.write_text(
            "OSS_RO_AK_ID=file-id\nOSS_RO_AK_SECRET=file-secret\n",
            encoding="utf-8",
        )
        key_id, secret = cloud_config.get_readonly_credentials()
        self.assertEqual((key_id, secret), ("env-id", "env-secret"))

    def test_program_dir_env_file_is_loaded(self):
        env_file = Path(self._tmp.name) / cloud_config.RO_ENV_FILE_NAME
        env_file.write_text(
            "# comment\nOSS_RO_AK_ID=file-id\nOSS_RO_AK_SECRET=file-secret\n",
            encoding="utf-8",
        )
        key_id, secret = cloud_config.get_readonly_credentials()
        self.assertEqual((key_id, secret), ("file-id", "file-secret"))

    def test_missing_credentials_returns_empty(self):
        key_id, secret = cloud_config.get_readonly_credentials()
        self.assertEqual((key_id, secret), ("", ""))


class TestSaveConfigProgramDir(unittest.TestCase):
    def setUp(self):
        self._saved = config._program_directory
        self._tmp = tempfile.TemporaryDirectory()
        config.set_program_dir(self._tmp.name)

    def tearDown(self):
        config._program_directory = self._saved
        self._tmp.cleanup()

    def test_save_config_writes_into_program_dir(self):
        config.save_config({"language": "zh_CN", "labels": ["aedes_f"]})
        target = Path(self._tmp.name) / "config.xanylabelingrc"
        self.assertTrue(target.exists())
        import yaml

        data = yaml.safe_load(target.read_text(encoding="utf-8"))
        self.assertEqual(data["labels"], ["aedes_f"])

    def test_default_config_creates_baseline_in_program_dir(self):
        target = Path(self._tmp.name) / "config.xanylabelingrc"
        self.assertFalse(target.exists())
        defaults = config.get_default_config()
        self.assertTrue(target.exists())
        self.assertIn("language", defaults)
        # 再次调用不会覆盖已有副本
        target.write_text("language: zh_CN\n", encoding="utf-8")
        config.get_default_config()
        self.assertEqual(
            target.read_text(encoding="utf-8"), "language: zh_CN\n"
        )


if __name__ == "__main__":
    unittest.main()
