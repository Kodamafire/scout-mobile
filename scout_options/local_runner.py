"""Local WSL supervision and Windows sign-in startup for read-only research."""
import argparse
import fcntl
import json
import os
import pwd
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from .credentials import load_credentials
from .diagnostics import running_credentials, check
from .health import healthy

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT/'.scout-options'
SECRET = Path.home()/'.config'/'scout-options'/'credentials.json'


def private_save(path, values):
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('Refusing symlink credential storage')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.stat().st_uid != os.getuid():
        raise ValueError('Credential directory owner mismatch')
    path.parent.chmod(0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, 'w') as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(json.dumps(values))
        output.flush()
        os.fsync(output.fileno())


def private_load(path=SECRET):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as source:
        stat = os.fstat(source.fileno())
        if stat.st_uid != os.getuid() or stat.st_mode & 0o077:
            raise ValueError('Credential file permissions must be owner-only')
        return load_credentials(json.load(source))


def processes(module):
    result = []
    for path in Path('/proc').iterdir():
        if not path.name.isdigit() or int(path.name) == os.getpid():
            continue
        try:
            args=(path/'cmdline').read_bytes().split(b'\0')
            if (path.stat().st_uid == os.getuid() and (path/'cwd').resolve() == ROOT and
                any(args[i:i+2] == [b'-m',module.encode()] for i in range(len(args)-1))):
                result.append(int(path.name))
        except OSError:
            continue
    return result


def psquote(value):
    return "'"+value.replace("'", "''")+"'"


def shortcut_script(distro, user, root, python):
    arguments=subprocess.list2cmdline(['-d',distro,'-u',user,'--cd',str(root),
                                       '--',str(python),'-m','scout_options.local_runner','--run'])
    return ("$ErrorActionPreference='Stop'; "
            "$startup=[Environment]::GetFolderPath('Startup'); "
            "$shell=New-Object -ComObject WScript.Shell; "
            "$link=$shell.CreateShortcut((Join-Path $startup 'ScoutOptionsResearch.lnk')); "
            "$link.TargetPath=Join-Path $env:SystemRoot 'System32\\wsl.exe'; "
            "$link.Arguments="+psquote(arguments)+"; "
            "$link.WorkingDirectory=$env:SystemRoot; $link.WindowStyle=7; $link.Save()")


def stop_process(pid):
    try:
        os.kill(pid,signal.SIGINT)
    except ProcessLookupError:
        return
    for _ in range(100):
        if not Path('/proc',str(pid)).exists():
            return
        time.sleep(.1)
    # Local research service only; release its writer lock if cleanup stalled.
    try:
        os.kill(pid,signal.SIGTERM)
    except ProcessLookupError:
        pass


def stop_child(child):
    if child.poll() is not None:
        return
    child.send_signal(signal.SIGINT)
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        child.terminate()
        child.wait(timeout=5)


def write_supervisor(values):
    temp=DATA/'supervisor-status.tmp'
    temp.write_text(json.dumps(dict(values,at=datetime.now(timezone.utc).isoformat())))
    temp.replace(DATA/'supervisor-status.json')


def worker_ready(started_at):
    if started_at is None or not healthy(DATA/'options-status.json'):
        return False
    try:
        heartbeat=datetime.fromisoformat(json.loads((DATA/'options-status.json').read_text())['heartbeat'])
        return heartbeat >= started_at
    except (OSError,ValueError,KeyError,TypeError):
        return False


