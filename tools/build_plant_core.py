"""Build the shared C++ plant, optional Python bridge and portable native SDK."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]


def package_sdk(cmake: str, build: Path, env: dict[str, str]) -> Path:
    """Qualify an installed native client before archiving its runtime files."""
    if os.name != "nt":
        raise RuntimeError("The portable SDK packaging check currently targets Windows x64.")
    with TemporaryDirectory(prefix="azeocore-sdk-") as directory:
        stage = Path(directory) / "azeocore-sdk-windows-x64"
        subprocess.run([cmake, "--install", str(build), "--config", "Release",
                        "--prefix", str(stage)], env=env, check=True)
        # Rebuild the consumer against ONLY installed headers/imports. This
        # catches missing exports and absolute source/build path dependencies.
        client = Path(directory) / "client"
        subprocess.run([cmake, "-S", str(stage / "examples"), "-B", str(client),
                        "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
                        f"-DCMAKE_PREFIX_PATH={stage}"], env=env, check=True)
        subprocess.run([cmake, "--build", str(client), "--parallel", "2"], env=env, check=True)
        clean_env = {name: value for name, value in os.environ.items()
                     if name.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC"}}
        windows = Path(clean_env.get("SystemRoot", clean_env.get("SYSTEMROOT", "C:/Windows")))
        clean_env["PATH"] = os.pathsep.join((str(windows / "System32"), str(windows)))
        for executable in (stage / "bin/azeocore_example.exe", client / "azeocore_example.exe"):
            subprocess.run([str(executable)], cwd=directory, env=clean_env, check=True, timeout=30)
        dumpbin = shutil.which("dumpbin", path=env.get("PATH"))
        if not dumpbin:
            raise RuntimeError("MSVC dumpbin is required to verify SDK DLL dependencies.")
        dependencies = subprocess.check_output(
            [dumpbin, "/DEPENDENTS", str(stage / "bin/azeocore.dll")],
            env=env, text=True, errors="replace")
        dependency_names = re.findall(r"(?im)^\s*([a-z0-9_.-]+\.dll)\s*$", dependencies)
        for name in dependency_names:
            if name.lower().startswith(("msvcp", "vcruntime", "concrt")) and not (stage / "bin" / name).is_file():
                raise RuntimeError(f"Missing app-local MSVC runtime: {name}")
        if any(name.lower().startswith(("python", "qt5", "qt6")) for name in dependency_names):
            raise RuntimeError("The native core must not depend on Python or Qt.")
        (stage / "dependencies.txt").write_text(dependencies, encoding="utf-8")
        compiler_files = list((build / "CMakeFiles").glob("*/CMakeCXXCompiler.cmake"))
        compiler_version = next((match.group(1) for path in compiler_files
            if (match := re.search(r'set\(CMAKE_CXX_COMPILER_VERSION "([^"]+)"\)',
                                   path.read_text(encoding="utf-8")))), None)
        if not compiler_version:
            raise RuntimeError("Cannot record the SDK's C++ compiler ABI.")
        (stage / "build.json").write_text(json.dumps({
            "version": "1.0.0", "abi_version": 1, "platform": "windows-x64",
            "compiler": "MSVC", "compiler_version": compiler_version,
            "vc_tools_version": env.get("VCTOOLSVERSION", "").rstrip("/\\"),
            "configuration": "Release", "cpp_standard": 20, "runtime": "MSVC shared CRT (/MD)",
            "requires_python": False, "requires_qt": False,
            "dll_dependencies": dependency_names,
            "checks": ["native core checks", "installed SDK consumer build",
                       "native clients with Windows-only PATH", "DLL dependency inspection"],
        }, indent=2) + "\n", encoding="utf-8")
        output = ROOT / "dist"
        output.mkdir(exist_ok=True)
        archive = shutil.make_archive(str(output / stage.name), "zip", directory, stage.name)
    return Path(archive)


def build_environment() -> dict[str, str]:
    env = os.environ.copy()
    if os.name != "nt":
        return env
    locator = Path(env.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / (
        "Microsoft Visual Studio/Installer/vswhere.exe")
    installation = subprocess.check_output([
        str(locator), "-latest", "-products", "*", "-requires",
        "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath",
    ], text=True).strip()
    if not installation:
        raise RuntimeError("Install Visual Studio C++ build tools for the native plant.")
    setup = Path(installation) / "VC/Auxiliary/Build/vcvars64.bat"
    # CPython on Windows requires MSVC; a MinGW compiler found on PATH cannot
    # link its extension. Import only the build environment, without exposing it.
    output = subprocess.check_output(
        f'cmd.exe /d /s /c ""{setup}" >nul && set"',
        text=True, errors="replace")
    # Windows names are case-insensitive; a second 'Path' key otherwise
    # leaves shutil.which reading the old PATH without the compiler tools.
    env.update((name.upper(), value) for name, value in
               (line.split("=", 1) for line in output.splitlines()
                if "=" in line and not line.startswith("=")))
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--cpp-only", action="store_true",
                        help="Build without Python/pybind11 in a separate build directory")
    parser.add_argument("--sdk", action="store_true",
                        help="Install, verify and zip a Windows x64 C++ SDK under dist/")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    if args.sdk and os.name != "nt":
        parser.error("--sdk currently packages Windows x64; use cmake --install on other platforms")
    python_options = ["-DAZEO_BUILD_PYTHON=OFF"]
    if not args.cpp_only:
        try:
            import pybind11
        except ImportError:
            parser.error(f'Install build dependencies: "{sys.executable}" -m pip install ".[native]"')
        python_options = ["-DAZEO_BUILD_PYTHON=ON", f"-DPython_EXECUTABLE={sys.executable}",
                          f"-Dpybind11_DIR={pybind11.get_cmake_dir()}"]
    env = build_environment()
    cmake = shutil.which("cmake", path=env.get("PATH"))
    if not cmake or not shutil.which("ninja", path=env.get("PATH")):
        parser.error("CMake and Ninja are required; install the native optional dependencies.")
    source = ROOT / "AzeoPlantSimulator/cpp"
    build = source / ("build-sdk" if args.cpp_only else "build")
    runtime_options = []
    if os.name == "nt":
        redist = Path(env.get("VCTOOLSREDISTDIR", ""))
        runtime_dirs = sorted((redist / "x64").glob("Microsoft.VC*.CRT"))
        if not runtime_dirs:
            parser.error("The Visual Studio x64 C++ redistributable files are required.")
        runtime_options = [f"-DAZEO_MSVC_REDIST_DIRECTORY={runtime_dirs[-1]}"]
    subprocess.run([cmake, "-S", str(source), "-B", str(build), "-G", "Ninja",
                    "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_TESTING=ON", *python_options,
                    *runtime_options],
                   env=env, check=True)
    subprocess.run([cmake, "--build", str(build), "--parallel", str(args.jobs)], env=env, check=True)
    ctest = str(Path(cmake).with_name("ctest.exe" if os.name == "nt" else "ctest"))
    subprocess.run([ctest, "--test-dir", str(build), "-C", "Release", "--output-on-failure"],
                   env=env, check=True)
    if args.sdk:
        print(f"Verified native SDK: {package_sdk(cmake, build, env)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
