"""Execute the documented full-upgrade commands against simulated Docker."""
import gzip
import hashlib
import io
import subprocess
import tarfile

from test_update_web import ROOT, TAG, UpgradeFixture


class UpgradeGuideTest(UpgradeFixture):
    def setUp(self):
        super().setUp()
        for name in ("nginx", "scripts"):
            (self.work / name).mkdir()
            (self.work / name / "keep.txt").write_text("old config")
        self.package = self.base / "package"
        self.package.mkdir()
        with gzip.open(self.package / f"mopilot-images-{TAG}.tar.gz", "wb") as image_file:
            image_file.write(b"image data")
        with tarfile.open(self.package / f"mopilot-deploy-{TAG}.tar.gz", "w:gz") as archive:
            for name, data in (("compose.yaml", (ROOT / "compose.yaml").read_bytes()),
                               (".env", b"must never overwrite the existing environment")):
                member = tarfile.TarInfo("./" + name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        (self.package / "source.txt").write_text(f"version={TAG}\nrequired_migration=00011\n")
        (self.package / "SHA256SUMS").write_text("".join(
            hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name + "\n"
            for path in sorted(self.package.iterdir()) if path.name != "SHA256SUMS"))
        (self.bin / "sha256sum").write_text('''#!/usr/bin/env python3
import hashlib, pathlib, sys
assert sys.argv[1:] == ['-c', 'SHA256SUMS']
for line in pathlib.Path('SHA256SUMS').read_text().splitlines():
    expected, name = line.split()
    if hashlib.sha256(pathlib.Path(name).read_bytes()).hexdigest() != expected: sys.exit(1)
''')
        (self.bin / "docker").write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
with (pathlib.Path(os.environ['CASE_DIR'])/'calls').open('a') as f: f.write(json.dumps(args)+'\\n')
if args[0] == 'load': assert sys.stdin.buffer.read() == b'image data'; sys.exit(0)
if args[:2] == ['image','inspect']: sys.exit(0)
assert args[:5] == ['compose','--env-file','.env','-f','compose.yaml']
assert sys.stdin.read() == '', 'Compose consumed the remaining upgrade commands'
action = args[5:]
if action[-2:] == ['migrate','up'] and os.environ.get('FAIL_MIGRATION'): sys.exit(8)
if action[-2:] == ['backup','once']: print('backup ready: /backups/backup-test')
if action[0] == 'up':
    env = pathlib.Path('.env').read_text()
    assert 'MOPILOT_IMAGE_TAG='+os.environ['TEST_TAG'] in env
    assert 'MOPILOT_WEB_IMAGE_TAG=\\n' in env
    assert 'CUSTOM=keep' in env
    print('services started')
''')
        for path in self.bin.iterdir():
            path.chmod(0o755)
        guide = (ROOT / "docs/upgrades/README.md").read_text()
        self.script = guide.split("set -euo pipefail\n", 1)[1].split("\nUPGRADE", 1)[0]
        self.script = "set -euo pipefail\n" + self.script.replace("TAG=填写目标版本", "TAG=" + TAG).replace(
            "PACKAGE=/填写四文件升级包所在目录", 'PACKAGE="' + str(self.package) + '"').replace(
            "EXPECTED_MIGRATION=填写目标迁移版本", "EXPECTED_MIGRATION=00011")

    def upgrade(self, **env):
        return subprocess.run(["bash"], input=self.script, cwd=self.work, env=dict(self.env, **env),
                              capture_output=True, text=True, errors="replace")

    def test_complete_flow_preserves_env_and_clears_frontend_override(self):
        result = self.upgrade()
        self.assertEqual(result.returncode, 0, result.stderr)
        actions = [call[5:] for call in self.calls() if call[0] == "compose"]
        stop = next(i for i, call in enumerate(actions) if call[0] == "stop")
        backup = next(i for i, call in enumerate(actions) if call[-2:] == ["backup", "once"])
        migrate = next(i for i, call in enumerate(actions) if call[-2:] == ["migrate", "up"])
        start = next(i for i, call in enumerate(actions) if call[0] == "up")
        self.assertLess(stop, backup)
        self.assertLess(backup, migrate)
        self.assertLess(migrate, start)
        self.assertTrue(any(call[-2:] == ["nginx", "-t"] for call in actions))
        backup_env = next(self.home.glob("mopilot-upgrade-backups/*/.env"))
        self.assertEqual(backup_env.read_text(), self.original_env)

    def test_bad_checksum_does_not_stop_services(self):
        (self.package / "source.txt").write_text("corrupted")
        self.assertNotEqual(self.upgrade().returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_failed_migration_does_not_start_services(self):
        self.assertNotEqual(self.upgrade(FAIL_MIGRATION="1").returncode, 0)
        self.assertFalse(any(call[5:6] == ["up"] for call in self.calls()))
