#!/usr/bin/env python3
"""water_moccasin CLI - build, run, stream, and deploy the Dolphin container.

Usage examples:
    python wm.py build
    python wm.py deploy
    python wm.py start
    python wm.py stream start
    python wm.py stream pair 1234
    python wm.py cleanup --archive
    python wm.py package-usb --dest /media/user/MYUSB
    python wm.py netplay host --game /game/melee.iso
    python wm.py controllers list
    python wm.py controllers telemetry /dev/input/event22

Stdlib only. Drives the docker CLI via subprocess; does not import the docker
SDK. Host-side runtime assumptions (X11 socket, PulseAudio, /dev/dri,
/dev/input, /dev/uinput) are Linux-only - on Windows, run under WSL2.
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import json
import logging
import logging.handlers
import os
import platform
import re
import secrets
import shlex
import shutil
import ssl
import stat as _stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Iterable, NoReturn, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent
ENV_FILE = PROJECT_ROOT / ".env"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"
LOG_DIR = PROJECT_ROOT / "logs"
SAVES_DIR = PROJECT_ROOT / "saves"
SUNSHINE_DIR = PROJECT_ROOT / "sunshine"
BACKUPS_DIR = PROJECT_ROOT / "dist" / "saves"
HOST_DIRS = ("game", "saves", "logs", "controller_configs", "sunshine")

# Sunshine port offsets relative to the base port (SUNSHINE_PORT). From the
# Sunshine source: nvhttp.h, confighttp.h, rtsp.h, stream.h.
SUNSHINE_TCP_OFFSETS = {"https": -5, "http": 0, "web-ui": 1, "rtsp": 21}
SUNSHINE_UDP_OFFSETS = {"video": 9, "control": 10, "audio": 11}

VALID_MODES = ("play", "netplay-host", "stream", "shell")
VALID_STREAM_DISPLAYS = ("xvfb", "host")
VALID_STREAM_INPUTS = ("uinput", "xtest")
VALID_NETWORK_MODES = ("host", "bridge")

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


def die(msg: str, code: int = 1) -> NoReturn:
    logger.error(msg)
    sys.exit(code)


def emit(line: str = "") -> None:
    """Plain (unprefixed) output for tables and instructions. Goes to stderr
    like everything else so stdout stays clean for scripting."""
    print(line, file=sys.stderr)


# ----------------------------------------------------------------------------- #
# host detection
# ----------------------------------------------------------------------------- #

def is_linux() -> bool:
    return platform.system() == "Linux"


def is_wsl() -> bool:
    """True inside a WSL2 distro (WSLg paths live under /mnt/wslg)."""
    if not is_linux():
        return False
    if Path("/mnt/wslg").is_dir():
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        return False


def docker_is_desktop() -> bool:
    """Docker Desktop runs the daemon in its own VM; host networking does not
    reach the LAN there unless the (opt-in) host-networking feature is on."""
    out = subprocess.run(
        [docker_bin(), "info", "--format", "{{.OperatingSystem}}"],
        capture_output=True, text=True, check=False,
    )
    return out.returncode == 0 and "docker desktop" in out.stdout.lower()


def host_x11_socket_dir() -> Path | None:
    """Directory holding the X11 unix sockets, or None if there is none."""
    for cand in ("/mnt/wslg/.X11-unix", "/tmp/.X11-unix"):
        p = Path(cand)
        if p.is_dir():
            return p
    return None


def host_pulse_socket(uid: int, override: str = "") -> str | None:
    """Return the PulseAudio socket path on the host, or None.

    Order: explicit override (.env PULSE_SERVER or host $PULSE_SERVER, either
    'unix:/path' or '/path'), WSLg's /mnt/wslg/PulseServer,
    $XDG_RUNTIME_DIR/pulse/native, /run/user/<uid>/pulse/native.
    """
    cands: list[str] = []
    for raw in (override, os.environ.get("PULSE_SERVER", "")):
        raw = raw.strip()
        if not raw:
            continue
        if raw.startswith("unix:"):
            raw = raw[len("unix:"):]
        if raw.startswith("/"):
            cands.append(raw)
    cands.append("/mnt/wslg/PulseServer")
    xdg = os.environ.get("XDG_RUNTIME_DIR", "")
    if xdg:
        cands.append(f"{xdg}/pulse/native")
    cands.append(f"/run/user/{uid}/pulse/native")
    for c in cands:
        p = Path(c)
        try:
            if p.exists() and _stat.S_ISSOCK(p.stat().st_mode):
                return str(p)
        except OSError:
            continue
    return None


def device_gids(paths: Iterable[str]) -> list[int]:
    """Distinct owning GIDs of the given device nodes (for --group-add)."""
    gids: list[int] = []
    for raw in paths:
        p = Path(raw)
        try:
            if p.is_dir():
                nodes = [c for c in p.iterdir()]
            else:
                nodes = [p]
            for n in nodes:
                gid = n.stat().st_gid
                if gid != 0 and gid not in gids:
                    gids.append(gid)
        except OSError:
            continue
    return gids


def node_accessible_by(path: str, uid: int, gids: Iterable[int]) -> bool:
    """Can a process with (uid, gids) read+write this device node?"""
    try:
        st = Path(path).stat()
    except OSError:
        return False
    mode = st.st_mode
    if st.st_uid == uid and (mode & 0o600) == 0o600:
        return True
    if st.st_gid in set(gids) and (mode & 0o060) == 0o060:
        return True
    return (mode & 0o006) == 0o006


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
    pulse_server: str = ""
    network_mode: str = "host"
    extra_docker_args: list[str] = field(default_factory=list)
    # streaming (Sunshine / Moonlight)
    stream_display: str = "xvfb"
    stream_resolution: str = "1920x1080"
    stream_encoder: str = "software"
    stream_input: str = "uinput"
    stream_autostart: bool = False
    sunshine_name: str = "water-moccasin"
    sunshine_user: str = "player"
    sunshine_pass: str = ""
    sunshine_port: int = 47989

    @property
    def image(self) -> str:
        return f"{self.image_name}:{self.image_tag}"

    @property
    def web_ui_port(self) -> int:
        return self.sunshine_port + SUNSHINE_TCP_OFFSETS["web-ui"]

    def sunshine_tcp_ports(self) -> list[int]:
        return sorted(self.sunshine_port + o for o in SUNSHINE_TCP_OFFSETS.values())

    def sunshine_udp_ports(self) -> list[int]:
        return sorted(self.sunshine_port + o for o in SUNSHINE_UDP_OFFSETS.values())


def _unquote(v: str) -> str:
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def _parse_env_file(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE parser. Ignores blanks/comments, tolerates a UTF-8
    BOM, CRLF endings, a leading `export `, and one pair of surrounding
    quotes (so the same .env works for docker compose). No expansion."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = _unquote(v.strip())
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


def _int_setting(key: str, raw: str) -> int:
    try:
        return int(raw)
    except ValueError:
        die(f"{key} must be an integer, got {raw!r} (check .env).")


def _bool_setting(raw: str) -> bool:
    return raw.strip().lower() in ("1", "true", "yes", "on", "enabled")


def _host_ids() -> tuple[int, int]:
    """Host uid/gid to bake into the image. Under sudo, prefer the invoking
    user so saves don't end up root-owned."""
    uid = os.getuid() if hasattr(os, "getuid") else 1000
    gid = os.getgid() if hasattr(os, "getgid") else 1000
    if uid == 0:
        s_uid, s_gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
        if s_uid and s_gid and s_uid.isdigit() and s_gid.isdigit():
            uid, gid = int(s_uid), int(s_gid)
    return uid, gid


