"""Prepare a dedicated clustering runtime on an authenticated SSH host."""

import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile


def root():
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "pymolplugins" / "clustering"


def _prefixes():
    home = Path.home()
    roots = [home / name for name in ("miniforge3", "mambaforge", "miniconda3", "anaconda3", "micromamba")]
    if os.environ.get("MAMBA_ROOT_PREFIX"):
        roots.insert(0, Path(os.environ["MAMBA_ROOT_PREFIX"]))
    if os.name != "nt":
        roots.append(Path("/opt/conda"))
    for name in ("VIRTUAL_ENV", "CONDA_PREFIX"):
        if os.environ.get(name):
            yield Path(os.environ[name])
    yield from roots
    for name in os.environ.get("CONDA_ENVS_PATH", "").split(os.pathsep):
        if name:
            yield from _env_children(Path(name))
    for directory in [*(path / "envs" for path in roots), home / ".conda/envs", home / ".venvs",
                      home / ".virtualenvs", home / "envs"]:
        yield from _env_children(directory)
    try:
        registry = (home / ".conda/environments.txt").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return
    for line in registry.splitlines():
        if line.strip() and not line.startswith("#"):
            yield Path(line.strip()).expanduser()


def _env_children(directory):
    try:
        return sorted(path for path in directory.iterdir() if path.is_dir())
    except OSError:
        return ()


def _probe(command, env):
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        output = subprocess.check_output(command, stderr=subprocess.DEVNULL,
                                         timeout=5, creationflags=flags, env=env)
    except (OSError, subprocess.SubprocessError):
        return ""
    return output.decode("utf-8", "replace").strip()


def discover(method, need_file, *, private=None, default_python=None):
    """Select runnable tools from existing environments before considering install."""
    home = Path.home()
    private = Path(private) if private is not None else root()
    prefixes = tuple(dict.fromkeys(_prefixes())) + (private / "environment",)
    common = [home / ".local/bin", home / "bin"]
    tools = {"sequence": ("mmseqs",), "structure": ("foldseek",), "both": ("mmseqs", "foldseek")}[method]
    env = os.environ.copy()
    exe = ".exe" if os.name == "nt" else ""
    for name in tools:
        key = "PYMOLPLUGINS_CLUSTER_" + name.upper()
        directories = list(common)
        for prefix in prefixes:
            directories.extend((prefix / "bin", prefix / "Library/bin", prefix / "Scripts", prefix))
        directories.extend(base / name / "bin" for base in
                           (home, home / "tools", home / ".local", Path("/opt"), private))
        candidates = [candidate for candidate in (env.pop(key, None), shutil.which(name)) if candidate]
        candidates.extend(str(directory / (name + exe)) for directory in dict.fromkeys(directories))
        for candidate in dict.fromkeys(candidates):
            if not os.path.isfile(candidate) or not os.access(candidate, os.X_OK):
                continue
            trial = dict(env, PATH=str(Path(candidate).parent) + os.pathsep + env.get("PATH", ""))
            if _probe([candidate, "version"], trial):
                env = trial
                env[key] = candidate
                break
    python = default_python or sys.executable
    if not need_file:
        return python, env
    names = ("python3.11", "python3.12", "python3.13", "python3.10", "python3.9")
    choices = [python, *(shutil.which(name) for name in names)]
    for prefix in prefixes:
        if os.name == "nt":
            choices.extend(str(prefix / name) for name in ("python.exe", "Scripts/python.exe"))
        else:
            choices.append(str(prefix / "bin/python"))
    probe = ("import sys; sys.path.append(sys.argv[1] + '/gemmi-%d.%d' % sys.version_info[:2]); "
             "import gemmi; print(gemmi.__version__)")
    arguments = ["-I", "-c"] if os.name == "nt" else ["-c"]
    for candidate in dict.fromkeys(choices):
        if candidate and os.path.isfile(candidate) and _probe([candidate, *arguments, probe, str(private)], env):
            return candidate, env
    if default_python is None and sys.version_info < (3, 9):
        python = next((path for path in choices[1:1 + len(names)] if path), python)
    return python, env


