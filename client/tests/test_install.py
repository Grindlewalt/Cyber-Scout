"""Sign in with a connection key, Stop and Uninstall (csclient/install.py) against the fake server; what the install
scripts would have installed lives in a throwaway HOME, and commands (launchctl, systemctl, cmd.exe) are recorded, never
run.  Run:  python3 -m unittest discover -s client/tests -v"""
import os
import plistlib
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from csclient import install as I                 # noqa: E402
from test_client import Base                      # noqa: E402

KEY = "7KQ2-M9XA-P4HD-W8NE"


def write(p, data=b"x"):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p


def fake_install(root, data_dir, platform, env=None, cgroup=None):
    """What get-client.sh / .ps1 put in a HOME under `root` -> (Install, paths, calls). The program is a stand-in
    file; `calls` records every command the Install would run."""
    home = os.path.join(root, "home")
    folder = os.path.join(home, "CyberScout")
    win = platform == "win32"
    prog = write(os.path.join(folder, "cyberscout-client.exe" if win else "cyberscout-client"), b"\x7fELF not really")
    if not win:
        write(os.path.join(folder, "logs", "client.out"), b"log\n")
    if platform == "darwin":
        item = os.path.join(home, "Library", "LaunchAgents", I.LABEL + ".plist")
        write(item, plistlib.dumps({"Label": I.LABEL, "ProgramArguments": [prog, "--no-browser"], "RunAtLoad": True,
                                    "KeepAlive": {"SuccessfulExit": False}}))
    elif platform == "linux":
        item = os.path.join(home, ".config", "systemd", "user", I.UNIT)
        write(item, f'[Unit]\nDescription=Cyber-Scout Client\n\n[Service]\nExecStart="{prog}" "--no-browser"\n'
                    'Restart=on-failure\n'.encode())
    else:
        item = os.path.join(home, "AppData", "Roaming", "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
                            I.LNK)
        write(item, b"L\0\0\0" + prog.encode("utf-16-le") + b"\0\0")       # (a .lnk names its target)
    calls = []
    inst = I.Install(data_dir, frozen=True, program=prog, platform=platform, env=env or {}, home=home,
                     run=lambda a: calls.append(a) or 0, spawn=lambda c, wait=None: calls.append(c),
                     cgroup=cgroup or os.path.join(root, "no-cgroup"))
    return inst, {"home": home, "folder": folder, "prog": prog, "item": item}, calls