def load_settings() -> Settings:
    file_env = _parse_env_file(ENV_FILE)

    def get(key: str, default: str) -> str:
        return os.environ.get(key) or file_env.get(key) or default

    uid_default, gid_default = _host_ids()
    uid = _int_setting("UID", get("UID", str(uid_default)))
    gid = _int_setting("GID", get("GID", str(gid_default)))
    if uid == 0 or gid == 0:
        die("Refusing to run the container as uid/gid 0. Set UID and GID in .env "
            "(or run wm.py as your normal user; sudo is detected via SUDO_UID).")
    input_gid = _int_setting("INPUT_GID", get("INPUT_GID", str(_detect_input_gid())))

    devs_raw = get("CONTROLLER_DEVICES", "")
    devs = [d.strip() for d in devs_raw.split(",") if d.strip()]

    mode = get("DOLPHIN_MODE", "play")
    if mode not in VALID_MODES:
        die(f"DOLPHIN_MODE must be one of {', '.join(VALID_MODES)}; got {mode!r}.")
    stream_display = get("STREAM_DISPLAY", "xvfb")
    if stream_display not in VALID_STREAM_DISPLAYS:
        die(f"STREAM_DISPLAY must be one of {', '.join(VALID_STREAM_DISPLAYS)}; got {stream_display!r}.")
    stream_input = get("STREAM_INPUT", "uinput")
    if stream_input not in VALID_STREAM_INPUTS:
        die(f"STREAM_INPUT must be one of {', '.join(VALID_STREAM_INPUTS)}; got {stream_input!r}.")
    network_mode = get("NETWORK_MODE", "host")
    if network_mode not in VALID_NETWORK_MODES:
        die(f"NETWORK_MODE must be one of {', '.join(VALID_NETWORK_MODES)}; got {network_mode!r}.")
    resolution = get("STREAM_RESOLUTION", "1920x1080")
    if not re.fullmatch(r"\d{3,5}x\d{3,5}", resolution):
        die(f"STREAM_RESOLUTION must look like 1920x1080; got {resolution!r}.")
    sunshine_port = _int_setting("SUNSHINE_PORT", get("SUNSHINE_PORT", "47989"))
    if not 1029 <= sunshine_port <= 65514:
        die("SUNSHINE_PORT must be between 1029 and 65514 (the other Sunshine ports are offsets of it).")

    return Settings(
        uid=uid,
        gid=gid,
        input_gid=input_gid,
        image_name=get("IMAGE_NAME", "water-moccasin/dolphin"),
        image_tag=get("IMAGE_TAG", "latest"),
        container_name=get("CONTAINER_NAME", "water-moccasin"),
        mem_limit=get("MEM_LIMIT", "2g"),
        dolphin_mode=mode,
        controller_devices=devs,
        netplay_port=_int_setting("NETPLAY_PORT", get("NETPLAY_PORT", "2626")),
        netplay_game=get("NETPLAY_GAME", ""),
        display=get("DISPLAY", ""),
        pulse_server=file_env.get("PULSE_SERVER", ""),
        network_mode=network_mode,
        extra_docker_args=shlex.split(get("EXTRA_DOCKER_ARGS", "")),
        stream_display=stream_display,
        stream_resolution=resolution,
        stream_encoder=get("STREAM_ENCODER", "software"),
        stream_input=stream_input,
        stream_autostart=_bool_setting(get("STREAM_AUTOSTART", "0")),
        sunshine_name=get("SUNSHINE_NAME", "water-moccasin"),
        sunshine_user=get("SUNSHINE_USER", "player"),
        sunshine_pass=get("SUNSHINE_PASS", ""),
        sunshine_port=sunshine_port,
    )


def update_env_key(key: str, value: str) -> None:
    """Set KEY=VALUE in .env, preserving other lines. Creates .env from
    .env.example if absent (the example ships identity keys commented out,
    so auto-detection keeps working)."""
    if not ENV_FILE.exists():
        if ENV_EXAMPLE.exists():
            ENV_FILE.write_text(ENV_EXAMPLE.read_text(encoding="utf-8-sig"),
                                encoding="utf-8", newline="\n")
        else:
            ENV_FILE.write_text("", encoding="utf-8", newline="\n")
    lines = ENV_FILE.read_text(encoding="utf-8-sig").splitlines()
    pat = re.compile(rf"^(export\s+)?{re.escape(key)}=")
    replaced = False
    out: list[str] = []
    for line in lines:
        if pat.match(line.strip()):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        if out and out[-1] != "":
            out.append("")
        out.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")


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


def exec_or_run(argv: Sequence[str]) -> NoReturn:
    """Replace this process with argv on POSIX (so signals and the TTY go
    straight to docker). On Windows os.exec* spawns and detaches instead of
    replacing, and mangles argv with spaces, so run as a child there."""
    logger.debug("$ %s", " ".join(shlex.quote(c) for c in argv))
    if os.name == "posix":
        os.execvp(argv[0], list(argv))
    rc = subprocess.run(list(argv), check=False).returncode
    sys.exit(rc)


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


