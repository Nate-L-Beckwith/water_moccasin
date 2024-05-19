# Water Moccasin Project

This project, named `water_moccasin`, aims to containerize, wrap, and deploy a Dolphin emulator along with a single game. It also includes scripts to manage and automate various tasks, such as packaging for USB deployment, setting up netplay, and configuring controllers.

## Features
1. **Containerize**: Package a Dolphin emulator and a single game into a Docker container.
2. **Wrap**: Create necessary scripts and configurations to manage the container.
3. **Deploy**: Automate the deployment process of these containers.
4. **Cleanup**: Ensure cleanup on exit while retaining saves locally and for deployment source.
5. **USB Deployment**: Package and run/deploy from or on a USB drive.
6. **Netplay**: Deploy and host netplay.
7. **Controller Management**: List, pick controllers, configurations, and modify and view live controller telemetry.

## Directory Structure

\`\`\`
water_moccasin/
│
├── Dockerfile
├── docker-compose.yml
├── logs/
│   └── README.md
├── controller_configs/
│   └── README.md
├── scripts/
│   ├── build.sh
│   ├── deploy.sh
│   ├── start.sh
│   ├── cleanup.sh
│   ├── package_usb.sh
│   ├── netplay_setup.sh
│   └── controller_config.sh
├── game/
│   └── game.iso
└── saves/
    └── README.md
\`\`\`

## How to Use

### Build the Docker Image

\`\`\`sh
./scripts/build.sh
\`\`\`

### Deploy the Container

\`\`\`sh
./scripts/deploy.sh
\`\`\`

### Start the Container

\`\`\`sh
./scripts/start.sh
\`\`\`

### Clean Up and Retain Saves

\`\`\`sh
./scripts/cleanup.sh
\`\`\`

### Package for USB Deployment

\`\`\`sh
./scripts/package_usb.sh
\`\`\`

### Set Up Netplay

\`\`\`sh
./scripts/netplay_setup.sh
\`\`\`

### Configure Controllers and View Telemetry

\`\`\`sh
./scripts/controller_config.sh
\`\`\`

## Detailed Instructions

### Build the Docker Image

\`\`\`sh
docker build --network="host" -t dolphin-dato .
\`\`\`

### Run the Docker Container

\`\`\`sh
docker run -it --net host --memory 2gb -e DISPLAY=unix$DISPLAY \\
  -v /media/storage0/ROM/dolphinconfigs:/root/.config/dolphin-emu \\
  -v /media/storage0/ROM/:/roms \\
  -v /dev/shm:/dev/shm \\
  -v /etc/machine-id:/etc/machine-id \\
  -v /run/user/$(id -u)/pulse:/run/user/$(id -u)/pulse \\
  -v /var/lib/dbus:/var/lib/dbus \\
  -v ~/.pulse:/root/.pulse \\
  --device /dev/input/event22 \\
  --device /dev/dri:/dev/dri \\
  --name dolphin-run dolphin-dato
\`\`\`

### Explanation of the Docker Run Command

- \`docker run -it\`: Interact with Dolphin directly (i.e., to play the game).
- \`--net host\`: Use the host network.
- \`--memory 2gb\`: Limit the memory to 2GB.
- \`-e DISPLAY=unix$DISPLAY\`: Use the host display for graphical applications.
- \`-v /media/storage0/ROM/dolphinconfigs:/root/.config/dolphin-emu\`: Export the Dolphin configuration folder from the host.
- \`-v /media/storage0/ROM/:/roms\`: Export the ROMs folder from the host.
- \`-v /dev/shm:/dev/shm\`: Share the host's shared memory.
- \`-v /etc/machine-id:/etc/machine-id\`: Share the host's machine ID.
- \`-v /run/user/$(id -u)/pulse:/run/user/$(id -u)/pulse\`: Use the PulseAudio server from the user session.
- \`-v /var/lib/dbus:/var/lib/dbus\`: Share the host's D-Bus.
- \`-v ~/.pulse:/root/.pulse\`: Use the PulseAudio configuration from the host.
- \`--device /dev/input/event22\`: Export the PS4 DualShock 4 controller.
- \`--device /dev/dri:/dev/dri\`: Share the Direct Rendering Infrastructure for hardware acceleration.
- \`--name dolphin-run\`: Name the container.
- \`dolphin-dato\`: Name of the image built.

## Author's Instructions

These parameters and configurations allow running Dolphin with GPU acceleration and sound through PulseAudio, using the host's display and input devices.

## Suggestions for Future Improvements

1. **Automate Configuration Adjustments**: Create scripts to automate the adjustment of emulator settings based on the host system's capabilities.
2. **Enhanced Logging**: Implement more detailed logging to monitor performance and troubleshoot issues.
3. **Web Interface**: Develop a web-based interface to manage and monitor the emulator and container status.
4. **Support for Additional Games**: Expand the project to support multiple games and provide a selection interface.
5. **Advanced Controller Support**: Improve controller configuration scripts to support a wider range of controllers and input devices.
6. **Performance Optimization**: Investigate and implement optimizations to reduce resource usage and improve performance.
7. **Security Enhancements**: Implement security best practices for running Docker containers, especially when exposed to network interfaces.

## References

- [How to make Dolphin work within Docker](https://forums.dolphin-emu.org/Thread-how-to-make-dolphin-works-within-docker)
