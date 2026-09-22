"""Unit tests for wm.py (host CLI). Stdlib unittest; no docker needed.

    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import importlib.util
import io
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("wm", ROOT / "wm.py")
wm = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["wm"] = wm  # dataclasses resolve `from __future__` annotations via sys.modules
spec.loader.exec_module(wm)


def _settings(**over) -> "wm.Settings":
    base = dict(uid=1000, gid=1000, input_gid=104)
    base.update(over)
    return wm.Settings(**base)


def _host(**over) -> "wm.HostResources":
    base = dict(x11_dir=None, pulse_socket=None, dri=False, machine_id=False,
                uinput=False, uhid=False, dev_input=False, wsl=False)
    base.update(over)
    return wm.HostResources(**base)


class TempProject(unittest.TestCase):
    """Redirect wm's project paths into a temp dir."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="wmtest-"))
        self.patches = [
            mock.patch.object(wm, "PROJECT_ROOT", self.tmp),
            mock.patch.object(wm, "ENV_FILE", self.tmp / ".env"),
            mock.patch.object(wm, "ENV_EXAMPLE", self.tmp / ".env.example"),
            mock.patch.object(wm, "LOG_DIR", self.tmp / "logs"),
            mock.patch.object(wm, "SAVES_DIR", self.tmp / "saves"),
            mock.patch.object(wm, "BACKUPS_DIR", self.tmp / "dist" / "saves"),
            mock.patch.object(wm, "docker_bin", lambda: "docker"),
        ]
        for p in self.patches:
            p.start()
        wm.configure_logging(-1)

    def tearDown(self) -> None:
        for h in list(wm.logger.handlers):
            h.close()
            wm.logger.removeHandler(h)
        for p in self.patches:
            p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)


