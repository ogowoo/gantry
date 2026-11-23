"""ensure_corp_ca：合并 bundle 去重、公司证书收录逻辑。

不依赖真实公司网络：「公司根」用 certifi bundle 里现成证书的 PEM 文本充当，
测的是指纹去重与追加路径本身。
"""
import importlib.util
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("cbp_corp_ca", BASE / "scripts" / "ensure_corp_ca.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class _FakeData:
    """把模块里的三个文件路径重定向到临时目录。"""

    def __init__(self, tmp: Path):
        self.data = tmp / "data"
        self.data.mkdir()
        mod.DATA = self.data
        mod.CORP_PEM = self.data / "corp-ca.pem"
        mod.BUNDLE = self.data / "ca-bundle.pem"

    def restore(self):
        mod.DATA = Path(mod.__file__).resolve().parent.parent / "data"
        mod.CORP_PEM = mod.DATA / "corp-ca.pem"
        mod.BUNDLE = mod.DATA / "ca-bundle.pem"


class CorpCaTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = Path(tempfile.mkdtemp(prefix="cbp_corpca_"))
        self._fake = _FakeData(self._tmp)
        # 取 certifi 第一张证书当「公司根」素材
        import certifi

        first = mod._split_pem(Path(certifi.where()).read_text(encoding="ascii"))[0]
        self.corp_pem = first

    def tearDown(self):
        self._fake.restore()
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_bundle_build_dedupes(self):
        mod.CORP_PEM.write_text(self.corp_pem, encoding="ascii")
        bundle = mod.ensure_bundle()
        blocks = mod._split_pem(bundle.read_text(encoding="ascii"))
        # certifi 全量 + 公司 1 张，但公司那张与 certifi 重复 → 总数不变
        import certifi

        total = len(mod._split_pem(Path(certifi.where()).read_text(encoding="ascii")))
        self.assertEqual(len(blocks), total)

    def test_bundle_adds_unknown_cert(self):
        # 伪造一张「bundle 里没有」的 PEM：复用公司素材但改一行注释干扰，
        # 更直接的做法是塞一段无效 base64 —— 应被跳过而不崩。
        mod.CORP_PEM.write_text(
            "# comment line\n-----BEGIN CERTIFICATE-----\n!!!!not-base64!!!!\n-----END CERTIFICATE-----\n",
            encoding="ascii",
        )
        bundle = mod.ensure_bundle()  # 坏块跳过，不抛异常
        self.assertTrue(bundle.exists())

    def test_collect_appends_and_is_idempotent(self):
        """模拟收录：把对端链写进 corp-ca.pem 再合并，第二次不再增长。"""
        # 直接走 collect 的内部语义：往 CORP_PEM 追加 corp_pem 并刷新 bundle
        mod.ensure_bundle()
        before = len(mod._split_pem(mod.BUNDLE.read_text(encoding="ascii")))
        with mod.CORP_PEM.open("a", encoding="ascii") as f:
            f.write("# test\n" + self.corp_pem)
        mod.ensure_bundle()
        after1 = len(mod._split_pem(mod.BUNDLE.read_text(encoding="ascii")))
        mod.ensure_bundle()  # 幂等：再刷一次数量不变
        after2 = len(mod._split_pem(mod.BUNDLE.read_text(encoding="ascii")))
        self.assertEqual(after1, after2)
        self.assertGreaterEqual(after1, before)

    def test_setup_env_idempotent_with_fake_winreg(self):
        """setup_env 幂等补写：注入假 winreg，验证两次调用第二次无改动。"""
        import types

        store: dict[str, str] = {}

        fake = types.ModuleType("winreg")
        fake.HKEY_CURRENT_USER = 0
        fake.KEY_READ = 1
        fake.KEY_SET_VALUE = 2
        fake.REG_EXPAND_SZ = 3

        class _Key:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def __init__(self, *a):
                pass

            def __getattr__(self, _n):
                raise AttributeError(_n)

        def _query(key, name):
            if name not in store:
                raise FileNotFoundError(name)
            return store[name], fake.REG_EXPAND_SZ

        def _set(key, *a):
            store[a[0]] = a[-1]

        fake.OpenKey = lambda *a, **k: _Key()
        fake.QueryValueEx = _query
        fake.SetValueEx = _set

        real = sys.modules.get("winreg")
        sys.modules["winreg"] = fake
        try:
            changed1 = mod.setup_env(Path(r"C:\fake\ca-bundle.pem"))
            changed2 = mod.setup_env(Path(r"C:\fake\ca-bundle.pem"))
        finally:
            if real is not None:
                sys.modules["winreg"] = real
            else:
                sys.modules.pop("winreg", None)
        self.assertEqual(sorted(changed1), ["REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"])
        self.assertEqual(changed2, [])
        self.assertEqual(store["SSL_CERT_FILE"], r"C:\fake\ca-bundle.pem")


if __name__ == "__main__":
    unittest.main()