def ensure_host_dirs() -> None:
    """Create bind-mount sources up front. If docker creates them, they come
    out root-owned and the container user can't write /saves."""
    for d in HOST_DIRS:
        (PROJECT_ROOT / d).mkdir(parents=True, exist_ok=True)


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
        "--build-arg", f"WITH_SUNSHINE={'0' if getattr(args, 'no_sunshine', False) else '1'}",
    ]
    if not getattr(args, "no_pull", False):
        cmd.append("--pull")  # refresh archlinux:base so the keyring isn't stale
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

@dataclass
class HostResources:
    """What the host actually offers, resolved once per start."""
    x11_dir: Path | None
    pulse_socket: str | None
    dri: bool
    machine_id: bool
    uinput: bool
    uhid: bool
    dev_input: bool
    wsl: bool


def probe_host(s: Settings) -> HostResources:
    return HostResources(
        x11_dir=host_x11_socket_dir() if is_linux() else None,
        pulse_socket=host_pulse_socket(s.uid, s.pulse_server) if is_linux() else None,
        dri=Path("/dev/dri").is_dir(),
        machine_id=Path("/etc/machine-id").is_file(),
        uinput=Path("/dev/uinput").exists(),
        uhid=Path("/dev/uhid").exists(),
        dev_input=Path("/dev/input").is_dir(),
        wsl=is_wsl(),
    )


def _preflight(s: Settings, h: HostResources) -> None:
    if not is_linux():
        warn(f"Host is {platform.system()}; the container expects a Linux host "
             "(WSL2 on Windows). Continuing, but X11/PulseAudio mounts will not work.")
        return
    headless = s.dolphin_mode == "stream" and s.stream_display == "xvfb"
    if not headless:
        if not s.display and not os.environ.get("DISPLAY"):
            warn("DISPLAY is unset; Dolphin UI will have no screen.")
        if h.x11_dir is None:
            warn("No X11 socket directory (/tmp/.X11-unix or /mnt/wslg/.X11-unix); "
                 "X11 forwarding will fail.")
        if h.pulse_socket is None:
            warn("No PulseAudio socket found ($PULSE_SERVER, /mnt/wslg/PulseServer, "
                 f"$XDG_RUNTIME_DIR/pulse/native, /run/user/{s.uid}/pulse/native); audio will be silent.")
    if not h.dri:
        warn("No /dev/dri; GPU acceleration unavailable (software rendering only).")
    elif h.wsl:
        warn("WSL2: /dev/dri exists but Mesa inside the container has no WSL GPU driver; "
             "expect software rendering.")
    if s.dolphin_mode == "stream":
        _preflight_stream(s, h)


def _preflight_stream(s: Settings, h: HostResources) -> None:
    if s.stream_input == "uinput":
        if not h.uinput:
            warn("/dev/uinput is missing on the host: Moonlight gamepads/keyboard will not work. "
                 "Run `sudo modprobe uinput` (see `wm.py stream setup-host`).")
        elif not node_accessible_by("/dev/uinput", s.uid, [s.input_gid, s.gid]):
            warn(f"/dev/uinput is not writable by uid {s.uid} / gid {s.input_gid}; Sunshine cannot "
                 "create virtual input devices. See `wm.py stream setup-host`.")
        if not h.uhid:
            warn("/dev/uhid is missing on the host: Xbox/PlayStation-style gamepads fall back to "
                 "the generic uinput gamepad. `sudo modprobe uhid` to enable.")
    if h.wsl:
        warn("WSL2: Moonlight clients on your LAN cannot reach the container's ports unless "
             "WSL mirrored networking or Docker Desktop host networking is enabled.")
    if s.stream_encoder == "software":
        log("Stream encoder is software (CPU). Lower STREAM_RESOLUTION or use vaapi/nvenc "
            "if the stream stutters.")


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


def _network_flags(s: Settings) -> list[str]:
    if s.network_mode == "host":
        return ["--net", "host"]
    flags = ["-p", f"{s.netplay_port}:{s.netplay_port}/tcp",
             "-p", f"{s.netplay_port}:{s.netplay_port}/udp"]
    if s.dolphin_mode == "stream":
        for p in s.sunshine_tcp_ports():
            flags += ["-p", f"{p}:{p}/tcp"]
        for p in s.sunshine_udp_ports():
            flags += ["-p", f"{p}:{p}/udp"]
    return flags


def _docker_run_argv(s: Settings, h: HostResources, extra_args: list[str],
                     interactive: bool | None = None) -> list[str]:
    if interactive is None:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if s.dolphin_mode == "shell" and not interactive:
        die("DOLPHIN_MODE=shell needs an interactive terminal.")

    headless = s.dolphin_mode == "stream" and s.stream_display == "xvfb"
    argv = [docker_bin(), "run", "--rm"]
    if interactive:
        argv.append("-it")
    argv += [
        "--name", s.container_name,
        *_network_flags(s),
        "--memory", s.mem_limit,
        "--cap-add", "SYS_NICE",
        "--group-add", str(s.input_gid),
    ]
    for gid in device_gids(["/dev/dri", *s.controller_devices]):
        if gid != s.input_gid:
            argv += ["--group-add", str(gid)]

    argv += [
        "-e", f"DOLPHIN_MODE={s.dolphin_mode}",
        "-e", f"NETPLAY_PORT={s.netplay_port}",
        "-e", f"NETPLAY_GAME={s.netplay_game}",
        "-v", f"{PROJECT_ROOT / 'game'}:/game:ro",
        "-v", f"{PROJECT_ROOT / 'saves'}:/saves",
        "-v", f"{PROJECT_ROOT / 'logs'}:/logs",
        "-v", f"{PROJECT_ROOT / 'controller_configs'}:/controller_configs:ro",
        "-v", "/dev/shm:/dev/shm",
    ]
    if h.machine_id:
        argv += ["-v", "/etc/machine-id:/etc/machine-id:ro"]

    if not headless:
        argv += ["-e", f"DISPLAY={s.display or os.environ.get('DISPLAY') or ':0'}"]
        if h.x11_dir is not None:
            argv += ["-v", f"{h.x11_dir.as_posix()}:/tmp/.X11-unix"]
        if h.pulse_socket is not None:
            argv += ["-e", f"PULSE_SERVER=unix:{h.pulse_socket}",
                     "-v", f"{h.pulse_socket}:{h.pulse_socket}"]
    if h.dri:
        argv += ["--device", "/dev/dri:/dev/dri"]

    if s.dolphin_mode == "stream":
        argv += [
            "-e", f"STREAM_DISPLAY={s.stream_display}",
            "-e", f"STREAM_RESOLUTION={s.stream_resolution}",
            "-e", f"STREAM_ENCODER={s.stream_encoder}",
            "-e", f"STREAM_AUTOSTART={'1' if s.stream_autostart else '0'}",
            "-e", f"SUNSHINE_NAME={s.sunshine_name}",
            "-e", f"SUNSHINE_USER={s.sunshine_user}",
            "-e", f"SUNSHINE_PASS={s.sunshine_pass}",
            "-e", f"SUNSHINE_PORT={s.sunshine_port}",
            "-v", f"{PROJECT_ROOT / 'sunshine'}:/sunshine",
        ]
        if s.stream_input == "uinput":
            if h.uinput:
                argv += ["--device", "/dev/uinput:/dev/uinput"]
            if h.uhid:
                argv += ["--device", "/dev/uhid:/dev/uhid"]
            if h.dev_input:
                # Bind (not --device) so the event nodes Sunshine creates at
                # stream time show up; the cgroup rule lets us open them.
                argv += ["-v", "/dev/input:/dev/input",
                         "--device-cgroup-rule", "c 13:* rmw"]

    argv += _controller_device_flags(s)
    argv += s.extra_docker_args
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
    if s.dolphin_mode == "stream" and not s.sunshine_pass:
        _ensure_sunshine_password()
        s = load_settings()
    ensure_host_dirs()
    h = probe_host(s)
    if not (s.dolphin_mode == "stream" and s.stream_display == "xvfb"):
        _xhost_allow()
    _preflight(s, h)
    log(f"Starting {s.container_name} (mode={s.dolphin_mode})")
    if s.dolphin_mode == "stream":
        _print_stream_hints(s)
    exec_or_run(_docker_run_argv(s, h, args.dolphin_args or []))


