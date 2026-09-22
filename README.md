# water_moccasin

Dolphin (GameCube/Wii) in a Docker container, driven by a single Python
script. Bring your own ROM. Play locally on the host's display, or stream it
to any Moonlight client (phone, TV, another PC) with the built-in Sunshine
host. Saves and settings live on the host, so the container is disposable.

## What you actually get

An Arch-based image with `dolphin-emu`, Mesa (OpenGL + Vulkan, hardware and
software), fonts, PulseAudio client bits, and optionally Sunshine + Xvfb +
PulseAudio server for headless streaming. A stdlib-only Python CLI (`wm.py`)
that handles building, running, streaming, pairing, cleanup, controller
forwarding, save backups, and bundling the whole thing onto a USB stick.

Dolphin's entire user directory (Config, GC, Wii, StateSaves, Cache, ...)
is pointed at `/saves`, bind-mounted from `./saves/`. No symlink tricks;
Dolphin writes straight into the mount.

## Layout

```text
water_moccasin/
├── wm.py                       host CLI (Python 3.10+, stdlib only)
├── Dockerfile                  Arch base (+ Sunshine unless --no-sunshine)
├── docker-compose.yml          alternative launcher (play mode)
├── docker-compose.stream.yml   compose overlay for stream mode
├── .env.example                copy to .env and edit
├── container/
│   └── entrypoint.py           runs inside the container
├── tests/                      unit tests (python -m unittest discover -s tests)
├── game/                       drop your ISO / GCM / RVZ / WBFS here
├── saves/                      Dolphin's user dir (persistent, host-owned)
├── sunshine/                   Sunshine config, apps.json, pairing state
├── controller_configs/         Dolphin .ini profiles (see Controllers)
├── logs/                       wm.log, container.log, sunshine.log
└── dist/saves/                 backup archives (created on first backup)
```

## Requirements

Docker (Engine or Desktop) and Python 3.10+ on the host. The CLI doesn't
import anything outside the stdlib.

The container assumes a Linux host: X11 socket, PulseAudio, `/dev/dri`,
`/dev/input`. On Windows that means WSL2 with WSLg. Native Windows without
WSL will not show a Dolphin window (but `saves`, `package-usb`, and the
tests all work anywhere).

## Quick start

```sh
cp .env.example .env             # optional: everything is auto-detected
python wm.py build               # first build is slow - pacman does its thing
cp your-game.iso game/
python wm.py deploy              # preflight + run
```

`deploy` warns about a missing DISPLAY, PulseAudio socket, `/dev/dri`, etc.
and then runs anyway with what exists. Nothing is mounted blindly: a host
without `/dev/dri` gets software rendering instead of a docker error.

## Subcommands

All of these take `--help`.

| Command | What it does |
| --- | --- |
| `wm.py build [--no-cache] [--no-pull] [--no-sunshine] [--tag name:tag] [--build-arg K=V]` | Build the image with your UID/GID baked in. `--no-sunshine` leaves streaming out. |
| `wm.py start [-- dolphin-args...]` | Run the container in `DOLPHIN_MODE`. Image has to exist already. |
| `wm.py deploy [--rebuild] [-- dolphin-args...]` | Build if needed, preflight, start. The "just run it" button. |
| `wm.py stream {start,status,pair,setup-host}` | See Streaming below. |
| `wm.py cleanup [--archive] [--wipe-saves] [--rmi]` | Stop + remove the container, snapshot container logs, optionally archive or nuke saves, optionally drop the image. |
| `wm.py package-usb --dest /path` | Export image + launchers to a USB mount. `--tarball file.tar.gz` for a portable archive; `--include-saves` bundles save data. |
| `wm.py netplay {host,join,status}` | See Netplay below. |
| `wm.py controllers {list,pick,telemetry,profiles}` | See Controllers below. |
| `wm.py saves {list,backup,list-backups,restore,remove,prune}` | See Saves below. |

Global flags (put them *before* the subcommand): `-v` for debug output,
`-q` for warnings only. The file log always runs at DEBUG regardless.

## Configuration (.env)

Everything has a sane default; `.env` is optional. wm.py and docker compose
read the same file, and quoted values are fine.