class EnvParsing(TempProject):
    def test_parse_handles_bom_crlf_quotes_export(self) -> None:
        env = self.tmp / ".env"
        env.write_bytes(
            b"\xef\xbb\xbfUID=1001\r\n"
            b"# comment\r\n"
            b'IMAGE_TAG="latest"\r\n'
            b"export NETPLAY_GAME='/game/my game.iso'\r\n"
            b"BROKEN LINE\r\n"
            b"EMPTY=\r\n"
        )
        got = wm._parse_env_file(env)
        self.assertEqual(got["UID"], "1001")
        self.assertEqual(got["IMAGE_TAG"], "latest")
        self.assertEqual(got["NETPLAY_GAME"], "/game/my game.iso")
        self.assertEqual(got["EMPTY"], "")
        self.assertNotIn("BROKEN LINE", got)

    def test_update_env_key_replaces_and_appends(self) -> None:
        (self.tmp / ".env.example").write_text("#UID=1000\nIMAGE_TAG=latest\n", encoding="utf-8")
        wm.update_env_key("IMAGE_TAG", "dev")
        wm.update_env_key("SUNSHINE_PASS", "s3cret")
        text = (self.tmp / ".env").read_bytes()
        self.assertNotIn(b"\r\n", text, "must write LF so run.sh's awk parses it")
        lines = text.decode().splitlines()
        self.assertIn("#UID=1000", lines, "commented identity keys stay commented")
        self.assertEqual(lines.count("IMAGE_TAG=dev"), 1)
        self.assertIn("SUNSHINE_PASS=s3cret", lines)

    def test_load_settings_validates_mode_and_ints(self) -> None:
        (self.tmp / ".env").write_text("UID=1000\nGID=1000\nDOLPHIN_MODE=bogus\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            wm.load_settings()
        (self.tmp / ".env").write_text("UID=1000\nGID=1000\nNETPLAY_PORT=abc\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            wm.load_settings()

    def test_load_settings_refuses_root(self) -> None:
        (self.tmp / ".env").write_text("UID=0\nGID=0\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            wm.load_settings()

    def test_load_settings_reads_stream_keys(self) -> None:
        (self.tmp / ".env").write_text(
            "UID=1000\nGID=1000\nSTREAM_ENCODER=vaapi\nSUNSHINE_PORT=48989\n"
            'EXTRA_DOCKER_ARGS=--gpus all -e "A=b c"\nSTREAM_AUTOSTART=yes\n',
            encoding="utf-8")
        with mock.patch.dict(os.environ, {}, clear=False):
            for k in ("UID", "GID", "DOLPHIN_MODE", "STREAM_ENCODER", "SUNSHINE_PORT"):
                os.environ.pop(k, None)
            s = wm.load_settings()
        self.assertEqual(s.stream_encoder, "vaapi")
        self.assertEqual(s.web_ui_port, 48990)
        self.assertEqual(s.extra_docker_args, ["--gpus", "all", "-e", "A=b c"])
        self.assertTrue(s.stream_autostart)
        self.assertEqual(s.sunshine_tcp_ports(), [48984, 48989, 48990, 49010])
        self.assertEqual(s.sunshine_udp_ports(), [48998, 48999, 49000])


class DockerArgv(TempProject):
    def test_play_mode_minimal_host(self) -> None:
        s = _settings()
        argv = wm._docker_run_argv(s, _host(), [], interactive=False)
        self.assertNotIn("-it", argv)
        self.assertNotIn("--device", argv, "no /dev/dri on host -> no --device")
        self.assertNotIn("/tmp/.X11-unix", " ".join(argv))
        self.assertNotIn("PULSE_SERVER", " ".join(argv))
        self.assertIn("--net", argv)
        self.assertEqual(argv[-1], s.image)

    def test_play_mode_full_host(self) -> None:
        s = _settings(display=":1")
        h = _host(x11_dir=Path("/mnt/wslg/.X11-unix"), pulse_socket="/mnt/wslg/PulseServer",
                  dri=True, machine_id=True)
        argv = wm._docker_run_argv(s, h, ["--", "x"], interactive=True)
        joined = " ".join(argv)
        self.assertIn("-it", argv)
        self.assertIn("/mnt/wslg/.X11-unix:/tmp/.X11-unix", joined)
        self.assertIn("PULSE_SERVER=unix:/mnt/wslg/PulseServer", joined)
        self.assertIn("/mnt/wslg/PulseServer:/mnt/wslg/PulseServer", joined)
        self.assertIn("DISPLAY=:1", joined)
        self.assertIn("/dev/dri:/dev/dri", joined)
        self.assertEqual(argv[-2:], ["--", "x"])

    def test_shell_mode_needs_tty(self) -> None:
        with self.assertRaises(SystemExit):
            wm._docker_run_argv(_settings(dolphin_mode="shell"), _host(), [], interactive=False)

    def test_stream_xvfb_mode(self) -> None:
        s = _settings(dolphin_mode="stream", sunshine_pass="pw")
        h = _host(x11_dir=Path("/tmp/.X11-unix"), pulse_socket="/run/user/1000/pulse/native",
                  dri=True, uinput=True, uhid=True, dev_input=True)
        argv = wm._docker_run_argv(s, h, [], interactive=False)
        joined = " ".join(argv)
        self.assertNotIn("/tmp/.X11-unix", joined, "headless: host X11 not mounted")
        self.assertNotIn("PULSE_SERVER", joined, "headless: container runs its own pulse")
        self.assertIn("DOLPHIN_MODE=stream", joined)
        self.assertIn("/dev/uinput:/dev/uinput", joined)
        self.assertIn("/dev/uhid:/dev/uhid", joined)
        self.assertIn("/dev/input:/dev/input", joined)
        self.assertIn("--device-cgroup-rule", argv)
        self.assertIn("c 13:* rmw", argv)
        self.assertIn("SUNSHINE_PASS=pw", joined)
        self.assertIn(":/sunshine", joined)

    def test_stream_xtest_skips_uinput(self) -> None:
        s = _settings(dolphin_mode="stream", stream_input="xtest")
        h = _host(uinput=True, uhid=True, dev_input=True)
        joined = " ".join(wm._docker_run_argv(s, h, [], interactive=False))
        self.assertNotIn("/dev/uinput", joined)
        self.assertNotIn("/dev/input:/dev/input", joined)

    def test_stream_host_display_keeps_x11(self) -> None:
        s = _settings(dolphin_mode="stream", stream_display="host", display=":0")
        h = _host(x11_dir=Path("/tmp/.X11-unix"), pulse_socket="/run/user/1000/pulse/native")
        joined = " ".join(wm._docker_run_argv(s, h, [], interactive=False))
        self.assertIn("/tmp/.X11-unix:/tmp/.X11-unix", joined)
        self.assertIn("PULSE_SERVER=unix:/run/user/1000/pulse/native", joined)

    def test_bridge_network_publishes_ports(self) -> None:
        s = _settings(dolphin_mode="stream", network_mode="bridge")
        argv = wm._docker_run_argv(s, _host(), [], interactive=False)
        self.assertNotIn("--net", argv)
        self.assertIn("2626:2626/udp", argv)
        self.assertIn("47990:47990/tcp", argv)
        self.assertIn("48000:48000/udp", argv)

    def test_extra_docker_args_before_image(self) -> None:
        s = _settings(extra_docker_args=["--gpus", "all"])
        argv = wm._docker_run_argv(s, _host(), [], interactive=False)
        self.assertLess(argv.index("--gpus"), argv.index(s.image))


class Saves(TempProject):
    def _seed(self) -> None:
        saves = self.tmp / "saves"
        (saves / "GC").mkdir(parents=True)
        (saves / "GC" / "MemoryCardA.USA.raw").write_bytes(b"card")
        (saves / "Cache" / "Shaders").mkdir(parents=True)
        (saves / "Cache" / "Shaders" / "big.bin").write_bytes(b"x" * 1024)
        (saves / ".gitkeep").write_text("", encoding="utf-8")

    def test_backup_skips_cache_by_default_and_restores(self) -> None:
        self._seed()
        path = wm.backup_saves(name="one")
        assert path is not None
        with tarfile.open(path) as tar:
            names = tar.getnames()
        self.assertIn("saves/GC/MemoryCardA.USA.raw", names)
        self.assertFalse(any(n.startswith("saves/Cache/") for n in names))
        self.assertFalse(list((self.tmp / "dist" / "saves").glob("*.tmp")))

        wm.wipe_saves()
        self.assertFalse(wm._saves_has_content())
        wm.restore_saves(path)
        self.assertEqual((self.tmp / "saves" / "GC" / "MemoryCardA.USA.raw").read_bytes(), b"card")

    def test_backup_include_cache(self) -> None:
        self._seed()
        path = wm.backup_saves(name="two", include_cache=True)
        with tarfile.open(path) as tar:
            self.assertIn("saves/Cache/Shaders/big.bin", tar.getnames())

    def test_backup_name_validation(self) -> None:
        self._seed()
        for bad in ("../escape", "/abs", "a/b", ".hidden", "name with space"):
            with self.assertRaises(SystemExit, msg=bad):
                wm.backup_saves(name=bad)
        with self.assertRaises(SystemExit):
            wm._resolve_backup("../../etc/passwd")

    def test_restore_rejects_foreign_members(self) -> None:
        (self.tmp / "dist" / "saves").mkdir(parents=True)
        evil = self.tmp / "dist" / "saves" / "evil.tar.gz"
        with tarfile.open(evil, "w:gz") as tar:
            ti = tarfile.TarInfo("wm.py")
            data = b"print('pwned')"
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))
        with self.assertRaises(SystemExit):
            wm.restore_saves(evil)
        self.assertFalse((self.tmp / "wm.py").exists())

        link = self.tmp / "dist" / "saves" / "link.tar.gz"
        with tarfile.open(link, "w:gz") as tar:
            ti = tarfile.TarInfo("saves/GC")
            ti.type = tarfile.SYMTYPE
            ti.linkname = "/etc"
            tar.addfile(ti)
        with self.assertRaises(SystemExit):
            wm.restore_saves(link)

        trav = self.tmp / "dist" / "saves" / "trav.tar.gz"
        with tarfile.open(trav, "w:gz") as tar:
            ti = tarfile.TarInfo("saves/../x")
            ti.size = 0
            tar.addfile(ti, io.BytesIO(b""))
        with self.assertRaises(SystemExit):
            wm.restore_saves(trav)


class LogRotation(TempProject):
    def test_rotation_is_idempotent_and_prunes(self) -> None:
        logs = self.tmp / "logs"
        logs.mkdir(exist_ok=True)
        (logs / "wm.log").write_text("keep me", encoding="utf-8")
        for i in range(8):
            (logs / "container.log").write_text(f"run {i}", encoding="utf-8")
            with mock.patch.object(wm._dt, "datetime") as dt:
                dt.now.return_value.strftime.return_value = f"2026010{min(i, 9)}-00000{i}"
                wm._rotate_logs()
        names = sorted(p.name for p in logs.iterdir())
        self.assertIn("wm.log", names)
        rotated = [n for n in names if wm._ROTATED_RE.match(n)]
        self.assertEqual(len(rotated), wm._ROTATED_KEEP)
        self.assertTrue(all(n.count(".") == 2 for n in rotated), f"double-rotated: {rotated}")
        # A second rotation with nothing new renames nothing.
        before = set(names)
        wm._rotate_logs()
        self.assertEqual(before, set(p.name for p in logs.iterdir()))


class Misc(unittest.TestCase):
    def test_parse_proc_input(self) -> None:
        text = (
            'I: Bus=0003 Vendor=045e Product=028e Version=0110\n'
            'N: Name="Microsoft X-Box 360 pad"\n'
            'H: Handlers=event22 js0\n'
            '\n'
            'N: Name="Power Button"\n'
            'H: Handlers=kbd event0\n'
            '\n'
            'N: Name="No handlers"\n'
            'H: Handlers=\n'
        )
        devs = wm.parse_proc_input(text)
        self.assertEqual([d.event for d in devs], ["/dev/input/event22", "/dev/input/event0"])
        self.assertEqual(devs[0].joystick, "/dev/input/js0")
        self.assertIsNone(devs[1].joystick)
        self.assertEqual(devs[0].name, "Microsoft X-Box 360 pad")

    def test_node_accessible_by(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "node"
            p.write_text("", encoding="utf-8")
            st = p.stat()
            self.assertTrue(wm.node_accessible_by(str(p), st.st_uid, [st.st_gid]))
            self.assertFalse(wm.node_accessible_by(str(p) + ".missing", 0, []))

    def test_fmt_size(self) -> None:
        self.assertEqual(wm._fmt_size(512), "512.0 B")
        self.assertEqual(wm._fmt_size(2048), "2.0 KB")

    def test_launchers_have_defaults_and_cr_stripping(self) -> None:
        self.assertIn("water-moccasin/dolphin", wm._LAUNCH_SH)
        self.assertIn(r"sub(/\r$/", wm._LAUNCH_SH)
        self.assertIn("set IMAGE_TAG=latest", wm._LAUNCH_CMD)

    def test_parser_has_stream_commands(self) -> None:
        p = wm.build_parser()
        ns = p.parse_args(["stream", "pair", "1234", "--name", "tv"])
        self.assertEqual((ns.cmd, ns.action, ns.pin, ns.name), ("stream", "pair", "1234", "tv"))
        ns = p.parse_args(["netplay", "host"])
        self.assertIsNone(ns.port, "--port must default to None so .env NETPLAY_PORT wins")
        ns = p.parse_args(["build", "--no-pull", "--no-sunshine"])
        self.assertTrue(ns.no_pull and ns.no_sunshine)


if __name__ == "__main__":
    unittest.main()
