"""Offline upgrade-flow checks; Docker lifecycle operations are simulated.

Run: python3 -m unittest discover -s tests -v
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
TAG = "acceptance-test"


class UpgradeFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.work = self.base / "deploy"
        self.frontend = self.base / "frontend"
        self.home = self.base / "home"
        self.bin = self.base / "bin"
        for path in (self.work, self.frontend, self.home, self.bin):
            path.mkdir()
        self.git = shutil.which("git")
        subprocess.run([self.git, "init", "-q", str(self.frontend)], check=True)
        (self.frontend / "index.html").write_text("committed source")
        subprocess.run([self.git, "-C", str(self.frontend), "add", "."], check=True)
        subprocess.run([self.git, "-C", str(self.frontend), "-c", "user.name=Test",
                        "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                        "-c", "core.hooksPath=/dev/null", "commit", "-qm", "fixture"], check=True)
        self.sha = subprocess.check_output(
            [self.git, "-C", str(self.frontend), "rev-parse", "HEAD"], text=True).strip()
        (self.frontend / "index.html").write_text("uncommitted changes must not ship")
        self.original_env = "MOPILOT_IMAGE_TAG=old-api\nMOPILOT_WEB_IMAGE_TAG=\nCUSTOM=keep\n"
        (self.work / ".env").write_text(self.original_env)
        self.legacy_compose = (ROOT / "compose.yaml").read_text().replace(
            "${MOPILOT_WEB_IMAGE_TAG:-${MOPILOT_IMAGE_TAG:-local}}", "${MOPILOT_IMAGE_TAG:-local}"
        ).replace("${MOPILOT_WEB_IMAGE_TAG:-${MOPILOT_IMAGE_TAG:-production}}",
                  "${MOPILOT_IMAGE_TAG:-production}")
        (self.work / "compose.yaml").write_text(self.legacy_compose)
        (self.bin / "git").write_text('''#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
if 'fetch' in args:
    assert args[-2:] == ['origin', os.environ.get('MOPILOT_SOURCE_REF', 'main')]
    sys.exit(0)
os.execv(os.environ['REAL_GIT'], ['git'] + args)
''')
        (self.bin / "docker").write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys, tarfile
args = sys.argv[1:]
base = pathlib.Path(os.environ['CASE_DIR'])
with (base/'calls').open('a') as f: f.write(json.dumps(args)+'\\n')
mode = os.environ.get('CHECK_MODE', '')
if args[0] == 'compose':
    index = next(i for i, arg in enumerate(args) if arg in ('ps', 'config', 'up'))
    action = args[index:]
    expected = ['compose', '--env-file', '.env', '-f', 'compose.yaml'] + json.loads(os.environ.get('EXTRA_ARGS', '[]'))
    assert args[:index] == expected, args
    assert sys.stdin.read() == '', 'Compose stdin must be closed'
    if action[:2] == ['ps', '-q']: print('web-container'); sys.exit(0)
    if action == ['config', '--images']:
        print('mopilot-web:wrong' if mode == 'override' else 'mopilot-web:'+os.environ['TEST_TAG'])
        sys.exit(0)
    if action[0] == 'config': sys.exit(0)
    if action[0] == 'up':
        assert action == ['up', '-d', '--no-deps', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '120', 'web'], action
        sys.exit(9 if mode == 'start-failure' else 0)
    if action[0] == 'ps': print('web healthy'); sys.exit(0)
    raise AssertionError(action)
if args[0] == 'inspect': print('mopilot-web:old-web'); sys.exit(0)
if args[:2] == ['image', 'inspect']:
    if not (base/'built').exists(): sys.exit(1)
    if '--format' in args: print('wrong-sha' if mode == 'collision' else os.environ['WEB_SHA'])
    sys.exit(0)
if args[0] == 'build':
    assert args[-3:] == ['-t', 'mopilot-web:'+os.environ['TEST_TAG'], '-'], args
    assert 'COMMIT_HASH='+os.environ['WEB_SHA'] in args
    with tarfile.open(fileobj=sys.stdin.buffer, mode='r|') as archive:
        for member in archive:
            if member.name == 'index.html': assert archive.extractfile(member).read() == b'committed source'
    if mode == 'build-failure': sys.exit(7)
    (base/'built').write_text('ok'); sys.exit(0)
raise AssertionError(args)
''')
        for binary in self.bin.iterdir():
            binary.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        HOME=str(self.home), REAL_GIT=self.git, CASE_DIR=str(self.base),
                        WEB_SHA=self.sha, TEST_TAG=TAG)
        for key in ("MOPILOT_FRONTEND_REPO", "MOPILOT_SOURCE_REF", "MOPILOT_WEB_IMAGE_TAG"):
            self.env.pop(key, None)

    def run_update(self, mode="", extra=(), tag=TAG, sha=None):
        return subprocess.run(["bash", str(ROOT / "scripts/update-web.sh"), tag, sha or self.sha, *extra],
                              cwd=self.work, env=dict(self.env, CHECK_MODE=mode, EXTRA_ARGS=json.dumps(extra)),
                              input="must not reach Compose", capture_output=True, text=True, errors="replace")

    def calls(self):
        path = self.base / "calls"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


class UpdateWebTest(UpgradeFixture):
    def test_success_and_repeat_preserve_backend_and_local_source(self):
        first = self.run_update()
        self.assertEqual(first.returncode, 0, first.stderr)
        new_env = (self.work / ".env").read_text()
        self.assertIn("MOPILOT_IMAGE_TAG=old-api", new_env)
        self.assertIn("CUSTOM=keep", new_env)
        self.assertEqual(new_env.count("MOPILOT_WEB_IMAGE_TAG="), 1)
        self.assertIn("MOPILOT_WEB_IMAGE_TAG=" + TAG, new_env)
        backup = next(self.home.glob("mopilot-upgrade-backups/*/.env"))
        self.assertEqual(backup.read_text(), self.original_env)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        second = self.run_update()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("复用已构建镜像", second.stdout)
        self.assertEqual(sum(c[0] == "build" for c in self.calls()), 1)
        self.assertEqual((self.frontend / "index.html").read_text(), "uncommitted changes must not ship")

    def test_build_failure_leaves_config_and_services_untouched(self):
        self.assertNotEqual(self.run_update("build-failure").returncode, 0)
        self.assertEqual((self.work / ".env").read_text(), self.original_env)
        self.assertEqual((self.work / "compose.yaml").read_text(), self.legacy_compose)
        self.assertFalse(any("up" in call for call in self.calls()))

    def test_image_collision_does_not_overwrite_or_restart(self):
        (self.base / "built").write_text("foreign image")
        self.assertNotEqual(self.run_update("collision").returncode, 0)
        self.assertFalse(any("up" in c or c[0] == "build" for c in self.calls()))

    def test_start_failure_reports_backup_and_returns_failure(self):
        result = self.run_update("start-failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("未通过启动检查", result.stderr)
        self.assertIn("mopilot-web:old-web", result.stderr)
        self.assertEqual(len(list(self.home.glob("mopilot-upgrade-backups/*/.env"))), 1)

    def test_conflicting_compose_stops_before_build(self):
        (self.work / "compose.yaml").write_text(self.legacy_compose.replace("COMMIT_HASH:", "CUSTOM_HASH:"))
        self.assertNotEqual(self.run_update().returncode, 0)
        self.assertFalse(any("up" in c or c[0] == "build" for c in self.calls()))

    def test_extra_compose_arguments_and_source_ref(self):
        self.env["MOPILOT_SOURCE_REF"] = "release/candidate"
        self.env["MOPILOT_FRONTEND_REPO"] = str(self.frontend)
        result = self.run_update(extra=("-f", "compose.extra.yaml", "-p", "existing-project"))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_image_override_conflict_does_not_restart(self):
        self.assertNotEqual(self.run_update("override").returncode, 0)
        self.assertFalse(any("up" in c for c in self.calls()))

    def test_invalid_inputs_fail_before_docker(self):
        for kwargs in ({"tag": "bad/tag"}, {"sha": "main"}, {"extra": ("-p",)}, {"extra": ("--env-file", "other")}):
            self.assertEqual(self.run_update(**kwargs).returncode, 64)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
