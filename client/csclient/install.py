"""Stop and Uninstall (POST /client/v1/quit, /client/v1/uninstall): what site/tools/get-client.sh / get-client.ps1
installed and how to undo it. The login item (macOS LaunchAgent com.cyberscout.client, Linux systemd user unit
cyberscout-client.service, Windows Startup shortcut "Cyber-Scout Client.lnk"), the program file, its folder (only
when it holds nothing but the program and logs/) and, when asked, the data folder (only the files the Client makes
there). Nothing else is ever removed, and nothing through a symbolic link. Platform, paths, environment and how
commands run are injectable (tests)."""
import logging
import os
import plistlib
import shlex
import shutil
import subprocess
import sys

log = logging.getLogger("cyberscout.install")

LABEL = "com.cyberscout.client"
UNIT = "cyberscout-client.service"
LNK = "Cyber-Scout Client.lnk"
PROGRAM_NAMES = ("cyberscout-client", "cyberscout-client.exe")     # what the install scripts call it
OUT_LOG = "client.out"                    # get-client.sh: launchd / systemd / nohup output in <dir>/logs/
JUNK = (".DS_Store", "Thumbs.db", "desktop.ini")
# what the Client keeps in its data folder (README "Storage"); anything else there is someone else's and stays
DATA_ENTRIES = ("config.json", "client.lock", "session.json", "client.sqlite3", "client.sqlite3-wal",
                "client.sqlite3-shm", "client.sqlite3-journal", "site", "state", "parts", "tmp", "web", "logs",
                "processing")
SOURCE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class NotInstalled(Exception):
    """Run from source: nothing was installed (Uninstall answers 409)."""


def _is_link(p):
    return os.path.islink(p) or bool(getattr(os.path, "isjunction", lambda _: False)(p))


def _run(args):
    """A short command (systemctl, launchctl); its failure is logged, never raised."""
    try:
        r = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           timeout=20)
        if r.returncode:
            log.info("%s: exit %d %s", " ".join(args), r.returncode, (r.stderr or b"").decode(errors="replace").strip())
        return r.returncode
    except (OSError, subprocess.SubprocessError) as e:
        log.info("%s: %s", " ".join(args), e)
        return None


def _spawn(cmd, wait=None):
    """Start a command that must outlive this process (its own session / process group, no console), optionally
    waiting `wait` s for it. `cmd`: an argument list, or on Windows a whole cmd.exe command line."""
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True,
          # not in a folder it may delete (a process's working folder can't be removed on Windows)
          "cwd": (os.environ.get("SystemRoot") or "C:\\") if os.name == "nt" else "/"}
    try:
        if os.name == "nt":
            flags = 0x08000000 | 0x00000200          # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
            try:
                p = subprocess.Popen(cmd, creationflags=flags | 0x01000000, **kw)    # + CREATE_BREAKAWAY_FROM_JOB
            except OSError:                          # (a job that doesn't allow breaking away)
                p = subprocess.Popen(cmd, creationflags=flags, **kw)
        else:
            p = subprocess.Popen(cmd, start_new_session=True, **kw)
    except OSError as e:
        log.warning("couldn't start %s: %s", cmd, e)
        return None
    if wait:
        try:
            p.wait(wait)
        except subprocess.TimeoutExpired:
            pass
    return p


