# syntax=docker/dockerfile:1.6
FROM archlinux:base

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUNBUFFERED=1

# 1 = also install Sunshine (game-stream host), Xvfb and PulseAudio so
# `wm.py stream` works. 0 = Dolphin only (smaller image).
ARG WITH_SUNSHINE=1

# Sunshine is not in the official Arch repos; LizardByte publishes a pacman
# repo (unsigned, hence SigLevel = Optional - their documented setup).
# Refresh archlinux-keyring first: a stale archlinux:base otherwise fails
# signature checks on everything else.
RUN pacman -Sy --noconfirm --needed archlinux-keyring \
 && if [ "$WITH_SUNSHINE" = "1" ]; then \
      printf '\n[lizardbyte]\nSigLevel = Optional\nServer = https://github.com/LizardByte/pacman-repo/releases/latest/download\n' \
        >> /etc/pacman.conf; \
      extra="lizardbyte/sunshine xorg-server-xvfb pulseaudio"; \
    else extra=""; fi \
 && pacman -Syu --noconfirm --needed \
      dolphin-emu \
      mesa \
      vulkan-icd-loader \
      vulkan-swrast \
      vulkan-radeon \
      vulkan-intel \
      libva-mesa-driver \
      libpulse \
      ttf-dejavu \
      python \
      tini \
      evtest \
      iproute2 \
      xorg-xauth \
      xorg-xrandr \
      ca-certificates \
      $extra \
 && pacman -Scc --noconfirm \
 && rm -rf /var/cache/pacman/pkg/* /var/lib/pacman/sync/*

# Match the host user so the /saves bind mount keeps sane ownership.
# INPUT_GID: the host's gid for /dev/input/* and /dev/uinput. archlinux:base
# already ships an `input` group, so re-number it to the host's gid when that
# gid is free; wm.py also passes --group-add at run time as a belt-and-braces.
ARG UID=1000
ARG GID=1000
ARG INPUT_GID=104
RUN set -eu; \
    getent group "${GID}" >/dev/null || groupadd -g "${GID}" player; \
    useradd -m -o -u "${UID}" -g "${GID}" -s /bin/bash player; \
    getent group "${INPUT_GID}" >/dev/null || groupmod -g "${INPUT_GID}" input; \
    usermod -aG "$(getent group "${INPUT_GID}" | cut -d: -f1),audio,video,render" player

RUN mkdir -p /game /saves /logs /controller_configs /sunshine /tmp/.X11-unix \
 && chown -R "${UID}:${GID}" /game /saves /logs /controller_configs /sunshine /home/player \
 && chmod 1777 /tmp/.X11-unix

COPY container/entrypoint.py /usr/local/bin/entrypoint.py
RUN chmod 0755 /usr/local/bin/entrypoint.py

USER player
WORKDIR /home/player

# DOLPHIN_EMU_USERPATH makes Dolphin keep *everything* (Config, GC, Wii,
# StateSaves, Cache, ...) under /saves instead of the XDG split across
# ~/.config, ~/.local/share and ~/.cache. Trailing slash on purpose.
ENV GAME_DIR=/game \
    SAVES_DIR=/saves \
    LOG_DIR=/logs \
    CONTROLLER_CONFIG_DIR=/controller_configs \
    SUNSHINE_DIR=/sunshine \
    DOLPHIN_EMU_USERPATH=/saves/ \
    DOLPHIN_MODE=play

EXPOSE 2626 47984-47990/tcp 48010/tcp 47998-48000/udp

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/bin/python", "/usr/local/bin/entrypoint.py"]
CMD []
