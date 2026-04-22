#!/usr/bin/env python3
"""water_moccasin CLI — build, run, and deploy the Dolphin container.

Usage examples:
    python wm.py build
    python wm.py deploy
    python wm.py start
    python wm.py cleanup --archive
    python wm.py package-usb --dest /media/user/MYUSB
    python wm.py netplay host --game /game/melee.iso
    python wm.py controllers list
    python wm.py controllers telemetry /dev/input/event22

Stdlib only. Drives the docker CLI via subprocess; does not import the docker
SDK. Host-side runtime assumptions (X11 socket, PulseAudio, /dev/dri,
/dev/input) are Linux-only — on Windows, run under WSL2.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import logging
import logging.handlers
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent
ENV_FILE = PROJECT_ROOT / ".env"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"
LOG_DIR = PROJECT_ROOT / "logs"
BACKUPS_DIR = PROJECT_ROOT / "dist" / "saves"

# ----------------------------------------------------------------------------- #
# logging
# ----------------------------------------------------------------------------- #

logger = logging.getLogger("wm")


class _ColorFormatter(logging.Formatter):
    _LEVEL_COLOR = {
        logging.DEBUG:    "\033[90m",   # grey
        logging.INFO:     "\033[36m",   # cyan
        logging.WARNING:  "\033[33m",   # yellow
        logging.ERROR:    "\033[31m",   # red
        logging.CRITICAL: "\033[31;1m", # bold red
    }
    _RESET = "\033[0m"

    def __init__(self, use_color: bool) -> None:
        super().__init__()
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        msg = record.getMessage()
        if not self.use_color:
            return f"[wm] {msg}"
        color = self._LEVEL_COLOR.get(record.levelno, "")
        return f"{color}[wm]{self._RESET} {msg}"


def configure_logging(verbosity: int = 0) -> None:
    """verbosity: -1 quiet, 0 normal, 1 verbose. Idempotent."""
    if verbosity >= 1:
        console_level = logging.DEBUG
    elif verbosity <= -1:
        console_level = logging.WARNING
    else:
        console_level = logging.INFO

    logger.setLevel(logging.DEBUG)
    for h in list(logger.handlers):
        logger.removeHandler(h)

    use_color = sys.stderr.isatty() and os.environ.get("NO_COLOR") is None
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(console_level)
    console.setFormatter(_ColorFormatter(use_color))
    logger.addHandler(console)

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            LOG_DIR / "wm.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8",
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        ))
        logger.addHandler(fh)
    except OSError as exc:  # read-only fs, permissions, etc.
        # Don't crash: file logging is a nice-to-have.
        print(f"[wm] (could not open logs/wm.log: {exc})", file=sys.stderr)

    logger.propagate = False


def log(msg: str) -> None:
    logger.info(msg)


def warn(msg: str) -> None:
    logger.warning(msg)


def die(msg: str, code: int = 1) -> None:
    logger.error(msg)
    sys.exit(code)


# ----------------------------------------------------------------------------- #
# settings (env loader)
# ----------------------------------------------------------------------------- #

@dataclass
class Settings:
    uid: int
    gid: int
    input_gid: int
    image_name: str = "water-moccasin/dolphin"
    image_tag: str = "latest"
    container_name: str = "water-moccasin"
    mem_limit: str = "2g"
    dolphin_mode: str = "play"
    controller_devices: list[str] = field(default_factory=list)
    netplay_port: int = 2626
    netplay_game: str = ""
    display: str = ""

    @property
    def image(self) -> str:
        return f"{self.image_name}:{self.image_tag}"


def _parse_env_file(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE parser. Ignores blanks/comments. No quoting/expansion."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def _detect_input_gid() -> int:
    try:
        out = subprocess.run(
            ["getent", "group", "input"], capture_output=True, text=True, check=False
        )
        if out.returncode == 0 and out.stdout:
            return int(out.stdout.strip().split(":")[2])
    except (FileNotFoundError, ValueError, IndexError):
        pass
    return 104


def load_settings() -> Settings:
    file_env = _parse_env_file(ENV_FILE)

    def get(key: str, default: str) -> str:
        return os.environ.get(key) or file_env.get(key) or default

    uid_default = os.getuid() if hasattr(os, "getuid") else 1000
    gid_default = os.getgid() if hasattr(os, "getgid") else 1000

    uid = int(get("UID", str(uid_default)))
    gid = int(get("GID", str(gid_default)))
    input_gid = int(get("INPUT_GID", str(_detect_input_gid())))

    devs_raw = get("CONTROLLER_DEVICES", "")
    devs = [d.strip() for d in devs_raw.split(",") if d.strip()]

    return Settings(
        uid=uid,
        gid=gid,
        input_gid=input_gid,
        image_name=get("IMAGE_NAME", "water-moccasin/dolphin"),
        image_tag=get("IMAGE_TAG", "latest"),
        container_name=get("CONTAINER_NAME", "water-moccasin"),
        mem_limit=get("MEM_LIMIT", "2g"),
        dolphin_mode=get("DOLPHIN_MODE", "play"),
        controller_devices=devs,
        netplay_port=int(get("NETPLAY_PORT", "2626")),
        netplay_game=get("NETPLAY_GAME", ""),
        display=get("DISPLAY", ""),
    )


def update_env_key(key: str, value: str) -> None:
    """Set KEY=VALUE in .env, preserving other lines. Creates .env from example if absent."""
    if not ENV_FILE.exists():
        if ENV_EXAMPLE.exists():
            ENV_FILE.write_text(ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            ENV_FILE.write_text("", encoding="utf-8")
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    pat = re.compile(rf"^{re.escape(key)}=")
    replaced = False
    out: list[str] = []
    for line in lines:
        if pat.match(line):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        if out and out[-1] != "":
            out.append("")
        out.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------------- #
# docker helpers
# ----------------------------------------------------------------------------- #

def docker_bin() -> str:
    path = shutil.which("docker")
    if not path:
        die("docker CLI not found on PATH.")
    return path


def run(cmd: Sequence[str], *, check: bool = True, capture: bool = False,
        env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    logger.debug("$ %s", " ".join(shlex.quote(c) for c in cmd))
    return subprocess.run(
        list(cmd),
        check=check,
        capture_output=capture,
        text=True,
        env=env,
    )


def image_exists(image: str) -> bool:
    out = subprocess.run(
        [docker_bin(), "image", "inspect", image],
        capture_output=True, text=True, check=False,
    )
    return out.returncode == 0


def container_exists(name: str) -> bool:
    out = subprocess.run(
        [docker_bin(), "ps", "-a", "--format", "{{.Names}}"],
        capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        return False
    return name in out.stdout.splitlines()


def container_running(name: str) -> bool:
    out = subprocess.run(
        [docker_bin(), "ps", "--format", "{{.Names}}"],
        capture_output=True, text=True, check=False,
    )
    return out.returncode == 0 and name in out.stdout.splitlines()


# ----------------------------------------------------------------------------- #
# subcommand: build
# ----------------------------------------------------------------------------- #

def cmd_build(args: argparse.Namespace) -> int:
    s = load_settings()
    tag = args.tag or s.image
    cmd = [
        docker_bin(), "build", "--network=host",
        "--build-arg", f"UID={s.uid}",
        "--build-arg", f"GID={s.gid}",
        "--build-arg", f"INPUT_GID={s.input_gid}",
    ]
    if args.no_cache:
        cmd.append("--no-cache")
    for ba in args.build_arg or []:
        cmd += ["--build-arg", ba]
    cmd += ["-t", tag, str(PROJECT_ROOT)]
    log(f"Building {tag} (UID={s.uid} GID={s.gid} INPUT_GID={s.input_gid})")
    run(cmd)
    log(f"Built {tag}")
    return 0


# ----------------------------------------------------------------------------- #
# subcommand: start
# ----------------------------------------------------------------------------- #

def _preflight_linux(s: Settings) -> None:
    if platform.system() != "Linux":
        warn(f"Host is {platform.system()}; the Dolphin GUI container expects a Linux host "
             "(WSL2 on Windows). Continuing, but X11/PulseAudio mounts will likely not work.")
        return
    if not s.display:
        warn("DISPLAY is unset; Dolphin UI will have no screen.")
    if not Path("/tmp/.X11-unix").exists():
        warn("/tmp/.X11-unix missing; X11 forwarding will fail.")
    if not Path(f"/run/user/{s.uid}/pulse").exists():
        warn(f"No PulseAudio socket at /run/user/{s.uid}/pulse; audio will be silent.")
    if not Path("/dev/dri").exists():
        warn("No /dev/dri; GPU acceleration unavailable (software rendering only).")


def _xhost_allow() -> None:
    if not shutil.which("xhost") or not os.environ.get("DISPLAY"):
        return
    try:
        user = os.environ.get("USER") or (
            subprocess.run(["id", "-un"], capture_output=True, text=True, check=False).stdout.strip()
        )
        if user:
            subprocess.run(["xhost", f"+SI:localuser:{user}"],
                           capture_output=True, check=False)
    except OSError:
        pass


def _controller_device_flags(s: Settings) -> list[str]:
    flags: list[str] = []
    for dev in s.controller_devices:
        if not Path(dev).exists():
            warn(f"Controller device missing, skipping: {dev}")
            continue
        flags += ["--device", f"{dev}:{dev}"]
    return flags


def _docker_run_argv(s: Settings, extra_args: list[str]) -> list[str]:
    argv = [
        docker_bin(), "run", "--rm", "-it",
        "--name", s.container_name,
        "--net", "host",
        "--memory", s.mem_limit,
        "--cap-add", "SYS_NICE",
        "--group-add", str(s.input_gid),
        "-e", f"DISPLAY={s.display or ':0'}",
        "-e", f"PULSE_SERVER=unix:/run/user/{s.uid}/pulse/native",
        "-e", f"XDG_RUNTIME_DIR=/run/user/{s.uid}",
        "-e", f"DOLPHIN_MODE={s.dolphin_mode}",
        "-e", f"NETPLAY_PORT={s.netplay_port}",
        "-e", f"NETPLAY_GAME={s.netplay_game}",
        "-v", f"{PROJECT_ROOT / 'game'}:/game:ro",
        "-v", f"{PROJECT_ROOT / 'saves'}:/saves",
        "-v", f"{PROJECT_ROOT / 'logs'}:/logs",
        "-v", f"{PROJECT_ROOT / 'controller_configs'}:/controller_configs:ro",
        "-v", "/tmp/.X11-unix:/tmp/.X11-unix",
        "-v", "/dev/shm:/dev/shm",
        "-v", "/etc/machine-id:/etc/machine-id:ro",
        "-v", f"/run/user/{s.uid}/pulse:/run/user/{s.uid}/pulse",
        "--device", "/dev/dri:/dev/dri",
    ]
    argv += _controller_device_flags(s)
    argv.append(s.image)
    argv += extra_args
    return argv


def cmd_start(args: argparse.Namespace) -> int:
    s = load_settings()
    if not image_exists(s.image):
        die(f"Image {s.image} not found. Run `python wm.py build` first.")
    if container_exists(s.container_name):
        warn(f"Removing existing container {s.container_name}")
        run([docker_bin(), "rm", "-f", s.container_name], capture=True)
    _xhost_allow()
    _preflight_linux(s)
    log(f"Starting {s.container_name} (mode={s.dolphin_mode})")
    os.execvp(docker_bin(), _docker_run_argv(s, args.dolphin_args or []))


# ----------------------------------------------------------------------------- #
# subcommand: deploy
# ----------------------------------------------------------------------------- #

def cmd_deploy(args: argparse.Namespace) -> int:
    s = load_settings()
    _preflight_linux(s)
    if args.rebuild or not image_exists(s.image):
        log(f"Building image {s.image}")
        cmd_build(argparse.Namespace(tag=None, no_cache=False, build_arg=None))
    else:
        log(f"Image {s.image} already present; skipping build (--rebuild to force).")
    log("Handing off to start")
    return cmd_start(argparse.Namespace(dolphin_args=args.dolphin_args or []))


# ----------------------------------------------------------------------------- #
# saves helpers (shared by `saves` and `cleanup --archive`)
# ----------------------------------------------------------------------------- #

SAVES_DIR = PROJECT_ROOT / "saves"


def _saves_has_content() -> bool:
    if not SAVES_DIR.is_dir():
        return False
    return any(p.name != ".gitkeep" for p in SAVES_DIR.iterdir())


def _dir_size(p: Path) -> int:
    total = 0
    for f in p.rglob("*"):
        try:
            if f.is_file() and not f.is_symlink():
                total += f.stat().st_size
        except OSError:
            continue
    return total


def _fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _tar_safe_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """Reject absolute paths and `..` traversal so restore can't clobber /etc/."""
    safe: list[tarfile.TarInfo] = []
    for m in tar.getmembers():
        name = Path(m.name)
        if name.is_absolute() or ".." in name.parts:
            die(f"Refusing to extract suspicious path: {m.name}")
        safe.append(m)
    return safe


