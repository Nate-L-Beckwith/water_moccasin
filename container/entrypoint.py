#!/usr/bin/env python3
"""Container entrypoint: persist saves, import controller profiles, launch Dolphin.

Runs on every container start. Idempotent.

Modes (DOLPHIN_MODE):
    play         : auto-pick game from $GAME_DIR or pass through argv
    netplay-host : launch UI with netplay breadcrumbs (hosting is a UI action)
    shell        : drop to /bin/bash
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import shutil
import sys
from pathlib import Path

GAME_DIR = Path(os.environ.get("GAME_DIR", "/game"))
SAVES_DIR = Path(os.environ.get("SAVES_DIR", "/saves"))
LOG_DIR = Path(os.environ.get("LOG_DIR", "/logs"))
CONTROLLER_CONFIG_DIR = Path(os.environ.get("CONTROLLER_CONFIG_DIR", "/controller_configs"))
CONFIG_DIR = Path.home() / ".config" / "dolphin-emu"
MODE = os.environ.get("DOLPHIN_MODE", "play")

GAME_EXTS = {".iso", ".gcm", ".wbfs", ".rvz", ".ciso", ".wia"}
# Dolphin subdirs that must survive a container wipe. Symlinked to $SAVES_DIR.
PERSIST_SUBDIRS = ("StateSaves", "GC", "Wii", "Cache", "ScreenShots", "Dump", "Load")

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


def persist_saves() -> None:
    (CONFIG_DIR / "Config").mkdir(parents=True, exist_ok=True)
    for sub in PERSIST_SUBDIRS:
        target = SAVES_DIR / sub
        target.mkdir(parents=True, exist_ok=True)
        link = CONFIG_DIR / sub
        # Replace whatever is there with a symlink into /saves.
        if link.is_symlink() or link.exists():
            if link.is_symlink() and Path(os.readlink(link)) == target:
                continue
            if link.is_dir() and not link.is_symlink():
                shutil.rmtree(link)
            else:
                link.unlink()
        link.symlink_to(target)


def import_controller_profiles() -> None:
    if not CONTROLLER_CONFIG_DIR.is_dir():
        return
    profiles = sorted(CONTROLLER_CONFIG_DIR.glob("*.ini"))
    if not profiles:
        return
    dest = CONFIG_DIR / "Config"
    dest.mkdir(parents=True, exist_ok=True)
    for p in profiles:
        target = dest / p.name
        if target.exists() and target.stat().st_mtime >= p.stat().st_mtime:
            continue
        shutil.copy2(p, target)
        log(f"Imported controller profile: {p.name}")


def pick_game() -> Path | None:
    if not GAME_DIR.is_dir():
        return None
    candidates = [
        p for p in sorted(GAME_DIR.rglob("*"))
        if p.is_file() and p.suffix.lower() in GAME_EXTS
    ]
    return candidates[0] if candidates else None


def exec_dolphin(*args: str) -> None:
    os.execvp("dolphin-emu", ["dolphin-emu", *args])


def main(argv: list[str]) -> int:
    _configure_logging()
    log(f"mode={MODE} game_dir={GAME_DIR} saves_dir={SAVES_DIR}")
    persist_saves()
    import_controller_profiles()

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

    if MODE == "shell":
        os.execvp("/bin/bash", ["/bin/bash"])

    log(f"Unknown DOLPHIN_MODE={MODE}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
