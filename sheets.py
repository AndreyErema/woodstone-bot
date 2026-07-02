"""
Google Sheets client, schema init, and data access helpers.
"""

from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials

from config import SPREADSHEET_ID, GOOGLE_CREDS_FILE, is_owner, owner_name, log

import os, json

SCOPES = ["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]

def get_creds():
    cj = os.environ.get("GOOGLE_CREDS_JSON","")
    if cj: return Credentials.from_service_account_info(json.loads(cj), scopes=SCOPES)
    return Credentials.from_service_account_file(GOOGLE_CREDS_FILE, scopes=SCOPES)

def get_ss():
    return gspread.authorize(get_creds()).open_by_key(SPREADSHEET_ID)

def gs(ss, title, headers):
    try: return ss.worksheet(title)
    except gspread.exceptions.WorksheetNotFound:
        sh = ss.add_worksheet(title=title, rows=1000, cols=len(headers))
        sh.append_row(headers, value_input_option="USER_ENTERED"); return sh

CUSTOMERS_HEADERS = ["Customer ID","Name","Address","phone","email","projects (PO)","Communication","Description","Data","Posted by"]

def repair_customers_sheet(ss):
    """Migrate an older Customers sheet (created before the Communication
    column existed) to the current schema, inserting the missing column
    without disturbing existing data."""
    try:
        cs=ss.worksheet("Customers")
        data=cs.get_all_values()
        if not data: return
        headers=data[0]
        if headers==CUSTOMERS_HEADERS or "Communication" in headers: return
        old_headers=[h for h in CUSTOMERS_HEADERS if h!="Communication"]
        if headers[:len(old_headers)]!=old_headers:
            log.error(f"Customers sheet headers unrecognized, skipping repair: {headers}")
            return
        comm_idx=CUSTOMERS_HEADERS.index("Communication")
        new_rows=[CUSTOMERS_HEADERS]
        for r in data[1:]:
            r=list(r)+[""]*max(0,len(old_headers)-len(r))
            new_rows.append(r[:comm_idx]+[""]+r[comm_idx:])
        cs.clear()
        if cs.col_count<len(CUSTOMERS_HEADERS): cs.resize(cols=len(CUSTOMERS_HEADERS))
        cs.update("A1",new_rows,value_input_option="USER_ENTERED")
        log.info("Customers sheet repaired: added Communication column")
    except Exception as e:
        log.error(f"Customers repair: {e}")

def init(ss):
    gs(ss,"Projects",["Project ID","PO","Customer","Address","Description","Price","Status","Incom","Expenses","balance","Date","Posted by"])
    gs(ss,"Payments",["Project ID","PO","Amount","Data","Posted by","Check"])
    gs(ss,"Expenses",["Project ID","PO","Category","Amount","Description","Data","Posted by"])
    gs(ss,"Shifts",["Data","sub","ID","Project ID","Start","Finish","hours","PO"])
    gs(ss,"Payroll",["ID","Sub","Amount","Data","Posted by"])
    gs(ss,"Subs",["Telegram ID","name","Date added","status","Posted by","Rate"])
    gs(ss,"Journal",["Project ID","PO","Description","Data","Posted by"])
    gs(ss,"Customers",CUSTOMERS_HEADERS)
    gs(ss,"Summary",["Metric","Value","Note"])
    repair_customers_sheet(ss)

# ============================================================
# HELPERS
# ============================================================
def sub_info(ss, uid):
    try:
        for i,r in enumerate(ss.worksheet("Subs").get_all_values()[1:], 2):
            if str(r[0]).strip()==str(uid): return {"name":r[1],"status":r[3],"row":i,"rate":float(r[5]) if len(r)>5 and r[5] else 0}
    except: pass
    return None

def user_name(update):
    uid=update.effective_user.id
    if is_owner(uid): return owner_name(uid)
    try:
        i=sub_info(get_ss(),uid)
        if i: return i["name"]
    except: pass
    return update.effective_user.first_name or "?"

def next_pid(ps):
    recs=ps.get_all_values()
    if len(recs)<=1: return "0001"
    mx=max((int(r[0]) for r in recs[1:] if r[0].isdigit()),default=0)
    return str(mx+1).zfill(4)

def next_cid(cs):
    recs=cs.get_all_values()
    if len(recs)<=1: return "C001"
    mx=max((int(r[0].replace("C","")) for r in recs[1:] if r[0].startswith("C") and r[0][1:].isdigit()),default=0)
    return f"C{mx+1:03d}"

def active_projects(ps):
    recs=ps.get_all_values()
    if len(recs)<=1: return []
    return [{"id":r[0],"po":r[1],"addr":r[3],"status":r[6] if len(r)>6 else "New"} for r in recs[1:] if (r[6] if len(r)>6 else "New")!="Completed"]

def all_projects(ps):
    recs=ps.get_all_values()
    if len(recs)<=1: return []
    return [{"id":r[0],"po":r[1],"customer":r[2] if len(r)>2 else "","addr":r[3] if len(r)>3 else "","desc":r[4] if len(r)>4 else "","price":r[5] if len(r)>5 else "","status":r[6] if len(r)>6 else "New"} for r in recs[1:]]

def find_proj_row(ps,pid):
    for i,r in enumerate(ps.get_all_values()):
        if str(r[0])==str(pid): return i+1
    return -1

def proj_addr(ps,pid):
    for r in ps.get_all_values()[1:]:
        if str(r[0])==str(pid): return r[3] if len(r)>3 else ""
    return ""

def proj_po(ps,pid):
    for r in ps.get_all_values()[1:]:
        if str(r[0])==str(pid): return r[1] if len(r)>1 else ""
    return ""

def update_totals(ss,pid):
    """Set live Google Sheets formulas for Incom/Expenses/balance (Sheets computes these, not the bot)."""
    ps=ss.worksheet("Projects")
    rn=find_proj_row(ps,pid)
    if rn>0:
        ps.update(f"H{rn}",[[f'=SUMIF(Payments!$A:$A,$A{rn},Payments!$C:$C)']], value_input_option="USER_ENTERED")
        ps.update(f"I{rn}",[[f'=SUMIF(Expenses!$A:$A,$A{rn},Expenses!$D:$D)']], value_input_option="USER_ENTERED")
        ps.update(f"J{rn}",[[f'=H{rn}-I{rn}']], value_input_option="USER_ENTERED")

def approved_subs(ss):
    try:
        recs=ss.worksheet("Subs").get_all_values()
        return [{"name":r[1],"rate":float(r[5]) if len(r)>5 and r[5] else 0} for r in recs[1:] if len(r)>3 and r[3]=="Approved"]
    except: return []

def active_shift(ss,uid):
    try:
        for i,r in enumerate(ss.worksheet("Shifts").get_all_values()[1:],2):
            if str(r[2]).strip()==str(uid) and r[4] and (len(r)<6 or not r[5]):
                return {"row":i,"pid":r[3],"po":r[7] if len(r)>7 else "","start":r[4]}
    except: pass
    return None

# ============================================================
# SUMMARY
# ============================================================
def build_summary(ss):
    """Weekly chat summary: account balance, this week's expenses by
    category, this week's hours/pay per sub, and this week's hours per
    project. Plain text, no icons/markdown."""
    now=datetime.now()
    mon=now-timedelta(days=now.weekday())
    fri=mon+timedelta(days=4)
    mon_s=mon.strftime("%Y-%m-%d"); fri_s=fri.strftime("%Y-%m-%d")

    proj_rows=ss.worksheet("Projects").get_all_values()[1:]

    bal=0
    proj_po={}
    for r in proj_rows:
        try: bal+=float(r[9]) if len(r)>9 and r[9] else 0
        except: pass
        if r and r[0]: proj_po[r[0]]=r[1] if len(r)>1 else ""

    ebc={}
    for r in ss.worksheet("Expenses").get_all_values()[1:]:
        try:
            cat=r[2]; amt=float(r[3]); ds=r[5][:10]
            if mon_s<=ds<=fri_s: ebc[cat]=ebc.get(cat,0)+amt
        except: pass

    rates={s["name"]:s["rate"] for s in approved_subs(ss)}
    hrs_by_sub={}; hrs_by_proj={}
    for r in ss.worksheet("Shifts").get_all_values()[1:]:
        try:
            ds=r[0][:10]
            if not (mon_s<=ds<=fri_s): continue
            sn=r[1] if len(r)>1 else ""; hrs=float(r[6]) if len(r)>6 and r[6] else 0
            pid=r[3] if len(r)>3 else ""
            if sn: hrs_by_sub[sn]=hrs_by_sub.get(sn,0)+hrs
            if pid: hrs_by_proj[pid]=hrs_by_proj.get(pid,0)+hrs
        except: pass

    t=f"Неделя: {mon.strftime('%m/%d')} — {fri.strftime('%m/%d')} (Mon-Fri)\n\n"
    t+=f"Balance: ${bal:,.2f}\n"

    if ebc:
        t+="\nExpenses:\n"
        for c,a in sorted(ebc.items()): t+=f"  {c}: ${a:,.2f}\n"

    if hrs_by_sub:
        t+="\nHours/$:\n"
        for sn,h in sorted(hrs_by_sub.items()):
            rate=rates.get(sn,0)
            if rate>0: t+=f"  {sn}: {h:g}h — к оплате ${h*rate:,.2f}\n"
            else: t+=f"  {sn}: {h:g}h — ставка не задана\n"

    if hrs_by_proj:
        t+="\nProjects Hours:\n"
        for pid,h in sorted(hrs_by_proj.items(), key=lambda kv: proj_po.get(kv[0],kv[0])):
            t+=f" {proj_po.get(pid,pid)} — {h:g}h\n"

    return t

def update_summary_sheet(ss):
    try:
        now=datetime.now()
        ps_d=ss.worksheet("Projects").get_all_values()
        pay_d=ss.worksheet("Payments").get_all_values()
        exp_d=ss.worksheet("Expenses").get_all_values()
        zp_d=ss.worksheet("Payroll").get_all_values()
        tp=tr=te=0;ac=cc=0
        for r in ps_d[1:]:
            try:
                p=float(r[5]) if r[5] else 0;rc=float(r[7]) if len(r)>7 and r[7] else 0
                ex=float(r[8]) if len(r)>8 and r[8] else 0;st=r[6] if len(r)>6 else ""
                tp+=p;tr+=rc;te+=ex
                if st=="Completed":cc+=1
                else:ac+=1
            except:pass
        ebc={};zbs={};tzp=0
        for r in exp_d[1:]:
            try:ebc[r[2]]=ebc.get(r[2],0)+float(r[3])
            except:pass
        for r in zp_d[1:]:
            try:tzp+=float(r[2]);zbs[r[1]]=zbs.get(r[1],0)+float(r[2])
            except:pass
        bal=tr-te-tzp;co=tp-tr
        # Weekly columns
        all_dates=[]
        for r in pay_d[1:]:
            try:all_dates.append(r[3][:10])
            except:pass
        for r in exp_d[1:]:
            try:all_dates.append(r[5][:10])
            except:pass
        for r in zp_d[1:]:
            try:all_dates.append(r[3][:10])
            except:pass
        if not all_dates:all_dates=[now.strftime("%Y-%m-%d")]
        try:min_dt=datetime.strptime(min(all_dates),"%Y-%m-%d")
        except:min_dt=now
        def wk_start(dt):return dt-timedelta(days=dt.weekday())
        weeks=[];w=wk_start(now)
        while w>=wk_start(min_dt):weeks.append((w,w+timedelta(days=4)));w-=timedelta(days=7) # Mon-Fri
        def inw(ds,s,e):
            try:return s.strftime("%Y-%m-%d")<=ds[:10]<=e.strftime("%Y-%m-%d")
            except:return False
        cats=sorted(ebc.keys());subs_n=sorted(zbs.keys())
        wdata=[]
        for s,e in weeks:
            wp=we=wz=0;wec={};wzs={}
            for r in pay_d[1:]:
                try:
                    if inw(r[3],s,e):wp+=float(r[2])
                except:pass
            for r in exp_d[1:]:
                try:
                    if inw(r[5],s,e):a=float(r[3]);c=r[2];we+=a;wec[c]=wec.get(c,0)+a
                except:pass
            for r in zp_d[1:]:
                try:
                    if inw(r[3],s,e):a=float(r[2]);sn=r[1];wz+=a;wzs[sn]=wzs.get(sn,0)+a
                except:pass
            wdata.append({"l":f"{s.strftime('%m/%d')}-{e.strftime('%m/%d')}","p":wp,"e":we,"z":wz,"ec":wec,"zs":wzs})
        labels=["SUMMARY","","--- TOTALS ---","Active","Completed","Total value","Received (all)","Expenses (all)","Payroll (all)","Owed by clients","BALANCE","","--- WEEKLY (Mon-Fri) ---","Received","Expenses","Payroll","","Expenses by category:"]
        for c in cats:labels.append(f"  {c}")
        labels+=["","Payroll by sub:"]
        for s in subs_n:labels.append(f"  {s}")
        nc=2+len(weeks);mx=[]
        for lb in labels:
            row=[lb]
            v=""
            if lb=="SUMMARY":v=f"Updated: {now.strftime('%Y-%m-%d %H:%M')}"
            elif lb=="Active":v=ac
            elif lb=="Completed":v=cc
            elif lb=="Total value":v=tp
            elif lb=="Received (all)":v=tr
            elif lb=="Expenses (all)":v=te
            elif lb=="Payroll (all)":v=tzp
            elif lb=="Owed by clients":v=co
            elif lb=="BALANCE":v=bal
            elif lb=="--- WEEKLY (Mon-Fri) ---":v="Current"
            elif lb.startswith("  ") and lb.strip() in ebc:v=ebc[lb.strip()]
            elif lb.startswith("  ") and lb.strip() in zbs:v=zbs[lb.strip()]
            row.append(v if v!="" else "")
            for wd in wdata:
                if lb=="--- WEEKLY (Mon-Fri) ---":row.append(wd["l"])
                elif lb=="Received":row.append(wd["p"] if wd["p"] else "")
                elif lb=="Expenses":row.append(wd["e"] if wd["e"] else "")
                elif lb=="Payroll":row.append(wd["z"] if wd["z"] else "")
                elif lb.startswith("  ") and lb.strip() in ebc:row.append(wd["ec"].get(lb.strip(),""))
                elif lb.startswith("  ") and lb.strip() in zbs:row.append(wd["zs"].get(lb.strip(),""))
                else:row.append("")
            mx.append(row)
        try:sh=ss.worksheet("Summary");sh.clear()
        except:sh=ss.add_worksheet(title="Summary",rows=50,cols=nc)
        if sh.col_count<nc:sh.resize(cols=nc)
        sh.update("A1",mx,value_input_option="USER_ENTERED")
    except Exception as e:log.error(f"Summary: {e}")

# Timesheet / Project Hours are no longer built here — see sheet_deploy.py.
# They're 100% Google Sheets formulas (period cells + QUERY/SEQUENCE/BYROW/
# BYCOL), set up once via the /deploy_sheet command and never touched again
# by the bot.
