"""Unit tests for container/entrypoint.py (pure functions only).

    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
_tmp_home = Path(tempfile.mkdtemp(prefix="ep-home-"))
os.environ.setdefault("DOLPHIN_EMU_USERPATH", str(_tmp_home / "saves"))
os.environ.setdefault("LOG_DIR", str(_tmp_home / "logs"))
spec = importlib.util.spec_from_file_location("entrypoint", ROOT / "container" / "entrypoint.py")
ep = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["entrypoint"] = ep
spec.loader.exec_module(ep)


class Profiles(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ep-"))
        self.cfg_root = self.tmp / "controller_configs"
        self.user = self.tmp / "saves"
        self.cfg_root.mkdir()
        self.patches = [
            mock.patch.object(ep, "CONTROLLER_CONFIG_DIR", self.cfg_root),
            mock.patch.object(ep, "USER_DIR", self.user),
            mock.patch.object(ep, "CONFIG_DIR", self.user / "Config"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self) -> None:
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_destinations(self) -> None:
        cases = {
            "melee.ini": "Config/Profiles/GCPad/melee.ini",
            "GCPadNew.ini": "Config/GCPadNew.ini",
            "Dolphin.ini": "Config/Dolphin.ini",
            "Wiimote/p1.ini": "Config/Profiles/Wiimote/p1.ini",
            "GCPad/sub/deep.ini": "Config/Profiles/GCPad/deep.ini",
            "Hotkeys/fast.ini": "Config/Profiles/Hotkeys/fast.ini",
            "Random/thing.ini": "Config/Profiles/GCPad/thing.ini",
        }
        for src, expected in cases.items():
            p = self.cfg_root / src
            got = ep.profile_destination(p, self.cfg_root)
            self.assertEqual(got.relative_to(self.user).as_posix(), expected, src)

    def test_import_copies_and_skips_unchanged(self) -> None:
        (self.cfg_root / "Wiimote").mkdir()
        (self.cfg_root / "melee.ini").write_text("[Profile]\n", encoding="utf-8")
        (self.cfg_root / "Wiimote" / "wm.ini").write_text("[Profile]\n", encoding="utf-8")
        (self.cfg_root / "GFX.ini").write_text("[Settings]\n", encoding="utf-8")
        ep.prepare_user_dir()
        self.assertEqual(ep.import_controller_profiles(), 3)
        self.assertTrue((self.user / "Config" / "Profiles" / "GCPad" / "melee.ini").is_file())
        self.assertTrue((self.user / "Config" / "Profiles" / "Wiimote" / "wm.ini").is_file())
        self.assertTrue((self.user / "Config" / "GFX.ini").is_file())
        self.assertEqual(ep.import_controller_profiles(), 0, "second run is a no-op")

    def test_prepare_user_dir_layout(self) -> None:
        ep.prepare_user_dir()
        for sub in ep.USER_SUBDIRS:
            self.assertTrue((self.user / sub).is_dir(), sub)
        self.assertFalse((self.user / ".write-test").exists())


class Sunshine(unittest.TestCase):
    def test_cmd_quoting(self) -> None:
        self.assertEqual(ep.sunshine_cmd(["dolphin-emu", "-b", "-e", "/game/a.iso"]),
                         "dolphin-emu -b -e /game/a.iso")
        self.assertEqual(ep.sunshine_cmd(["dolphin-emu", "-e", '/game/my "game".iso']),
                         'dolphin-emu -e "/game/my \\"game\\".iso"')
        self.assertIn('"/game/Super Smash.rvz"', ep.sunshine_cmd(["x", "/game/Super Smash.rvz"]))

    def test_build_apps(self) -> None:
        # PurePosixPath: the entrypoint only ever runs on Linux, but these
        # tests also run on Windows hosts.
        root = PurePosixPath("/game")
        games = [root / "melee.iso", root / "Dolphin.rvz", root / "sub" / "melee.iso"]
        apps = ep.build_apps(games, root)["apps"]
        names = [a["name"] for a in apps]
        self.assertEqual(names[0], "Dolphin")
        self.assertEqual(names[-1], "Desktop")
        self.assertNotIn("cmd", apps[-1])
        self.assertEqual(len(names), len(set(names)), f"duplicate app names: {names}")
        game_app = next(a for a in apps if a["name"] == "melee")
        self.assertIn("-C Dolphin.Display.Fullscreen=True", game_app["cmd"])
        self.assertIn("-b -e /game/melee.iso", game_app["cmd"])
        self.assertEqual(game_app["working-dir"], "/game")
        self.assertFalse(game_app["auto-detach"])
        self.assertIn("Dolphin (2)", names, "root-level game named Dolphin gets a suffix")
        # Sunshine's own JSON parser gets valid JSON
        json.dumps(ep.build_apps(games, root))

    def test_build_conf_and_custom_override(self) -> None:
        state = Path("/sunshine/state")
        conf = ep.build_sunshine_conf(
            name="wm", port=47989, encoder="software", state_dir=state,
            apps_json=Path("/sunshine/apps.json"), log_path=Path("/logs/sunshine.log"),
            headless=True, custom={"min_log_level": "debug", "hevc_mode": "1"},
        )
        self.assertEqual(conf["capture"], "x11")
        self.assertEqual(conf["system_tray"], "disabled")
        self.assertEqual(conf["credentials_file"], str(state / "sunshine_state.json"))
        self.assertEqual(conf["min_log_level"], "debug", "custom keys win")
        self.assertEqual(conf["hevc_mode"], "1")
        self.assertIn("min_threads", conf)
        self.assertNotIn("adapter_name", conf)

    def test_conf_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sunshine.conf"
            ep.write_sunshine_conf(p, {"port": "47989", "capture": "x11"})
            parsed = ep._parse_conf(p.read_text(encoding="utf-8"))
        self.assertEqual(parsed, {"port": "47989", "capture": "x11"})

    def test_apps_override_wins(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            apps = Path(d) / "apps.json"
            apps.with_name("apps.override.json").write_text('{"apps": []}', encoding="utf-8")
            ep.write_apps_json(apps, [], Path("/game"))
            self.assertEqual(json.loads(apps.read_text(encoding="utf-8")), {"apps": []})

    def test_dolphin_argv(self) -> None:
        self.assertEqual(ep.dolphin_argv(), ["dolphin-emu"])
        g = PurePosixPath("/game/x.iso")
        self.assertEqual(ep.dolphin_argv(g), ["dolphin-emu", "-b", "-e", "/game/x.iso"])
        self.assertEqual(ep.dolphin_argv(g, fullscreen=True)[-2:], ["-C", "Dolphin.Display.Fullscreen=True"])


if __name__ == "__main__":
    unittest.main()
