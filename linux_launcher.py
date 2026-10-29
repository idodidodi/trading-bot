"""Standalone Linux entrypoint with persistent per-user files and one worker."""
import os
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import webbrowser


class DiagnosticStream:
    """Keep terminal output while retaining tracebacks across detached restarts."""
    def __init__(self, original, handler):
        self.original = original
        self.handler = handler
        try:
            source = os.fstat(original.fileno())
            destination = os.fstat(handler.stream.fileno())
            self.echo = (source.st_dev, source.st_ino) != (destination.st_dev, destination.st_ino)
        except (AttributeError, OSError):
            self.echo = original is not None

    def write(self, text):
        if text:
            record = logging.LogRecord('runtime', logging.INFO, '', 0, text, (), None)
            self.handler.handle(record)
            if self.echo:
                try:
                    self.original.write(text)
                    self.original.flush()
                except (BrokenPipeError, OSError):
                    self.echo = False
        return len(text)

    def flush(self):
        self.handler.flush()
        if self.echo:
            try:
                self.original.flush()
            except (BrokenPipeError, OSError):
                self.echo = False

    def __getattr__(self, name):
        return getattr(self.original, name)


def configure_verbose_logging(root):
    if isinstance(sys.stdout, DiagnosticStream):
        return Path(os.environ['TRADING_BOT_LOG_FILE'])
    directory = root/'logs'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory/'runtime.log'
    descriptor = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_APPEND, 0o600)
    os.close(descriptor)
    handler = RotatingFileHandler(path, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')
    handler.terminator = ''
    # Disk/logging errors must not recurse through our stderr wrapper.
    handler.handleError = lambda record: None
    os.environ['TRADING_BOT_LOG_FILE'] = str(path)
    sys.stdout = DiagnosticStream(sys.stdout, handler)
    sys.stderr = DiagnosticStream(sys.stderr, handler)
    return path


def app_root():
    directory=os.environ.get('XDG_DATA_HOME')
    base=Path(directory) if directory and Path(directory).is_absolute() else Path.home()/'.local/share'
    return base/'FamilyTradingBotPostgres'


def settings_path():
    custom=os.environ.get('TRADING_BOT_ENV_FILE')
    if custom:
        path=Path(custom).expanduser()
        if not path.is_absolute():
            raise ValueError('TRADING_BOT_ENV_FILE must be an absolute path')
        return path
    directory=os.environ.get('XDG_CONFIG_HOME')
    base=Path(directory) if directory and Path(directory).is_absolute() else Path.home()/'.config'
    return base/'FamilyTradingBotPostgres'/'.env'


def prepare_settings(root, bundle):
    settings=settings_path()
    if os.environ.get('TRADING_BOT_ENV_FILE'):
        # A typo in a custom secrets path must not silently create a dry-run setup.
        if not settings.is_file():
            raise FileNotFoundError('TRADING_BOT_ENV_FILE must point to an existing file')
    else:
        settings.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if not settings.exists():
            legacy=root/'.env'
            text=legacy.read_text(encoding='utf-8') if legacy.exists() else (bundle/'.env.example').read_text(encoding='utf-8').replace('DRY_RUN=false','DRY_RUN=true')
            # Keep the old file for rollback; never overwrite configured secrets.
            fd=os.open(settings,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w',encoding='utf-8') as output:output.write(text)
    return settings


def configure_storage(root, bundle):
    os.environ['TRADING_BOT_ROOT']=str(root)
    os.environ['TRADING_BOT_ENV_FILE']=str(prepare_settings(root,bundle))
    from platform_app import load_env
    load_env()
    # Load .env before choosing data storage so DATA_DIR works in either source.
    data=Path(os.environ.get('DATA_DIR',str(root/'data'))).expanduser()
    if not data.is_absolute():
        raise ValueError('DATA_DIR must be an absolute path for the Linux executable')
    data.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.environ['DATA_DIR']=str(data)


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
        log_path = configure_verbose_logging(root)
        from app_version import VERSION
        print(f'[runtime] Starting Family Trading Bot {VERSION}; PID {os.getpid()}; log {log_path}', flush=True)
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
        configure_storage(root,bundle)
        os.chdir(root)
        import certifi
        os.environ['SSL_CERT_FILE']=certifi.where()
        threading.Timer(2,open_dashboard).start()
    else:
        from platform_app import ROOT, load_env
        from runtime_env import configure
        load_env();configure(ROOT)
        threading.Timer(2,lambda:webbrowser.open('http://127.0.0.1:'+os.environ.get('PORT','8080'))).start()
    from platform_app import main as run
    run()


if __name__=='__main__':main()
