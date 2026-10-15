import hashlib
import json
import os
import shutil
import subprocess
import tarfile

from test_update_web import ROOT, TAG, UpgradeFixture


class PackageTest(UpgradeFixture):
    def setUp(self):
        super().setUp()
        (self.work / "scripts").mkdir()
        shutil.copy(ROOT / "scripts/package.sh", self.work / "scripts/package.sh")
        shutil.copytree(ROOT / "docs/upgrades", self.work / "docs/upgrades")
        (self.work / ".gitignore").write_text(".env\n")
        self.backend = self.base / "backend"
        self.backend.mkdir()
        (self.backend / "Dockerfile").write_text("FROM scratch\n")
        self.api_sha = self.commit(self.backend)
        self.deploy_sha = self.commit(self.work)
        self.output = self.base / "package"
        (self.bin / "docker").write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
base = pathlib.Path(os.environ['CASE_DIR'])
with (base/'calls').open('a') as f: f.write(json.dumps(args)+'\\n')
state = base/'images.json'
images = json.loads(state.read_text()) if state.exists() else {}
if args[:2] == ['image', 'inspect']:
    name = args[-1]
    if name not in images: sys.exit(1)
    if '--format' in args: print('wrong metadata' if os.environ.get('COLLISION') else images[name])
    sys.exit(0)
if args[:2] == ['buildx', 'build']:
    name = args[args.index('-t')+1]
    platform = args[args.index('--platform')+1]
    labels = dict(arg.split('=',1) for i,arg in enumerate(args) if i and args[i-1]=='--label')
    context = pathlib.Path(args[-1])
    if name.startswith('mopilot-web:'): assert (context/'index.html').read_text() == 'committed source'
    images[name] = labels['org.opencontainers.image.revision']+' '+platform+' '+labels['org.opencontainers.image.version']
    state.write_text(json.dumps(images)); sys.exit(0)
if args[0] == 'save': sys.stdout.buffer.write(b'simulated Docker archive'); sys.exit(0)
if args[0] == 'pull':
    assert args == ['pull','--platform','linux/amd64','postgres:17-alpine']; sys.exit(0)
raise AssertionError(args)
''')
        self.bin.joinpath("docker").chmod(0o755)

    def commit(self, path):
        subprocess.run([self.git, "init", "-q", str(path)], check=True)
        subprocess.run([self.git, "-C", str(path), "add", "."], check=True)
        subprocess.run([self.git, "-C", str(path), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                        "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", "commit", "-qm", "fixture"], check=True)
        return subprocess.check_output([self.git, "-C", str(path), "rev-parse", "HEAD"], text=True).strip()

    def package(self, output=None, **env):
        return subprocess.run(["bash", str(self.work / "scripts/package.sh"), TAG, "linux/amd64", self.sha,
                               self.api_sha, self.deploy_sha, "00011", str(output or self.output)],
                              cwd=self.work, env=dict(self.env, MOPILOT_INCLUDE_POSTGRES="0", **env),
                              capture_output=True, text=True, errors="replace")

    def test_four_files_pinned_sources_checksums_and_no_env(self):
        result = self.package()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({p.name for p in self.output.iterdir()},
                         {"SHA256SUMS", "source.txt", f"mopilot-images-{TAG}.tar.gz", f"mopilot-deploy-{TAG}.tar.gz"})
        for line in (self.output / "SHA256SUMS").read_text().splitlines():
            expected, name = line.split()
            self.assertEqual(hashlib.sha256((self.output / name).read_bytes()).hexdigest(), expected)
        self.assertIn("frontend_commit=" + self.sha, (self.output / "source.txt").read_text())
        with tarfile.open(self.output / f"mopilot-deploy-{TAG}.tar.gz") as archive:
            names = {name.removeprefix("./") for name in archive.getnames()}
        self.assertNotIn(".env", names)
        self.assertIn("docs/upgrades/README.md", names)
        self.assertFalse(any(c[0] == "pull" for c in self.calls()))
        self.assertNotEqual(self.package().returncode, 0)
        self.assertEqual(sum(c[:2] == ["buildx", "build"] for c in self.calls()), 2)
        reused = self.package(self.base / "second-package")
        self.assertEqual(reused.returncode, 0, reused.stderr)
        self.assertEqual(sum(c[:2] == ["buildx", "build"] for c in self.calls()), 2)

    def test_image_collision_blocks_publication(self):
        self.assertEqual(self.package().returncode, 0)
        target = self.base / "collision-package"
        self.assertNotEqual(self.package(target, COLLISION="1").returncode, 0)
        self.assertFalse(target.exists())

    def test_first_install_can_include_postgres(self):
        self.env["MOPILOT_INCLUDE_POSTGRES"] = "1"
        # Explicit input is separate from the normal-upgrade default.
        result = subprocess.run(["bash", str(self.work / "scripts/package.sh"), TAG, "linux/amd64", self.sha,
                                 self.api_sha, self.deploy_sha, "00011", str(self.output)],
                                cwd=self.work, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(["pull", "--platform", "linux/amd64", "postgres:17-alpine"], self.calls())
        self.assertIn("postgres:17-alpine", next(c for c in self.calls() if c[0] == "save"))
