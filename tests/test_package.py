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
        (self.backend / "migrations").mkdir()
        (self.backend / "migrations/00009_first.sql").write_text("-- first migration")
        (self.backend / "migrations/00011_latest.sql").write_text("-- latest migration")
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
if args[0] == 'version': print('linux/amd64'); sys.exit(0)
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

    def remotes(self):
        # Real local Git remotes exercise fetch and FETCH_HEAD without internet access.
        (self.bin / "git").write_text('''#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
if 'fetch' in args and os.environ.get('FAIL_FETCH'): sys.exit(12)
os.execv(os.environ['REAL_GIT'], ['git'] + args)
''')
        for repo in (self.frontend, self.backend, self.work):
            remote = self.base / (repo.name + "-remote.git")
            subprocess.run([self.git, "init", "--bare", "-q", str(remote)], check=True)
            subprocess.run([self.git, "-C", str(repo), "remote", "add", "origin", str(remote)], check=True)
            subprocess.run([self.git, "-C", str(repo), "push", "-q", "origin", "HEAD:main"], check=True)

    def auto_package(self, *args, script=None, **env):
        return subprocess.run(["bash", str(script or self.work / "scripts/package.sh"), TAG, *args],
                              cwd=self.work, env=dict(self.env, **env), capture_output=True, text=True, errors="replace")

    def test_automatic_main_uses_remote_commits_and_detects_migration(self):
        self.remotes()
        local_sha = self.commit(self.frontend)
        self.assertNotEqual(local_sha, self.sha)
        (self.work / "docs/upgrades/README.md").write_text("local edits must not ship")
        result = self.auto_package()
        self.assertEqual(result.returncode, 0, result.stderr)
        output = self.base / ".scratch/packages" / TAG
        source = (output / "source.txt").read_text()
        for expected in ("frontend_commit=" + self.sha, "backend_commit=" + self.api_sha,
                         "deploy_runtime_commit=" + self.deploy_sha, "required_migration=00011", "platform=linux/amd64"):
            self.assertIn(expected, source)
        with tarfile.open(output / f"mopilot-deploy-{TAG}.tar.gz") as archive:
            content = archive.extractfile("./docs/upgrades/README.md").read()
            self.assertNotIn(b"local edits must not ship", content)

    def test_extracted_script_and_explicit_target_platform(self):
        self.remotes()
        runner = self.base / "runner"
        runner.mkdir()
        shutil.copy(ROOT / "scripts/package.sh", runner / "package.sh")
        result = self.auto_package("linux/arm64", script=runner / "package.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("platform=linux/arm64", (self.base / ".scratch/packages" / TAG / "source.txt").read_text())
        self.assertFalse(any(c[0] == "version" for c in self.calls()))

    def test_candidate_branch_uses_remote_sources_in_all_repositories(self):
        self.remotes()
        branch = "feat/requirement-collaboration"
        (self.frontend / "index.html").write_text("committed source")
        (self.backend / "migrations/00013_candidate.sql").write_text("-- candidate")
        candidates = []
        for repo in (self.frontend, self.backend, self.work):
            (repo / "candidate.txt").write_text("candidate-only change")
            candidates.append(self.commit(repo))
            subprocess.run([self.git, "-C", str(repo), "push", "-q", "origin", "HEAD:" + branch], check=True)
        result = self.auto_package(MOPILOT_SOURCE_REF=branch)
        self.assertEqual(result.returncode, 0, result.stderr)
        source = (self.base / ".scratch/packages" / TAG / "source.txt").read_text()
        for key, sha in zip(("frontend_commit", "backend_commit", "deploy_runtime_commit"), candidates):
            self.assertIn(key + "=" + sha, source)
        self.assertIn("required_migration=00013", source)

    def test_missing_candidate_branch_does_not_fall_back_to_main(self):
        self.remotes()
        result = self.auto_package(MOPILOT_SOURCE_REF="feat/missing")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[:2] == ["buildx", "build"] for c in self.calls()))

    def test_invalid_candidate_branch_is_rejected(self):
        result = self.auto_package(MOPILOT_SOURCE_REF="../main")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[:2] == ["buildx", "build"] for c in self.calls()))

    def test_failed_fetch_never_reuses_stale_fetch_head(self):
        self.remotes()
        for repo in (self.frontend, self.backend, self.work):
            subprocess.run([self.git, "-C", str(repo), "fetch", "-q", "origin", "main"], check=True)
        result = self.auto_package(FAIL_FETCH="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[:2] == ["buildx", "build"] for c in self.calls()))

    def test_automatic_mode_refuses_missing_migrations(self):
        shutil.rmtree(self.backend / "migrations")
        self.api_sha = self.commit(self.backend)
        self.remotes()
        result = self.auto_package()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[:2] == ["buildx", "build"] for c in self.calls()))

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
