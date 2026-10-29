"""Exercise the cloud upgrade in a disposable local PostgreSQL cluster only."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

root=Path(__file__).resolve().parent.parent
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--legacy',action='store_true',help='Start without asset-strategies or BB Pin-Bars migrations')
args=parser.parse_args()
bin=Path(os.environ.get('RSI_PB_POSTGRES_BIN') or subprocess.check_output(['pg_config','--bindir'],text=True).strip())


def run(arguments):
    result=subprocess.run(arguments,capture_output=True,text=True)
    if result.returncode:
        print((result.stdout+result.stderr)[-6000:])
        result.check_returncode()


with tempfile.TemporaryDirectory(prefix='rsi-pb-pg-',dir='/tmp') as directory:
    folder=Path(directory);database=folder/'cluster'
    run([str(bin/'initdb'),'-D',str(database),'--auth=trust','--no-locale'])
    run([str(bin/'pg_ctl'),'-D',str(database),'-l',str(folder/'server.log'),'-o',"-k "+directory+" -c listen_addresses=''",'-w','start'])
    try:
        files=['test_auth_stub.sql','schema.sql','backtests.sql','sharing.sql','providers.sql',
               'asset-strategies.sql','momentum.sql','pin-bars.sql','csv-backtests.sql',
               'bb-pin-bars.sql','telegram-routing.sql','rsi-pb.sql','rsi-pb.sql','test_rsi_pb.sql']
        if args.legacy:files=[name for name in files if name not in ('asset-strategies.sql','bb-pin-bars.sql')]
        for name in files:
            run([str(bin/'psql'),'-h',directory,'-d','postgres','-v','ON_ERROR_STOP=1','-f',str(root/'cloud'/name)])
        print('RSI + PB SQL checks passed: migration reruns, defaults/opt-outs, pagination, authorization, CSV and routes.')
    finally:
        run([str(bin/'pg_ctl'),'-D',str(database),'-m','fast','-w','stop'])