def supervise():
    DATA.mkdir(exist_ok=True)
    with (DATA/'supervisor.lock').open('a') as writer:
        try:
            fcntl.flock(writer,fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return  # Sign-in shortcut and installer may start simultaneously.
        key,secret=private_load()
        env=dict(os.environ,ALPACA_API_KEY=key,ALPACA_SECRET_KEY=secret,SCOUT_OPTIONS_FEED='indicative')
        commands={
            'worker':[sys.executable,'-m','scout_options.service','--capital','10000',
                      '--ledger',str(DATA/'research.sqlite'),'--status',str(DATA/'options-status.json')],
            'dashboard':[sys.executable,'-m','scout_options.status_server','--directory',str(DATA),
                         '--bind','127.0.0.1','--port','8080']}
        children={};started={};started_wall={};next_start={name:0 for name in commands};restarts=0
        stopping=False
        def stop(*_):
            nonlocal stopping
            stopping=True
        signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
        try:
            while not stopping:
                now=time.monotonic()
                for name,command in commands.items():
                    child=children.get(name)
                    if child and child.poll() is None:
                        if name == 'worker' and now-started[name] > 30 and not healthy(DATA/'options-status.json'):
                            stop_child(child);next_start[name]=now+10;restarts+=1
                        continue
                    if child is not None:
                        children.pop(name)
                        next_start[name]=max(next_start[name],now+10)
                        restarts+=1
                    if now >= next_start[name]:
                        children[name]=subprocess.Popen(command,cwd=ROOT,env=env,
                            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                        started[name]=now
                        started_wall[name]=datetime.now(timezone.utc)
                write_supervisor(dict(pid=os.getpid(),children={k:v.pid for k,v in children.items()},
                    restarts=restarts,worker_ready=worker_ready(started_wall.get('worker')),mode='READ_ONLY_INDICATIVE'))
                time.sleep(1)
        finally:
            for child in children.values(): stop_child(child)
            write_supervisor(dict(pid=None,children={},worker_ready=False,mode='STOPPED'))


def install():
    from alpaca.trading.client import TradingClient
    distro=os.environ.get('WSL_DISTRO_NAME')
    if not distro:
        raise RuntimeError('This installer requires Ubuntu on Windows through WSL')
    if processes('scout_options.local_runner'):
        print('Local supervisor already running. No changes made.')
        return
    key,secret=running_credentials()
    if not check(TradingClient(key,secret,paper=True)):
        print('Setup stopped before changing any running service. Correct authentication first.')
        return
    # Private local storage is necessary to restart without prompting for keys.
    private_save(SECRET,dict(ALPACA_API_KEY=key,ALPACA_SECRET_KEY=secret))
    user=pwd.getpwuid(os.getuid()).pw_name
    script=shortcut_script(distro,user,ROOT,sys.executable)
    completed=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],
                              stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if completed.returncode:
        print('Windows startup registration failed. Existing service left running; local credential file saved.')
        return
    for module in ('scout_options.service','scout_options.status_server'):
        for pid in processes(module): stop_process(pid)
    DATA.mkdir(exist_ok=True)
    subprocess.Popen([sys.executable,'-m','scout_options.local_runner','--run'],cwd=ROOT,
        start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    print('Windows sign-in startup shortcut installed. Credentials saved locally with owner-only file permissions.')
    print('Starting supervised read-only research. No subscriptions or brokerage orders.')
    for _ in range(30):
        time.sleep(1)
        try:
            state=json.loads((DATA/'supervisor-status.json').read_text())
            if state.get('pid') and state.get('worker_ready') and healthy(DATA/'options-status.json'):
                print('BACKGROUND SERVICE READY. Refresh http://localhost:8080/options.html')
                print('Automatic start after Windows sign-in is configured; a future sign-in test is still needed.')
                return
        except (OSError,ValueError):
            pass
    print('Startup readiness not confirmed. Run python -m scout_options.local_runner --status')


def main():
    parser=argparse.ArgumentParser(description='Read-only local research background service')
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--install',action='store_true')
    group.add_argument('--run',action='store_true')
    group.add_argument('--status',action='store_true')
    group.add_argument('--stop',action='store_true')
    args=parser.parse_args()
    try:
        if args.install: install()
        elif args.run: supervise()
        elif args.stop:
            for pid in processes('scout_options.local_runner'): os.kill(pid,signal.SIGTERM)
            print('Stop requested. Startup shortcut remains installed.')
        else:
            print((DATA/'supervisor-status.json').read_text())
    except (OSError,ValueError,RuntimeError):
        print('Local setup needs attention. No credential values or remote error details are displayed.')


if __name__ == '__main__': main()
