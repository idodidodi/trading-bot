"""Native Signal Service window. Scanner and API start only on an explicit click."""
import argparse
import json
from pathlib import Path
import webbrowser
from signal_desktop_model import Controller,app_directory,read_settings,save_settings,service
from app_version import VERSION


def window(home,smoke=False):
    import tkinter as tk
    from tkinter import ttk,messagebox
    root=tk.Tk();root.title('Signal Service');root.geometry('940x680');root.minsize(820,600)
    style=ttk.Style(root);style.theme_use('clam');style.configure('TLabel',font=('Segoe UI',10))
    style.configure('Title.TLabel',font=('Segoe UI',22,'bold'));style.configure('TButton',padding=8)
    outer=ttk.Frame(root,padding=20);outer.pack(fill='both',expand=True)
    ttk.Label(outer,text='Signal Service',style='Title.TLabel').pack(anchor='w')
    ttk.Label(outer,text=f'Market signals, subscribers and Telegram · v{VERSION}').pack(anchor='w',pady=(4,16))
    controller=Controller(home);states={r:tk.StringVar(value='Stopped') for r in ('worker','api')}
    def action(fn):
        try:fn()
        except Exception as exc:messagebox.showerror('Signal Service',str(exc) if isinstance(exc,(ValueError,RuntimeError)) else type(exc).__name__,parent=root)
    for role,title in [('worker','Signal scanner & Telegram'),('api','Subscriber API')]:
        row=ttk.Frame(outer);row.pack(fill='x',pady=4)
        ttk.Label(row,text=title,width=28).pack(side='left');ttk.Label(row,textvariable=states[role]).pack(side='left')
        ttk.Button(row,text='Start',command=lambda r=role:action(lambda:controller.start(r))).pack(side='right')
        ttk.Button(row,text='Stop',command=lambda r=role:controller.stop(r)).pack(side='right',padx=6)
    tabs=ttk.Notebook(outer);tabs.pack(fill='both',expand=True,pady=12)
    settings=ttk.Frame(tabs,padding=16);overview=ttk.Frame(tabs,padding=16)
    tabs.add(overview,text='Signals & subscribers');tabs.add(settings,text='Configuration')
    ttk.Label(overview,text='Signals stay here. Trading accounts and orders belong to Trading Executor.',wraplength=800).pack(anchor='w',pady=10)
    ttk.Label(overview,text='Use the dashboard to inspect findings, manage assets, and review Telegram settings.\nThe subscription API serves registered projects without sharing your database.',wraplength=800).pack(anchor='w',pady=10)
    def dashboard():
        port=read_settings(controller.config).get('PORT','8080');webbrowser.open('http://127.0.0.1:'+port)
    ttk.Button(overview,text='Open signal dashboard',command=lambda:action(dashboard)).pack(anchor='w',pady=10)
    ttk.Label(overview,text='No service starts automatically when this window opens.\nKeep any existing production scanner stopped before starting its replacement.',wraplength=800).pack(anchor='w',pady=10)
    ttk.Label(overview,text=f'Private configuration and logs: {home}',wraplength=800).pack(anchor='w',pady=10)
    register=ttk.LabelFrame(overview,text='Register a subscriber',padding=12);register.pack(fill='x',pady=10)
    project=tk.StringVar(value='trading-executor');notices=tk.BooleanVar(value=False)
    ttk.Label(register,text='Project name').pack(anchor='w');ttk.Entry(register,textvariable=project,width=40).pack(anchor='w',pady=4)
    ttk.Checkbutton(register,text='Allow this project to report execution facts for Telegram',variable=notices).pack(anchor='w')
    def create_subscriber():
        import urllib.request
        values=read_settings(controller.config)
        request=urllib.request.Request('http://127.0.0.1:'+values['SIGNAL_API_PORT']+'/v1/projects',
            data=json.dumps(dict(project=project.get().strip(),mode='live',filters={},execution_notices=notices.get())).encode(),
            headers={'Authorization':'Bearer '+values.get('SIGNAL_ADMIN_TOKEN',''),'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=5) as response:result=json.load(response)
        popup=tk.Toplevel(root);popup.title('Subscriber created');popup.geometry('600x190')
        ttk.Label(popup,text='Copy this token into the other application. It is shown only once.',wraplength=560).pack(padx=20,pady=16)
        token=ttk.Entry(popup,width=70);token.insert(0,result['token']);token.configure(state='readonly');token.pack(padx=20)
        def copy():root.clipboard_clear();root.clipboard_append(result['token'])
        ttk.Button(popup,text='Copy token',command=copy).pack(pady=12)
    ttk.Button(register,text='Create live subscriber',command=lambda:action(create_subscriber)).pack(anchor='w',pady=8)

    saved=read_settings(controller.config);fields={}
    labels=[('TRADING_DATABASE_URL','Signal Postgres connection',True),('TELEGRAM_BOT_TOKEN','Telegram bot token',True),
            ('TELEGRAM_CHAT_ID','Telegram General chat',False),('TELEGRAM_SENDER_ID','Existing Telegram sender identity',False),
            ('SIGNAL_ADMIN_TOKEN','Subscriber admin token',True),('PORT','Dashboard port',False),('SIGNAL_API_PORT','Subscriber API port',False)]
    for i,(key,label,secret) in enumerate(labels):
        ttk.Label(settings,text=label).grid(row=i,column=0,sticky='w',pady=4)
        fields[key]=tk.StringVar(value=saved.get(key,''));ttk.Entry(settings,textvariable=fields[key],show='•' if secret else '',width=55).grid(row=i,column=1,sticky='ew',padx=(14,0),pady=4)
    settings.columnconfigure(1,weight=1)
    flags={}
    for i,(key,label) in enumerate([('DRY_RUN','Telegram dry run'),('DIVERGENCE_CHASING_ENABLED','Chasing alerts'),('TELEGRAM_CHECKLIST_ENABLED','Telegram callbacks'),('TELEGRAM_GROUP_DISMISSAL_ENABLED','Group dismissal')],7):
        flags[key]=tk.BooleanVar(value=saved.get(key)=='true');ttk.Checkbutton(settings,text=label,variable=flags[key]).grid(row=i,column=0,columnspan=2,sticky='w')
    def save():
        if any(controller.running(r) for r in states):raise ValueError('Stop both services before changing configuration')
        values={k:v.get().strip() for k,v in fields.items()}|{k:str(v.get()).lower() for k,v in flags.items()}
        save_settings(controller.config,values)
    ttk.Button(settings,text='Save configuration',command=lambda:action(save)).grid(row=11,column=1,sticky='e',pady=8)
    ttk.Label(settings,text='Market-data provider credentials go in providers.env in this app’s private directory.\nPreserve existing Telegram flags and identity during cutover.',wraplength=800).grid(row=12,column=0,columnspan=2,sticky='w')
    closing=False
    def close():
        nonlocal closing
        if any(controller.running(r) for r in states):
            if not messagebox.askyesno('Stop services?','Closing stops this app’s scanner and subscriber API. Stop and close?',parent=root):return
            closing=True
            for role in states:controller.stop(role)
        else:root.destroy()
    root.protocol('WM_DELETE_WINDOW',close)
    def refresh():
        if closing and not any(controller.running(r) for r in states):root.destroy();return
        for role,var in states.items():var.set(controller.status(role))
        root.after(500,refresh)
    if smoke:
        root.update();assert not any(controller.running(r) for r in states)
        assert not home.exists();root.after(150,root.destroy)
    else:refresh()
    root.mainloop()
    return dict(app='Signal Service',version=VERSION,window_created=True,worker_started=False)


def main():
    p=argparse.ArgumentParser();p.add_argument('--home',type=Path);p.add_argument('--service',choices=['worker','api'])
    p.add_argument('--smoke-test',action='store_true');p.add_argument('--smoke-report',type=Path);a=p.parse_args()
    if a.service:
        if a.home is None:p.error('--service requires --home')
        return service(a.home,a.service)
    if a.smoke_test and a.home is None:p.error('--smoke-test requires isolated --home')
    result=window(a.home or app_directory(),a.smoke_test)
    if a.smoke_report:a.smoke_report.write_text(json.dumps(result),encoding='utf-8')


if __name__=='__main__':main()
