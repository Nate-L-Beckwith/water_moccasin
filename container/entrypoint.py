#!/usr/bin/env python3
"""Container entrypoint: prepare Dolphin's user dir, import controller
profiles, then launch Dolphin (or Sunshine, which launches Dolphin).

Runs on every container start. Idempotent.

Modes (DOLPHIN_MODE):
    play         : auto-pick game from $GAME_DIR or pass through argv
    netplay-host : launch UI with netplay breadcrumbs (hosting is a UI action)
    stream       : Sunshine game-stream host; Moonlight clients pick an app
    shell        : drop to /bin/bash

Dolphin's whole user directory (Config, GC, Wii, StateSaves, ...) lives at
$DOLPHIN_EMU_USERPATH, which the Dockerfile points at /saves. Nothing here
symlinks anything; Dolphin writes straight into the bind mount.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

GAME_DIR = Path(os.environ.get("GAME_DIR", "/game"))
LOG_DIR = Path(os.environ.get("LOG_DIR", "/logs"))
CONTROLLER_CONFIG_DIR = Path(os.environ.get("CONTROLLER_CONFIG_DIR", "/controller_configs"))
SUNSHINE_DIR = Path(os.environ.get("SUNSHINE_DIR", "/sunshine"))
USER_DIR = Path(os.environ.get("DOLPHIN_EMU_USERPATH", "/saves"))
CONFIG_DIR = USER_DIR / "Config"
MODE = os.environ.get("DOLPHIN_MODE", "play")

GAME_EXTS = {".iso", ".gcm", ".wbfs", ".rvz", ".ciso", ".wia", ".dol", ".elf"}
# Subdirs Dolphin expects under its user dir. It creates them itself; we
# pre-create them so a fresh ./saves is self-explanatory on the host.
USER_SUBDIRS = ("Config", "GC", "Wii", "StateSaves", "ScreenShots", "Dump", "Load", "Cache")
# Dolphin's controller-profile picker only looks in Config/Profiles/<kind>/.
PROFILE_KINDS = ("GCPad", "Wiimote", "GCKey", "Hotkeys", "GBA")
# Whole-device config files that live directly in Config/.
TOP_LEVEL_CONFIGS = {
    "Dolphin.ini", "GCPadNew.ini", "WiimoteNew.ini", "GCKeyNew.ini", "GFX.ini",
    "Hotkeys.ini", "Logger.ini", "DSUClient.ini", "FreeLook.ini", "RetroAchievements.ini",
}

_logger = logging.getLogger("entrypoint")


def _configure_logging() -> None:
    _logger.setLevel(logging.DEBUG)
    for h in list(_logger.handlers):
        _logger.removeHandler(h)
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.INFO)
    sh.setFormatter(logging.Formatter("[entrypoint] %(message)s"))
    _logger.addHandler(sh)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            LOG_DIR / "container.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8",
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        ))
        _logger.addHandler(fh)
    except OSError:
        # /logs may be read-only or missing; stderr is enough.
        pass
    _logger.propagate = False


def log(msg: str) -> None:
    _logger.info(msg)


def warn(msg: str) -> None:
    _logger.warning(msg)


def fail(msg: str, code: int = 1) -> int:
    _logger.error(msg)
    return code


# ----------------------------------------------------------------------------- #
# Dolphin user directory
# ----------------------------------------------------------------------------- #

def prepare_user_dir() -> None:
    """Make sure /saves is ours and has Dolphin's layout. Raises PermissionError
    with a host-side hint if docker created the bind source as root."""
    try:
        USER_DIR.mkdir(parents=True, exist_ok=True)
        for sub in USER_SUBDIRS:
            (USER_DIR / sub).mkdir(exist_ok=True)
        probe = USER_DIR / ".write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except PermissionError as exc:
        raise PermissionError(
            f"{USER_DIR} is not writable by uid {os.getuid()}. On the host run "
            f"`sudo chown -R $(id -u):$(id -g) ./saves` (docker creates missing bind "
            f"sources as root), then start again. ({exc})"
        ) from exc


def profile_destination(src: Path, root: Path) -> Path:
    """Where a controller_configs/*.ini file belongs inside Dolphin's Config/.

    controller_configs/<Kind>/x.ini -> Config/Profiles/<Kind>/x.ini
    controller_configs/GCPadNew.ini -> Config/GCPadNew.ini (whole-device files)
    controller_configs/x.ini        -> Config/Profiles/GCPad/x.ini
    """
    rel = src.relative_to(root)
    if len(rel.parts) >= 2 and rel.parts[0] in PROFILE_KINDS:
        return CONFIG_DIR / "Profiles" / rel.parts[0] / src.name
    if src.name in TOP_LEVEL_CONFIGS:
        return CONFIG_DIR / src.name
    return CONFIG_DIR / "Profiles" / "GCPad" / src.name


def import_controller_profiles() -> int:
    if not CONTROLLER_CONFIG_DIR.is_dir():
        return 0
    count = 0
    for p in sorted(CONTROLLER_CONFIG_DIR.rglob("*.ini")):
        if not p.is_file():
            continue
        target = profile_destination(p, CONTROLLER_CONFIG_DIR)
        if target.exists() and target.stat().st_mtime >= p.stat().st_mtime:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
        log(f"Imported controller profile: {p.relative_to(CONTROLLER_CONFIG_DIR)} -> {target.relative_to(USER_DIR)}")
        count += 1
    return count


def list_games() -> list[Path]:
    if not GAME_DIR.is_dir():
        return []
    return [
        p for p in sorted(GAME_DIR.rglob("*"))
        if p.is_file() and p.suffix.lower() in GAME_EXTS
    ]


def pick_game() -> Path | None:
    games = list_games()
    return games[0] if games else None


def dolphin_argv(game: Path | None = None, *extra: str, fullscreen: bool = False) -> list[str]:
    argv = ["dolphin-emu"]
    if game is not None:
        argv += ["-b", "-e", str(game)]
    if fullscreen:
        argv += ["-C", "Dolphin.Display.Fullscreen=True"]
    argv += list(extra)
    return argv


def exec_dolphin(*args: str) -> None:
    os.execvp("dolphin-emu", ["dolphin-emu", *args])


def ensure_runtime_dir() -> Path:
    """Qt and PulseAudio want XDG_RUNTIME_DIR; give them a private one."""
    raw = os.environ.get("XDG_RUNTIME_DIR", "")
    rt = Path(raw) if raw else Path(f"/tmp/runtime-{os.getuid()}")
    try:
        rt.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not os.access(rt, os.W_OK):
            raise PermissionError(rt)
    except OSError:
        rt = Path(f"/tmp/runtime-{os.getuid()}")
        rt.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.environ["XDG_RUNTIME_DIR"] = str(rt)
    return rt


# ----------------------------------------------------------------------------- #
# stream mode: Xvfb + PulseAudio + Sunshine
# ----------------------------------------------------------------------------- #

def _wait_for(path: Path, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.1)
    raise RuntimeError(f"{what} did not come up within {timeout:.0f}s ({path} missing)")


def start_xvfb(display: str, resolution: str) -> subprocess.Popen:
    if not shutil.which("Xvfb"):
        raise RuntimeError("Xvfb is not installed; rebuild without --no-sunshine.")
    Path("/tmp/.X11-unix").mkdir(mode=0o1777, exist_ok=True)
    num = display.lstrip(":").split(".")[0]
    argv = [
        "Xvfb", display,
        "-screen", "0", f"{resolution}x24",
        "-dpi", "96", "-ac", "-nolisten", "tcp", "-noreset",
        "+extension", "GLX", "+extension", "RANDR", "+render",
    ]
    log("Starting " + " ".join(argv))
    proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=sys.stderr)
    _wait_for(Path(f"/tmp/.X11-unix/X{num}"), 10, "Xvfb")
    return proc


_PULSE_CONFIG = """\
# Generated by water_moccasin's entrypoint for headless streaming.
load-module module-native-protocol-unix auth-anonymous=1 socket={socket}
load-module module-null-sink sink_name=dolphin sink_properties=device.description=Dolphin
load-module module-always-sink
set-default-sink dolphin
"""


def start_pulseaudio(runtime_dir: Path) -> subprocess.Popen:
    if not shutil.which("pulseaudio"):
        raise RuntimeError("pulseaudio is not installed; rebuild without --no-sunshine.")
    sock_dir = runtime_dir / "pulse"
    sock_dir.mkdir(mode=0o700, exist_ok=True)
    socket = sock_dir / "native"
    conf = runtime_dir / "default.pa"
    conf.write_text(_PULSE_CONFIG.format(socket=socket), encoding="utf-8")
    argv = [
        "pulseaudio", "--daemonize=no", "-n", "-F", str(conf),
        "--exit-idle-time=-1", "--disallow-exit",
        "--log-target=stderr", "--log-level=error",
    ]
    log("Starting PulseAudio (null sink) at " + str(socket))
    proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=sys.stderr)
    _wait_for(socket, 10, "PulseAudio")
    os.environ["PULSE_SERVER"] = f"unix:{socket}"
    return proc


def sunshine_cmd(argv: list[str]) -> str:
    """Sunshine splits `cmd` shell-style with double quotes; quote what needs it."""
    out: list[str] = []
    for a in argv:
        if a and not any(ch.isspace() or ch in '"\\' for ch in a):
            out.append(a)
        else:
            out.append('"' + a.replace("\\", "\\\\").replace('"', '\\"') + '"')
    return " ".join(out)


def build_apps(games: list[Path], games_root: Path) -> dict:
    """apps.json for Sunshine: one entry per game, a bare Dolphin UI, and the
    implicit Desktop (no cmd = stream the screen as-is)."""
    apps: list[dict] = [{
        "name": "Dolphin",
        "cmd": sunshine_cmd(dolphin_argv()),
        "auto-detach": False,
        "exit-timeout": 10,
    }]
    seen = {"Dolphin", "Desktop"}
    for g in games:
        name = g.stem
        if name in seen:
            name = g.relative_to(games_root).with_suffix("").as_posix()
        base, n = name, 2
        while name in seen:
            name = f"{base} ({n})"
            n += 1
        seen.add(name)
        apps.append({
            "name": name,
            "cmd": sunshine_cmd(dolphin_argv(g, fullscreen=True)),
            "working-dir": str(g.parent),
            "auto-detach": False,
            "exit-timeout": 10,
        })
    apps.append({"name": "Desktop"})
    return {"env": {}, "apps": apps}


def write_apps_json(path: Path, games: list[Path], games_root: Path) -> None:
    override = path.with_name("apps.override.json")
    if override.is_file():
        shutil.copyfile(override, path)
        log(f"Using {override.name} for Sunshine apps.")
        return
    path.write_text(json.dumps(build_apps(games, games_root), indent=2) + "\n", encoding="utf-8")
    log(f"Wrote {path} ({len(games)} game(s) + Dolphin + Desktop)")


def _parse_conf(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def build_sunshine_conf(*, name: str, port: int, encoder: str, state_dir: Path,
                        apps_json: Path, log_path: Path, headless: bool,
                        custom: dict[str, str] | None = None) -> dict[str, str]:
    conf = {
        "sunshine_name": name,
        "port": str(port),
        "capture": "x11",
        "encoder": encoder,
        "origin_web_ui_allowed": "lan",
        "upnp": "disabled",
        "system_tray": "disabled",
        "min_log_level": "info",
        "file_apps": str(apps_json),
        "file_state": str(state_dir / "sunshine_state.json"),
        "credentials_file": str(state_dir / "sunshine_state.json"),
        "pkey": str(state_dir / "cakey.pem"),
        "cert": str(state_dir / "cacert.pem"),
        "log_path": str(log_path),
    }
    if encoder == "vaapi" and Path("/dev/dri/renderD128").exists():
        conf["adapter_name"] = "/dev/dri/renderD128"
    if encoder == "software" and headless:
        # x11 capture + software encode is CPU-bound; let ffmpeg use the cores.
        conf.setdefault("min_threads", str(max(2, (os.cpu_count() or 2) // 2)))
    if custom:
        conf.update(custom)
    return conf


def write_sunshine_conf(path: Path, conf: dict[str, str]) -> None:
    lines = [
        "# Generated by water_moccasin on every start from .env (STREAM_*, SUNSHINE_*).",
        "# To override or add keys, put them in sunshine.custom.conf next to this file.",
    ]
    lines += [f"{k} = {v}" for k, v in conf.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_stream_mode() -> int:
    if not shutil.which("sunshine"):
        return fail("sunshine is not installed in this image; rebuild without --no-sunshine.")
    display_mode = os.environ.get("STREAM_DISPLAY", "xvfb")
    resolution = os.environ.get("STREAM_RESOLUTION", "1920x1080")
    encoder = os.environ.get("STREAM_ENCODER", "software")
    name = os.environ.get("SUNSHINE_NAME", "water-moccasin")
    user = os.environ.get("SUNSHINE_USER", "player")
    password = os.environ.get("SUNSHINE_PASS", "")
    port = int(os.environ.get("SUNSHINE_PORT", "47989"))
    autostart = os.environ.get("STREAM_AUTOSTART", "0") == "1"

    runtime_dir = ensure_runtime_dir()
    children: list[subprocess.Popen] = []
    headless = display_mode == "xvfb"
    if headless:
        os.environ["DISPLAY"] = os.environ.get("STREAM_XDISPLAY", ":99")
        os.environ.pop("WAYLAND_DISPLAY", None)  # force Sunshine's x11 path
        os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
        children.append(start_xvfb(os.environ["DISPLAY"], resolution))
        children.append(start_pulseaudio(runtime_dir))
    else:
        if not os.environ.get("DISPLAY"):
            return fail("STREAM_DISPLAY=host but DISPLAY is unset.")
        log(f"Capturing host display {os.environ['DISPLAY']}")

    SUNSHINE_DIR.mkdir(parents=True, exist_ok=True)
    state_dir = SUNSHINE_DIR / "state"
    state_dir.mkdir(exist_ok=True)
    apps_json = SUNSHINE_DIR / "apps.json"
    conf_path = SUNSHINE_DIR / "sunshine.conf"
    custom_path = SUNSHINE_DIR / "sunshine.custom.conf"
    custom = _parse_conf(custom_path.read_text(encoding="utf-8")) if custom_path.is_file() else {}

    games = list_games()
    write_apps_json(apps_json, games, GAME_DIR)
    conf = build_sunshine_conf(
        name=name, port=port, encoder=encoder, state_dir=state_dir, apps_json=apps_json,
        log_path=LOG_DIR / "sunshine.log", headless=headless, custom=custom,
    )
    write_sunshine_conf(conf_path, conf)

    if password:
        rc = subprocess.run(["sunshine", str(conf_path), "--creds", user, password],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode
        if rc != 0:
            warn(f"sunshine --creds exited {rc}; web UI login may not work.")
        else:
            log(f"Sunshine web UI credentials set for user '{user}'.")
    elif not (state_dir / "sunshine_state.json").exists():
        warn("SUNSHINE_PASS is empty and no credentials exist yet: open the web UI once to create them.")

    if autostart:
        game = pick_game()
        argv = dolphin_argv(game, fullscreen=True) if game else dolphin_argv()
        log("Autostart: " + " ".join(argv))
        children.append(subprocess.Popen(argv))

    web_port = port + 1
    log(f"Sunshine '{name}': web UI https://<host>:{web_port}, capture=x11 encoder={encoder} "
        f"display={os.environ.get('DISPLAY')} ({'virtual ' + resolution if headless else 'host'})")
    log("Pair from the host with: python wm.py stream pair <PIN>")
    # Replace this process; tini (PID 1) keeps reaping Xvfb/PulseAudio/Dolphin.
    sys.stderr.flush()
    os.execvp("sunshine", ["sunshine", str(conf_path)])
    return 0  # unreachable


# ----------------------------------------------------------------------------- #
# main
# ----------------------------------------------------------------------------- #

def main(argv: list[str]) -> int:
    _configure_logging()
    log(f"mode={MODE} game_dir={GAME_DIR} user_dir={USER_DIR}")
    try:
        prepare_user_dir()
    except PermissionError as exc:
        return fail(str(exc))
    import_controller_profiles()
    ensure_runtime_dir()

    if MODE == "play":
        if argv:
            log(f"Launching with explicit args: {' '.join(argv)}")
            exec_dolphin(*argv)
        game = pick_game()
        if game is not None:
            log(f"Launching {game}")
            exec_dolphin("-b", "-e", str(game))
        log(f"No game in {GAME_DIR}; starting Dolphin UI.")
        exec_dolphin()

    if MODE == "netplay-host":
        port = os.environ.get("NETPLAY_PORT", "2626")
        game = os.environ.get("NETPLAY_GAME") or (str(pick_game()) if pick_game() else "<auto-pick>")
        log(f"Netplay host mode. Port={port} Game={game}")
        log("In Dolphin: Tools -> NetPlay -> Host... (select the game above)")
        exec_dolphin()

    if MODE == "stream":
        try:
            return run_stream_mode()
        except (RuntimeError, OSError) as exc:
            return fail(f"stream mode failed: {exc}")

    if MODE == "shell":
        os.execvp("/bin/bash", ["/bin/bash"])

    return fail(f"Unknown DOLPHIN_MODE={MODE}", 2)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
