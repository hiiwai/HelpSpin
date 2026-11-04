"""0.9.13: the macOS disk image -- optional Developer ID signing, notarisation,
and a GitHub workflow that builds it.

The real build needs a Mac (PyInstaller does not cross-compile, hdiutil and
codesign are macOS-only), so these tests pin what can be checked anywhere:
the entitlements, the spec wiring, the script's argument checks (run for
real, with `uname` stubbed), and the workflow's structure.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "packaging" / "build_macos.sh"
SPEC = ROOT / "packaging" / "helspin_macos.spec"
ENTITLEMENTS = ROOT / "packaging" / "entitlements.plist"
WORKFLOW = ROOT / ".github" / "workflows" / "macos-dmg.yml"

needs_bash = pytest.mark.skipif(
    shutil.which("bash") is None or sys.platform == "win32",
    reason="the build script is bash, run on macOS",
)


def test_entitlements_are_exactly_the_two_a_frozen_python_app_needs():
    with ENTITLEMENTS.open("rb") as handle:
        entitlements = plistlib.load(handle)
    assert entitlements == {
        "com.apple.security.cs.allow-unsigned-executable-memory": True,
        "com.apple.security.cs.disable-library-validation": True,
    }


def test_spec_signs_with_an_identity_only_when_one_is_given():
    spec = SPEC.read_text(encoding="utf-8")
    assert 'os.environ.get("HELSPIN_CODESIGN_IDENTITY", "")' in spec
    assert "codesign_identity=CODESIGN_IDENTITY" in spec
    assert "entitlements_file=ENTITLEMENTS" in spec
    # Entitlements only with a real identity: with ad-hoc signing they would
    # be meaningless, and the default build must stay exactly as before.
    assert 'if CODESIGN_IDENTITY else None' in spec
    assert "codesign_identity=None" not in spec


def _spec_values(identity: str | None) -> tuple:
    """Evaluate the spec's signing lines as PyInstaller would."""
    spec = SPEC.read_text(encoding="utf-8")
    start = spec.index("CODESIGN_IDENTITY = ")
    end = spec.index("\n\n", spec.index("ENTITLEMENTS = "))
    env = dict(os.environ)
    env.pop("HELSPIN_CODESIGN_IDENTITY", None)
    if identity is not None:
        env["HELSPIN_CODESIGN_IDENTITY"] = identity
    namespace = {"os": type("os", (), {"environ": env}), "ROOT": ROOT, "str": str}
    exec(spec[start:end], namespace)   # noqa: S102 -- our own spec text
    return namespace["CODESIGN_IDENTITY"], namespace["ENTITLEMENTS"]


@pytest.mark.parametrize("value", [None, "", "   "])
def test_no_identity_means_ad_hoc(value):
    assert _spec_values(value) == (None, None)


def test_an_identity_brings_the_entitlements():
    identity, entitlements = _spec_values(" Developer ID Application: X (T1) ")
    assert identity == "Developer ID Application: X (T1)"
    assert Path(entitlements) == ENTITLEMENTS


@needs_bash
def test_script_parses():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def _run_script(tmp_path, env_extra: dict, darwin: bool = True):
    """Run build_macos.sh with `uname` stubbed; it must stop at the checks."""
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    uname = stub / "uname"
    uname.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = "-s" ]; then echo {"Darwin" if darwin else "Linux"}; '
        'else echo arm64; fi\n'
    )
    uname.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("HELSPIN_")}
    env["PATH"] = f"{stub}{os.pathsep}{env['PATH']}"
    env.update(env_extra)
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True,
                          text=True, timeout=60)


@needs_bash
def test_script_refuses_to_run_off_macos(tmp_path):
    result = _run_script(tmp_path, {}, darwin=False)
    assert result.returncode == 1
    assert "must run on macOS" in result.stderr


@needs_bash
def test_notarising_without_an_identity_is_refused_up_front(tmp_path):
    result = _run_script(tmp_path, {"HELSPIN_NOTARY_PROFILE": "helspin-notary"})
    assert result.returncode == 1
    assert "needs HELSPIN_CODESIGN_IDENTITY" in result.stderr
    assert not (ROOT / "dist" / "HelSpin.app").exists()


@needs_bash
@pytest.mark.parametrize("missing", ["file", "key_id", "issuer"])
def test_an_incomplete_api_key_is_refused_up_front(tmp_path, missing):
    key = tmp_path / "AuthKey.p8"
    key.write_text("-----BEGIN PRIVATE KEY-----\n")
    env = {
        "HELSPIN_CODESIGN_IDENTITY": "Developer ID Application: X (T1)",
        "HELSPIN_NOTARY_KEY": str(key if missing != "file" else tmp_path / "nope.p8"),
        "HELSPIN_NOTARY_KEY_ID": "" if missing == "key_id" else "ABC123",
        "HELSPIN_NOTARY_ISSUER": "" if missing == "issuer" else "uuid",
    }
    result = _run_script(tmp_path, env)
    assert result.returncode == 1
    assert "HELSPIN_NOTARY_KEY needs" in result.stderr


def test_script_never_re_signs_a_developer_id_build_ad_hoc():
    text = SCRIPT.read_text(encoding="utf-8")
    signed = text.index('if [[ -n "$IDENTITY" ]]; then\n    # PyInstaller already')
    adhoc = text.index('codesign --force --deep --sign - "$APP"')
    otherwise = text.index("\nelse\n", signed)
    assert signed < otherwise < adhoc
    # A rejected notarisation must fail the build, judged from Apple's JSON.
    assert '"$STATUS" != "Accepted"' in text
    assert "xcrun stapler staple" in text


def test_script_tells_recipients_about_open_anyway():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "Open Anyway" in text
    assert "xattr -dr com.apple.quarantine /Applications/HelSpin.app" in text


def test_workflow_structure():
    yaml = pytest.importorskip("yaml")
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    trigger = workflow[True]            # YAML 1.1 reads the key `on` as True
    inputs = trigger["workflow_dispatch"]["inputs"]
    assert inputs["tag"]["required"] is True
    assert inputs["intel"]["type"] == "boolean"
    job = workflow["jobs"]["build"]
    steps = {step.get("name", step.get("uses")): step for step in job["steps"]}
    assert steps["Build the disk image"]["run"].strip() == "./packaging/build_macos.sh"
    # Signing is optional: guarded by flags computed from the secrets, never
    # by `secrets` in an `if:` (not allowed there).
    assert steps["Import the Developer ID certificate"]["if"] == \
        "env.HAS_CERTIFICATE == 'true'"
    assert "env.HAS_NOTARY_KEY == 'true'" in steps["Prepare notarisation"]["if"]
    assert steps["Remove the signing material"]["if"] == "always()"
    assert "gh release upload" in steps["Attach to the draft release"]["run"]
    assert all("secrets." not in str(step.get("if", "")) for step in job["steps"])


@pytest.mark.skipif(shutil.which("actionlint") is None and
                    not Path("/tmp/actionlint").exists(),
                    reason="actionlint not installed")
def test_workflow_passes_actionlint():
    tool = shutil.which("actionlint") or "/tmp/actionlint"
    result = subprocess.run([tool, str(WORKFLOW)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
