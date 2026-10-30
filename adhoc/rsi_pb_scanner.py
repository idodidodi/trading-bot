#!/usr/bin/env python3
"""Run this file in PyCharm to open the local S&P 500 RSI + PB scanner."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / 'scripts' / 'scan-rsi-pb-sp500.py'
RUNS = ROOT / 'data' / 'ad-hoc'
SETTINGS = RUNS / 'ui-settings.json'
FRAMES = ('monthly', 'weekly', 'daily', '4h')
PREFIX = 'RSIPB_EVENT '


def scan_command(frames, recent, history, feed='Configured', membership='Refresh online', output=None, resume=None):
    """Build a shell-free command using PyCharm's selected interpreter."""
    command = [sys.executable, '-u', str(ENGINE), '--events']
    if resume:
        return command + ['--resume', str(resume)]
    if not frames or any(f not in FRAMES for f in frames):
        raise ValueError('Select at least one timeframe.')
    try:
        recent, history = int(recent), int(history)
    except (TypeError, ValueError):
        raise ValueError('Recent candles and history target must be whole numbers.') from None
    if recent < 1 or history < max(100, recent + 84):
        raise ValueError('Recent candles must be positive. History must be at least 100 and at least recent candles + 84.')
    command += ['--timeframes', *frames, '--recent-bars', str(recent), '--history-bars', str(history)]
    if feed != 'Configured':
        command += ['--feed', feed.lower()]
    if membership == 'Repository snapshot':
        command += ['--universe-file', str(ROOT/'stock_universe.json')]
    if output:
        command += ['--output', str(output)]
    return command


def filtered_patterns(patterns, search='', direction='All', frame='All', unbreached=False, latest=False):
    return [p for p in patterns if search.upper() in p['symbol'].upper()
            and (direction == 'All' or p['direction'] == direction.lower())
            and (frame == 'All' or p['timeframe'] == frame)
            and (not unbreached or not p['structural_breach'])
            and (not latest or p['age_closed_candles'] == 0)]


def stock_rows(patterns):
    grouped = {}
    for pattern in patterns:
        grouped.setdefault(pattern['symbol'], []).append(pattern)
    return [dict(symbol=symbol, timeframes=', '.join(f for f in FRAMES if any(p['timeframe']==f for p in items)),
                 directions=', '.join(sorted({p['direction'] for p in items})),
                 confirmation_ny=max(p['confirmation_ny'] for p in items),
                 unbreached='Yes' if any(not p['structural_breach'] for p in items) else 'No',
                 latest='Yes' if any(p['age_closed_candles']==0 for p in items) else 'No')
            for symbol, items in sorted(grouped.items())]