```ini
#UID=1000                    # auto: your uid (or the sudo-invoking user's)
#GID=1000
#INPUT_GID=104               # auto: gid of the host `input` group
IMAGE_NAME=water-moccasin/dolphin
IMAGE_TAG=latest
CONTAINER_NAME=water-moccasin
MEM_LIMIT=2g
NETWORK_MODE=host            # host | bridge (bridge publishes only the needed ports)
EXTRA_DOCKER_ARGS=           # e.g. --gpus all
DOLPHIN_MODE=play            # play | netplay-host | stream | shell
CONTROLLER_DEVICES=          # e.g. /dev/input/event22,/dev/input/js0
#DISPLAY=:0                  # auto
#PULSE_SERVER=               # auto (WSLg included); unix:/path to force
NETPLAY_PORT=2626
NETPLAY_GAME=                # absolute container path, e.g. /game/melee.iso
STREAM_DISPLAY=xvfb          # xvfb (headless) | host
STREAM_RESOLUTION=1920x1080
STREAM_ENCODER=software      # software | vaapi | nvenc | quicksync | vulkan
STREAM_INPUT=uinput          # uinput (gamepads) | xtest (mouse/keyboard for the UI)
STREAM_AUTOSTART=0
SUNSHINE_NAME=water-moccasin
SUNSHINE_USER=player
SUNSHINE_PASS=               # generated on first `stream start`
SUNSHINE_PORT=47989
```

UID/GID are baked in at build time; rebuild after changing them. INPUT_GID
and everything else apply at the next `start`.

## Streaming (Sunshine + Moonlight)