class TestKeyLogin(Base):
    def test_login_key(self):
        for bad in (None, 5, "", "   ", "K" * 65, "7KQ2\nM9XA"):
            code, d = self.c.req("POST", "/client/v1/login-key", {"key": bad})
            self.assertEqual((code, d["ok"]), (422, False), (bad, d))
        code, d = self.c.req("POST", "/client/v1/login-key", {"key": "AAAA-BBBB-CCCC-DDDD"})
        self.assertEqual((code, d["ok"]), (401, False), d)
        self.assertTrue(d["detail"].startswith("That connection key isn't valid ("), d)    # (sentence case)
        self.fs.state.key_limited = True
        code, d = self.c.req("POST", "/client/v1/login-key", {"key": KEY})
        self.assertEqual((code, d["ok"]), (429, False), d)
        self.fs.state.key_limited = False
        # a stale CSRF token: refreshed once, then signed in (like the password sign-in)
        self.app.session.d["cookies"].pop("cs_csrf", None)
        self.app.session.d["csrf"] = "stale"
        n = len(self.fs.state.requests)
        code, d = self.c.req("POST", "/client/v1/login-key", {"key": f" {KEY} "})
        self.assertEqual((code, d), (200, {"ok": True, "user": {"display_name": "Scout One", "role": "scout"}}))
        sent = [r[1] for r in self.fs.state.requests[n:]]
        self.assertEqual(sent.count("/app/v1/auth/key-login"), 2, sent)
        self.assertIn("/app/v1/session", sent)
        s = self.status()
        self.assertTrue(s["signed_in"])
        self.assertEqual(s["user"], {"display_name": "Scout One", "role": "scout"})
        # offline -> 503 {offline}; no server -> 400
        self.c.req("POST", "/client/v1/logout", {})
        self.fs.state.down = True
        code, d = self.c.req("POST", "/client/v1/login-key", {"key": KEY})
        self.assertEqual((code, d["ok"], d.get("offline")), (503, False, True), d)
        self.fs.state.down = False
        self.assertEqual(self.c.req("PUT", "/client/v1/config", {"server": None})[0], 200)
        code, d = self.c.req("POST", "/client/v1/login-key", {"key": KEY})
        self.assertEqual((code, d["ok"]), (400, False), d)

    def test_make_key_is_never_queued(self):
        """An admin's "Make key" through the client: the key is only in the answer, so the write goes straight to the
        server (offline: 503), never into the outbox."""
        self.login()
        code, d = self.c.req("POST", "/app/v1/users/scout/connect-key", {})
        self.assertEqual(code, 201, d)
        self.assertRegex(d["key"], r"^[0-9A-F]{4}(-[0-9A-F]{4}){3}$")
        self.c.req("POST", "/client/v1/logout", {})
        self.assertEqual(self.c.req("POST", "/client/v1/login-key", {"key": d["key"]})[0], 200)
        self.fs.state.down = True
        self.app.engine.set_online(False, "test")
        for method in ("POST", "DELETE"):
            code, d = self.c.req(method, "/app/v1/users/scout/connect-key", {})
            self.assertEqual((code, d.get("offline")), (503, True), (method, d))
        self.assertEqual(self.app.db.items(), [])

    def test_page_key_login_signs_the_client_in(self):
        """The web app's own POST /app/v1/auth/key-login (server-style) signs the client in, like /auth/login."""
        code, hdrs, b = self.c.req("POST", "/app/v1/auth/key-login", {"key": KEY}, raw=True)
        self.assertEqual(code, 200, b)
        self.assertNotIn("cs_session", " ".join(hdrs.get_all("Set-Cookie") or []))
        self.assertNotIn(self.app.session.d["cookies"]["cs_session"].encode(), b)
        self.assertIn(b'"csrf": "%s"' % self.app.config["local_csrf"].encode(), b)
        self.assertTrue(self.status()["signed_in"])
        self.c.req("POST", "/client/v1/logout", {})
        code, d = self.c.req("POST", "/app/v1/auth/key-login", {"key": KEY + "X"})
        self.assertEqual(code, 401, d)


class TestStop(Base):
    def test_status_install_from_source(self):
        self.assertEqual(self.status()["install"], {"frozen": False, "program": None, "autostart": None,
                                                    "can_uninstall": False, "data_dir": self.app.data_dir})

    def test_quit_from_source(self):
        code, d = self.c.req("POST", "/client/v1/quit", {})
        self.assertEqual((code, d["ok"], d["how"]), (200, True, "exit"), d)
        self.assertIn("cyberscout_client.py", d["message"])
        self.assertTrue(self.app.quit_ev.wait(5))             # main() would shut down now (not running here)
        self.assertIsNone(self.app.after_quit)

    def test_quit_under_launchd_unloads_the_job(self):
        inst, p, calls = fake_install(self.tmp, self.app.data_dir, "darwin", env={"XPC_SERVICE_NAME": I.LABEL})
        self.app.install = inst
        s = self.status()["install"]
        self.assertEqual((s["frozen"], s["program"], s["autostart"], s["can_uninstall"]),
                         (True, p["prog"], "launchagent", True))
        code, d = self.c.req("POST", "/client/v1/quit", {})
        self.assertEqual((code, d["how"]), (200, "launchagent"), d)
        self.assertIn("next login", d["message"])
        self.assertTrue(self.app.quit_ev.wait(5))
        self.assertEqual(calls, [])                           # nothing before the shutdown
        self.app.shutdown()
        self.app.after_quit()
        self.assertEqual(calls, [["launchctl", "bootout", f"gui/{inst.uid}/{I.LABEL}"]])
        self.assertTrue(os.path.exists(p["item"]) and os.path.exists(p["prog"]))     # Stop removes nothing

    def test_quit_under_systemd_is_a_clean_exit(self):
        cg = write(os.path.join(self.tmp, "cgroup"),
                   b"0::/user.slice/user-1000.slice/user@1000.service/app.slice/cyberscout-client.service\n")
        inst, p, calls = fake_install(self.tmp, self.app.data_dir, "linux", env={"INVOCATION_ID": "abc"}, cgroup=cg)
        self.app.install = inst
        self.assertEqual(self.status()["install"]["autostart"], "systemd")
        code, d = self.c.req("POST", "/client/v1/quit", {})
        self.assertEqual((code, d["how"]), (200, "systemd"), d)
        self.assertTrue(self.app.quit_ev.wait(5))
        self.assertIsNone(self.app.after_quit)                # exit 0: Restart=on-failure leaves it stopped
        self.assertEqual(calls, [])