# ----------------------------------------------------------------------------- #
# subcommand: deploy
# ----------------------------------------------------------------------------- #

def cmd_deploy(args: argparse.Namespace) -> int:
    s = load_settings()
    if args.rebuild or not image_exists(s.image):
        log(f"Building image {s.image}")
        cmd_build(argparse.Namespace(tag=None, no_cache=False, build_arg=None,
                                     no_pull=False, no_sunshine=False))
    else:
        log(f"Image {s.image} already present; skipping build (--rebuild to force).")
    log("Handing off to start")
    return cmd_start(argparse.Namespace(dolphin_args=args.dolphin_args or []))


# ----------------------------------------------------------------------------- #
# saves helpers (shared by `saves` and `cleanup --archive`)
# ----------------------------------------------------------------------------- #

_BACKUP_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# Dolphin's shader/cover caches are big and regenerate themselves; skip them
# in backups unless asked.
_BACKUP_SKIP_DIRS = ("Cache",)


def _check_backup_name(name: str) -> str:
    if not _BACKUP_NAME_RE.fullmatch(name) or ".." in name:
        die(f"Invalid backup name {name!r}: use letters, digits, '.', '_' and '-' only.")
    return name


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
    """Only plain files/dirs under `saves/`. Rejects absolute paths, `..`,
    links and device nodes so restore can only ever touch ./saves."""
    safe: list[tarfile.TarInfo] = []
    for m in tar.getmembers():
        name = PurePosixPath(m.name)
        parts = name.parts
        if m.name.startswith(("/", "\\")) or name.is_absolute() or ".." in parts or "\\" in m.name:
            die(f"Refusing to extract suspicious path: {m.name}")
        if not parts or parts[0] != "saves":
            die(f"Refusing to extract {m.name!r}: not under saves/ (is this a wm.py backup?)")
        if not (m.isfile() or m.isdir()):
            die(f"Refusing to extract non-regular member: {m.name} (type {m.type!r})")
        safe.append(m)
    return safe


