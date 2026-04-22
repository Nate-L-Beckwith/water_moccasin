# water_moccasin

Containerize and deploy the [Dolphin](https://dolphin-emu.org/) GameCube/Wii
emulator with a game of your choice. Everything host-side is driven by a
single Python CLI ([wm.py](wm.py)) — no bash scripts, no external deps.

## What it does

- **Containerize** — Arch Linux-based image with `dolphin-emu`, Mesa/Vulkan
  runtime, PulseAudio client, and a Python entrypoint.
- **Wrap** — `wm.py` drives `docker build` / `docker run` with the right
  mounts, devices, and user mapping.
- **Deploy** — one command to build + preflight the host + launch.
- **Cleanup** — stops the container, rotates logs, preserves saves by default
  (optionally archives or wipes them).
- **USB deployment** — bundles the exported image plus launchers onto a USB
  stick or into a `.tar.gz`.
- **Netplay** — host/join/status subcommands; actual hosting is a Dolphin UI
  action (Tools → NetPlay → Host).
- **Controller management** — list detected input devices, pick which to
  forward into the container, stream live telemetry via `evtest`.

## Layout

```text
water_moccasin/
├── wm.py                    ← host-side CLI (stdlib only)
├── Dockerfile
├── docker-compose.yml       ← optional alternative launcher
├── .env.example             ← copy to .env and edit
├── container/
│   └── entrypoint.py        ← runs inside the container
├── game/                    ← drop your ISO/GCM/RVZ/WBFS here
├── saves/                   ← persistent GC/Wii saves + cache (live)
├── dist/saves/              ← backup archives (tar.gz, created on demand)
├── logs/                    ← wm.log + container.log (rotated)
└── controller_configs/      ← Dolphin controller .ini profiles
```

## Requirements

- **Docker** (Engine or Desktop). The CLI shells out to `docker` on `PATH`.
- **Python 3.10+** on the host. Stdlib only — nothing to install.
- **Linux host** (or **WSL2** on Windows). The container expects an X11
  socket at `/tmp/.X11-unix`, a PulseAudio socket under `/run/user/<uid>/`,
  and `/dev/dri` for GPU. Running on native Windows without WSL2 will not
  surface the Dolphin UI.

## Quick start

```sh
cp .env.example .env            # edit UID/GID and INPUT_GID to match your host
python wm.py build              # build the image
cp your-game.iso game/          # drop in one legally-obtained game
python wm.py deploy             # preflight + launch
```

`deploy` does a host preflight (DISPLAY, X11 socket, PulseAudio socket,
`/dev/dri`) and warns on each missing piece before running the container.

## Commands

Every subcommand supports `--help`:

| Command | What it does |
| --- | --- |
| `python wm.py build [--no-cache] [--tag name:tag]` | Build the image with `UID`/`GID`/`INPUT_GID` baked in. |
| `python wm.py start [-- dolphin-args...]` | Run the container. Image must already exist. |
| `python wm.py deploy [--rebuild] [-- dolphin-args...]` | Build-if-needed, preflight, then start. |
| `python wm.py cleanup [--archive] [--wipe-saves] [--rmi]` | Stop container, rotate logs, optionally archive/wipe saves or remove image. |
| `python wm.py package-usb --dest /path/to/usb` | Export image + launchers to a USB mount. `--tarball file.tar.gz` writes a portable archive instead. `--include-saves` bundles save data. |
| `python wm.py netplay host [--game /game/foo.iso] [--port 2626]` | Launch UI in netplay-host mode (the actual hosting is done from Dolphin's UI). |
| `python wm.py netplay join --peer HOST:PORT` | Launch UI so you can connect to a peer. |
| `python wm.py netplay status` | Show container + listening-port status. |
| `python wm.py controllers list` | Print detected `/dev/input` devices. |
| `python wm.py controllers pick [--device ...]` | Persist a device (or pair) into `CONTROLLER_DEVICES=` in `.env`. |
| `python wm.py controllers telemetry [/dev/input/eventN]` | Live button/axis events via `evtest` (host or containerized fallback). |
| `python wm.py controllers profiles` | List `.ini` profiles in `controller_configs/`. |
| `python wm.py saves list` | Show subdirectories of `./saves` with sizes. |
| `python wm.py saves backup [--name NAME] [--force]` | Archive `./saves` to `dist/saves/<name>.tar.gz` (default name: timestamp). |
| `python wm.py saves list-backups` | Show existing backups (name, size, mtime). |
| `python wm.py saves restore NAME [--force]` | Restore a backup into `./saves`. Refuses to merge into a non-empty dir without `--force`. |
| `python wm.py saves remove NAME` | Delete a named backup. |
| `python wm.py saves prune --keep N` | Keep the newest `N` backups, remove older ones. |

Global flags (before the subcommand): `-v/--verbose` for DEBUG output
(includes subprocess commands), `-q/--quiet` for warnings and errors only.
The file log at `logs/wm.log` always captures DEBUG regardless, with
rotation at 1 MB × 5 backups.

## Configuration (`.env`)

```ini
UID=1000                   # match your host user so saves keep the right owner
GID=1000
INPUT_GID=104              # gid of /dev/input/event* on your host
IMAGE_NAME=water-moccasin/dolphin
IMAGE_TAG=latest
CONTAINER_NAME=water-moccasin
MEM_LIMIT=2g
DOLPHIN_MODE=play          # play | netplay-host | shell
CONTROLLER_DEVICES=        # e.g. /dev/input/event22,/dev/input/js0
NETPLAY_PORT=2626
NETPLAY_GAME=              # absolute container path, e.g. /game/melee.iso
```

Find your `INPUT_GID` with `getent group input | cut -d: -f3`.

## How saves persist

Inside the container, Dolphin writes to `~/.config/dolphin-emu/{StateSaves,
GC, Wii, Cache, ...}`. The entrypoint symlinks each of those subdirectories
to `/saves/<name>` (which is bind-mounted from `./saves/` on the host). Wipe
the container as often as you want; saves live on the host.

### Backup / restore

```sh
python wm.py saves backup --name before-risky-glitch
python wm.py saves list-backups
python wm.py saves restore before-risky-glitch
python wm.py saves prune --keep 5
```

Backups are plain `tar.gz` under `dist/saves/`. `saves restore` refuses to
overwrite a non-empty `./saves` unless you pass `--force` (in which case it
merges — wipe first with `cleanup --wipe-saves` for a clean restore).

`cleanup --wipe-saves` is the only command that deletes live save data, and
it preserves `.gitkeep`. `cleanup --archive` is a shortcut for `saves
backup` with a timestamp name.

## Logging

- **`logs/wm.log`** — host CLI activity. DEBUG-level (every subprocess
  call, every decision). Rotates at 1 MB × 5 backups.
- **`logs/container.log`** — entrypoint messages from inside the container.
  Rotates at 1 MB × 3 backups.
- **Console** — INFO by default; `-v` for DEBUG, `-q` for warnings only.

Both log files are plain text with ISO-8601 timestamps.

## Controllers

Dolphin speaks to controllers through `/dev/input/event*` and, for joystick
APIs, `/dev/input/js*`. Forward your device(s) explicitly:

```sh
python wm.py controllers list
python wm.py controllers pick          # interactive, writes to .env
python wm.py start                     # next start includes the chosen device
```

Drop exported Dolphin controller profiles (`.ini`) into
`controller_configs/` — the entrypoint copies them into the container's
`~/.config/dolphin-emu/Config/` on every run.

## USB deployment

```sh
python wm.py build
python wm.py package-usb --dest /media/$USER/MYUSB
# or:
python wm.py package-usb --tarball water-moccasin.tar.gz --include-saves
```

The bundle contains `image.tar` (exported Docker image), `wm.py`,
`docker-compose.yml`, `.env.example`, `container/entrypoint.py`, your game
and controller configs, and two launchers:

- `run.sh` (Linux/macOS) — `docker load` on first run, then `wm.py start`.
- `run.cmd` (Windows) — same flow via `cmd.exe`.

The target machine still needs Docker and Python 3.10+.

## Netplay

Dolphin's netplay hosting action lives in the UI. `wm.py netplay host` just
launches the UI pre-configured with breadcrumbs (port + intended game) and
prints the host's reachable IP addresses. Once the UI is up:

1. Tools → NetPlay → Host…
2. Pick the game, set the port to `NETPLAY_PORT` (default 2626), click Host.
3. Share an IP from the log with your peer.

Peers run `python wm.py netplay join --peer YOUR_IP:2626` and use Tools →
NetPlay → Connect… in their Dolphin UI.

## Docker compose (optional)

If you prefer compose:

```sh
docker compose build
docker compose up
```

Compose doesn't know about `CONTROLLER_DEVICES` — for controllers, either
use `wm.py start` or write a `docker-compose.override.yml` with extra
`devices:` entries. `wm.py` is the recommended path.

## Troubleshooting

- **`DISPLAY is unset`** — export DISPLAY (e.g. `:0`) before running; under
  WSL2, WSLg sets this automatically.
- **Black screen / no GPU** — verify `/dev/dri/renderD128` exists on the
  host. Inside WSL2, this requires a recent Windows 11 build.
- **Silent audio** — check `/run/user/<uid>/pulse/native` exists; on WSL2
  you may need `pulseaudio` (WSLg provides it).
- **Container runs as wrong user** — rebuild after changing `UID`/`GID`;
  those are baked into the image at build time.
- **Controller not detected** — confirm the right device with
  `python wm.py controllers list`, then `pick`, then `start` again.

## Legal

This repo does not distribute ROMs. Bring your own, legally acquired. The
Docker image contains only open-source components (Arch Linux, Dolphin,
Python, runtime libraries).

## References

- Dolphin forum thread that prompted this packaging approach:
  [How to make Dolphin work within Docker](https://forums.dolphin-emu.org/Thread-how-to-make-dolphin-works-within-docker)