class TestUninstall(Base):
    def test_from_source_is_refused(self):
        code, d = self.c.req("POST", "/client/v1/uninstall", {"delete_data": True})
        self.assertEqual(code, 409, d)
        self.assertRegex(d["detail"], r"^This Client runs from source \(.+\): nothing is installed to remove\. "
                                      r"Use Stop instead\.$")
        self.assertFalse(self.app.quit_ev.wait(0.6))
        self.assertTrue(os.path.isfile(os.path.join(self.app.data_dir, "config.json")))
        code, d = self.c.req("POST", "/client/v1/uninstall", {"delete_data": "yes"})
        self.assertEqual(code, 422, d)

    def test_macos_with_data(self):
        inst, p, calls = fake_install(self.tmp, self.app.data_dir, "darwin", env={"XPC_SERVICE_NAME": I.LABEL})
        self.app.install = inst
        mine = write(os.path.join(self.app.data_dir, "my notes.txt"), b"not the client's")
        code, d = self.c.req("POST", "/client/v1/uninstall", {"delete_data": True})
        self.assertEqual(code, 200, d)
        self.assertEqual(d["removed"], [p["item"], p["prog"], p["folder"], self.app.data_dir])
        self.assertEqual((d["kept"], d["errors"]), ([mine], []))
        # gone at once: the login item, the program and its folder (only the program + logs/client.out were in it)
        for k in ("item", "prog", "folder"):
            self.assertFalse(os.path.lexists(p[k]), k)
        self.assertTrue(os.path.isdir(p["home"]))
        self.assertTrue(self.app.quit_ev.wait(5))
        self.assertEqual(calls, [])                           # (bootout of our own job: after the shutdown)
        self.app.shutdown()
        self.app.db.db.close()      # (Windows can't delete an open file; there the data goes to cmd.exe after the exit)
        self.app.after_quit()
        self.assertEqual(calls, [["launchctl", "bootout", f"gui/{inst.uid}/{I.LABEL}"]])
        self.assertEqual(os.listdir(self.app.data_dir), ["my notes.txt"])        # only the Client's own files went

    def test_linux_keeps_data_and_a_shared_folder(self):
        inst, p, calls = fake_install(self.tmp, self.app.data_dir, "linux")
        self.app.install = inst
        other = write(os.path.join(p["folder"], "README.txt"))
        code, d = self.c.req("POST", "/client/v1/uninstall", {})
        self.assertEqual(code, 200, d)
        self.assertEqual(d["removed"], [p["item"], p["prog"]])
        self.assertEqual(d["kept"], [p["folder"], self.app.data_dir])
        self.assertEqual(calls, [["systemctl", "--user", "disable", I.UNIT], ["systemctl", "--user", "daemon-reload"]])
        self.assertFalse(os.path.exists(p["item"]) or os.path.exists(p["prog"]))
        self.assertTrue(os.path.exists(other) and os.path.exists(os.path.join(p["folder"], "logs", "client.out")))
        self.assertTrue(self.app.quit_ev.wait(5))
        self.app.shutdown()
        self.app.after_quit()
        self.assertEqual(len(calls), 2)                       # not under the unit: a plain exit
        self.assertTrue(os.path.isfile(os.path.join(self.app.data_dir, "config.json")))

    def test_windows_hands_the_running_exe_to_cmd(self):
        inst, p, calls = fake_install(self.tmp, self.app.data_dir, "win32")
        self.app.install = inst
        self.assertEqual(self.status()["install"]["autostart"], "startup")
        code, d = self.c.req("POST", "/client/v1/uninstall", {"delete_data": True})
        self.assertEqual(code, 200, d)
        self.assertEqual(d["removed"], [p["item"], p["prog"], p["folder"], self.app.data_dir])
        self.assertFalse(os.path.exists(p["item"]))           # the shortcut goes at once
        self.assertTrue(os.path.exists(p["prog"]))            # a running .exe can't: cmd.exe deletes it after the exit
        self.assertTrue(self.app.quit_ev.wait(5))
        self.app.shutdown()
        self.app.after_quit()
        self.assertEqual(len(calls), 1, calls)
        cmd = calls[0]
        self.assertTrue(cmd.startswith('cmd.exe /d /s /c "(for /l %i in (1,1,40) do @if exist "' + p["prog"]), cmd)
        self.assertIn(f'del /f /q "{p["prog"]}"', cmd)
        self.assertIn(f'del /f /q "{os.path.join(self.app.data_dir, "config.json")}"', cmd)
        self.assertIn(f'rmdir /s /q "{os.path.join(self.app.data_dir, "site")}"', cmd)
        self.assertTrue(cmd.endswith(f'rmdir "{p["folder"]}" >nul 2>&1 & rmdir "{self.app.data_dir}" >nul 2>&1"'), cmd)
        self.assertTrue(os.path.isfile(os.path.join(self.app.data_dir, "config.json")))   # (nothing deleted here)