class ScanJob:
    """Own only this scan's child process; never block Tk's event thread."""
    def __init__(self, command, events):
        self.events = events
        self.stop_requested = False
        self.process = None
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, args=(command,), daemon=True)

    def start(self):
        self.thread.start()

    def _run(self, command):
        try:
            options = dict(cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, encoding='utf-8', errors='replace', bufsize=1)
            if os.name == 'nt':
                options['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                options['start_new_session'] = True
            with self.lock:
                self.process = subprocess.Popen(command, **options)
                stop = self.stop_requested
            if stop:
                self.stop()
            for line in self.process.stdout:
                if line.startswith(PREFIX):
                    try:
                        self.events.put(('event', json.loads(line[len(PREFIX):])))
                    except json.JSONDecodeError:
                        self.events.put(('log', 'Could not read a progress update.\n'))
                else:
                    self.events.put(('log', line))
            code = self.process.wait()
            self.process.stdout.close()
            self.events.put(('exit', code))
        except Exception as exc:
            # A launch error is local (for example a missing Python executable).
            self.events.put(('error', f'Unable to run scanner: {type(exc).__name__}. Check the selected Python interpreter.'))
            self.events.put(('exit', -1))

    def stop(self, force=False):
        with self.lock:
            self.stop_requested = True
            process = self.process
            if process is not None and process.poll() is None:
                try:
                    process.kill() if force else process.send_signal(signal.CTRL_BREAK_EVENT if os.name=='nt' else signal.SIGINT)
                except (OSError, ProcessLookupError):
                    pass


class ScannerUI:
    def __init__(self, root):
        self.root = root
        root.title('Ad hoc · S&P 500 RSI + PB')
        root.geometry('1280x850')
        root.minsize(1150, 700)
        self.events = queue.Queue()
        self.job = None
        self.closing = False
        self.output = None
        self.manifest = None
        self.patterns, self.coverage = [], []
        self.tables, self.table_rows = {}, {}
        self.controls = []
        self.frame_vars = {f:tk.BooleanVar(value=True) for f in FRAMES}
        self.recent = tk.StringVar(value='10')
        self.history = tk.StringVar(value='250')
        self.feed = tk.StringVar(value='Configured')
        self.membership = tk.StringVar(value='Refresh online')
        self.search, self.direction, self.frame_filter = tk.StringVar(), tk.StringVar(value='All'), tk.StringVar(value='All')
        self.unbreached, self.latest = tk.BooleanVar(), tk.BooleanVar()
        self.status = tk.StringVar(value='Ready · Select timeframes and press Run scan.')
        self.source = tk.StringVar(value='Full S&P 500 universe · Existing RSI + PB strategy · Closed candles only')
        self.counts = tk.StringVar(value='0 qualified stocks    ·    0 patterns    ·    0 combinations processed')
        self._build()
        self._load_settings()
        root.protocol('WM_DELETE_WINDOW', self._close)
        root.bind('<Control-r>', lambda _: self.run())
        root.bind('<Escape>', lambda _: self.stop() if self.job else None)
        root.after(100, self._poll)

    def _build(self):
        style = ttk.Style()
        if 'clam' in style.theme_names():
            style.theme_use('clam')
        style.configure('Treeview', rowheight=28)
        style.configure('Treeview.Heading', font=('TkDefaultFont', 11, 'bold'))
        style.configure('Title.TLabel', font=('TkDefaultFont', 21, 'bold'))
        style.configure('Run.TButton', padding=(18,9))
        main = ttk.Frame(self.root, padding=16); main.pack(fill='both', expand=True)
        ttk.Label(main, text='S&P 500 · RSI + PB', style='Title.TLabel').pack(anchor='w')
        ttk.Label(main, textvariable=self.source, wraplength=1180).pack(anchor='w', pady=(4,12))
        settings = ttk.LabelFrame(main, text='Scan settings', padding=12); settings.pack(fill='x')
        row = ttk.Frame(settings); row.pack(fill='x')
        ttk.Label(row, text='Timeframes').pack(side='left', padx=(0,12))
        for frame, label in [('monthly','Monthly'),('weekly','Weekly'),('daily','Daily'),('4h','4 hours')]:
            widget = ttk.Checkbutton(row, text=label, variable=self.frame_vars[frame]); widget.pack(side='left', padx=7); self.controls.append(widget)
        for label, variable, lo, hi in [('Recent candles',self.recent,1,100),('History target',self.history,100,2000)]:
            ttk.Label(row, text=label).pack(side='left', padx=(20,6))
            widget=ttk.Spinbox(row, from_=lo, to=hi, textvariable=variable, width=6); widget.pack(side='left'); self.controls.append(widget)
        row=ttk.Frame(settings); row.pack(fill='x', pady=(12,0))
        for label, variable, values in [('Alpaca feed',self.feed,('Configured','SIP','IEX')),('Membership',self.membership,('Refresh online','Repository snapshot'))]:
            ttk.Label(row,text=label).pack(side='left',padx=(0,6))
            widget=ttk.Combobox(row,textvariable=variable,values=values,state='readonly',width=21); widget.pack(side='left',padx=(0,20)); self.controls.append(widget)
        ttk.Label(row,text='Recency = completed candles per timeframe; 1 = latest candle only.').pack(side='left')
        ttk.Label(settings, text='4-hour downloads can be slow and include extended sessions. A history target of 100 is faster; under 250 candles is flagged as limited.',wraplength=1170).pack(anchor='w',pady=(10,0))
        toolbar=ttk.Frame(main); toolbar.pack(fill='x',pady=12)
        self.run_button=ttk.Button(toolbar,text='▶  Run scan',style='Run.TButton',command=self.run);self.run_button.pack(side='left')
        self.stop_button=ttk.Button(toolbar,text='■  Stop',command=self.stop,state='disabled');self.stop_button.pack(side='left',padx=6)
        self.force_button=ttk.Button(toolbar,text='Force stop',command=lambda:self.stop(force=True),state='disabled');self.force_button.pack(side='left',padx=(0,12))
        self.resume_button=ttk.Button(toolbar,text='Resume run…',command=self.resume);self.resume_button.pack(side='left',padx=4)
        self.load_button=ttk.Button(toolbar,text='Load results…',command=self.load_dialog);self.load_button.pack(side='left',padx=4)
        self.folder_button=ttk.Button(toolbar,text='Open output folder',command=self.open_folder,state='disabled');self.folder_button.pack(side='left',padx=4)
        ttk.Button(toolbar,text='Export visible CSV…',command=self.export).pack(side='right')
        ttk.Button(toolbar,text='Copy tickers',command=self.copy_tickers).pack(side='right',padx=6)
        self.progress=ttk.Progressbar(main,mode='determinate');self.progress.pack(fill='x')
        ttk.Label(main,textvariable=self.status).pack(anchor='w',pady=(5,4))
        ttk.Label(main,textvariable=self.counts,font=('TkDefaultFont',12,'bold')).pack(anchor='w',pady=(0,10))
        filters=ttk.Frame(main);filters.pack(fill='x',pady=(0,10))
        ttk.Label(filters,text='Filter: ticker').pack(side='left')
        ttk.Entry(filters,textvariable=self.search,width=14).pack(side='left',padx=6)
        for variable, values, width in [(self.direction,('All','Bullish','Bearish'),12),(self.frame_filter,('All',*FRAMES),11)]:
            ttk.Combobox(filters,textvariable=variable,values=values,width=width,state='readonly').pack(side='left',padx=4)
        ttk.Checkbutton(filters,text='Unbreached patterns only',variable=self.unbreached).pack(side='left',padx=8)
        ttk.Checkbutton(filters,text='Latest candle only',variable=self.latest).pack(side='left',padx=8)
        ttk.Button(filters,text='Reset filters',command=self.reset_filters).pack(side='right')
        self.notebook=ttk.Notebook(main);self.notebook.pack(fill='both',expand=True)
        self._table('Stocks', [('symbol','Stock',90),('timeframes','Timeframes',180),('directions','RSI direction',140),('confirmation_ny','Latest confirmation (New York)',280),('unbreached','Unbreached pattern',160),('latest','Latest candle pattern',160)])
        self._table('Patterns',[('symbol','Stock',85),('timeframe','Timeframe',90),('direction','RSI direction',110),('pinbar_direction','Opposite pinbar',120),('confirmation_ny','Confirmed (New York)',250),('age_closed_candles','Age (candles)',110),('structural_breach','Structural breach',130),('close','Close',100)])
        self._table('Coverage',[('symbol','Stock',85),('timeframe','Timeframe',90),('status','Status',180),('candles','Candles',90),('latest_closed_ny','Latest close (New York)',250),('limited_history','Limited history',120),('reason','Notes',350)])
        log_frame=ttk.Frame(self.notebook);self.notebook.add(log_frame,text='Activity log')
        self.log=tk.Text(log_frame,wrap='word',height=8,state='disabled');self.log.pack(side='left',fill='both',expand=True)
        bar=ttk.Scrollbar(log_frame,command=self.log.yview);bar.pack(side='right',fill='y');self.log.configure(yscrollcommand=bar.set)
        ttk.Label(main,text='Double-click a row for evidence. Breach flags describe later price action and do not exclude strategy matches.').pack(anchor='w',pady=(8,0))
        for variable in (self.search,self.direction,self.frame_filter,self.unbreached,self.latest):
            variable.trace_add('write',lambda *_:self.refresh())

    def _table(self,name,columns):
        frame=ttk.Frame(self.notebook);self.notebook.add(frame,text=name)
        table=ttk.Treeview(frame,columns=[c[0] for c in columns],show='headings',selectmode='extended')
        for key,label,width in columns:
            table.heading(key,text=label,command=lambda k=key,t=table:self.sort(t,k))
            table.column(key,width=width,minwidth=70,stretch=True)
        vertical=ttk.Scrollbar(frame,orient='vertical',command=table.yview)
        horizontal=ttk.Scrollbar(frame,orient='horizontal',command=table.xview)
        table.configure(yscrollcommand=vertical.set,xscrollcommand=horizontal.set)
        table.grid(row=0,column=0,sticky='nsew');vertical.grid(row=0,column=1,sticky='ns');horizontal.grid(row=1,column=0,sticky='ew')
        frame.rowconfigure(0,weight=1);frame.columnconfigure(0,weight=1)
        table.tag_configure('issue',foreground='#a33b24')
        table.bind('<Double-1>',lambda _,n=name:self.details(n))
        self.tables[name]=table

    def sort(self,table,key):
        reverse=getattr(table,'sort_key',None)==(key,False)
        def value(item):
            text=table.set(item,key)
            try:return (0,float(text))
            except ValueError:return (1,text.casefold())
        for index,item in enumerate(sorted(table.get_children(),key=value,reverse=reverse)):
            table.move(item,'',index)
        table.sort_key=(key,reverse)

    def _log(self,text):
        self.log.configure(state='normal');self.log.insert('end',text);self.log.see('end');self.log.configure(state='disabled')

    def _busy(self,busy):
        for widget in self.controls:
            widget.configure(state='disabled' if busy else 'readonly' if isinstance(widget,ttk.Combobox) else 'normal')
        for widget in (self.run_button,self.resume_button,self.load_button):widget.configure(state='disabled' if busy else 'normal')
        self.stop_button.configure(state='normal' if busy else 'disabled')
        self.force_button.configure(state='disabled')

    def run(self):
        if self.job:return
        try:
            out=RUNS/('rsi-pb-sp500-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
            command=scan_command([f for f,v in self.frame_vars.items() if v.get()],self.recent.get(),self.history.get(),self.feed.get(),self.membership.get(),output=out)
        except ValueError as exc:
            messagebox.showerror('Scan settings',str(exc),parent=self.root);return
        self._start(command,out)

    def _start(self,command,out):
        self._save_settings()
        summary=out/'summary.json'
        self.report_before=summary.stat().st_mtime_ns if summary.exists() else None
        self.output=out;self.manifest=None;self.patterns=[];self.coverage=[]
        self.source.set('Preparing scan · Loading local credentials and constituent membership…')
        self.reset_filters();self.refresh();self._busy(True)
        self.folder_button.configure(state='disabled')
        self.progress.configure(mode='indeterminate');self.progress.start(12)
        self.status.set('Starting scan…');self._log('\n— Starting scan —\n')
        self.job=ScanJob(command,self.events);self.job.start()

    def stop(self,force=False):
        if self.job:
            self.job.stop(force)
            self.stop_button.configure(state='disabled');self.force_button.configure(state='normal' if not force else 'disabled')
            self.status.set('Stopping… waiting for the current request to finish and partial results to save.' if not force else 'Forced stop. Completed candle batches remain available to resume.')

    def resume(self):
        if self.job:return
        folder=filedialog.askdirectory(title='Select interrupted run folder (contains manifest.json)',initialdir=str(RUNS if RUNS.exists() else ROOT),parent=self.root)
        if folder:
            path=Path(folder)
            if not (path/'manifest.json').is_file():messagebox.showerror('Resume','This folder has no manifest.json.',parent=self.root);return
            self._start(scan_command([],1,250,resume=path),path)

    def _poll(self):
        try:
            while True:
                kind,data=self.events.get_nowait()
                if kind=='log':self._log(data)
                elif kind=='error':self._log(data+'\n');self.status.set(data)
                elif kind=='event':self._event(data)
                elif kind=='exit':
                    self._finished(data)
                    if self.closing:return
        except queue.Empty:
            pass
        if self.root.winfo_exists():self.root.after(100,self._poll)

    def _event(self,data):
        if data['event']=='started':
            self.output=Path(data['output']);self.manifest=data['manifest']
            self._show_manifest()
            self.progress.stop();self.progress.configure(mode='determinate',maximum=len(self.manifest['universe']['symbols'])*len(self.manifest['timeframes']),value=0)
            self.status.set('Scanning closed candles…')
            self.folder_button.configure(state='normal')
        elif data['event']=='batch':
            self.coverage.extend(data['coverage']);self.patterns.extend(data['signals'])
            self.progress.configure(value=data['completed'])
            if not (self.job and self.job.stop_requested):
                self.status.set(f'{data["timeframe"]} · {data["completed"]:,} / {data["total"]:,} stock/timeframe combinations processed')
            self.refresh()

    def _show_manifest(self):
        if self.manifest:
            m=self.manifest
            cutoff=datetime.fromtimestamp(m['cutoff_ms']/1000,timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M %Z')
            self.source.set(f'{len(m["universe"]["symbols"])} securities · {", ".join(m["timeframes"])} · Alpaca {m["feed"].upper()} · Latest {m["recent_bars"]} closed candles · Cutoff {cutoff} · Membership {m["universe"]["as_of"]}')

    def _finished(self,code):
        stopped=self.job and self.job.stop_requested
        self.job=None;self.progress.stop();self.progress.configure(mode='determinate');self._busy(False)
        summary=self.output/'summary.json' if self.output else None
        fresh_report=summary and summary.exists() and summary.stat().st_mtime_ns!=getattr(self,'report_before',None)
        if fresh_report:
            try:self.load_results(self.output)
            except (OSError,ValueError,KeyError,TypeError) as exc:self._log(f'Unable to read final report: {type(exc).__name__}\n')
        elif self.output and (self.output/'manifest.json').exists():
            self._log('No updated final report saved. Use Resume run to rebuild it from saved candle batches.\n')
            if self.manifest:
                present={(r['symbol'],r['timeframe']) for r in self.coverage}
                self.coverage.extend(dict(symbol=s,timeframe=f,status='not scanned') for f in self.manifest['timeframes'] for s in self.manifest['universe']['symbols'] if (s,f) not in present)
                self.refresh()
        else:
            self.notebook.select(3)
        self.status.set('Stopped · Partial results shown. Resume to finish the original snapshot.' if stopped or code==130 else 'Complete · Results saved.' if code==0 else 'Scan incomplete · Check Coverage and Activity log; resume to retry.')
        if self.closing:
            self.root.destroy()

    def refresh(self):
        patterns=filtered_patterns(self.patterns,self.search.get().strip(),self.direction.get(),self.frame_filter.get(),self.unbreached.get(),self.latest.get())
        rows={'Stocks':stock_rows(patterns),'Patterns':sorted(patterns,key=lambda p:(p['symbol'],p['timeframe'],-p['confirmed_at'])),
              'Coverage':[r for r in self.coverage if self.search.get().strip().upper() in r['symbol'].upper() and (self.frame_filter.get()=='All' or r['timeframe']==self.frame_filter.get())]}
        for name,items in rows.items():
            table=self.tables[name];table.delete(*table.get_children());self.table_rows[name]={}
            for index,item in enumerate(items):
                iid=str(index);values=[]
                for key in table['columns']:
                    value=item.get(key,'')
                    values.append(('Yes' if value else 'No') if isinstance(value,bool) else value)
                table.insert('', 'end', iid=iid, values=values,tags=('issue',) if item.get('status') in ('unavailable','insufficient history','not scanned') else ())
                self.table_rows[name][iid]=item
        issues=sum(r['status'] in ('unavailable','insufficient history','not scanned') for r in self.coverage)
        self.counts.set(f'{len(rows["Stocks"]):,} visible qualified stocks    ·    {len(patterns):,} visible patterns    ·    {sum(r["status"]!="not scanned" for r in self.coverage):,} combinations processed    ·    {issues:,} coverage issues')

    def reset_filters(self):
        self.search.set('');self.direction.set('All');self.frame_filter.set('All');self.unbreached.set(False);self.latest.set(False)

    def load_dialog(self):
        folder=filedialog.askdirectory(title='Select saved results folder',initialdir=str(RUNS if RUNS.exists() else ROOT),parent=self.root)
        if folder:
            try:
                self.load_results(Path(folder));self.status.set('Saved results loaded · These reflect the original cutoff, not a fresh scan.')
            except (OSError,ValueError,KeyError,TypeError):messagebox.showerror('Load results','Select a scanner output folder containing manifest.json, signals.json and coverage.json.',parent=self.root)

    def load_results(self,folder):
        manifest=json.loads((folder/'manifest.json').read_text())
        patterns=json.loads((folder/'signals.json').read_text())
        coverage=json.loads((folder/'coverage.json').read_text())
        if not isinstance(manifest,dict) or not isinstance(patterns,list) or not isinstance(coverage,list):raise ValueError('Invalid report')
        # Validate the fields used by the UI before replacing the current view.
        for p in patterns:
            for key in ('symbol','direction','timeframe','confirmation_ny','confirmed_at','structural_breach','age_closed_candles'):p[key]
        for row in coverage:
            for key in ('symbol','timeframe','status'):row[key]
        manifest['universe']['symbols'];manifest['universe']['as_of'];manifest['cutoff_ms'];manifest['timeframes'];manifest['feed'];manifest['recent_bars']
        self.manifest=manifest;self.patterns=patterns;self.coverage=coverage;self.output=folder
        self._show_manifest();self.refresh();self.folder_button.configure(state='normal')
        self.progress.configure(maximum=max(1,len(coverage)),value=sum(r['status']!='not scanned' for r in coverage))

    def details(self,name):
        selection=self.tables[name].selection()
        if not selection:return
        row=self.table_rows[name][selection[0]]
        evidence=[p for p in self.patterns if p['symbol']==row['symbol']] if name=='Stocks' else row
        window=tk.Toplevel(self.root);window.title(f'{row["symbol"]} · Evidence');window.geometry('850x600')
        text=tk.Text(window,wrap='word');text.pack(side='left',fill='both',expand=True)
        bar=ttk.Scrollbar(window,command=text.yview);bar.pack(side='right',fill='y');text.configure(yscrollcommand=bar.set)
        text.insert('1.0',json.dumps(evidence,indent=2));text.configure(state='disabled')

    def copy_tickers(self):
        tickers=[self.tables['Stocks'].set(i,'symbol') for i in self.tables['Stocks'].get_children()]
        self.root.clipboard_clear();self.root.clipboard_append('\n'.join(tickers))
        self.status.set(f'Copied {len(tickers)} visible tickers.')

    def export(self):
        index=self.notebook.index(self.notebook.select())
        if index>=3:messagebox.showinfo('Export','Select Stocks, Patterns or Coverage to export a table.',parent=self.root);return
        name=('Stocks','Patterns','Coverage')[index];table=self.tables[name]
        path=filedialog.asksaveasfilename(title='Export visible rows',defaultextension='.csv',initialfile=name.lower()+'.csv',filetypes=[('CSV','*.csv')],parent=self.root)
        if path:
            try:
                with open(path,'w',newline='',encoding='utf-8-sig') as output:
                    writer=csv.writer(output);writer.writerow([table.heading(c,'text') for c in table['columns']]);writer.writerows(table.item(i,'values') for i in table.get_children())
                self.status.set(f'Exported {len(table.get_children())} rows to {path}')
            except OSError:messagebox.showerror('Export','Unable to write the selected file.',parent=self.root)

    def open_folder(self):
        if self.output and self.output.is_dir():
            try:
                if sys.platform=='win32':os.startfile(str(self.output))
                else:subprocess.Popen(['open' if sys.platform=='darwin' else 'xdg-open',str(self.output)])
            except OSError:messagebox.showerror('Open folder',f'Open this folder manually:\n{self.output}',parent=self.root)

    def _load_settings(self):
        try:
            settings=json.loads(SETTINGS.read_text())
            for frame,var in self.frame_vars.items():var.set(frame in settings['timeframes'])
            for key,var in [('recent',self.recent),('history',self.history),('feed',self.feed),('membership',self.membership)]:
                if key in settings:var.set(str(settings[key]))
        except (OSError,ValueError,KeyError,TypeError):pass

    def _save_settings(self):
        try:
            SETTINGS.parent.mkdir(parents=True,exist_ok=True)
            SETTINGS.write_text(json.dumps(dict(timeframes=[f for f,v in self.frame_vars.items() if v.get()],recent=self.recent.get(),history=self.history.get(),feed=self.feed.get(),membership=self.membership.get()),indent=2))
        except OSError:pass

    def _close(self):
        self._save_settings()
        if self.job:
            self.closing=True;self.stop()
            self.status.set('Closing after the scan stops and partial results save. Force stop is available if needed.')
        else:self.root.destroy()


def main():
    root=tk.Tk()
    app=ScannerUI(root)
    # PyCharm's process stop can send SIGTERM; request a graceful scan shutdown.
    if hasattr(signal,'SIGTERM'):
        signal.signal(signal.SIGTERM,lambda *_:root.after(0,app._close))
    root.mainloop()


if __name__=='__main__':
    main()
