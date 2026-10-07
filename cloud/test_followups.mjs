import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {calculateFollowup} from './functions/backtests/followup.mjs';
const cases=JSON.parse(execFileSync('python3',['-c',`
import json
from dataclasses import asdict,replace
from test_finding_followup import signal,bars
from finding_followup import calculate
cases=[]
for bearish in (False,True):
 s=signal();b=bars()
 if bearish:
  s['direction']='bearish';b=[replace(c,open=200-c.open,high=200-c.low,low=200-c.high,close=200-c.close) for c in b]
 for mode in ('recovery','all'):
  for count in range(6):
   subset=b[:count];cases.append(dict(signal=s,bars=[asdict(c) for c in subset],mode=mode,expected=calculate(s,subset,mode)))
print(json.dumps(cases))
`],{encoding:'utf8'}));
for(const c of cases)assert.deepEqual(calculateFollowup(c.signal,c.bars,c.mode),c.expected);
assert.throws(()=>calculateFollowup(cases[0].signal,[],'bad'),/Invalid/);
console.log('Follow-up parity passed for bullish/bearish, both windows, missing history, zero gain and recovery.');