class TestInstallUnits(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="csc-inst-")
        self.data = os.path.join(self.tmp, "data")
        write(os.path.join(self.data, "config.json"), b"{}")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def symlink(self, src, dst):
        try:
            os.symlink(src, dst)
        except (OSError, NotImplementedError):
            self.skipTest("no symlinks here")

    def test_another_copys_login_item_is_left_alone(self):
        inst, p, calls = fake_install(self.tmp, self.data, "darwin")
        other = os.path.join(self.tmp, "elsewhere", "cyberscout-client")
        write(p["item"], plistlib.dumps({"Label": I.LABEL, "ProgramArguments": [write(other)]}))
        self.assertIsNone(inst.info()["autostart"])
        out, then = inst.uninstall(False)
        self.assertNotIn(p["item"], out["removed"])
        self.assertTrue(os.path.exists(p["item"]))
        self.assertEqual(calls, [])

    def test_symlinks_are_refused(self):
        inst, p, calls = fake_install(self.tmp, self.data, "darwin")
        real = os.path.join(self.tmp, "real.plist")
        os.replace(p["item"], real)
        self.symlink(real, p["item"])
        out, _ = inst.uninstall(False)
        self.assertIn(p["item"], out["kept"])
        self.assertTrue(any("symbolic link" in e for e in out["errors"]), out)
        self.assertTrue(os.path.islink(p["item"]) and os.path.exists(real))
        # a symlinked data folder
        link = os.path.join(self.tmp, "data-link")
        self.symlink(self.data, link)
        inst2, p2, _ = fake_install(os.path.join(self.tmp, "b"), link, "linux")
        out, then = inst2.uninstall(True)
        self.assertIn(link, out["kept"])
        then()
        self.assertTrue(os.path.isfile(os.path.join(self.data, "config.json")))

    def test_home_is_never_the_data_folder(self):
        inst, p, _ = fake_install(self.tmp, self.data, "linux")
        inst.data_dir = p["home"]
        write(os.path.join(p["home"], "web", "my site.html"))
        out, then = inst.uninstall(True)
        self.assertIn(p["home"], out["kept"])
        then()
        self.assertTrue(os.path.isfile(os.path.join(p["home"], "web", "my site.html")))

    def test_cmd_refuses_unquotable_paths(self):
        self.assertIsNone(I.Install._win_cleanup([r"C:\Users\a%PATH%b\x.exe"], [], [], []))
        self.assertIsNone(I.Install._win_cleanup([], [], [], []))


if __name__ == "__main__":
    unittest.main()
