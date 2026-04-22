# water_moccasin

Dolphin (GameCube/Wii) in a Docker container, driven by a single Python
script. Bring your own ROM. Everything else is wrapped up so you can blow
the container away without losing your saves.

## What you actually get

An Arch-based image with `dolphin-emu`, Mesa/Vulkan, and the PulseAudio
client bits. A stdlib-only Python CLI (`wm.py`) that handles building,
running, cleanup, controller forwarding, save backups, and bundling the
whole thing onto a USB stick. Saves live on the host via symlinks, so the
container is disposable. Netplay works too, with the caveat below.

## Layout

```text
water_moccasin/
├── wm.py                   host CLI (Python 3.10+, stdlib only)
├── Dockerfile              Arch base
├── docker-compose.yml      alternative launcher if you prefer compose
├── .env.example            copy to .env and edit
├── container/
│   └── entrypoint.py       runs inside the container
├── game/                   drop your ISO / GCM / RVZ / WBFS here
├── saves/                  live save data (persistent, host-owned)
├── dist/saves/             backup archives (tar.gz)
├── logs/                   wm.log + container.log (rotated)
└── controller_configs/     Dolphin .ini profiles
```

## Requirements

Docker (Engine or Desktop) and Python 3.10+. That's it; the CLI doesn't
import anything that isn't in the stdlib.

The container itself assumes a Linux host for the GUI side of things: X11
socket at `/tmp/.X11-unix`, PulseAudio at `/run/user/<uid>/pulse`, and
`/dev/dri` for GPU. On Windows that means WSL2 (works fine with WSLg).
Running on native Windows without WSL won't surface the Dolphin window.

## Quick start

```sh
cp .env.example .env             # edit UID / GID / INPUT_GID to match your host
python wm.py build               # first build is slow — pacman does its thing
cp your-game.iso game/
python wm.py deploy              # preflight + run
```

`deploy` warns about missing DISPLAY, missing PulseAudio socket, missing
`/dev/dri`, etc. It won't refuse to run — sometimes you genuinely don't
care about audio.

## Subcommands

All of these take `--help`.

| Command | What it does |
| --- | --- |
| `wm.py build [--no-cache] [--tag name:tag]` | Build the image with your UID/GID baked in. |
| `wm.py start [-- dolphin-args...]` | Run the container. Image has to exist already. |
| `wm.py deploy [--rebuild]` | Build if needed, preflight, start. The "just run it" button. |
| `wm.py cleanup [--archive] [--wipe-saves] [--rmi]` | Stop + remove the container, rotate logs, optionally archive or nuke saves, optionally drop the image. |
| `wm.py package-usb --dest /path` | Export image + launchers to a USB mount. Use `--tarball file.tar.gz` for a portable archive instead; `--include-saves` bundles save data. |
| `wm.py netplay {host,join,status}` | See Netplay below. |
| `wm.py controllers {list,pick,telemetry,profiles}` | See Controllers below. |
| `wm.py saves {list,backup,list-backups,restore,remove,prune}` | See Saves below. |

Global flags (put them *before* the subcommand): `-v` for debug output,
`-q` for warnings only. The file log always runs at DEBUG regardless.

## Configuration (.env)

```ini
UID=1000                     # match your host user so saves get the right owner
GID=1000
INPUT_GID=104                # gid of /dev/input/event* on your host
IMAGE_NAME=water-moccasin/dolphin
IMAGE_TAG=latest
CONTAINER_NAME=water-moccasin
MEM_LIMIT=2g
DOLPHIN_MODE=play            # play | netplay-host | shell
CONTROLLER_DEVICES=          # e.g. /dev/input/event22,/dev/input/js0
NETPLAY_PORT=2626
NETPLAY_GAME=                # absolute container path, e.g. /game/melee.iso
```

Find your input gid with `getent group input | cut -d: -f3`.

## Saves

Inside the container, Dolphin writes to
`~/.config/dolphin-emu/{StateSaves, GC, Wii, Cache, ...}`. The entrypoint
symlinks each of those to the matching subdir under `/saves`, which is
bind-mounted from `./saves/` on the host. Net result: the container is
disposable; your saves are not.

Backup and restore:

```sh
python wm.py saves backup --name before-risky-glitch
python wm.py saves list-backups
python wm.py saves restore before-risky-glitch
python wm.py saves prune --keep 5
```

