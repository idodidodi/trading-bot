"""Build a native desktop executable and per-app installer; no runtime starts."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parent.parent
TRADING=(ROOT/'desktop.py').exists()
NAME='TradingExecutor' if TRADING else 'SignalService'
TITLE='Trading Executor' if TRADING else 'Signal Service'
SLUG='trading-executor' if TRADING else 'signal-service'
ENTRY='desktop.py' if TRADING else 'signal_desktop.py'
sys.path.insert(0,str(ROOT))
from app_version import VERSION


def main():
    os.chdir(ROOT)
    sha=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    (ROOT/'build').mkdir(exist_ok=True)
    info=ROOT/'build/desktop-build.json';info.write_text(json.dumps(dict(app=TITLE,version=VERSION,source_commit=sha)),encoding='utf-8')
    separator=';' if sys.platform=='win32' else ':'
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onefile','--name',NAME,
        '--collect-data','certifi','--collect-all','tzdata','--add-data',str(info)+separator+'.']
    if sys.platform=='win32':command.append('--windowed')
    if not TRADING:
        for package in ('psycopg','psycopg_binary','psycopg_pool','reportlab'):command+=['--collect-all',package]
        for source,target in [('web','web'),('skills','skills'),('cloud/browser-config.json','cloud'),('scanner.json','.'),('stock_universe.json','.'),('tradingview_template.pine','.'),('.env.example','.'),('alert-symbol-map.xml','.')]:
            command+=['--add-data',source+separator+target]
    subprocess.run(command+[ENTRY],check=True)
    binary=ROOT/'dist'/(NAME+('.exe' if sys.platform=='win32' else ''))
    if sys.platform=='win32':
        compiler=shutil.which('iscc') or str(Path(os.environ.get('ProgramFiles(x86)',r'C:\Program Files (x86)'))/'Inno Setup 6/ISCC.exe')
        if not Path(compiler).is_file():raise RuntimeError('Install Inno Setup 6 before packaging')
        script=ROOT/'build/desktop.iss'
        script.write_text(f'''[Setup]
AppId={NAME}
AppName={TITLE}
AppVersion={VERSION}
DefaultDirName={{localappdata}}\\Programs\\{NAME}
DefaultGroupName={TITLE}
PrivilegesRequired=lowest
OutputDir={ROOT/'dist'}
OutputBaseFilename={NAME}-Setup
UninstallDisplayIcon={{app}}\\{NAME}.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
[Files]
Source: "{binary}"; DestDir: "{{app}}"; Flags: ignoreversion
[Icons]
Name: "{{group}}\\{TITLE}"; Filename: "{{app}}\\{NAME}.exe"
Name: "{{userdesktop}}\\{TITLE}"; Filename: "{{app}}\\{NAME}.exe"
''',encoding='utf-8')
        subprocess.run([compiler,str(script)],check=True)
        installer=ROOT/'dist'/f'{NAME}-Setup.exe'
    elif sys.platform=='linux':
        tree=ROOT/'build/deb';shutil.rmtree(tree,ignore_errors=True)
        executable=tree/'opt'/SLUG/NAME;executable.parent.mkdir(parents=True);shutil.copy2(binary,executable);executable.chmod(0o755)
        control=tree/'DEBIAN';control.mkdir()
        (control/'control').write_text(f'Package: {SLUG}\nVersion: {VERSION}\nArchitecture: amd64\nMaintainer: idodidodi\nDepends: libx11-6, libxext6, libxrender1, libxft2, libfontconfig1\nDescription: {TITLE} independent desktop application\n',encoding='utf-8')
        applications=tree/'usr/share/applications';applications.mkdir(parents=True)
        (applications/(SLUG+'.desktop')).write_text(f'[Desktop Entry]\nType=Application\nName={TITLE}\nExec=/opt/{SLUG}/{NAME}\nTerminal=false\nCategories=Office;Finance;\n',encoding='utf-8')
        installer=ROOT/'dist'/f'{SLUG}_{VERSION}_amd64.deb'
        subprocess.run(['dpkg-deb','--root-owner-group','--build',str(tree),str(installer)],check=True)
    else:raise RuntimeError('Build installers on Windows or Linux')
    result=dict(app=TITLE,version=VERSION,source_commit=sha,platform=sys.platform,files={p.name:dict(size=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in (binary,installer)})
    (ROOT/'dist'/f'{SLUG}-manifest.json').write_text(json.dumps(result,indent=2),encoding='utf-8')


if __name__=='__main__':main()