Stream mode runs [Sunshine](https://github.com/LizardByte/Sunshine) inside
the container. By default it is fully headless: a virtual X screen (Xvfb) and
a PulseAudio null sink are started in the container, Sunshine captures that
screen, and each game in `./game` shows up as an app in Moonlight. No monitor,
no desktop session, no host X server required.

```sh
python wm.py stream setup-host   # prints the udev/modprobe steps for virtual gamepads
python wm.py stream setup-host --apply   # ...or runs them via sudo
python wm.py stream start        # generates SUNSHINE_PASS into .env on first run
```

Then, on the client:

1. Install [Moonlight](https://moonlight-stream.org). Add the host by IP if
   it does not appear automatically (the container has no mDNS responder).
2. Click the host; Moonlight shows a 4-digit PIN.
3. On the host: `python wm.py stream pair 1234 --name living-room-tv`
   (or open `https://<host>:47990`, log in with `SUNSHINE_USER` /
   `SUNSHINE_PASS`, and enter it there).
4. Pick an app: one entry per game (launches Dolphin fullscreen with that
   game), `Dolphin` (the bare UI), or `Desktop` (stream the screen as-is).

Disconnecting in Moonlight leaves the game running; "Quit app" kills it.
`python wm.py stream status` shows the container, listening ports and paired
clients.

### Input

Sunshine turns Moonlight gamepads, keyboard and mouse into virtual Linux
input devices via `/dev/uinput` (and `/dev/uhid`). The host needs the kernel
modules loaded and `/dev/uinput` writable by the `input` group; that's what
`stream setup-host` sets up. Dolphin reads the virtual gamepads through
evdev exactly like real ones, so map them in Dolphin's controller settings
once and save the profile to `controller_configs/`.

Trade-off under the headless (Xvfb) display: Xvfb does not consume evdev
devices, so Moonlight's *mouse* never reaches the Dolphin Qt UI. Gamepads
and the keyboard still work for gameplay (they're evdev devices Dolphin can
bind). If you need to click around the UI remotely, set `STREAM_INPUT=xtest`:
Sunshine then injects keyboard and mouse straight into the X server instead,
at the cost of no gamepads. The practical workflow: configure controllers
once with a real pad (or a profile file), then stream with `uinput`.

WSL2 kernels ship without `uinput`, so on WSL2 you get video and audio but
no gamepad input.

### Performance

The default `STREAM_ENCODER=software` encodes on the CPU and Dolphin under
Xvfb renders with llvmpipe (also CPU). That's fine for 2D and lighter 3D
titles at 720p; for demanding games either lower `STREAM_RESOLUTION`, use
`STREAM_ENCODER=vaapi` (AMD/Intel, needs `/dev/dri`), or use
`STREAM_DISPLAY=host` to capture a real, GPU-accelerated X11 desktop on a
Linux box (Sunshine then captures that display; Dolphin gets `/dev/dri`).
`STREAM_DISPLAY=host` does not work well under WSLg (XWayland).

### Files

- `sunshine/sunshine.conf` is regenerated on every start from `.env`. Put
  extra keys in `sunshine/sunshine.custom.conf` (same `key = value` format);
  they override the generated ones.
- `sunshine/apps.json` is regenerated from `./game` on every start. To take
  over, write `sunshine/apps.override.json` and it is used verbatim.
- `sunshine/state/` holds the web UI credentials, the self-signed cert and
  the list of paired clients. Back it up if you want to avoid re-pairing.
- `logs/sunshine.log` is Sunshine's own log.

Compose users: `docker compose -f docker-compose.yml -f docker-compose.stream.yml up`.

### Ports

Base port `SUNSHINE_PORT` (47989). Sunshine derives the rest: TCP 47984
(HTTPS), 47989 (HTTP), 47990 (web UI), 48010 (RTSP); UDP 47998, 47999,
48000. With `NETWORK_MODE=host` they're simply open on the host. With
`bridge` wm.py publishes exactly those.

## Saves

`DOLPHIN_EMU_USERPATH=/saves` inside the container, so `./saves/` on the
host *is* Dolphin's user directory: `Config/` (Dolphin.ini, GFX.ini,
controller bindings), `GC/`, `Wii/`, `StateSaves/`, `ScreenShots/`, `Cache/`.
Settings you change in the UI survive container recreation.

```sh
python wm.py saves backup --name before-risky-glitch
python wm.py saves list-backups
python wm.py saves restore before-risky-glitch
python wm.py saves prune --keep 5
```

Backups are plain `.tar.gz` files under `dist/saves/`, written atomically.
`Cache/` (shader caches, regenerates itself) is skipped unless you pass
`--include-cache`. `restore` refuses a non-empty `./saves` unless you pass
`--force` (then it merges), and only ever extracts regular files and
directories under `saves/`. For a clean restore, run `cleanup --wipe-saves`
first.

`cleanup --archive` is the same thing as `saves backup` with an
auto-timestamp name.

## Controllers

Dolphin reads controllers via `/dev/input/event*` (and `/dev/input/js*`).
Docker needs each one forwarded explicitly for play mode:

```sh
python wm.py controllers list     # shows what the kernel sees
python wm.py controllers pick     # interactive; writes CONTROLLER_DEVICES into .env
python wm.py start                # next start includes it
```

`controllers telemetry` streams live `evtest` output so you can see which
event code maps to which button before you commit to a profile.

Profiles: files in `controller_configs/` are copied into Dolphin's config on
every start, routed by name:

- `controller_configs/<name>.ini` -> `Config/Profiles/GCPad/<name>.ini`
  (selectable in the GameCube controller dialog's profile dropdown)
- `controller_configs/Wiimote/<name>.ini` -> `Config/Profiles/Wiimote/`
  (likewise `GCKey/`, `Hotkeys/`, `GBA/`)
- `GCPadNew.ini`, `WiimoteNew.ini`, `Hotkeys.ini`, `Dolphin.ini`, `GFX.ini`
  -> `Config/` directly (whole-device bindings, overwrite what's there)

The copy only happens when the source is newer than what's in `saves/`.

## Netplay

Dolphin's netplay hosting lives in the UI; there's no CLI flag that starts
hosting for you. `wm.py netplay host` launches the UI in netplay-host mode,
prints your reachable IPs, and drops breadcrumbs (port, intended game) into
the log so you don't have to retype anything. From there:

1. Tools -> NetPlay -> Host...
2. Pick the game and port (`NETPLAY_PORT`, default 2626), click Host.
3. Send a peer one of the IPs from the log.

The peer runs `wm.py netplay join --peer YOUR_IP:2626` and uses Tools ->
NetPlay -> Connect... from their Dolphin UI.

`wm.py netplay status` tells you whether the container is up and whether
anything's bound to the netplay port. Under WSL2 the printed addresses are
the distro's NAT addresses; LAN peers need the Windows host's IP plus WSL
mirrored networking (or a `netsh portproxy` rule), and `ss` cannot see
sockets that live inside Docker Desktop's VM.

## USB packaging

```sh
python wm.py build
python wm.py package-usb --dest /media/$USER/MYUSB
# or, for a portable archive you can copy anywhere:
python wm.py package-usb --tarball water-moccasin.tar.gz --include-saves
```

You end up with `image.tar` (the exported Docker image), `wm.py`, the
`Dockerfile`, both compose files, `.env.example`, the container entrypoint,
your games and controller profiles, and two launchers: `run.sh` for Linux
and `run.cmd` for Windows (Docker Desktop + WSL2). The target machine still
needs Docker and Python. Run it with `bash run.sh`: FAT/exFAT sticks drop
the exec bit, so `./run.sh` won't work there.

Re-running `package-usb --dest` onto the same stick merges into existing
`saves/`, `game/`, `logs/` and `sunshine/` instead of wiping them. Staging
happens under `dist/`, not `/tmp`, because image + ISOs can run to many GB.

## Logging

- `logs/wm.log`: host CLI activity. DEBUG-level, every subprocess call
  included. Rotates at 1 MB x 5 backups.
- `logs/container.log`: entrypoint messages from inside the container.
  Rotates at 1 MB x 3 backups.
- `logs/sunshine.log`: Sunshine's log (stream mode).

`cleanup` snapshots `container.log` and `sunshine.log` to
`<name>.<timestamp>.log` and keeps the newest five snapshots of each.
Console verbosity follows `-v` / `-q`; the file logs ignore those.

## Compose (optional)

```sh
docker compose build
docker compose up
# streaming:
docker compose -f docker-compose.yml -f docker-compose.stream.yml up
```

Compose can't probe the host, so it mounts fixed paths. On WSLg set
`X11_SOCKET_DIR=/mnt/wslg/.X11-unix` and `PULSE_SOCKET=/mnt/wslg/PulseServer`
in `.env`; on a host without `/dev/dri` delete that `devices:` line; for
controllers add a `docker-compose.override.yml` with `devices:` entries.
`wm.py start` does all of that dynamically, which is why it's the
recommended path.

## Troubleshooting

- **Nothing on screen (play mode).** `DISPLAY` unset, or no X11 socket
  directory. Under WSL2 those come from WSLg; wm.py mounts
  `/mnt/wslg/.X11-unix` when it exists.
- **Black screen, no acceleration.** `/dev/dri/renderD128` is missing or
  not readable. wm.py adds the node's owning group to the container; on the
  host, check the node's group and mode (`ls -l /dev/dri`). Under WSL2 the
  container has no WSL GPU driver, so rendering is software regardless.
- **Silent.** No PulseAudio socket found. wm.py checks `$PULSE_SERVER`,
  `/mnt/wslg/PulseServer`, `$XDG_RUNTIME_DIR/pulse/native` and
  `/run/user/<uid>/pulse/native`; set `PULSE_SERVER=unix:/path` in `.env`
  to force one.
- **Container exits with "not writable by uid".** `./saves` was created by
  docker as root (it happens when the directory was missing). `sudo chown -R
  $(id -u):$(id -g) saves` and start again.
- **Build fails with signature / keyring errors.** The cached
  `archlinux:base` is stale. `wm.py build` pulls a fresh one by default;
  if you passed `--no-pull`, drop it.
- **Container runs as the wrong user.** UID/GID are baked in at build
  time. Rebuild after editing `.env`.
- **Controller doesn't show up in Dolphin (play mode).** `controllers list`
  should show it first; if not, it's a host issue. If it shows up there but
  not in Dolphin, `controllers pick` and start again.
- **Moonlight pairs but gamepads do nothing.** `/dev/uinput` missing or not
  writable by the `input` group on the host: `wm.py stream setup-host
  --apply`. Then bind the virtual pad in Dolphin's controller config (it
  appears as an evdev device once a client is connected).
- **Moonlight can't find the host.** Add it by IP. Under Docker Desktop or
  WSL2 the container's ports are only reachable from the LAN with Docker
  Desktop host networking or WSL mirrored networking enabled.
- **`stream pair` says no pending pairing.** Start pairing from Moonlight
  first; the PIN prompt on the client creates the request wm.py completes.
- **Stream stutters.** Software encoding at 1080p is CPU-heavy. Lower
  `STREAM_RESOLUTION`, try `STREAM_ENCODER=vaapi`, or `STREAM_DISPLAY=host`
  on a real Linux desktop.

## Legal

No ROMs in this repo and none in the image. Bring your own, legally
acquired. The image contains only open-source software (Arch Linux,
Dolphin, Sunshine, Mesa, PulseAudio, Python, runtime libraries). Sunshine is
installed from LizardByte's own pacman repository, which is unsigned
(`SigLevel = Optional`, their documented setup); pass `--no-sunshine` to
`build` if you'd rather not.

## Reference

Original inspiration and the shape of the `docker run` invocation came
from a Dolphin forums thread:
[how to make Dolphin work within Docker](https://forums.dolphin-emu.org/Thread-how-to-make-dolphin-works-within-docker).
Sunshine docs: <https://docs.lizardbyte.dev/projects/sunshine/>. Moonlight:
<https://moonlight-stream.org>.
