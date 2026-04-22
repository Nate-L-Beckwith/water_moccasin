# syntax=docker/dockerfile:1.6
FROM archlinux:base

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUNBUFFERED=1

# Rolling-release base means no apt pinning games. Trim pacman's cache aggressively.
RUN pacman -Syu --noconfirm --needed \
      dolphin-emu \
      mesa \
      vulkan-icd-loader \
      libpulse \
      python \
      tini \
      evtest \
      iproute2 \
      xorg-xauth \
      ca-certificates \
 && pacman -Scc --noconfirm \
 && rm -rf /var/cache/pacman/pkg/* /var/lib/pacman/sync/*

ARG UID=1000
ARG GID=1000
ARG INPUT_GID=104
RUN groupadd -g "${GID}" player \
 && useradd  -m -u "${UID}" -g "${GID}" -s /bin/bash player \
 && (getent group input >/dev/null || groupadd -g "${INPUT_GID}" input) \
 && (getent group audio >/dev/null || groupadd -g 63 audio) \
 && (getent group video >/dev/null || groupadd -g 27 video) \
 && usermod  -aG input,audio,video player

RUN mkdir -p /game /saves /logs /controller_configs \
             /home/player/.config/dolphin-emu/Config \
 && chown -R player:player /game /saves /logs /controller_configs /home/player

COPY container/entrypoint.py /usr/local/bin/entrypoint.py
RUN chmod 0755 /usr/local/bin/entrypoint.py

USER player
WORKDIR /home/player

ENV GAME_DIR=/game \
    SAVES_DIR=/saves \
    LOG_DIR=/logs \
    CONTROLLER_CONFIG_DIR=/controller_configs \
    DOLPHIN_MODE=play

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/bin/python", "/usr/local/bin/entrypoint.py"]
CMD []
