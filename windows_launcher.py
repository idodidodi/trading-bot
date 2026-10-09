"""Standalone Windows entrypoint; user data lives outside the executable."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import webbrowser


def main():
    frozen=getattr(sys,'frozen',False)
    if frozen:
        root=Path(os.environ['LOCALAPPDATA'])/'FamilyTradingBot'
        binary=root/'bin'/'FamilyTradingBot.exe'
        binary.parent.mkdir(parents=True,exist_ok=True)
        if Path(sys.executable).resolve()!=binary.resolve():
            if not binary.exists():shutil.copy2(sys.executable,binary)
            subprocess.Popen([str(binary)],env={**os.environ,'PYINSTALLER_RESET_ENVIRONMENT':'1'})
            return
        bundle=Path(sys._MEIPASS)
        for name in ('web','skills'):
            shutil.copytree(bundle/name,root/name,dirs_exist_ok=True)
        for name in ('scanner.json','stock_universe.json','tradingview_template.pine','.env.example'):
            shutil.copy2(bundle/name,root/name)
        if not (root/'.env').exists():
            (root/'.env').write_text((root/'.env.example').read_text().replace('DRY_RUN=false','DRY_RUN=true'))
        os.environ['TRADING_BOT_ROOT']=str(root)
        os.environ['DATA_DIR']=str(root/'data')
        os.chdir(root)
        # Windows lacks system IANA zoneinfo; tzdata is included by the builder.
        import certifi
        os.environ['SSL_CERT_FILE']=certifi.where()
        import ctypes
        mutex=ctypes.windll.kernel32.CreateMutexW(None,False,'Local\\FamilyTradingBotScanner')
        if ctypes.windll.kernel32.GetLastError()==183:
            webbrowser.open('http://127.0.0.1:8080')
            return
    threading.Timer(2,lambda:webbrowser.open('http://127.0.0.1:8080')).start()
    from platform_app import main as run
    run()


if __name__=='__main__':
    main()