def _gemmi_target():
    return root() / ("gemmi-%d.%d" % sys.version_info[:2])


def configure():
    managed = root() / "environment/bin"
    if managed.is_dir():
        os.environ["PATH"] = str(managed) + os.pathsep + os.environ.get("PATH", "")
    target = _gemmi_target()
    if target.is_dir():
        sys.path.append(str(target))


def _install_gemmi(run, cancel):
    target = _gemmi_target()
    if target.exists():
        raise RuntimeError("Gemmi in the clustering user directory could not load: " + str(target))
    with tempfile.TemporaryDirectory(prefix="setup-", dir=str(root())) as stage:
        destination = Path(stage) / "gemmi"
        command = [sys.executable, "-m", "pip", "--isolated", "install", "--no-deps",
                   "--only-binary=:all:", "--index-url", "https://pypi.org/simple",
                   "--target", str(destination), "gemmi==0.7.4"]
        run(command, stage, cancel)
        cancel()
        destination.rename(target)
    configure()


def _manager(run, stage, cancel):
    for name in ("micromamba", "mamba", "conda"):
        executable = shutil.which(name)
        if executable:
            return executable, name
    architecture = {"x86_64": "64", "aarch64": "aarch64"}.get(platform.machine())
    curl = shutil.which("curl")
    if platform.system() != "Linux" or not architecture or not curl:
        raise RuntimeError("Install Conda, Mamba or Micromamba on this SSH host, then retry.")
    binary = root() / "micromamba"
    if not binary.is_file():
        downloaded = Path(stage) / "micromamba"
        url = "https://github.com/mamba-org/micromamba-releases/releases/download/2.9.0-0/micromamba-linux-" + architecture
        run([curl, "-fLsS", "--proto", "=https", "--max-time", "180", "-o", str(downloaded), url],
            stage, cancel)
        downloaded.chmod(0o755)
        run([str(downloaded), "--version"], stage, cancel)
        cancel()
        downloaded.replace(binary)
    return str(binary), "micromamba"


def install(method, need_file, check, run, cancel):
    """Install only when requested and return the same report as a check."""
    current = check(method, need_file)
    if current["ready"]:
        return current
    root().mkdir(parents=True, exist_ok=True)
    if current["missing"] == ["gemmi"] and sys.version_info >= (3, 9):
        _install_gemmi(run, cancel)
        return check(method, need_file)

    target = root() / "environment"
    # Publish Conda environments at their final prefix, since installed
    # executables may embed that absolute path.
    lock = root() / "install.lock"
    try:
        lock.mkdir()
    except FileExistsError:
        raise RuntimeError("Another clustering dependency installation is in progress.")
    try:
        current = check(method, need_file)
        if current["ready"]:
            return current
        with tempfile.TemporaryDirectory(prefix="setup-", dir=str(root())) as stage:
            executable, name = _manager(run, stage, cancel)
            packages = ["python=3.11", "gemmi", "mmseqs2"]
            # Include previously available tools in the private environment
            # only when required by the selected method.
            if method != "sequence":
                packages.append("foldseek")
            subcommand = "install" if (target / "conda-meta/history").is_file() else "create"
            options = ["--root-prefix", str(root() / "mamba"), "--no-rc"] if name == "micromamba" else []
            run([executable, *options, subcommand, "-y", "-p", str(target), "--override-channels",
                 "--strict-channel-priority", "-c", "conda-forge", "-c", "bioconda", *packages],
                stage, cancel)
        configure()
        python = target / "bin/python"
        if need_file and python.is_file():
            subprocess.run([str(python), "-c", "import gemmi"], check=True, timeout=15)
        result = check(method, False)
        if need_file:
            version = subprocess.check_output([str(python), "-c", "import gemmi; print(gemmi.__version__)"],
                                              universal_newlines=True, timeout=15).strip()
            result["available"]["gemmi"] = version
        result["environment"] = str(python)
        return result
    finally:
        lock.rmdir()