def backup_saves(name: str | None = None, force: bool = False,
                 include_cache: bool = False) -> Path | None:
    """Archive ./saves to dist/saves/<name>.tar.gz. Returns the path, or None if empty."""
    if not _saves_has_content():
        warn("Saves directory is empty; nothing to back up.")
        return None
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    name = _check_backup_name(name or _dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    path = BACKUPS_DIR / f"{name}.tar.gz"
    if path.exists() and not force:
        die(f"Backup exists: {path}. Use --force to overwrite.")
    log(f"Backing up saves -> {path}")
    skip = set() if include_cache else set(_BACKUP_SKIP_DIRS)

    def _filter(ti: tarfile.TarInfo) -> tarfile.TarInfo | None:
        rel = PurePosixPath(ti.name).parts[1:]  # drop leading 'saves'
        if rel and rel[0] in skip:
            return None
        return ti

    tmp = path.with_name(path.name + ".tmp")
    try:
        with tarfile.open(tmp, "w:gz") as tar:
            tar.add(SAVES_DIR, arcname="saves", filter=_filter)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if skip:
        log(f"Skipped {', '.join(sorted(skip))}/ (pass --include-cache to keep it)")
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

# Logs written by processes inside the container. wm.log is left alone because
# this process holds it open.
_ROTATABLE_LOGS = ("container.log", "sunshine.log")
_ROTATED_RE = re.compile(r"^(?P<base>.+)\.(?P<ts>\d{8}-\d{6})\.log$")
_ROTATED_KEEP = 5


def _rotate_logs() -> list[Path]:
    """Snapshot container.log / sunshine.log to <base>.<ts>.log, keep the
    newest few snapshots per base. Idempotent: already-rotated files are
    never renamed again."""
    rotated: list[Path] = []
    if not LOG_DIR.is_dir():
        return rotated
    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    for name in _ROTATABLE_LOGS:
        f = LOG_DIR / name
        if not f.is_file() or f.stat().st_size == 0:
            continue
        base = name[:-len(".log")]
        target = LOG_DIR / f"{base}.{ts}.log"
        f.rename(target)
        rotated.append(target)
    # prune
    by_base: dict[str, list[Path]] = {}
    for f in LOG_DIR.glob("*.log"):
        m = _ROTATED_RE.match(f.name)
        if m:
            by_base.setdefault(m.group("base"), []).append(f)
    for base, files in by_base.items():
        files.sort(key=lambda p: p.name, reverse=True)
        for old in files[_ROTATED_KEEP:]:
            old.unlink()
    return rotated


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

    rotated = _rotate_logs()
    if rotated:
        log("Rotated: " + ", ".join(p.name for p in rotated))
    else:
        log("No container logs to rotate.")

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
    if name.endswith(".tar.gz"):
        name = name[:-len(".tar.gz")]
    _check_backup_name(name)
    candidate = BACKUPS_DIR / f"{name}.tar.gz"
    if candidate.is_file():
        return candidate
    die(f"Backup not found: {name} (looked in {BACKUPS_DIR})")


def restore_saves(src: Path, force: bool = False) -> None:
    if _saves_has_content() and not force:
        die("Saves directory is not empty. Use --force to merge, or "
            "`python wm.py cleanup --wipe-saves` first.")
    log(f"Restoring {src} -> {SAVES_DIR}")
    SAVES_DIR.mkdir(exist_ok=True)
    extract_kwargs: dict[str, object] = {}
    if hasattr(tarfile, "data_filter"):  # 3.12+, and backported to 3.10.12/3.11.4
        extract_kwargs["filter"] = "data"
    with tarfile.open(src, "r:gz") as tar:
        members = _tar_safe_members(tar)
        tar.extractall(PROJECT_ROOT, members=members, **extract_kwargs)  # type: ignore[arg-type]
    log("Restore complete.")


def cmd_saves(args: argparse.Namespace) -> int:
    if args.action == "list":
        if not SAVES_DIR.is_dir():
            log("No saves directory.")
            return 0
        subs = [p for p in sorted(SAVES_DIR.iterdir()) if p.name != ".gitkeep"]
        if not subs:
            log("No save data yet.")
            return 0
        emit(f"{'NAME':<16} {'SIZE':>10}")
        emit("-" * 28)
        total = 0
        for p in subs:
            sz = _dir_size(p) if p.is_dir() else (p.stat().st_size if p.exists() else 0)
            total += sz
            emit(f"{p.name:<16} {_fmt_size(sz):>10}")
        emit("-" * 28)
        emit(f"{'total':<16} {_fmt_size(total):>10}")
        return 0

    if args.action == "backup":
        path = backup_saves(name=args.name, force=args.force, include_cache=args.include_cache)
        return 0 if path is not None else 1

    if args.action == "list-backups":
        backups = _list_backups()
        if not backups:
            log("No backups yet.")
            return 0
        emit(f"{'NAME':<30} {'SIZE':>10}  CREATED")
        emit("-" * 60)
        for e in backups:
            st = e.stat()
            ts = _dt.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
            stem = e.name[:-len(".tar.gz")]
            emit(f"{stem:<30} {_fmt_size(st.st_size):>10}  {ts}")
        return 0

    if args.action == "restore":
        restore_saves(_resolve_backup(args.name), force=args.force)
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


# ----------------------------------------------------------------------------- #
# subcommand: package-usb
# ----------------------------------------------------------------------------- #

_LAUNCH_SH = """#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -f .env ] && [ -f .env.example ]; then cp .env.example .env; fi
# Strip CRs so a .env edited on Windows still parses; default like wm.py does.
n="$(awk -F= '{sub(/\\r$/,"")} /^IMAGE_NAME=/{v=$2} END{print v}' .env | tr -d "\\"'")"
t="$(awk -F= '{sub(/\\r$/,"")} /^IMAGE_TAG=/{v=$2}  END{print v}' .env | tr -d "\\"'")"
IMAGE="${n:-water-moccasin/dolphin}:${t:-latest}"
if ! docker image inspect "${IMAGE}" >/dev/null 2>&1; then
  echo "Loading ${IMAGE} from image.tar ..."
  docker load -i image.tar
fi
exec python3 wm.py start "$@"
"""

_LAUNCH_CMD = """@echo off
cd /d "%~dp0"
if not exist .env if exist .env.example copy .env.example .env >nul
set "IMAGE_NAME="
set "IMAGE_TAG="
for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
  if /I "%%A"=="IMAGE_NAME" set "IMAGE_NAME=%%B"
  if /I "%%A"=="IMAGE_TAG"  set "IMAGE_TAG=%%B"
)
rem strip surrounding quotes like wm.py does, then default like wm.py does
if defined IMAGE_NAME set IMAGE_NAME=%IMAGE_NAME:"=%
if defined IMAGE_NAME set IMAGE_NAME=%IMAGE_NAME:'=%
if defined IMAGE_TAG set IMAGE_TAG=%IMAGE_TAG:"=%
if defined IMAGE_TAG set IMAGE_TAG=%IMAGE_TAG:'=%
if not defined IMAGE_NAME set IMAGE_NAME=water-moccasin/dolphin
if not defined IMAGE_TAG set IMAGE_TAG=latest
docker image inspect "%IMAGE_NAME%:%IMAGE_TAG%" >nul 2>&1 || docker load -i image.tar
python wm.py start %*
"""

# Directories in the bundle that hold the recipient's data once they've run
# it. Never clobber them on a re-package; merge instead.
_BUNDLE_USER_DIRS = ("saves", "game", "logs", "controller_configs", "sunshine")


def _copy_tree_contents(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for p in src.iterdir():
        if p.name == ".gitkeep":
            continue
        target = dst / p.name
        if p.is_dir():
            shutil.copytree(p, target, dirs_exist_ok=True)
        else:
            shutil.copy2(p, target)


def _exec_filter(ti: tarfile.TarInfo) -> tarfile.TarInfo:
    """Force the exec bit on run.sh even when staged on Windows."""
    if ti.name.endswith("/run.sh"):
        ti.mode = 0o755
    return ti


def cmd_package_usb(args: argparse.Namespace) -> int:
    s = load_settings()
    if not (args.dest or args.tarball):
        die("Specify --dest <usb-mount> or --tarball <file>.")
    if not image_exists(s.image):
        die(f"Image {s.image} not found. Build it first.")

    # Stage under the project, not /tmp: image.tar plus ISOs can be many GB
    # and /tmp is often a RAM-backed tmpfs.
    stage_root = PROJECT_ROOT / "dist"
    stage_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="usb-stage-", dir=stage_root) as tmp:
        stage = Path(tmp)
        log(f"Staging bundle in {stage}")
        for d in _BUNDLE_USER_DIRS:
            (stage / d).mkdir()
            (stage / d / ".gitkeep").write_text("", encoding="utf-8")

        log("Exporting image -> image.tar")
        run([docker_bin(), "save", s.image, "-o", str(stage / "image.tar")])

        # Everything the recipient needs to run *and* rebuild.
        for name in ("wm.py", "Dockerfile", "docker-compose.yml", "docker-compose.stream.yml",
                     ".env.example", "README.md"):
            src = PROJECT_ROOT / name
            if src.exists():
                shutil.copy2(src, stage / name)
        (stage / "container").mkdir()
        shutil.copy2(PROJECT_ROOT / "container" / "entrypoint.py",
                     stage / "container" / "entrypoint.py")

        for d in ("controller_configs", "game"):
            src = PROJECT_ROOT / d
            if src.is_dir():
                _copy_tree_contents(src, stage / d)
        if args.include_saves and SAVES_DIR.is_dir():
            _copy_tree_contents(SAVES_DIR, stage / "saves")

        (stage / "run.sh").write_text(_LAUNCH_SH, encoding="utf-8", newline="\n")
        (stage / "run.sh").chmod(0o755)
        (stage / "run.cmd").write_text(_LAUNCH_CMD, encoding="utf-8", newline="\r\n")

        manifest = [
            "water_moccasin USB bundle",
            f"built:     {_dt.datetime.now().isoformat(timespec='seconds')}",
            f"image:     {s.image}",
            f"host_uid:  {s.uid}",
            f"saves:     {'bundled' if args.include_saves else 'empty'}",
            "",
            "run:       bash run.sh   (Linux; FAT/exFAT sticks drop the exec bit, so not ./run.sh)",
            "           run.cmd      (Windows; needs Docker Desktop + WSL2)",
            "",
            "files:",
        ]
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                manifest.append(f"  {p.relative_to(stage).as_posix()}")
        (stage / "MANIFEST.txt").write_text("\n".join(manifest) + "\n", encoding="utf-8", newline="\n")

        if args.dest:
            dest = Path(args.dest)
            if not dest.is_dir():
                die(f"Destination {dest} does not exist or isn't a directory.")
            out = dest / "water_moccasin"
            out.mkdir(exist_ok=True)
            log(f"Copying bundle to {out}")
            for p in stage.iterdir():
                target = out / p.name
                if p.is_dir():
                    if p.name in _BUNDLE_USER_DIRS and target.exists() and any(target.iterdir()):
                        log(f"Keeping existing {target.name}/ at destination (merging).")
                    shutil.copytree(p, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(p, target)
            log(f"Done. To run: cd {out} && bash run.sh   (Windows: run.cmd)")

        if args.tarball:
            tb = Path(args.tarball)
            log(f"Writing tarball {tb}")
            tmp_tb = tb.with_name(tb.name + ".tmp")
            try:
                with tarfile.open(tmp_tb, "w:gz") as tar:
                    tar.add(stage, arcname="water_moccasin", filter=_exec_filter)
                os.replace(tmp_tb, tb)
            except BaseException:
                tmp_tb.unlink(missing_ok=True)
                raise
            log("Done. Extract anywhere and run: bash run.sh")

    return 0


# ----------------------------------------------------------------------------- #
# subcommand: netplay
# ----------------------------------------------------------------------------- #

def _print_host_addresses() -> None:
    if not (is_linux() and shutil.which("ip")):
        return
    log("Host addresses (share one with your peer):")
    out = subprocess.run(["ip", "-brief", "addr"], capture_output=True, text=True, check=False)
    for line in (out.stdout or "").splitlines():
        emit(f"  {line}")
    if is_wsl():
        warn("WSL2: these are the distro's NAT addresses. Peers on your LAN need the Windows "
             "host's IPv4 (`ipconfig` in PowerShell) plus WSL mirrored networking, or a "
             "`netsh interface portproxy` rule.")


def cmd_netplay(args: argparse.Namespace) -> int:
    s = load_settings()
    if args.action == "host":
        port = args.port if args.port is not None else s.netplay_port
        _print_host_addresses()
        game = args.game or s.netplay_game
        if not game:
            warn("No --game given; entrypoint will auto-pick the first game in ./game.")
        log(f"Netplay port: {port}")
        os.environ["DOLPHIN_MODE"] = "netplay-host"
        os.environ["NETPLAY_PORT"] = str(port)
        os.environ["NETPLAY_GAME"] = game or ""
        return cmd_start(argparse.Namespace(dolphin_args=[]))

    if args.action == "join":
        log(f"Launching Dolphin UI. In the netplay dialog, connect to: {args.peer}")
        os.environ["DOLPHIN_MODE"] = "play"
        return cmd_start(argparse.Namespace(dolphin_args=[]))

    if args.action == "status":
        _container_status(s)
        if is_linux() and shutil.which("ss"):
            log(f"Listening on netplay port {s.netplay_port}:")
            _print_listeners(s.netplay_port)
        return 0

    die(f"Unknown netplay action: {args.action}")


def _container_status(s: Settings, tail: int = 20) -> bool:
    if container_running(s.container_name):
        log(f"Container {s.container_name} is running.")
        out = subprocess.run(
            [docker_bin(), "logs", "--tail", str(tail), s.container_name],
            capture_output=True, text=True, check=False,
        )
        for line in (out.stdout or "").splitlines() + (out.stderr or "").splitlines():
            emit(f"  {line}")
        return True
    log(f"Container {s.container_name} is not running.")
    return False


def _print_listeners(*ports: int) -> None:
    out = subprocess.run(["ss", "-lntup"], capture_output=True, text=True, check=False)
    wanted = {f":{p} " for p in ports}
    found = False
    for line in (out.stdout or "").splitlines():
        if any(w in line + " " for w in wanted):
            emit(f"  {line}")
            found = True
    if not found:
        emit("  (nothing bound)")
    if is_wsl() and docker_is_desktop():
        warn("Docker Desktop: sockets live in its VM, so `ss` here cannot see them.")


# ----------------------------------------------------------------------------- #
# subcommand: controllers
# ----------------------------------------------------------------------------- #

_PROC_INPUT = Path("/proc/bus/input/devices")


@dataclass
class InputDevice:
    name: str
    event: str
    joystick: str | None


def parse_proc_input(text: str) -> list[InputDevice]:
    devices: list[InputDevice] = []
    for block in text.split("\n\n"):
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


def list_input_devices() -> list[InputDevice]:
    if not _PROC_INPUT.is_file():
        return []
    return parse_proc_input(_PROC_INPUT.read_text(encoding="utf-8", errors="replace"))


def _print_device_table(devs: Iterable[InputDevice]) -> None:
    emit(f"{'#':<3} {'EVENT':<22} {'JS':<18} NAME")
    emit(f"{'-'*3} {'-'*22} {'-'*18} {'-'*36}")
    for i, d in enumerate(devs, 1):
        emit(f"{i:<3} {d.event:<22} {(d.joystick or '-'): <18} {d.name}")


def _prompt_pick(devs: list[InputDevice]) -> InputDevice:
    _print_device_table(devs)
    print(f"\nPick device number (1-{len(devs)}): ", end="", file=sys.stderr, flush=True)
    try:
        idx = int(input().strip())
    except (ValueError, EOFError):
        die("Not a number.")
    if not 1 <= idx <= len(devs):
        die("Out of range.")
    return devs[idx - 1]


def _list_profiles(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.ini") if p.is_file())


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
            chosen = _prompt_pick(devs)
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
            dev = _prompt_pick(devs).event
        if not Path(dev).exists():
            die(f"Device does not exist: {dev}")
        if shutil.which("evtest"):
            log(f"Streaming evtest for {dev} (Ctrl+C to stop)")
            exec_or_run(["evtest", dev])
        log(f"evtest not on host; running inside container against {dev}")
        exec_or_run([
            docker_bin(), "run", "--rm", "-it",
            "--device", f"{dev}:{dev}",
            "--group-add", str(s.input_gid),
            "--entrypoint", "/usr/bin/evtest",
            s.image, dev,
        ])

    if args.action == "profiles":
        d = PROJECT_ROOT / "controller_configs"
        profiles = _list_profiles(d)
        if not profiles:
            log(f"No .ini profiles in {d}")
            return 0
        for p in profiles:
            emit(str(p.relative_to(d).as_posix()))
        return 0

    die(f"Unknown controllers action: {args.action}")


# ----------------------------------------------------------------------------- #
# subcommand: stream (Sunshine host inside the container, Moonlight clients)
# ----------------------------------------------------------------------------- #

_UDEV_RULE = (
    '# water_moccasin: let the container user create virtual input devices\n'
    'KERNEL=="uinput", SUBSYSTEM=="misc", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"\n'
    'KERNEL=="uhid", GROUP="input", MODE="0660"\n'
)
_MODULES_CONF = "uinput\nuhid\n"


def _ensure_sunshine_password() -> None:
    """Sunshine's web UI and pairing API need credentials. Generate one once
    and persist it to .env so pairing works out of the box."""
    pw = secrets.token_urlsafe(12)
    update_env_key("SUNSHINE_PASS", pw)
    log(f"Generated Sunshine web UI password and saved it to {ENV_FILE.name} (SUNSHINE_PASS).")


def _print_stream_hints(s: Settings) -> None:
    emit("")
    emit("Moonlight: add this machine by IP if it does not show up, then pair.")
    emit(f"  Web UI:   https://<this-host>:{s.web_ui_port}   (user: {s.sunshine_user})")
    emit(f"  Pair:     python wm.py stream pair <PIN shown in Moonlight>")
    emit(f"  Ports:    TCP {', '.join(map(str, s.sunshine_tcp_ports()))}"
         f"  UDP {', '.join(map(str, s.sunshine_udp_ports()))}")
    emit("")


class SunshineUnreachable(RuntimeError):
    pass


def _sunshine_api(s: Settings, method: str, path: str, body: dict | None = None,
                  timeout: float = 30.0) -> tuple[int, object]:
    """Call Sunshine's HTTPS API on localhost with basic auth. Returns
    (status, parsed-json-or-text). Self-signed cert, so verification is off.
    Raises SunshineUnreachable when nothing answers."""
    url = f"https://127.0.0.1:{s.web_ui_port}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    token = base64.b64encode(f"{s.sunshine_user}:{s.sunshine_pass}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    logger.debug("%s %s %s", method, url, body)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    except (urllib.error.URLError, OSError) as exc:
        raise SunshineUnreachable(f"Cannot reach Sunshine at {url}: {exc}") from exc
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw


def _sunshine_pair(s: Settings, pin: str, name: str) -> bool:
    if not re.fullmatch(r"\d{4}", pin):
        die("PIN must be exactly 4 digits (as shown by Moonlight).")
    if not s.sunshine_pass:
        die("SUNSHINE_PASS is empty in .env; set it (or start once so it's generated).")
    try:
        status, pending = _sunshine_api(s, "GET", "/api/pin")
    except SunshineUnreachable as exc:
        die(f"{exc}. Is `wm.py stream start` running?")
    if status == 401:
        die("Sunshine rejected the credentials. Check SUNSHINE_USER / SUNSHINE_PASS in .env.")
    body: dict[str, str] = {"pin": pin, "name": name}
    if status == 200 and isinstance(pending, dict):
        pairings = pending.get("pairings") or []
        if not pairings:
            die("No pending pairing request. Start pairing from Moonlight first, then run this.")
        if len(pairings) > 1:
            warn("Several clients are waiting to pair; using the first one:")
            for p in pairings:
                emit(f"  {p.get('id')}  {p.get('name', '')}  {p.get('address', '')}")
        body["pairing_id"] = str(pairings[0].get("id", ""))
    # Older Sunshine builds have no GET /api/pin and accept {pin, name} alone.
    log("Submitting PIN to Sunshine (this waits for Moonlight to finish the handshake)...")
    try:
        status, result = _sunshine_api(s, "POST", "/api/pin", body, timeout=60)
    except SunshineUnreachable as exc:
        die(str(exc))
    ok = status == 200 and isinstance(result, dict) and bool(result.get("status"))
    if ok:
        log(f"Paired '{name}'. Pick an app in Moonlight to start streaming.")
    else:
        warn(f"Pairing failed (HTTP {status}): {result}")
    return ok


def _stream_setup_host(s: Settings, apply: bool) -> int:
    if not is_linux():
        die("stream setup-host only applies to a Linux (or WSL2) host.")
    steps = [
        ["modprobe", "uinput"],
        ["modprobe", "uhid"],
        ["sh", "-c", f"printf %s {shlex.quote(_MODULES_CONF)} > /etc/modules-load.d/water-moccasin.conf"],
        ["sh", "-c", f"printf %s {shlex.quote(_UDEV_RULE)} > /etc/udev/rules.d/60-water-moccasin.rules"],
        ["udevadm", "control", "--reload-rules"],
        ["udevadm", "trigger", "--subsystem-match=misc", "--subsystem-match=input"],
    ]
    emit("Host setup for Moonlight input (virtual gamepads/keyboard via uinput):")
    for st in steps:
        emit("  sudo " + " ".join(shlex.quote(c) for c in st))
    emit(f"  # then make sure your user is in group 'input' (gid {s.input_gid})")
    if is_wsl():
        warn("WSL2 kernels ship without uinput; these steps will not work there. Video and audio "
             "still stream; use STREAM_INPUT=xtest for keyboard/mouse.")
    if not apply:
        emit("\nRe-run with --apply to execute these via sudo.")
        return 0
    if not shutil.which("sudo"):
        die("sudo not found; run the commands above as root yourself.")
    for st in steps:
        log("sudo " + " ".join(shlex.quote(c) for c in st))
        rc = subprocess.run(["sudo", *st], check=False).returncode
        if rc != 0:
            warn(f"exit {rc}: {' '.join(st)} (continuing)")
    ok = node_accessible_by("/dev/uinput", s.uid, [s.input_gid, s.gid])
    log("/dev/uinput is now accessible." if ok else
        "/dev/uinput is still not accessible to your uid/input gid; check the rule output above.")
    return 0 if ok else 1


def cmd_stream(args: argparse.Namespace) -> int:
    s = load_settings()
    if args.action == "start":
        os.environ["DOLPHIN_MODE"] = "stream"
        if args.display:
            os.environ["STREAM_DISPLAY"] = args.display
        if args.encoder:
            os.environ["STREAM_ENCODER"] = args.encoder
        if args.resolution:
            os.environ["STREAM_RESOLUTION"] = args.resolution
        if args.autostart:
            os.environ["STREAM_AUTOSTART"] = "1"
        return cmd_start(argparse.Namespace(dolphin_args=[]))

    if args.action == "status":
        running = _container_status(s)
        if is_linux() and shutil.which("ss"):
            log(f"Sunshine ports (base {s.sunshine_port}):")
            _print_listeners(*s.sunshine_tcp_ports(), *s.sunshine_udp_ports())
        if running and s.sunshine_pass:
            try:
                status, clients = _sunshine_api(s, "GET", "/api/clients/list", timeout=5)
            except SunshineUnreachable as exc:
                warn(f"{exc} (Sunshine may still be starting).")
                status, clients = 0, None
            if status == 200 and isinstance(clients, dict):
                names = clients.get("named_certs") or clients.get("clients") or []
                log(f"Paired Moonlight clients: {len(names)}")
                for c in names:
                    emit(f"  {c.get('name', '?') if isinstance(c, dict) else c}")
            elif status == 401:
                warn("Sunshine credentials in .env do not match the running instance.")
        _print_stream_hints(s)
        return 0

    if args.action == "pair":
        return 0 if _sunshine_pair(s, args.pin, args.name) else 1

    if args.action == "setup-host":
        return _stream_setup_host(s, args.apply)

    die(f"Unknown stream action: {args.action}")


# ----------------------------------------------------------------------------- #
# CLI
# ----------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wm",
        description="water_moccasin - Dolphin-in-Docker launcher with Sunshine streaming.",
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
    b.add_argument("--no-pull", action="store_true",
                   help="Don't refresh archlinux:base first (offline rebuilds).")
    b.add_argument("--no-sunshine", action="store_true",
                   help="Leave Sunshine/Xvfb/PulseAudio out of the image (no stream mode).")
    b.add_argument("--build-arg", action="append", metavar="KEY=VALUE")
    b.set_defaults(func=cmd_build)

    s = sub.add_parser("start", help="Run the container (image must exist).")
    s.add_argument("dolphin_args", nargs=argparse.REMAINDER,
                   help="Passed through to dolphin-emu after `--`.")
    s.set_defaults(func=cmd_start)

    d = sub.add_parser("deploy", help="Build-if-needed, preflight host, then start.")
    d.add_argument("--rebuild", action="store_true")
    d.add_argument("dolphin_args", nargs=argparse.REMAINDER,
                   help="Passed through to dolphin-emu after `--`.")
    d.set_defaults(func=cmd_deploy)

    c = sub.add_parser("cleanup", help="Stop container, snapshot container logs, optionally wipe/archive saves.")
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
    nh.add_argument("--game", default="", help="Container path, e.g. /game/melee.iso (default: NETPLAY_GAME).")
    nh.add_argument("--port", type=int, default=None, help="Override NETPLAY_PORT from .env.")
    nj = nsub.add_parser("join")
    nj.add_argument("--peer", required=True)
    nsub.add_parser("status")
    n.set_defaults(func=cmd_netplay)

    st = sub.add_parser("stream", help="Stream Dolphin to Moonlight clients via Sunshine.")
    stsub = st.add_subparsers(dest="action", required=True)
    sts = stsub.add_parser("start", help="Run the container in stream mode.")
    sts.add_argument("--display", choices=VALID_STREAM_DISPLAYS,
                     help="xvfb: headless virtual screen (default). host: capture the host's X display.")
    sts.add_argument("--encoder", help="software | vaapi | nvenc | quicksync | vulkan (default: STREAM_ENCODER).")
    sts.add_argument("--resolution", help="Virtual screen size, e.g. 1280x720 (default: STREAM_RESOLUTION).")
    sts.add_argument("--autostart", action="store_true",
                     help="Launch Dolphin at boot so the 'Desktop' app shows it immediately.")
    stsub.add_parser("status", help="Container, ports, paired clients.")
    stp = stsub.add_parser("pair", help="Submit the PIN Moonlight shows.")
    stp.add_argument("pin")
    stp.add_argument("--name", default="moonlight", help="Label for the client in Sunshine.")
    sth = stsub.add_parser("setup-host", help="Show (or --apply) the udev/modprobe setup for virtual input.")
    sth.add_argument("--apply", action="store_true", help="Run the steps with sudo.")
    st.set_defaults(func=cmd_stream)

    sv = sub.add_parser("saves", help="Inspect, back up, restore, and prune Dolphin saves.")
    svsub = sv.add_subparsers(dest="action", required=True)
    svsub.add_parser("list", help="Show subdirectories of ./saves with sizes.")
    svb = svsub.add_parser("backup", help="Archive ./saves to dist/saves/<name>.tar.gz.")
    svb.add_argument("--name", help="Backup name (default: timestamp).")
    svb.add_argument("--force", action="store_true", help="Overwrite existing backup.")
    svb.add_argument("--include-cache", action="store_true", help="Also archive saves/Cache (shader caches).")
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