def backup_saves(name: str | None = None, force: bool = False) -> Path | None:
    """Archive ./saves to dist/saves/<name>.tar.gz. Returns the path, or None if empty."""
    if not _saves_has_content():
        warn("Saves directory is empty; nothing to back up.")
        return None
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    name = name or _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = BACKUPS_DIR / f"{name}.tar.gz"
    if path.exists() and not force:
        die(f"Backup exists: {path}. Use --force to overwrite.")
    log(f"Backing up saves -> {path}")
    with tarfile.open(path, "w:gz") as tar:
        tar.add(SAVES_DIR, arcname="saves")
    log(f"Backup size: {_fmt_size(path.stat().st_size)}")
    return path


def wipe_saves() -> None:
    if not SAVES_DIR.is_dir():
        return
    for p in SAVES_DIR.iterdir():
        if p.name == ".gitkeep":
            continue
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        else:
            p.unlink()


# ----------------------------------------------------------------------------- #
# subcommand: cleanup
# ----------------------------------------------------------------------------- #

def _rotate_logs() -> None:
    if not LOG_DIR.is_dir():
        return
    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    # Rotate *.log except the active wm.log (whose handler we'd break).
    for f in LOG_DIR.glob("*.log"):
        if f.name == "wm.log":
            continue
        f.rename(f.with_suffix(f".{ts}.log"))


