"""Standalone Linux entrypoint with persistent per-user files and one worker."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import webbrowser


def app_root():
    directory=os.environ.get('XDG_DATA_HOME')
    base=Path(directory) if directory and Path(directory).is_absolute() else Path.home()/'.local/share'
    return base/'FamilyTradingBot'


def external_environment():
    env=dict(os.environ)
    # Native tools must use system libraries, rather than PyInstaller's bundle.
    original=env.pop('LD_LIBRARY_PATH_ORIG',None)
    if original is None:env.pop('LD_LIBRARY_PATH',None)
    else:env['LD_LIBRARY_PATH']=original
    env['PYINSTALLER_RESET_ENVIRONMENT']='1'
    return env


def open_dashboard():
    if os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'):
        try:subprocess.Popen(['xdg-open','http://127.0.0.1:'+os.environ.get('PORT','8080')],env=external_environment(),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        except OSError:pass


def main():
    if getattr(sys,'frozen',False):
        import fcntl
        root=app_root()
        root.mkdir(parents=True,exist_ok=True,mode=0o700)
        # Hold the lock for the whole process. It is never inherited by updates.
        lock=(root/'scanner.lock').open('a')
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            open_dashboard()
            return
        binary=root/'bin'/'FamilyTradingBot'
        binary.parent.mkdir(parents=True,exist_ok=True)
        if Path(sys.executable).resolve()!=binary.resolve():
            if not binary.exists():
                staged=binary.with_suffix('.install')
                shutil.copy2(sys.executable,staged)
                staged.chmod(0o700)
                staged.replace(binary)
            lock.close()
            subprocess.Popen([str(binary)],env=external_environment(),close_fds=True)
            return
        bundle=Path(sys._MEIPASS)
        for name in ('web','skills'):
            shutil.copytree(bundle/name,root/name,dirs_exist_ok=True)
        for name in ('scanner.json','stock_universe.json','tradingview_template.pine','.env.example'):
            shutil.copy2(bundle/name,root/name)
        settings=root/'.env'
        if not settings.exists():
            settings.write_text((root/'.env.example').read_text(encoding='utf-8').replace('DRY_RUN=false','DRY_RUN=true'),encoding='utf-8')
            settings.chmod(0o600)
        os.environ['TRADING_BOT_ROOT']=str(root)
        os.environ['DATA_DIR']=str(root/'data')
        os.chdir(root)
        import certifi
        os.environ['SSL_CERT_FILE']=certifi.where()
        threading.Timer(2,open_dashboard).start()
    else:
        threading.Timer(2,lambda:webbrowser.open('http://127.0.0.1:8080')).start()
    from platform_app import main as run
    run()


if __name__=='__main__':main()
