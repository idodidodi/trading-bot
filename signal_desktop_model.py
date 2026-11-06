"""Signal desktop process ownership. Opening the app never starts a scanner."""
import json
import os
from pathlib import Path
import subprocess
import sys

KEYS=('TRADING_DATABASE_URL','TELEGRAM_BOT_TOKEN','TELEGRAM_CHAT_ID','TELEGRAM_SENDER_ID',
      'SIGNAL_ADMIN_TOKEN','SIGNAL_API_PORT','PORT','DRY_RUN','DIVERGENCE_CHASING_ENABLED',
      'TELEGRAM_CHECKLIST_ENABLED','TELEGRAM_GROUP_DISMISSAL_ENABLED')
DEFAULTS=dict(PORT='8080',SIGNAL_API_PORT='8090',DRY_RUN='true',DIVERGENCE_CHASING_ENABLED='false',
              TELEGRAM_CHECKLIST_ENABLED='false',TELEGRAM_GROUP_DISMISSAL_ENABLED='false')


def app_directory():
    if sys.platform=='win32':base=Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData/Local'))
    elif sys.platform=='darwin':base=Path.home()/'Library/Application Support'
    else:
        value=os.environ.get('XDG_DATA_HOME','');base=Path(value) if Path(value).is_absolute() else Path.home()/'.local/share'
    return base/'SignalService'


def read_settings(path):
    if not path.exists():return dict(DEFAULTS)
    values=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(values,dict) or set(values)-set(KEYS):raise ValueError('Unknown signal-service settings')
    return DEFAULTS|values


def save_settings(path,values):
    if set(values)-set(KEYS) or any(not isinstance(v,str) or '\n' in v or '\r' in v for v in values.values()):raise ValueError('Invalid settings')
    for key in ('PORT','SIGNAL_API_PORT'):
        if not values.get(key,'').isdigit() or not 1024<=int(values[key])<=65535:raise ValueError('Ports must be between 1024 and 65535')
    if values['PORT']==values['SIGNAL_API_PORT']:raise ValueError('Worker and API need different ports')
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700);tmp=path.with_suffix('.tmp')
    fd=os.open(tmp,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(values,f,indent=2)
    os.replace(tmp,path);os.chmod(path,0o600)


class Controller:
    def __init__(self,home):self.home=Path(home);self.config=self.home/'settings.json';self.processes={};self.stopping=set()
    def running(self,role):return role in self.processes and self.processes[role].poll() is None
    def start(self,role):
        if role not in ('worker','api'):raise ValueError('Unknown service')
        if self.running(role):raise ValueError('Already running')
        values=read_settings(self.config)
        if not values.get('TRADING_DATABASE_URL'):raise ValueError('Save the signal database connection first')
        if role=='api' and len(values.get('SIGNAL_ADMIN_TOKEN',''))<32:raise ValueError('API admin token must have at least 32 characters')
        self.home.mkdir(parents=True,exist_ok=True,mode=0o700);(self.home/(role+'.stop')).unlink(missing_ok=True);self.stopping.discard(role)
        command=[sys.executable] if getattr(sys,'frozen',False) else [sys.executable,str(Path(__file__).with_name('signal_desktop.py'))]
        environment={k:v for k,v in os.environ.items() if k not in KEYS}
        with open(self.home/(role+'.log'),'ab') as log:
            self.processes[role]=subprocess.Popen(command+['--service',role,'--home',str(self.home)],env=environment,stdin=subprocess.DEVNULL,stdout=log,stderr=log)
    def stop(self,role):
        if self.running(role):(self.home/(role+'.stop')).write_text('stop\n',encoding='utf-8');self.stopping.add(role)
    def status(self,role):
        if self.running(role):return 'Stopping…' if role in self.stopping else 'Running'
        process=self.processes.get(role)
        return f'Exited ({process.returncode}); see {role}.log' if process and process.returncode else 'Stopped'


def service(home,role):
    settings=read_settings(home/'settings.json')
    # Do not inherit the legacy app's environment file or broker configuration.
    for key in KEYS:os.environ.pop(key,None)
    os.environ.update(settings)
    bundle=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
    os.environ.update(TRADING_BOT_ROOT=str(bundle),TRADING_BOT_ENV_FILE=str(home/'providers.env'),
        DATA_DIR=str(home/'data'),TRADING_BOT_ENV='prod',TRADING_BOT_ROLE='worker',SIGNAL_STOP_FILE=str(home/(role+'.stop')))
    if getattr(sys,'frozen',False):
        sys.stdout=sys.stderr=open(home/(role+'.log'),'a',encoding='utf-8',buffering=1)
    if role=='worker':
        from platform_app import main
    else:
        from signal_service import main
    main()