def cmd_cleanup(args: argparse.Namespace) -> int:
    s = load_settings()
    if container_exists(s.container_name):
        log(f"Stopping {s.container_name}")
        subprocess.run([docker_bin(), "stop", "--time", "10", s.container_name],
                       capture_output=True, check=False)
        subprocess.run([docker_bin(), "rm", "-f", s.container_name],
                       capture_output=True, check=False)
    else:
        log(f"No container named {s.container_name}")

    _rotate_logs()
    log(f"Rotated logs in {LOG_DIR}")

    if args.archive:
        backup_saves()

    if args.wipe_saves:
        warn(f"Wiping {SAVES_DIR} (irreversible).")
        wipe_saves()

    if args.rmi and image_exists(s.image):
        log(f"Removing image {s.image}")
        subprocess.run([docker_bin(), "rmi", s.image], capture_output=True, check=False)

    log("Cleanup complete.")
    return 0


# ----------------------------------------------------------------------------- #
# subcommand: saves
# ----------------------------------------------------------------------------- #

def _list_backups() -> list[Path]:
    if not BACKUPS_DIR.is_dir():
        return []
    return sorted(BACKUPS_DIR.glob("*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)


def _resolve_backup(name: str) -> Path:
    for candidate in (BACKUPS_DIR / f"{name}.tar.gz", BACKUPS_DIR / name):
        if candidate.is_file():
            return candidate
    die(f"Backup not found: {name} (looked in {BACKUPS_DIR})")
    return Path()  # unreachable


def cmd_saves(args: argparse.Namespace) -> int:
    if args.action == "list":
        if not SAVES_DIR.is_dir():
            log("No saves directory.")
            return 0
        subs = [p for p in sorted(SAVES_DIR.iterdir()) if p.name != ".gitkeep"]
        if not subs:
            log("No save data yet.")
            return 0
        print(f"{'NAME':<16} {'SIZE':>10}", file=sys.stderr)
        print("-" * 28, file=sys.stderr)
        total = 0
        for p in subs:
            sz = _dir_size(p) if p.is_dir() else (p.stat().st_size if p.exists() else 0)
            total += sz
            print(f"{p.name:<16} {_fmt_size(sz):>10}", file=sys.stderr)
        print("-" * 28, file=sys.stderr)
        print(f"{'total':<16} {_fmt_size(total):>10}", file=sys.stderr)
        return 0

    if args.action == "backup":
        path = backup_saves(name=args.name, force=args.force)
        return 0 if path is not None else 1

    if args.action == "list-backups":
        backups = _list_backups()
        if not backups:
            log("No backups yet.")
            return 0
        print(f"{'NAME':<30} {'SIZE':>10}  CREATED", file=sys.stderr)
        print("-" * 60, file=sys.stderr)
        for e in backups:
            stat = e.stat()
            ts = _dt.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            # Strip .tar.gz cleanly (Path.stem only strips one suffix).
            stem = e.name[:-len(".tar.gz")]
            print(f"{stem:<30} {_fmt_size(stat.st_size):>10}  {ts}", file=sys.stderr)
        return 0

    if args.action == "restore":
        src = _resolve_backup(args.name)
        if _saves_has_content() and not args.force:
            die("Saves directory is not empty. Use --force to merge, or "
                "`python wm.py cleanup --wipe-saves` first.")
        log(f"Restoring {src} -> {SAVES_DIR}")
        SAVES_DIR.mkdir(exist_ok=True)
        extract_kwargs: dict[str, object] = {}
        if sys.version_info >= (3, 12):
            extract_kwargs["filter"] = "data"  # stdlib tarfile safety filter
        with tarfile.open(src, "r:gz") as tar:
            members = _tar_safe_members(tar)
            tar.extractall(PROJECT_ROOT, members=members, **extract_kwargs)  # type: ignore[arg-type]
        log("Restore complete.")
        return 0

    if args.action == "remove":
        src = _resolve_backup(args.name)
        src.unlink()
        log(f"Removed {src}")
        return 0

    if args.action == "prune":
        backups = _list_backups()
        if len(backups) <= args.keep:
            log(f"Nothing to prune (have {len(backups)} <= keep={args.keep}).")
            return 0
        for b in backups[args.keep:]:
            log(f"Removing {b.name}")
            b.unlink()
        log(f"Kept {args.keep}, removed {len(backups) - args.keep}.")
        return 0

    die(f"Unknown saves action: {args.action}")
    return 2  # unreachable


# ----------------------------------------------------------------------------- #
# subcommand: package-usb
# ----------------------------------------------------------------------------- #

_LAUNCH_SH = """#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -f .env ] && [ -f .env.example ]; then cp .env.example .env; fi
IMAGE="$(awk -F= '/^IMAGE_NAME=/{n=$2} /^IMAGE_TAG=/{t=$2} END{print n ":" t}' .env)"
if ! docker image inspect "${IMAGE}" >/dev/null 2>&1; then
  echo "Loading ${IMAGE} from image.tar ..."
  docker load -i image.tar
fi
exec python3 wm.py start "$@"
"""

_LAUNCH_CMD = """@echo off
cd /d "%~dp0"
if not exist .env if exist .env.example copy .env.example .env >nul
for /f "tokens=1,2 delims==" %%A in (.env) do (
  if /I "%%A"=="IMAGE_NAME" set IMAGE_NAME=%%B
  if /I "%%A"=="IMAGE_TAG"  set IMAGE_TAG=%%B
)
docker image inspect %IMAGE_NAME%:%IMAGE_TAG% >nul 2>&1 || docker load -i image.tar
python wm.py start %*
"""


def cmd_package_usb(args: argparse.Namespace) -> int:
    s = load_settings()
    if not (args.dest or args.tarball):
        die("Specify --dest <usb-mount> or --tarball <file>.")
    if not image_exists(s.image):
        die(f"Image {s.image} not found. Build it first.")

    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        log(f"Staging bundle in {stage}")
        for d in ("game", "saves", "logs", "controller_configs"):
            (stage / d).mkdir()

        log("Exporting image -> image.tar")
        run([docker_bin(), "save", s.image, "-o", str(stage / "image.tar")])

        # Copy host artifacts that the recipient needs to run.
        shutil.copy2(PROJECT_ROOT / "wm.py", stage / "wm.py")
        shutil.copy2(PROJECT_ROOT / "docker-compose.yml", stage / "docker-compose.yml")
        shutil.copy2(PROJECT_ROOT / ".env.example", stage / ".env.example")
        if (PROJECT_ROOT / "README.md").exists():
            shutil.copy2(PROJECT_ROOT / "README.md", stage / "README.md")

        # Container entrypoint isn't strictly needed at the target (it's
        # baked into image.tar), but ship it so the bundle is self-documenting.
        (stage / "container").mkdir()
        shutil.copy2(PROJECT_ROOT / "container" / "entrypoint.py",
                     stage / "container" / "entrypoint.py")

        for src_dir, dst_dir in [
            ("controller_configs", "controller_configs"),
            ("game", "game"),
        ]:
            src = PROJECT_ROOT / src_dir
            if src.is_dir():
                for p in src.iterdir():
                    if p.name == ".gitkeep":
                        continue
                    if p.is_dir():
                        shutil.copytree(p, stage / dst_dir / p.name)
                    else:
                        shutil.copy2(p, stage / dst_dir / p.name)

        if args.include_saves:
            saves_src = PROJECT_ROOT / "saves"
            if saves_src.is_dir():
                for p in saves_src.iterdir():
                    if p.name == ".gitkeep":
                        continue
                    dst = stage / "saves" / p.name
                    if p.is_dir():
                        shutil.copytree(p, dst)
                    else:
                        shutil.copy2(p, dst)

        (stage / "run.sh").write_text(_LAUNCH_SH, encoding="utf-8")
        (stage / "run.sh").chmod(0o755)
        (stage / "run.cmd").write_text(_LAUNCH_CMD, encoding="utf-8")

        manifest = [
            "water_moccasin USB bundle",
            f"built:     {_dt.datetime.now().isoformat(timespec='seconds')}",
            f"image:     {s.image}",
            f"host_uid:  {s.uid}",
            f"saves:     {'bundled' if args.include_saves else 'empty'}",
            "",
            "files:",
        ]
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                manifest.append(f"  {p.relative_to(stage)}")
        (stage / "MANIFEST.txt").write_text("\n".join(manifest) + "\n", encoding="utf-8")

        if args.dest:
            dest = Path(args.dest)
            if not dest.is_dir():
                die(f"Destination {dest} does not exist or isn't a directory.")
            out = dest / "water_moccasin"
            out.mkdir(exist_ok=True)
            log(f"Copying bundle to {out}")
            for p in stage.iterdir():
                target = out / p.name
                if target.exists():
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                if p.is_dir():
                    shutil.copytree(p, target)
                else:
                    shutil.copy2(p, target)
            log(f"Done. To run: cd {out} && ./run.sh   (Windows: run.cmd)")

        if args.tarball:
            tb = Path(args.tarball)
            log(f"Writing tarball {tb}")
            with tarfile.open(tb, "w:gz") as tar:
                tar.add(stage, arcname="water_moccasin")
            log("Done. Extract anywhere and run ./run.sh")

    return 0


# ----------------------------------------------------------------------------- #
# subcommand: netplay
# ----------------------------------------------------------------------------- #

def cmd_netplay(args: argparse.Namespace) -> int:
    s = load_settings()
    if args.action == "host":
        if platform.system() == "Linux" and shutil.which("ip"):
            log("Host addresses (share one with your peer):")
            out = subprocess.run(["ip", "-brief", "addr"], capture_output=True, text=True, check=False)
            for line in (out.stdout or "").splitlines():
                print(f"  {line}", file=sys.stderr)
        game = args.game or s.netplay_game
        if not game:
            warn("No --game given; entrypoint will auto-pick the first game in ./game.")
        log(f"Netplay port: {args.port}")
        os.environ["DOLPHIN_MODE"] = "netplay-host"
        os.environ["NETPLAY_PORT"] = str(args.port)
        os.environ["NETPLAY_GAME"] = game or ""
        return cmd_start(argparse.Namespace(dolphin_args=[]))

    if args.action == "join":
        log(f"Launching Dolphin UI. In the netplay dialog, connect to: {args.peer}")
        os.environ["DOLPHIN_MODE"] = "play"
        return cmd_start(argparse.Namespace(dolphin_args=[]))

    if args.action == "status":
        if container_running(s.container_name):
            log(f"Container {s.container_name} is running.")
            out = subprocess.run(
                [docker_bin(), "logs", "--tail", "20", s.container_name],
                capture_output=True, text=True, check=False,
            )
            for line in (out.stdout or "").splitlines() + (out.stderr or "").splitlines():
                print(f"  {line}", file=sys.stderr)
        else:
            log(f"Container {s.container_name} is not running.")
        if platform.system() == "Linux" and shutil.which("ss"):
            log(f"Listening on netplay port {s.netplay_port}:")
            out = subprocess.run(["ss", "-lntup"], capture_output=True, text=True, check=False)
            for line in (out.stdout or "").splitlines():
                if f":{s.netplay_port}" in line:
                    print(f"  {line}", file=sys.stderr)
        return 0

    die(f"Unknown netplay action: {args.action}")
    return 2  # unreachable


# ----------------------------------------------------------------------------- #
# subcommand: controllers
# ----------------------------------------------------------------------------- #

_PROC_INPUT = Path("/proc/bus/input/devices")


@dataclass
class InputDevice:
    name: str
    event: str
    joystick: str | None


def list_input_devices() -> list[InputDevice]:
    if not _PROC_INPUT.is_file():
        return []
    devices: list[InputDevice] = []
    for block in _PROC_INPUT.read_text(encoding="utf-8", errors="replace").split("\n\n"):
        name = ""
        handlers = ""
        for line in block.splitlines():
            if line.startswith("N: Name="):
                name = line[len("N: Name="):].strip().strip('"')
            elif line.startswith("H: Handlers="):
                handlers = line[len("H: Handlers="):].strip()
        ev = re.search(r"event\d+", handlers)
        if not ev:
            continue
        js = re.search(r"js\d+", handlers)
        devices.append(InputDevice(
            name=name,
            event=f"/dev/input/{ev.group(0)}",
            joystick=f"/dev/input/{js.group(0)}" if js else None,
        ))
    return devices


def _print_device_table(devs: Iterable[InputDevice]) -> None:
    rows = list(devs)
    print(f"{'EVENT':<22} {'JS':<18} NAME", file=sys.stderr)
    print(f"{'-'*22} {'-'*18} {'-'*36}", file=sys.stderr)
    for d in rows:
        print(f"{d.event:<22} {(d.joystick or '-'): <18} {d.name}", file=sys.stderr)


def cmd_controllers(args: argparse.Namespace) -> int:
    s = load_settings()
    if args.action == "list":
        devs = list_input_devices()
        if not devs:
            die("No input devices found (is this a Linux host?).")
        _print_device_table(devs)
        return 0

    if args.action == "pick":
        dev = args.device
        if not dev:
            devs = list_input_devices()
            if not devs:
                die("No input devices found.")
            _print_device_table(devs)
            print(f"\nPick device number (1-{len(devs)}): ", end="", file=sys.stderr, flush=True)
            try:
                idx = int(input().strip())
            except ValueError:
                die("Not a number.")
            if not 1 <= idx <= len(devs):
                die("Out of range.")
            chosen = devs[idx - 1]
            dev = chosen.event
            if chosen.joystick:
                dev = f"{chosen.event},{chosen.joystick}"
        update_env_key("CONTROLLER_DEVICES", dev)
        log(f"Saved CONTROLLER_DEVICES={dev} to {ENV_FILE}")
        return 0

    if args.action == "telemetry":
        dev = args.device
        if not dev:
            devs = list_input_devices()
            if not devs:
                die("No input devices found.")
            _print_device_table(devs)
            print(f"\nPick device number (1-{len(devs)}): ", end="", file=sys.stderr, flush=True)
            try:
                idx = int(input().strip())
            except ValueError:
                die("Not a number.")
            dev = devs[idx - 1].event
        if not Path(dev).exists():
            die(f"Device does not exist: {dev}")
        if shutil.which("evtest"):
            log(f"Streaming evtest for {dev} (Ctrl+C to stop)")
            os.execvp("evtest", ["evtest", dev])
        log(f"evtest not on host; running inside container against {dev}")
        os.execvp(docker_bin(), [
            docker_bin(), "run", "--rm", "-it",
            "--device", f"{dev}:{dev}",
            "--group-add", str(s.input_gid),
            "--entrypoint", "/usr/bin/evtest",
            s.image, dev,
        ])

    if args.action == "profiles":
        d = PROJECT_ROOT / "controller_configs"
        profiles = sorted(d.glob("*.ini"))
        if not profiles:
            log(f"No .ini profiles in {d}")
            return 0
        for p in profiles:
            print(p, file=sys.stderr)
        return 0

    die(f"Unknown controllers action: {args.action}")
    return 2  # unreachable


# ----------------------------------------------------------------------------- #
# CLI
# ----------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wm",
        description="water_moccasin — Dolphin-in-Docker launcher.",
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument("-v", "--verbose", action="store_true",
                   help="Debug-level logging (including subprocess commands).")
    g.add_argument("-q", "--quiet", action="store_true",
                   help="Warnings and errors only on stderr (file log is unaffected).")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="Build the Dolphin image.")
    b.add_argument("--tag", help="Override IMAGE_NAME:IMAGE_TAG for this build.")
    b.add_argument("--no-cache", action="store_true")
    b.add_argument("--build-arg", action="append", metavar="KEY=VALUE")
    b.set_defaults(func=cmd_build)

    s = sub.add_parser("start", help="Run the container (image must exist).")
    s.add_argument("dolphin_args", nargs=argparse.REMAINDER,
                   help="Passed through to dolphin-emu after `--`.")
    s.set_defaults(func=cmd_start)

    d = sub.add_parser("deploy", help="Build-if-needed, preflight host, then start.")
    d.add_argument("--rebuild", action="store_true")
    d.add_argument("dolphin_args", nargs=argparse.REMAINDER)
    d.set_defaults(func=cmd_deploy)

    c = sub.add_parser("cleanup", help="Stop container, rotate logs, optionally wipe/archive saves.")
    c.add_argument("--wipe-saves", action="store_true")
    c.add_argument("--archive", action="store_true")
    c.add_argument("--rmi", action="store_true", help="Also remove the image.")
    c.set_defaults(func=cmd_cleanup)

    u = sub.add_parser("package-usb", help="Bundle image+scripts to a USB dir or tarball.")
    u.add_argument("--dest", help="USB mount directory.")
    u.add_argument("--tarball", help="Output .tar.gz path (alternative to --dest).")
    u.add_argument("--include-saves", action="store_true")
    u.set_defaults(func=cmd_package_usb)

    n = sub.add_parser("netplay", help="Host or join a Dolphin netplay session.")
    nsub = n.add_subparsers(dest="action", required=True)
    nh = nsub.add_parser("host")
    nh.add_argument("--game", default="")
    nh.add_argument("--port", type=int, default=2626)
    nj = nsub.add_parser("join")
    nj.add_argument("--peer", required=True)
    nsub.add_parser("status")
    n.set_defaults(func=cmd_netplay)

    sv = sub.add_parser("saves", help="Inspect, back up, restore, and prune Dolphin saves.")
    svsub = sv.add_subparsers(dest="action", required=True)
    svsub.add_parser("list", help="Show subdirectories of ./saves with sizes.")
    svb = svsub.add_parser("backup", help="Archive ./saves to dist/saves/<name>.tar.gz.")
    svb.add_argument("--name", help="Backup name (default: timestamp).")
    svb.add_argument("--force", action="store_true", help="Overwrite existing backup.")
    svsub.add_parser("list-backups", help="Show available backup archives.")
    svr = svsub.add_parser("restore", help="Restore a named backup into ./saves.")
    svr.add_argument("name")
    svr.add_argument("--force", action="store_true",
                     help="Merge into non-empty ./saves (default refuses).")
    svrm = svsub.add_parser("remove", help="Delete a named backup.")
    svrm.add_argument("name")
    svp = svsub.add_parser("prune", help="Keep newest N backups, remove the rest.")
    svp.add_argument("--keep", type=int, default=5)
    sv.set_defaults(func=cmd_saves)

    ctl = sub.add_parser("controllers", help="List / pick / stream telemetry for input devices.")
    csub = ctl.add_subparsers(dest="action", required=True)
    csub.add_parser("list")
    cp = csub.add_parser("pick")
    cp.add_argument("--device")
    ct = csub.add_parser("telemetry")
    ct.add_argument("device", nargs="?")
    csub.add_parser("profiles")
    ctl.set_defaults(func=cmd_controllers)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    verbosity = 1 if args.verbose else (-1 if args.quiet else 0)
    configure_logging(verbosity)
    logger.debug("argv=%s", sys.argv)
    # argparse's REMAINDER eats a leading `--`; strip it so it isn't passed to dolphin.
    if hasattr(args, "dolphin_args") and args.dolphin_args and args.dolphin_args[0] == "--":
        args.dolphin_args = args.dolphin_args[1:]
    return args.func(args) or 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except subprocess.CalledProcessError as exc:
        die(f"Command failed ({exc.returncode}): {' '.join(exc.cmd) if isinstance(exc.cmd, list) else exc.cmd}",
            code=exc.returncode or 1)