Backups are plain `.tar.gz` files under `dist/saves/`. `restore` won't
overwrite a non-empty `./saves` unless you pass `--force` (in which case
it merges). For a clean restore, run `cleanup --wipe-saves` first.

`cleanup --archive` is the same thing as `saves backup` with an
auto-timestamp name. `cleanup --wipe-saves` is the only command that
touches live save data, and it still preserves `.gitkeep`.

## Controllers

Dolphin reads controllers via `/dev/input/event*`, and via
`/dev/input/js*` for the joystick API. Docker needs each one forwarded
explicitly. The easy path:

```sh
python wm.py controllers list     # shows what the kernel sees
python wm.py controllers pick     # interactive; writes CONTROLLER_DEVICES into .env
python wm.py start                # next start includes it
```

If you've got exported Dolphin profiles, drop the `.ini` files into
`controller_configs/` and the entrypoint copies them into the container on
every run.

`controllers telemetry` streams live `evtest` output so you can see which
event code maps to which button before you commit to a profile.

## Netplay

Dolphin's netplay hosting lives in the UI; there's no CLI flag that starts
hosting for you. `wm.py netplay host` launches the UI in netplay-host
mode, prints your reachable IPs, and drops breadcrumbs (port, intended
game) into the log so you don't have to retype anything. From there:

1. Tools → NetPlay → Host…
2. Pick the game and port (default 2626), click Host.
3. Send a peer one of the IPs from the log.

The peer runs `wm.py netplay join --peer YOUR_IP:2626` and uses Tools →
NetPlay → Connect… from their Dolphin UI.

`wm.py netplay status` tells you whether the container is up and whether
anything's actually bound to the netplay port.

## USB packaging

```sh
python wm.py build
python wm.py package-usb --dest /media/$USER/MYUSB
# or, for a portable archive you can copy anywhere:
python wm.py package-usb --tarball water-moccasin.tar.gz --include-saves
```

You end up with `image.tar` (the exported Docker image), `wm.py`,
`docker-compose.yml`, `.env.example`, the container entrypoint, your game
and controller profiles, and two launchers: `run.sh` for Linux/macOS and
`run.cmd` for Windows. The target machine still needs Docker and Python.

## Logging

- `logs/wm.log` — host CLI activity. DEBUG-level, every subprocess call
  included. Rotates at 1 MB × 5 backups.
- `logs/container.log` — entrypoint messages from inside the container.
  Rotates at 1 MB × 3 backups.

Both are plain text with ISO-8601 timestamps. Console verbosity follows
`-v` / `-q`; the file log ignores those and always captures everything.

## Compose (optional)

If you'd rather `docker compose up`:

```sh
docker compose build
docker compose up
```

Compose doesn't know about `CONTROLLER_DEVICES` from `.env`, so if you
need controllers you'll want a `docker-compose.override.yml` with the
right `devices:` entries. `wm.py start` does that for you dynamically,
which is why it's the recommended path.

## Troubleshooting

- **Nothing on screen.** `DISPLAY` unset, or `/tmp/.X11-unix` not getting
  mounted. Under WSL2 those come from WSLg — make sure you're on a recent
  Windows 11 build.
- **Black screen, no acceleration.** `/dev/dri/renderD128` is missing. On
  native Linux, confirm your host user's in the `video` group. Under WSL2
  this needs a recent Windows 11 build.
- **Silent.** No PulseAudio socket at `/run/user/<uid>/pulse/native`. On
  Linux, make sure PulseAudio (or pipewire-pulse) is running in your
  session. WSLg provides it automatically.
- **Container runs as the wrong user.** UID/GID are baked in at build
  time. Rebuild after editing `.env`.
- **Controller doesn't show up in Dolphin.** `controllers list` should
  show it first; if not, it's a host issue. If it shows up there but not
  in Dolphin, `controllers pick` and start again — you probably forgot
  `CONTROLLER_DEVICES`.

## Legal

No ROMs in this repo and none in the image. Bring your own, legally
acquired. The image contains only open-source software (Arch Linux,
Dolphin, Python, runtime libraries).

## Reference

Original inspiration and the shape of the `docker run` invocation came
from a Dolphin forums thread:
[how to make Dolphin work within Docker](https://forums.dolphin-emu.org/Thread-how-to-make-dolphin-works-within-docker).
