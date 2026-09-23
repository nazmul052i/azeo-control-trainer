"""Exercise a test-identity Setup without touching the normal Azeo installation."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import winreg

ROOT = Path(__file__).resolve().parents[1]
QUALIFICATION_RUNNING_MUTEX = "Local\\AzeoSuite.SetupQualification.Running"


def checked_installer(path):
    path = path.resolve()
    record = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if record.get("test_identity") is not True or record["installer_sha256"] != actual:
        raise ValueError("Use an unchanged --test-identity installer for qualification")
    return path, record["version"]


def assert_payload_unchanged(installed):
    manifest = json.loads((installed / "portable-manifest.json").read_text(encoding="utf-8"))
    for name, expected_hash in manifest["files"].items():
        path = installed / name
        if path.exists():
            with path.open("rb") as stream:
                assert hashlib.file_digest(stream, "sha256").hexdigest() == expected_hash, name
    assert not list(installed.rglob("__pycache__")), "Launch wrote caches inside the installation"
    known = set(manifest["files"]) | {"installation.ini", "portable-manifest.json", "verification.json"}
    extra = [path.relative_to(installed).as_posix() for path in installed.rglob("*")
             if path.is_file() and path.relative_to(installed).as_posix() not in known
             and not path.is_relative_to(installed / "sdk")]
    assert not extra, f"Launch wrote unexpected files into the installation: {extra[:10]}"


def registry_version():
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Azeo\SetupQualification") as key:
        return winreg.QueryValueEx(key, "Version")[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("installer", type=Path)
    parser.add_argument("--upgrade", type=Path)
    parser.add_argument("--patch", type=Path,
                        help="test-identity patch Setup whose base is this installer's payload")
    args = parser.parse_args()
    installer, version = checked_installer(args.installer)
    upgrade = checked_installer(args.upgrade) if args.upgrade else None
    patch = checked_installer(args.patch) if args.patch else None
    patch_record = json.loads(args.patch.with_suffix(".json").read_text(encoding="utf-8")) if patch else {}
    evidence = ROOT / "logs/diagnostics/installer-maintenance"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "results.json").unlink(missing_ok=True)
    results = []
    env = {key: value for key, value in os.environ.items() if key.upper() in {
        "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "LOCALAPPDATA", "APPDATA",
        "USERPROFILE", "USERNAME", "USERDOMAIN", "COMPUTERNAME"}}
    windows = Path(os.environ.get("SystemRoot", "C:/Windows"))
    env.update(PATH=os.pathsep.join((str(windows / "System32"), str(windows))),
               PYTHONHOME="Z:/no-python", PYTHONPATH="Z:/no-packages", AZEO_NATIVE="1")
    with TemporaryDirectory(prefix="install-check-", dir=ROOT / "build") as temporary:
        base = Path(temporary)
        destination = base / "Azeo Ω"
        workspace = base / "User workspace"
        group_name = "Azeo Setup Qualification"
        shortcuts = Path(env["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs" / group_name
        env["AZEO_WORKSPACE_DIR"] = str(workspace)

        def setup(label, file=installer, *options, ok=True):
            log = evidence / (label + ".log")
            process = subprocess.run([str(file), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                f"/GROUP={group_name}", f"/DIR={destination}", f"/LOG={log}", *options],
                env=env, timeout=300, check=False)
            assert (process.returncode == 0) == ok, (label, process.returncode, log)
            results.append({"check": label, "exit_code": process.returncode, "ok": True})
            print(label + ": PASS", flush=True)

        def verify(mode, ver=version, executable="AzeoOperatorStation.exe"):
            output = evidence / (mode + ".json")
            output.unlink(missing_ok=True)
            process = subprocess.run([str(destination / "versions" / ver / executable),
                "--verify-package", mode, str(output)], env=env, timeout=180, check=False)
            report = json.loads(output.read_text(encoding="utf-8"))
            assert process.returncode == 0 and report["ok"], report
            results.append({"check": mode, "ok": True})
            print(mode + ": PASS", flush=True)

        try:
            setup("operator-install", installer, "/TYPE=operator")
            installed = destination / "versions" / version
            assert (installed / "AzeoOperatorStation.exe").exists()
            assert (installed / "AzeoHelp.exe").exists()
            assert (installed / "output/pdf/Azeo_Control_Trainer_User_Manual.pdf").exists()
            for product in ("Explorer", "Control_Designer", "Graphics_Designer",
                            "Operator_Station", "Simulation_Workbench", "PA_Designer"):
                assert (installed / f"output/pdf/Azeo_{product}_User_Manual.pdf").exists()
            assert not (installed / "AzeoControlDesigner.exe").exists()
            assert not (installed / "AzeoPADesigner.exe").exists()
            assert (shortcuts / "Azeo Operator Station.lnk").exists()
            for title in ("Azeo Suite", "Azeo Explorer", "Azeo Control Designer",
                          "Azeo Graphics Designer", "Azeo Operator Station",
                          "Azeo Simulation Workbench", "Azeo PA Designer"):
                assert (shortcuts / "User Guide" / f"{title} User Manual.lnk").exists()
            assert not (shortcuts / "Azeo Control Designer.lnk").exists()
            verify("environment")
            verify("help")
            verify("installed_station")
            assert_payload_unchanged(installed)
            marker = workspace / "projects/AzeoPlantVirtualController/qualification-user-data.txt"
            marker.write_text("Keep this user data through every maintenance step.", encoding="utf-8")
            expected = marker.read_bytes()
            # Remove one owned file, then verify same-version repair restores it.
            owned_file = installed / "src/azeo_control_trainer/config/distribution.py"
            assert owned_file.resolve().is_relative_to(base.resolve())
            owned_file.unlink()
            setup("repair")
            assert owned_file.is_file() and marker.read_bytes() == expected
            setup("add-procedure", installer, "/TYPE=custom", "/COMPONENTS=explorer,pa_designer,operator_station")
            assert (installed / "AzeoPADesigner.exe").exists()
            assert (shortcuts / "Azeo PA Designer.lnk").exists()
            verify("procedures", executable="AzeoPADesigner.exe")
            setup("add-simulator", installer, "/TYPE=custom",
                  "/COMPONENTS=explorer,pa_designer,operator_station,simulation_workbench")
            assert (installed / "AzeoSimulator.exe").exists()
            assert (shortcuts / "Azeo Plant Simulator.lnk").exists()
            verify("simulator", executable="AzeoSimulator.exe")
            setup("add-sdk", installer, "/TYPE=custom", "/COMPONENTS=operator_station,sdk")
            assert (installed / "sdk/bin/azeocore.dll").exists()
            assert list((installed / "sdk/python").glob("_azeocore.*.pyd"))
            setup("remove-procedure", installer, "/TYPE=operator")
            assert not (installed / "AzeoPADesigner.exe").exists()
            assert not (shortcuts / "Azeo PA Designer.lnk").exists()
            assert not (installed / "sdk/bin/azeocore.dll").exists()
            assert marker.read_bytes() == expected
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
            kernel.CreateMutexW.restype = ctypes.c_void_p
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel.CreateMutexW(None, False, QUALIFICATION_RUNNING_MUTEX)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                setup("running-process-refused", ok=False)
            finally:
                kernel.CloseHandle(handle)
            if patch:
                patch_file, patch_version = patch
                patched = destination / "versions" / patch_version
                # A damaged base must be refused before anything changes, and the
                # half-assembled folder must not remain. The probe is a file the patch
                # copies from the base; a packaged file would simply be replaced.
                probe = installed / patch_record["probe_file"]
                assert probe.is_file(), probe
                original = probe.read_bytes()
                probe.write_bytes(original + b"\n")
                setup("patch-damaged-base-refused", patch_file, ok=False)
                probe.write_bytes(original)
                assert not patched.exists(), "a refused patch left an assembled folder behind"
                assert registry_version() == version
                setup("patch", patch_file)
                assert (patched / "AzeoOperatorStation.exe").exists()
                assert not (patched / "AzeoControlDesigner.exe").exists(), "a patch changed the selection"
                assert (installed / "AzeoOperatorStation.exe").exists(), "the base must stay for rollback"
                assert registry_version() == patch_version and marker.read_bytes() == expected
                verify("environment", ver=patch_version)
                verify("installed_station", ver=patch_version)
                assert_payload_unchanged(patched)
                setup("patch-wrong-base-refused", patch_file, ok=False)
                setup("patch-rollback", installer, "/ALLOWDOWNGRADE=1")
                assert registry_version() == version and marker.read_bytes() == expected
                verify("environment")
            if upgrade:
                newer, new_version = upgrade
                setup("upgrade", newer)
                new_root = destination / "versions" / new_version
                assert (new_root / "AzeoOperatorStation.exe").exists()
                assert not (new_root / "AzeoPADesigner.exe").exists()
                assert marker.read_bytes() == expected
                verify("environment", ver=new_version)
                verify("installed_station", ver=new_version)
                assert_payload_unchanged(new_root)
                setup("downgrade-refused", ok=False)
                setup("controlled-rollback", installer, "/ALLOWDOWNGRADE=1")
                assert marker.read_bytes() == expected
                verify("environment")
        finally:
            uninstaller = destination / "maintenance/unins000.exe"
            if uninstaller.exists():
                process = subprocess.run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                    f"/LOG={evidence / 'uninstall.log'}"], env=env, timeout=180, check=False)
                assert process.returncode == 0, "Qualification uninstall failed; retain its log"
        assert marker.read_bytes() == expected
        assert not (destination / "versions" / version / "AzeoOperatorStation.exe").exists()
        if patch:
            assert not (destination / "versions" / patch[1]).exists(), "uninstall left the patched version"
        assert not (shortcuts / "Azeo Operator Station.lnk").exists()
        results.append({"check": "uninstall-preserves-workspace", "ok": True})
    (evidence / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"{len(results)} maintenance checks passed. Evidence: {evidence}")


if __name__ == "__main__":
    main()
