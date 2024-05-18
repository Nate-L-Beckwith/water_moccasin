# Water Moccasin

Water Moccasin is a project that containerizes the Dolphin emulator and a single game, wraps necessary scripts and configurations, and automates the deployment process of these containers. It also provides additional features like cleanup, USB deployment, netplay hosting, and controller configuration.

## Features

1. Containerize: Package a Dolphin emulator and a single game into a Docker container.
2. Wrap: Create necessary scripts and configurations to manage the container.
3. Deploy: Automate the deployment process of these containers.
4. Clean up on exit, but retain saves locally and for deployment source.
5. Ability to package and run/deploy from or on a USB drive.
6. Deploy and host netplay.
7. List, pick controllers, configurations, and modify and view live controllers telemetry.

## Prerequisites

- Docker and Docker Compose installed on your system.
- A game ISO file placed in the `game` directory.

## Project Structure

water_moccasin/
│
├── Dockerfile
├── docker-compose.yml
├── scripts/
│ ├── build.sh
│ ├── deploy.sh
│ ├── start.sh
│ ├── cleanup.sh
│ ├── package_usb.sh
│ ├── netplay_setup.sh
│ └── controller_config.sh
├── game/
│ └── game.iso
└── saves/
## How to Run

### Step 1: Build the Docker Image

```
./scripts/build.sh
# Step 2: Deploy the Container

./scripts/deploy.sh
Step 3: Start the Container

./scripts/start.sh
Step 4: Clean Up and Retain Saves

./scripts/cleanup.sh
Step 5: Package for USB Deployment

./scripts/package_usb.sh
Step 6: Set Up Netplay

./scripts/netplay_setup.sh
Step 7: Configure Controllers and View Telemetry

./scripts/controller_config.sh
Detailed Instructions
Build the Docker Image

docker build --network="host" -t dolphin-dato .
Run the Docker Container

docker run -it --net host --memory 2gb -e DISPLAY=unix$DISPLAY \
  -v /media/storage0/ROM/dolphinconfigs:/root/.config/dolphin-emu \
  -v /media/storage0/ROM/:/roms \
  -v /dev/shm:/dev/shm \
  -v /etc/machine-id:/etc/machine-id \
  -v /run/user/$(id -u)/pulse:/run/user/$(id -u)/pulse \
  -v /var/lib/dbus:/var/lib/dbus \
  -v ~/.pulse:/root/.pulse \
  --device /dev/input/event22 \
  --device /dev/dri:/dev/dri \
  --name dolphin-run dolphin-dato
Explanation of the Docker Run Command
docker run -it -> Interact with Dolphin directly (i.e., to play the game).
--net host -> Use the host network.
--memory 2gb -> Limit the memory to 2GB.
-e DISPLAY=unix$DISPLAY -> Use the host display for graphical applications.
-v /media/storage0/ROM/dolphinconfigs:/root/.config/dolphin-emu -> Export the Dolphin configuration folder from the host.
-v /media/storage0/ROM/:/roms -> Export the ROMs folder from the host.
-v /dev/shm:/dev/shm -> Share the host's shared memory.
-v /etc/machine-id:/etc/machine-id -> Share the host's machine ID.
-v /run/user/$(id -u)/pulse:/run/user/$(id -u)/pulse -> Use the PulseAudio server from the user session.
-v /var/lib/dbus:/var/lib/dbus -> Share the host's D-Bus.
-v ~/.pulse:/root/.pulse -> Use the PulseAudio configuration from the host.
--device /dev/input/event22 -> Export the PS4 DualShock 4 controller.
--device /dev/dri:/dev/dri -> Share the Direct Rendering Infrastructure for hardware acceleration.
--name dolphin-run -> Name the container.
dolphin-dato -> Name of the image built.
Author's Instructions
These parameters and configurations allow running Dolphin with GPU acceleration and sound through PulseAudio, using the host's display and input devices.

TODO
Fix up this README file.
Add more features as needed.
References
[How to make Dolphin work within Docker](https://forums.dolphin-emu.org/Thread-how-to-make-dolphin-works-within-docker)
python
Copy code

This README provides comprehensive instructions on building and running the `water_moccasin` project, ensuring users can easily set up and use the Docker container for the Dolphin emulator. 

For your convenience, here is the updated zip file including the README:

[Download water_moccasin_project.zip](sandbox:/mnt/data/water_moccasin_project.zip)