class Install:
    def __init__(self, data_dir, frozen=None, program=None, platform=None, env=None, home=None, run=None, spawn=None,
                 cgroup="/proc/self/cgroup"):
        self.data_dir = os.path.abspath(data_dir)
        self.frozen = bool(getattr(sys, "frozen", False)) if frozen is None else bool(frozen)
        self.program = os.path.abspath(program or sys.executable) if self.frozen else None
        plat = platform or sys.platform
        self.os = "mac" if plat == "darwin" else "win" if plat.startswith(("win", "cygwin")) else "linux"
        self.env = os.environ if env is None else env
        self.home = home or os.path.expanduser("~")
        self.run, self.spawn, self.cgroup = run or _run, spawn or _spawn, cgroup
        self.uid = os.getuid() if hasattr(os, "getuid") else 0

    # ------------------------------------------------------------------------------------------------ what is here
    def plist(self):
        return os.path.join(self.home, "Library", "LaunchAgents", LABEL + ".plist")

    def unit_file(self):
        return os.path.join(self.env.get("XDG_CONFIG_HOME") or os.path.join(self.home, ".config"), "systemd", "user",
                            UNIT)

    def startup_lnk(self):
        appdata = self.env.get("APPDATA") or os.path.join(self.home, "AppData", "Roaming")
        return os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup", LNK)

    def under(self):
        """The login item that started this process: "launchagent" (launchd sets XPC_SERVICE_NAME to the job's
        label), "systemd" (our unit's cgroup) or None."""
        if self.os == "mac":
            return "launchagent" if self.env.get("XPC_SERVICE_NAME") == LABEL else None
        if self.os == "linux" and self.env.get("INVOCATION_ID"):
            try:
                with open(self.cgroup, encoding="utf-8", errors="replace") as f:
                    if "/" + UNIT in f.read():
                        return "systemd"
            except OSError:
                pass
        return None

    def login_item(self):
        """(kind, path) of the login item that starts this program ("launchagent" | "systemd" | "startup"), else
        (None, None). One that starts another copy (another --dir) isn't ours."""
        under = self.under()
        if self.os == "mac":
            p = self.plist()
            if os.path.lexists(p) and (under == "launchagent" or self._same(self._plist_program(p))):
                return "launchagent", p
        elif self.os == "linux":
            p = self.unit_file()
            if os.path.lexists(p) and (under == "systemd" or self._same(self._unit_program(p))):
                return "systemd", p
        else:
            p = self.startup_lnk()
            if os.path.lexists(p) and self.program and (self._lnk_names_program(p) or self._same(self._win_default())):
                return "startup", p
        return None, None

    def info(self):
        """status.install: {frozen, program, autostart, can_uninstall, data_dir}."""
        return {"frozen": self.frozen, "program": self.program, "autostart": self.login_item()[0],
                "can_uninstall": self.frozen, "data_dir": self.data_dir}

    def _same(self, p):
        if not p or not self.program:
            return False
        try:
            return os.path.samefile(p, self.program)
        except OSError:
            return os.path.normcase(os.path.realpath(p)) == os.path.normcase(os.path.realpath(self.program))

    @staticmethod
    def _plist_program(p):
        try:
            with open(p, "rb") as f:
                d = plistlib.load(f)
            args = d.get("ProgramArguments") or [d.get("Program")]
            return args[0] if isinstance(args, list) and args and isinstance(args[0], str) else None
        except Exception:                           # (unreadable / not a plist: not ours)
            return None

    @staticmethod
    def _unit_program(p):
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.startswith("ExecStart="):
                        args = shlex.split(line[len("ExecStart="):])
                        return args[0] if args else None
        except (OSError, ValueError):
            pass
        return None

    def _lnk_names_program(self, p):
        """A .lnk keeps its target as ANSI (LinkInfo) and UTF-16 (StringData) text: look for this program's path."""
        try:
            with open(p, "rb") as f:
                data = f.read(256 * 1024).lower()
        except OSError:
            return False
        prog = self.program.lower()
        for enc in ("utf-16-le", "mbcs", "utf-8"):
            try:
                if prog.encode(enc) in data:
                    return True
            except (LookupError, UnicodeEncodeError):
                continue
        return False

    def _win_default(self):
        local = self.env.get("LOCALAPPDATA") or os.path.join(self.home, "AppData", "Local")
        return os.path.join(local, "CyberScout", "cyberscout-client.exe")

    # ------------------------------------------------------------------------------------------------ stop
    def stop(self):
        """How Stop ends this process -> (how, then). "launchagent": a clean exit, then `then` unloads our job
        (launchctl bootout; KeepAlive would start it again, and it is loaded again at the next login); "systemd": a
        clean exit (the unit restarts only on failure); "exit": nothing else starts it again until the next login."""
        how = self.under() or "exit"
        return how, (self.bootout if how == "launchagent" else None)

    def stop_message(self, how):
        if how in ("launchagent", "systemd") or self.login_item()[0]:
            return "The Cyber-Scout Client stopped. It starts again at your next login."
        if self.frozen:
            return f"The Cyber-Scout Client stopped. Start it again: {self.program}"
        return "The Cyber-Scout Client stopped. Start it again: python3 " + os.path.join(SOURCE_DIR,
                                                                                         "cyberscout_client.py")

    def bootout(self):
        """Unload our LaunchAgent job, from inside it, after the shutdown: launchd SIGTERMs this process (main()
        exits quietly by then). launchctl gets its own session: launchd also ends the job's process group."""
        self.spawn(["launchctl", "bootout", f"gui/{self.uid}/{LABEL}"], wait=15)

    # ------------------------------------------------------------------------------------------------ uninstall
    def uninstall(self, delete_data=False):
        """Remove what can go while the client runs, plan the rest -> ({ok, removed, kept, errors}, then). `then`
        runs after the shutdown: the data folder, our LaunchAgent job (bootout), and on Windows (a running .exe and
        open files can't be deleted) a detached cmd.exe that waits for this process to end, then deletes."""
        if not self.frozen:
            raise NotInstalled(f"This Client runs from source ({SOURCE_DIR}): nothing is installed to remove. "
                               "Use Stop instead.")
        removed, kept, errors = [], [], []
        win_wait, win_files, win_trees, win_dirs = [], [], [], []
        under = self.under()

        def refuse(p, why):
            kept.append(p)
            errors.append(f"{p}: {why}, not removed")

        def rm(p):
            try:
                os.remove(p)
                removed.append(p)
                return True
            except OSError as e:
                refuse(p, e.strerror or str(e))
                return False

        # the login item, so nothing starts the client again
        kind, item = self.login_item()
        if item and _is_link(item):
            refuse(item, "a symbolic link")
        elif item:
            if kind == "systemd":
                self.run(["systemctl", "--user", "disable", UNIT])
            if rm(item) and kind == "systemd":
                self.run(["systemctl", "--user", "daemon-reload"])
            if kind == "launchagent" and under != "launchagent":
                self.run(["launchctl", "bootout", f"gui/{self.uid}/{LABEL}"])      # (loaded, started elsewhere)

        # the program, and its folder when it is the install scripts' (only the program and logs/ in it)
        prog = self.program
        folder = os.path.dirname(prog)
        logs = os.path.join(folder, "logs")
        folder_ok = self._install_folder_ok(folder)
        if _is_link(prog):
            refuse(prog, "a symbolic link")
            folder_ok = False
        elif self.os == "win":
            win_wait.append(prog)
            removed.append(prog)
        elif not rm(prog):
            folder_ok = False
        if folder_ok:
            if self.os == "win":
                win_dirs.append(folder)
            else:
                try:
                    for d in (logs, folder):
                        if os.path.isdir(d):
                            for n in os.listdir(d):
                                if n in JUNK or (d == logs and n == OUT_LOG):
                                    os.remove(os.path.join(d, n))
                            os.rmdir(d)                       # (only ever an empty folder)
                except OSError as e:
                    refuse(folder, e.strerror or str(e))
                    folder_ok = False
            if folder_ok:
                removed.append(folder)
        elif os.path.basename(prog) in PROGRAM_NAMES and os.path.isdir(folder) and not _is_link(prog):
            kept.append(folder)                               # (it holds other files too)

        # the data folder: only the Client's own files in it
        data_names = []
        if not delete_data:
            kept.append(self.data_dir)
        elif _is_link(self.data_dir):
            refuse(self.data_dir, "a symbolic link")
        elif self._precious(self.data_dir):
            refuse(self.data_dir, "your home folder or a folder above it")
        else:
            try:
                names = sorted(os.listdir(self.data_dir))
            except OSError as e:
                names = []
                refuse(self.data_dir, e.strerror or str(e))
            for n in names:
                p = os.path.join(self.data_dir, n)
                if not (n in DATA_ENTRIES or n in JUNK or n.startswith(".cs-tmp-")):
                    kept.append(p)
                elif _is_link(p):
                    refuse(p, "a symbolic link")
                else:
                    data_names.append(n)
            if data_names:
                removed.append(self.data_dir)
                if self.os == "win":
                    for n in data_names:
                        p = os.path.join(self.data_dir, n)
                        (win_trees if os.path.isdir(p) else win_files).append(p)
                    win_dirs.append(self.data_dir)

        script = None
        if self.os == "win":
            script = self._win_cleanup(win_wait, win_files, win_trees, win_dirs)
            if not script:                            # (a path cmd.exe can't quote: say so, delete nothing later)
                for p in win_wait + [d for d in win_dirs if d in removed]:
                    removed.remove(p)
                    refuse(p, "a name cmd.exe can't handle")

        def then():
            if self.os == "win":
                if script:
                    self.spawn(script)
                return
            if data_names:
                self._remove_data(data_names)
            if under == "launchagent":
                self.bootout()

        log.info("uninstall: removing %s; keeping %s%s", removed, kept, f"; problems: {errors}" if errors else "")
        return {"ok": True, "removed": removed, "kept": kept, "errors": errors}, then

    def _install_folder_ok(self, folder):
        prog = os.path.basename(self.program)
        if prog not in PROGRAM_NAMES or _is_link(folder) or self._precious(folder):
            return False
        try:
            for n in os.listdir(folder):
                p = os.path.join(folder, n)
                if n == prog or n in JUNK:
                    continue
                if n == "logs" and os.path.isdir(p) and not _is_link(p) and all(
                        m == OUT_LOG or m in JUNK for m in os.listdir(p)):
                    continue
                return False
        except OSError:
            return False
        return True

    def _precious(self, p):
        """The home folder, a folder above it, or a drive / filesystem root: never removed (paranoia)."""
        r = os.path.normcase(os.path.realpath(p))
        home = os.path.normcase(os.path.realpath(self.home))
        return r == home or home.startswith(r.rstrip(os.sep) + os.sep) or os.path.dirname(r) == r

    def _remove_data(self, names):
        def err(_fn, path, exc):
            log.warning("uninstall: couldn't remove %s: %s", path, exc[1] if isinstance(exc, tuple) else exc)
        kw = {"onexc": err} if sys.version_info >= (3, 12) else {"onerror": err}
        for n in names:
            p = os.path.join(self.data_dir, n)
            try:
                if os.path.isdir(p) and not _is_link(p):
                    shutil.rmtree(p, **kw)                   # (rmtree never follows a symlink inside)
                else:
                    os.remove(p)
            except OSError as e:
                log.warning("uninstall: couldn't remove %s: %s", p, e)
        try:
            os.rmdir(self.data_dir)
        except OSError:
            pass
        log.info("uninstall: removed the data folder %s", self.data_dir)

    @staticmethod
    def _win_cleanup(wait, files, trees, dirs):
        """One cmd.exe command line: retry deleting `wait` (the program) until this process and the one-file
        launcher have exited (up to ~40 s), then the data files and folders; `dirs` only go when empty (rmdir
        without /s)."""
        allp = wait + files + trees + dirs
        if not allp:
            return None
        if any(c in p for p in allp for c in '"%\r\n'):
            log.warning("uninstall: a path cmd.exe can't quote safely; leaving %s", allp)
            return None
        steps = [f'(for /l %i in (1,1,40) do @if exist "{f}" (ping -n 2 127.0.0.1 >nul & del /f /q "{f}" >nul 2>&1))'
                 for f in wait]
        steps += [f'del /f /q "{f}" >nul 2>&1' for f in files]
        steps += [f'rmdir /s /q "{d}" >nul 2>&1' for d in trees]
        steps += [f'rmdir "{d}" >nul 2>&1' for d in dirs]
        return 'cmd.exe /d /s /c "' + " & ".join(steps) + '"'
