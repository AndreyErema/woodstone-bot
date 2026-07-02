"""
Wood & Stone Construction LLC — Telegram Bot v6
AI-driven: owners type free text, Claude parses intent.
Subs: button-based shift tracking.
"""

import os, json, logging, base64, urllib.request
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters
import gspread
from google.oauth2.service_account import Credentials

# ============================================================
# CONFIG
# ============================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
GOOGLE_CREDS_FILE = os.environ.get("GOOGLE_CREDS_FILE", "credentials.json")
RECEIPTS_CHANNEL_ID = int(os.environ.get("RECEIPTS_CHANNEL_ID", "0"))
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

_owners_raw = os.environ.get("OWNERS_JSON", '{"76341596":"Jeremy"}')
try: OWNERS = {int(k):v for k,v in json.loads(_owners_raw).items()}
except: OWNERS = {76341596:"Jeremy"}

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ============================================================
# STATES
# ============================================================
(
    OWNER_MENU_ST, OWNER_FREE_TEXT,
    PHOTO_WAIT_RECEIPT, PHOTO_CONFIRM_RECEIPT,
    PHOTO_WAIT_INVOICE, PHOTO_CONFIRM_INVOICE,
    SUB_MENU_ST, SUB_SHIFT_SELECT, SUB_REGISTER_NAME,
    CONFIRM_ACTION,
) = range(10)

# ============================================================
# GOOGLE SHEETS
# ============================================================
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

def init(ss):
    gs(ss,"Projects",["Project ID","PO","Customer","Address","Description","Price","Status","Incom","Expenses","balance","Date","Posted by"])
    gs(ss,"Payments",["Project ID","PO","Amount","Data","Posted by","Check"])
    gs(ss,"Expenses",["Project ID","PO","Category","Amount","Description","Data","Posted by"])
    gs(ss,"Shifts",["Data","sub","ID","Project ID","Start","Finish","hours","PO"])
    gs(ss,"Payroll",["ID","Sub","Amount","Data","Posted by"])
    gs(ss,"Subs",["Telegram ID","name","Date added","status","Posted by","Rate"])
    gs(ss,"Journal",["Project ID","PO","Description","Data","Posted by"])
    gs(ss,"Customers",["Customer ID","Name","Address","phone","email","projects (PO)","Communication","Description","Data","Posted by"])
    gs(ss,"Summary",["Metric","Value","Note"])

# ============================================================
# HELPERS
# ============================================================
def is_owner(uid): return uid in OWNERS
def owner_name(uid): return OWNERS.get(uid,"?")

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
    ps=ss.worksheet("Projects");pays=ss.worksheet("Payments");exps=ss.worksheet("Expenses")
    tp=sum(float(r[2]) for r in pays.get_all_values()[1:] if str(r[0])==str(pid) and r[2])
    te=sum(float(r[3]) for r in exps.get_all_values()[1:] if str(r[0])==str(pid) and r[3])
    rn=find_proj_row(ps,pid)
    if rn>0: ps.update(f"H{rn}",[[tp]]);ps.update(f"I{rn}",[[te]]);ps.update(f"J{rn}",[[tp-te]])

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
# CLAUDE API
# ============================================================
def claude(prompt, image_b64=None, max_tokens=300):
    if not ANTHROPIC_API_KEY: return ""
    content = []
    if image_b64:
        content.append({"type":"image","source":{"type":"base64","media_type":"image/jpeg","data":image_b64}})
    content.append({"type":"text","text":prompt})
    body=json.dumps({"model":"claude-sonnet-4-20250514","max_tokens":max_tokens,
        "messages":[{"role":"user","content":content}]})
    req=urllib.request.Request("https://api.anthropic.com/v1/messages",data=body.encode(),
        headers={"Content-Type":"application/json","x-api-key":ANTHROPIC_API_KEY,"anthropic-version":"2023-06-01"})
    try:
        with urllib.request.urlopen(req,timeout=60) as resp:
            res=json.loads(resp.read())
        return "".join(b.get("text","") for b in res.get("content",[]) if b.get("type")=="text")
    except Exception as e: log.error(f"Claude API: {e}"); return ""

def ai_parse(text, projects, subs):
    """Send user message to Claude, get structured action."""
    proj_list = "\n".join(f"  ID:{p['id']} PO:{p['po']} Address:{p['addr']} Status:{p['status']}" for p in projects) or "  (none)"
    sub_list = "\n".join(f"  {s['name']} rate:${s['rate']}/hr" for s in subs) or "  (none)"

    prompt = f"""You are a construction project management bot assistant. Parse the user's message and return a JSON action.

CURRENT PROJECTS:
{proj_list}

CURRENT SUBS:
{sub_list}

AVAILABLE ACTIONS (return exactly one):
- {{"action":"create_project","po":"short name","customer":"client name","address":"full address","description":"work description","price":number_or_0}}
- {{"action":"payment","project_id":"N","amount":number,"note":"optional note"}}
- {{"action":"expense","project_id":"N","category":"Materials|Subcontractor|Equipment Rental|Other","amount":number,"description":"what"}}
- {{"action":"change_status","project_id":"N","status":"New|In Progress|On Hold|Completed"}}
- {{"action":"journal","project_id":"N","description":"update text"}}
- {{"action":"pay_sub","sub_name":"name","amount":number}}
- {{"action":"set_rate","sub_name":"name","rate":number}}
- {{"action":"record_hours","sub_name":"name","hours":number,"project_id":"N or empty","date":"YYYY-MM-DD or today"}}
- {{"action":"show_summary"}}
- {{"action":"show_project","project_id":"N"}}
- {{"action":"list_projects"}}
- {{"action":"add_customer","name":"","address":"","phone":"","email":"","communication":"sms/messenger/email"}}
- {{"action":"scan_receipt"}} (user wants to scan a store receipt - expense)
- {{"action":"scan_invoice"}} (user wants to scan a client invoice/check - payment)
- {{"action":"unknown","reply":"your helpful response"}}

RULES:
- Match projects by ID, PO, address fragment, or street number. "773" matches "773 Central Heights". "Falling Leaf" matches "2090 Falling Leaf".
- Match subs by name (partial ok). "Родя" = "Родя", "Дане" = "Даня".
- If user mentions receiving money/check/deposit FROM client → payment. If user mentions spending/buying/purchasing → expense.
- "закрой проект" or "close project" → status Completed.
- Currency: always USD.
- If you can't determine the action, return unknown with a helpful reply.
- Return ONLY valid JSON, no markdown, no explanation.

USER MESSAGE: {text}"""

    result = claude(prompt)
    log.info(f"AI parse: {result}")
    try:
        # Clean potential markdown
        r = result.strip()
        if r.startswith("```"): r = r.split("\n",1)[1] if "\n" in r else r[3:]
        if r.endswith("```"): r = r[:-3]
        r = r.strip()
        return json.loads(r)
    except: return {"action":"unknown","reply":"Не понял. Попробуй переформулировать."}

def scan_amount(fp, receipt_type="receipt"):
    prompt = "This is a store receipt. Find the TOTAL amount paid (not subtotal, not tax)." if receipt_type=="receipt" else "This is a handwritten invoice/check from a client. Find the TOTAL amount."
    prompt += " Return ONLY the number. Example: 2004.14"
    try:
        with open(fp,"rb") as f: img=base64.b64encode(f.read()).decode()
        ans=claude(prompt, image_b64=img, max_tokens=50)
        return float(ans.strip().replace("$","").replace(",",""))
    except: return 0.0

async def send_to_channel(ctx, fid, caption):
    if not RECEIPTS_CHANNEL_ID: return ""
    try:
        msg=await ctx.bot.send_photo(chat_id=RECEIPTS_CHANNEL_ID,photo=fid,caption=caption)
        if msg: return f"https://t.me/c/{str(RECEIPTS_CHANNEL_ID).replace('-100','')}/{msg.message_id}"
    except Exception as e: log.error(f"Channel: {e}")
    return ""

# ============================================================
# SUMMARY
# ============================================================
def build_summary(ss):
    now=datetime.now()
    # Find Monday of current week
    mon=now-timedelta(days=now.weekday())
    fri=mon+timedelta(days=4)
    mon_s=mon.strftime("%Y-%m-%d"); fri_s=fri.strftime("%Y-%m-%d")

    ps=ss.worksheet("Projects").get_all_values()
    tp=tr=te=0;ac=cc=0
    for r in ps[1:]:
        try:
            p=float(r[5]) if r[5] else 0;rc=float(r[7]) if len(r)>7 and r[7] else 0
            ex=float(r[8]) if len(r)>8 and r[8] else 0;st=r[6] if len(r)>6 else ""
            tp+=p;tr+=rc;te+=ex
            if st=="Completed":cc+=1
            else:ac+=1
        except:pass
    wpay=wexp=wzp=0;ebc={};zbs={};tzp=0
    for r in ss.worksheet("Payments").get_all_values()[1:]:
        try:
            ds=r[3][:10]
            if mon_s<=ds<=fri_s: wpay+=float(r[2])
        except:pass
    for r in ss.worksheet("Expenses").get_all_values()[1:]:
        try:
            cat=r[2];amt=float(r[3]);ds=r[5][:10]
            ebc[cat]=ebc.get(cat,0)+amt
            if mon_s<=ds<=fri_s: wexp+=amt
        except:pass
    for r in ss.worksheet("Payroll").get_all_values()[1:]:
        try:
            s=r[1];a=float(r[2]);ds=r[3][:10]
            tzp+=a;zbs[s]=zbs.get(s,0)+a
            if mon_s<=ds<=fri_s: wzp+=a
        except:pass
    bal=tr-te-tzp;co=tp-tr
    t=f"📊 *WEEKLY SUMMARY*\n📅 {mon.strftime('%m/%d')} — {fri.strftime('%m/%d/%Y')} (Mon-Fri)\n\n"
    t+=f"*This week:*\n💰 Received: ${wpay:,.2f}\n💸 Expenses: ${wexp:,.2f}\n👷 Payroll: ${wzp:,.2f}\n"
    t+=f"\n*Total:*\n🏗 Active: {ac}\n💵 Total value: ${tp:,.2f}\n✅ Received: ${tr:,.2f}\n"
    t+=f"💸 Expenses: ${te:,.2f}\n👷 Payroll: ${tzp:,.2f}\n📈 Owed by clients: ${co:,.2f}\n💰 Balance: ${bal:,.2f}\n"
    if ebc:
        t+="\n*By category:*\n"
        for c,a in sorted(ebc.items()): t+=f"  • {c}: ${a:,.2f}\n"
    if zbs:
        t+="\n*Payroll by sub:*\n"
        for s,a in sorted(zbs.items()): t+=f"  • {s}: ${a:,.2f}\n"
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

# ============================================================
# MENUS
# ============================================================
OWNER_KB = ReplyKeyboardMarkup([
    ["📋 New project","💰 Payment"],
    ["💸 Expense","🧾 Scan receipt"],
    ["📄 Scan invoice","📝 Journal"],
    ["🔄 Status","📊 Project info"],
    ["📈 Summary","💵 Pay sub"],
    ["🟢 Start shift","🔴 End shift"],
    ["📁 Archive"],
], resize_keyboard=True)

SUB_KB = ReplyKeyboardMarkup([["🟢 Start shift"],["🔴 End shift"]], resize_keyboard=True)

# ============================================================
# /START
# ============================================================
async def start(update, ctx):
    uid=update.effective_user.id
    if is_owner(uid):
        await update.message.reply_text(f"👋 {owner_name(uid)}!\n🏗 Wood & Stone Tracker\n\nType anything or use buttons:", reply_markup=OWNER_KB)
        return OWNER_MENU_ST
    try:
        ss=get_ss();info=sub_info(ss,uid)
        if info:
            if info["status"]=="Approved":
                await update.message.reply_text(f"👋 {info['name']}!", reply_markup=SUB_KB); return SUB_MENU_ST
            elif info["status"]=="Pending":
                await update.message.reply_text("⏳ Waiting for approval."); return ConversationHandler.END
            else:
                await update.message.reply_text("⛔ Access denied."); return ConversationHandler.END
    except:pass
    await update.message.reply_text("👋 Not registered.\nEnter your name to register as a sub:", reply_markup=ReplyKeyboardRemove())
    return SUB_REGISTER_NAME

# ============================================================
# OWNER: BUTTON HANDLER + FREE TEXT
# ============================================================
async def owner_handler(update, ctx):
    uid=update.effective_user.id
    if not is_owner(uid): return OWNER_MENU_ST
    t=update.message.text

    # Button shortcuts
    if t=="📋 New project": await update.message.reply_text("📋 Describe the project in one message:\nExample: Nancy Stalnaker, 102 E 5th Ave Watauga TN, landscaping and patio, 30000", reply_markup=ReplyKeyboardRemove()); return OWNER_FREE_TEXT
    if t=="💰 Payment": await update.message.reply_text("💰 Type: project name/number + amount\nExample: 773 received check 5188", reply_markup=ReplyKeyboardRemove()); return OWNER_FREE_TEXT
    if t=="💸 Expense": await update.message.reply_text("💸 Type: project + amount + what\nExample: Falling Leaf materials 2300 lumber", reply_markup=ReplyKeyboardRemove()); return OWNER_FREE_TEXT
    if t=="🧾 Scan receipt":
        return await show_proj_btns(update, ctx, PHOTO_WAIT_RECEIPT, "🧾 Select project for receipt:")
    if t=="📄 Scan invoice":
        return await show_proj_btns(update, ctx, PHOTO_WAIT_INVOICE, "📄 Select project for invoice:")
    if t=="📝 Journal": await update.message.reply_text("📝 Type: project + description\nExample: Falling Leaf - framing done", reply_markup=ReplyKeyboardRemove()); return OWNER_FREE_TEXT
    if t=="🔄 Status": await update.message.reply_text("🔄 Type: project + new status\nExample: close project 4261\nor: Cookie Loop in progress", reply_markup=ReplyKeyboardRemove()); return OWNER_FREE_TEXT
    if t=="📊 Project info": await update.message.reply_text("📊 Type project name/number\nExample: show 773", reply_markup=ReplyKeyboardRemove()); return OWNER_FREE_TEXT
    if t=="📈 Summary": return await do_summary(update, ctx)
    if t=="💵 Pay sub": await update.message.reply_text("💵 Type: sub name + amount\nExample: paid Батя 1500", reply_markup=ReplyKeyboardRemove()); return OWNER_FREE_TEXT
    if t=="🟢 Start shift": return await owner_shift_start(update, ctx)
    if t=="🔴 End shift": return await owner_shift_end(update, ctx)
    if t=="📁 Archive": return await do_archive(update, ctx)

    # Free text → AI parse
    return await process_free_text(update, ctx, t)

async def free_text_handler(update, ctx):
    return await process_free_text(update, ctx, update.message.text)

async def process_free_text(update, ctx, text):
    uid=update.effective_user.id; uname=owner_name(uid)
    now_s=datetime.now().strftime("%Y-%m-%d %H:%M")

    try: ss=get_ss(); projs=all_projects(ss.worksheet("Projects")); subs=approved_subs(ss)
    except Exception as e:
        log.error(f"DB: {e}"); await update.message.reply_text("❌ Database error.", reply_markup=OWNER_KB); return OWNER_MENU_ST

    await update.message.reply_text("⏳ Processing...")
    action=ai_parse(text, projs, subs)
    act=action.get("action","unknown")

    try:
        if act=="create_project":
            ps=ss.worksheet("Projects"); pid=next_pid(ps)
            po=action.get("po",""); cust=action.get("customer",""); addr=action.get("address",""); desc=action.get("description",""); price=float(action.get("price",0))
            if not po and addr: po=addr[:30]
            ps.append_row([pid,po,cust,addr,desc,price,"New",0,0,0,now_s,uname], value_input_option="USER_ENTERED")
            # Add customer if provided
            if cust:
                try:
                    cs=ss.worksheet("Customers"); cid=next_cid(cs)
                    cs.append_row([cid,cust,addr,"","",po,"","",now_s,uname], value_input_option="USER_ENTERED")
                except:pass
            await update.message.reply_text(f"✅ Project created!\n🆔 {pid}\n📋 PO: {po}\n👤 Customer: {cust or '—'}\n📍 {addr}\n📝 {desc or '—'}\n💵 ${price:,.2f}", reply_markup=OWNER_KB)

        elif act=="payment":
            pid=action.get("project_id",""); amt=float(action.get("amount",0)); note=action.get("note","")
            po=proj_po(ss.worksheet("Projects"),pid)
            ss.worksheet("Payments").append_row([pid,po,amt,now_s,f"{uname} {note}".strip(),""], value_input_option="USER_ENTERED")
            update_totals(ss,pid); update_summary_sheet(ss)
            await update.message.reply_text(f"✅ Payment recorded!\n🆔 {pid} ({po})\n💰 ${amt:,.2f}", reply_markup=OWNER_KB)

        elif act=="expense":
            pid=action.get("project_id",""); cat=action.get("category","Materials"); amt=float(action.get("amount",0)); desc=action.get("description","")
            po=proj_po(ss.worksheet("Projects"),pid)
            ss.worksheet("Expenses").append_row([pid,po,cat,amt,desc,now_s,uname], value_input_option="USER_ENTERED")
            update_totals(ss,pid); update_summary_sheet(ss)
            await update.message.reply_text(f"✅ Expense recorded!\n🆔 {pid} ({po})\n📂 {cat}: ${amt:,.2f}\n📝 {desc or '—'}", reply_markup=OWNER_KB)

        elif act=="change_status":
            pid=action.get("project_id",""); ns=action.get("status","")
            ps=ss.worksheet("Projects"); rn=find_proj_row(ps,pid)
            if rn>0:
                ps.update(f"G{rn}",[[ns]])
                po=proj_po(ps,pid)
                ss.worksheet("Journal").append_row([pid,po,f"Status → {ns}",now_s,uname], value_input_option="USER_ENTERED")
                await update.message.reply_text(f"✅ {pid} ({po}) → {ns}", reply_markup=OWNER_KB)
            else: await update.message.reply_text("❌ Project not found.", reply_markup=OWNER_KB)

        elif act=="journal":
            pid=action.get("project_id",""); desc=action.get("description","")
            po=proj_po(ss.worksheet("Projects"),pid)
            ss.worksheet("Journal").append_row([pid,po,desc,now_s,uname], value_input_option="USER_ENTERED")
            await update.message.reply_text(f"✅ Journal entry added!\n🆔 {pid} ({po})\n📝 {desc}", reply_markup=OWNER_KB)

        elif act=="pay_sub":
            sn=action.get("sub_name",""); amt=float(action.get("amount",0))
            ss.worksheet("Payroll").append_row(["",sn,amt,now_s,uname], value_input_option="USER_ENTERED")
            update_summary_sheet(ss)
            await update.message.reply_text(f"✅ Paid!\n👷 {sn}: ${amt:,.2f}", reply_markup=OWNER_KB)

        elif act=="set_rate":
            sn=action.get("sub_name",""); rate=float(action.get("rate",0))
            sh=ss.worksheet("Subs")
            for i,r in enumerate(sh.get_all_values()[1:],2):
                if r[1].lower().strip()==sn.lower().strip():
                    sh.update(f"F{i}",[[rate]])
                    await update.message.reply_text(f"✅ {sn} rate → ${rate}/hr", reply_markup=OWNER_KB); break
            else:
                await update.message.reply_text(f"❌ Sub '{sn}' not found.", reply_markup=OWNER_KB)

        elif act=="record_hours":
            sn=action.get("sub_name",""); hrs=float(action.get("hours",0)); pid=action.get("project_id","")
            dt=action.get("date",""); 
            if not dt or dt=="today": dt=datetime.now().strftime("%Y-%m-%d")
            po=proj_po(ss.worksheet("Projects"),pid) if pid else ""
            # Find sub's telegram ID
            sub_tid=""
            for r in ss.worksheet("Subs").get_all_values()[1:]:
                if r[1].lower().strip()==sn.lower().strip(): sub_tid=r[0]; break
            ss.worksheet("Shifts").append_row([dt,sn,sub_tid,pid,f"{dt} 09:00",f"{dt} {9+hrs:.0f}:00",hrs,po], value_input_option="USER_ENTERED")
            # Auto payroll if rate exists
            rate=0
            for s in approved_subs(ss):
                if s["name"].lower().strip()==sn.lower().strip(): rate=s["rate"]; break
            if rate>0:
                pay=round(hrs*rate,2)
                ss.worksheet("Payroll").append_row(["",sn,pay,f"{dt} (manual)",uname], value_input_option="USER_ENTERED")
                await update.message.reply_text(f"✅ {sn}: {hrs}h on {dt}\n💵 Auto-pay: ${pay:,.2f} ({hrs}h × ${rate}/hr)", reply_markup=OWNER_KB)
            else:
                await update.message.reply_text(f"✅ {sn}: {hrs}h on {dt}\n⚠️ No rate set — payroll not calculated.", reply_markup=OWNER_KB)
            update_summary_sheet(ss)

        elif act=="show_summary":
            return await do_summary(update, ctx)

        elif act=="show_project":
            pid=action.get("project_id","")
            return await do_show_project(update, ctx, ss, pid)

        elif act=="list_projects":
            projs=active_projects(ss.worksheet("Projects"))
            if not projs: await update.message.reply_text("📭 No active projects.", reply_markup=OWNER_KB)
            else:
                t="📋 *Active projects:*\n\n"
                for p in projs: t+=f"• {p['id']} — {p['po']} [{p['status']}]\n"
                await update.message.reply_text(t, parse_mode="Markdown", reply_markup=OWNER_KB)

        elif act=="add_customer":
            cs=ss.worksheet("Customers"); cid=next_cid(cs)
            cs.append_row([cid,action.get("name",""),action.get("address",""),action.get("phone",""),action.get("email",""),"",action.get("communication",""),"",now_s,uname], value_input_option="USER_ENTERED")
            await update.message.reply_text(f"✅ Customer added: {action.get('name','')}", reply_markup=OWNER_KB)

        elif act=="scan_receipt":
            return await show_proj_btns(update, ctx, PHOTO_WAIT_RECEIPT, "🧾 Select project:")

        elif act=="scan_invoice":
            return await show_proj_btns(update, ctx, PHOTO_WAIT_INVOICE, "📄 Select project:")

        else:
            reply=action.get("reply","I don't understand. Try rephrasing.")
            await update.message.reply_text(reply, reply_markup=OWNER_KB)

    except Exception as e:
        log.error(f"Action error: {e}"); await update.message.reply_text(f"❌ Error: {e}", reply_markup=OWNER_KB)

    return OWNER_MENU_ST

# ============================================================
# SHOW PROJECT
# ============================================================
async def do_show_project(update, ctx, ss, pid):
    ps=ss.worksheet("Projects"); rn=find_proj_row(ps,pid)
    if rn<1: await update.message.reply_text("❌ Not found.", reply_markup=OWNER_KB); return OWNER_MENU_ST
    r=ps.row_values(rn)
    price=float(r[5]) if len(r)>5 and r[5] else 0
    inc=float(r[7]) if len(r)>7 and r[7] else 0
    exp=float(r[8]) if len(r)>8 and r[8] else 0
    bal=float(r[9]) if len(r)>9 and r[9] else 0
    t=f"📊 *Project {r[0]}*\n📋 PO: {r[1]}\n👤 Customer: {r[2] if len(r)>2 else '—'}\n📍 {r[3] if len(r)>3 else '—'}\n📝 {r[4] if len(r)>4 else '—'}\n🔄 {r[6] if len(r)>6 else 'New'}\n\n"
    t+=f"💵 Price: ${price:,.2f}\n✅ Received: ${inc:,.2f}\n💸 Expenses: ${exp:,.2f}\n📈 Owed: ${price-inc:,.2f}\n💰 Balance: ${bal:,.2f}\n"
    pp=[x for x in ss.worksheet("Payments").get_all_values()[1:] if str(x[0])==str(pid)]
    if pp:
        t+="\n*Payments:*\n"
        for p in pp[-5:]: t+=f"  • {p[3]} — ${float(p[2]):,.2f} ({p[4]})\n"
    pe=[x for x in ss.worksheet("Expenses").get_all_values()[1:] if str(x[0])==str(pid)]
    if pe:
        t+="\n*Expenses:*\n"
        for e in pe[-5:]: t+=f"  • {e[5]} — ${float(e[3]):,.2f} [{e[2]}] ({e[6]})\n"
    pj=[x for x in ss.worksheet("Journal").get_all_values()[1:] if str(x[0])==str(pid)]
    if pj:
        t+="\n*Journal:*\n"
        for j in pj[-5:]: t+=f"  • {j[3]} — {j[2]} ({j[4]})\n"
    await update.message.reply_text(t, parse_mode="Markdown", reply_markup=OWNER_KB)
    return OWNER_MENU_ST

async def do_summary(update, ctx):
    try:
        ss=get_ss(); t=build_summary(ss)
        await update.message.reply_text(t, parse_mode="Markdown", reply_markup=OWNER_KB)
    except: await update.message.reply_text("❌ Error.", reply_markup=OWNER_KB)
    return OWNER_MENU_ST

async def do_archive(update, ctx):
    try:
        arch=[p for p in all_projects(get_ss().worksheet("Projects")) if p["status"]=="Completed"]
    except: arch=[]
    if not arch: await update.message.reply_text("📁 Empty.", reply_markup=OWNER_KB)
    else:
        t="📁 *Archive:*\n"+"\n".join(f"• {p['id']} — {p['po']}" for p in arch)
        await update.message.reply_text(t, parse_mode="Markdown", reply_markup=OWNER_KB)
    return OWNER_MENU_ST

# ============================================================
# SCAN RECEIPT / INVOICE (photo flow)
# ============================================================
async def show_proj_btns(update, ctx, state, msg):
    try:
        projs=active_projects(get_ss().worksheet("Projects"))
    except: await update.message.reply_text("❌ Error.", reply_markup=OWNER_KB); return OWNER_MENU_ST
    if not projs: await update.message.reply_text("📭 No projects.", reply_markup=OWNER_KB); return OWNER_MENU_ST
    btns=[[InlineKeyboardButton(f"{p['id']} — {p['po']}", callback_data=f"proj_{p['id']}")] for p in projs]
    btns.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(btns))
    return state

async def receipt_proj_select(update, ctx):
    q=update.callback_query; await q.answer()
    if q.data=="cancel": await q.edit_message_text("❌"); await q.message.reply_text("Menu:", reply_markup=OWNER_KB); return OWNER_MENU_ST
    ctx.user_data["scan_pid"]=q.data.replace("proj_",""); ctx.user_data["scan_type"]="receipt"
    await q.edit_message_text("🧾 Send receipt photo:"); return PHOTO_WAIT_RECEIPT

async def invoice_proj_select(update, ctx):
    q=update.callback_query; await q.answer()
    if q.data=="cancel": await q.edit_message_text("❌"); await q.message.reply_text("Menu:", reply_markup=OWNER_KB); return OWNER_MENU_ST
    ctx.user_data["scan_pid"]=q.data.replace("proj_",""); ctx.user_data["scan_type"]="invoice"
    await q.edit_message_text("📄 Send invoice photo:"); return PHOTO_WAIT_INVOICE

async def photo_received(update, ctx):
    if not update.message.photo: await update.message.reply_text("❌ Send photo."); return PHOTO_WAIT_RECEIPT if ctx.user_data.get("scan_type")=="receipt" else PHOTO_WAIT_INVOICE
    await update.message.reply_text("⏳ Scanning...")
    ph=update.message.photo[-1]; f=await ctx.bot.get_file(ph.file_id)
    fp=f"/tmp/scan_{ph.file_id}.jpg"; await f.download_to_drive(fp)
    ctx.user_data["scan_fp"]=fp; ctx.user_data["scan_fid"]=ph.file_id
    total=scan_amount(fp, ctx.user_data.get("scan_type","receipt"))
    ctx.user_data["scan_amt"]=total
    confirm_state = PHOTO_CONFIRM_RECEIPT if ctx.user_data.get("scan_type")=="receipt" else PHOTO_CONFIRM_INVOICE
    if total>0:
        btns=[[InlineKeyboardButton(f"✅ ${total:,.2f}",callback_data="scanc_yes")],[InlineKeyboardButton("✏️ Manual",callback_data="scanc_manual")],[InlineKeyboardButton("❌",callback_data="cancel")]]
        await update.message.reply_text(f"Amount: *${total:,.2f}*\nCorrect?", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))
    else:
        btns=[[InlineKeyboardButton("✏️ Enter manually",callback_data="scanc_manual")],[InlineKeyboardButton("❌",callback_data="cancel")]]
        await update.message.reply_text("Couldn't read amount.\nEnter manually:", reply_markup=InlineKeyboardMarkup(btns))
    return confirm_state

async def scan_confirm(update, ctx):
    q=update.callback_query; await q.answer()
    if q.data=="cancel":
        try:os.remove(ctx.user_data.get("scan_fp",""))
        except:pass
        await q.edit_message_text("❌"); await q.message.reply_text("Menu:", reply_markup=OWNER_KB); return OWNER_MENU_ST
    if q.data=="scanc_manual": await q.edit_message_text("✏️ Enter amount:"); return PHOTO_CONFIRM_RECEIPT if ctx.user_data.get("scan_type")=="receipt" else PHOTO_CONFIRM_INVOICE
    if q.data=="scanc_yes":
        return await save_scan(q, ctx, ctx.user_data.get("scan_amt",0), cb=True)

async def scan_manual_amt(update, ctx):
    try: amt=float(update.message.text.replace(",","").replace("$",""))
    except: await update.message.reply_text("❌ Number!"); return PHOTO_CONFIRM_RECEIPT if ctx.user_data.get("scan_type")=="receipt" else PHOTO_CONFIRM_INVOICE
    return await save_scan(update, ctx, amt, cb=False)

async def save_scan(src, ctx, amt, cb=True):
    pid=ctx.user_data.get("scan_pid",""); fid=ctx.user_data.get("scan_fid",""); stype=ctx.user_data.get("scan_type","receipt")
    uid = src.from_user.id if cb else src.effective_user.id
    uname=owner_name(uid); now_s=datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        ss=get_ss(); po=proj_po(ss.worksheet("Projects"),pid)
        link=""
        if fid:
            emoji="🧾" if stype=="receipt" else "📄"
            link=await send_to_channel(ctx,fid,f"{emoji} {pid} — {po}\n💵 ${amt:,.2f}\n📅 {now_s}\n👤 {uname}")
        if stype=="receipt":
            ss.worksheet("Expenses").append_row([pid,po,"Materials",amt,f"Receipt: {link}" if link else "Receipt",now_s,uname], value_input_option="USER_ENTERED")
        else:
            ss.worksheet("Payments").append_row([pid,po,amt,now_s,uname,link], value_input_option="USER_ENTERED")
        update_totals(ss,pid); update_summary_sheet(ss)
        emoji="🧾" if stype=="receipt" else "📄"
        msg=f"✅ {emoji} Recorded!\n🆔 {pid} ({po})\n💵 ${amt:,.2f}"
        if cb: await src.edit_message_text(msg); await src.message.reply_text("Menu:", reply_markup=OWNER_KB)
        else: await src.message.reply_text(msg, reply_markup=OWNER_KB)
    except Exception as e:
        log.error(f"Scan save: {e}")
        if cb: await src.edit_message_text("❌ Error."); await src.message.reply_text("Menu:", reply_markup=OWNER_KB)
        else: await src.message.reply_text("❌ Error.", reply_markup=OWNER_KB)
    try:os.remove(ctx.user_data.get("scan_fp",""))
    except:pass
    for k in ["scan_pid","scan_fp","scan_fid","scan_amt","scan_type"]: ctx.user_data.pop(k,None)
    return OWNER_MENU_ST

# ============================================================
# OWNER SHIFTS
# ============================================================
async def owner_shift_start(update, ctx):
    uid=update.effective_user.id
    try:
        ss=get_ss(); a=active_shift(ss,uid)
        if a: await update.message.reply_text(f"⚠️ Already on shift!\n📍 {a['po']}\n🕐 {a['start']}", reply_markup=OWNER_KB); return OWNER_MENU_ST
        projs=active_projects(ss.worksheet("Projects"))
    except: await update.message.reply_text("❌", reply_markup=OWNER_KB); return OWNER_MENU_ST
    if not projs: await update.message.reply_text("📭 No projects.", reply_markup=OWNER_KB); return OWNER_MENU_ST
    btns=[[InlineKeyboardButton(f"{p['id']} — {p['po']}",callback_data=f"oshift_{p['id']}")] for p in projs]
    btns.append([InlineKeyboardButton("❌",callback_data="cancel")])
    await update.message.reply_text("📍 Project:", reply_markup=InlineKeyboardMarkup(btns))
    return CONFIRM_ACTION

async def oshift_cb(update, ctx):
    q=update.callback_query; await q.answer()
    if q.data=="cancel": await q.edit_message_text("❌"); await q.message.reply_text("Menu:", reply_markup=OWNER_KB); return OWNER_MENU_ST
    pid=q.data.replace("oshift_",""); uid=q.from_user.id; name=owner_name(uid)
    now_s=datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        ss=get_ss(); po=proj_po(ss.worksheet("Projects"),pid)
        ss.worksheet("Shifts").append_row([now_s[:10],name,str(uid),pid,now_s,"","",po], value_input_option="USER_ENTERED")
        await q.edit_message_text(f"🟢 Shift started!\n👤 {name}\n📍 {pid} — {po}\n🕐 {now_s}")
    except: await q.edit_message_text("❌")
    await q.message.reply_text("Menu:", reply_markup=OWNER_KB); return OWNER_MENU_ST

async def owner_shift_end(update, ctx):
    uid=update.effective_user.id; name=owner_name(uid)
    try:
        ss=get_ss(); a=active_shift(ss,uid)
        if not a: await update.message.reply_text("❌ No active shift.", reply_markup=OWNER_KB); return OWNER_MENU_ST
        sh=ss.worksheet("Shifts"); now=datetime.now(); now_s=now.strftime("%Y-%m-%d %H:%M")
        start=datetime.strptime(a["start"],"%Y-%m-%d %H:%M"); hrs=round((now-start).total_seconds()/3600,2)
        sh.update(f"F{a['row']}",[[now_s]]); sh.update(f"G{a['row']}",[[hrs]])
        await update.message.reply_text(f"🔴 Shift ended!\n👤 {name}\n📍 {a['po']}\n⏱ {hrs}h", reply_markup=OWNER_KB)
    except Exception as e:
        log.error(f"Shift end: {e}"); await update.message.reply_text("❌", reply_markup=OWNER_KB)
    return OWNER_MENU_ST

# ============================================================
# SUB: REGISTRATION + SHIFTS
# ============================================================
async def sub_register(update, ctx):
    name=update.message.text.strip(); uid=update.effective_user.id; now_s=datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        ss=get_ss(); ss.worksheet("Subs").append_row([str(uid),name,now_s,"Pending","",0], value_input_option="USER_ENTERED")
    except: await update.message.reply_text("❌ Error."); return ConversationHandler.END
    await update.message.reply_text(f"✅ Application sent!\n👷 {name}\n⏳ Waiting for approval.")
    for oid in OWNERS:
        try:
            btns=[[InlineKeyboardButton("✅ Approve",callback_data=f"approve_{uid}")],[InlineKeyboardButton("❌ Reject",callback_data=f"reject_{uid}")]]
            await ctx.bot.send_message(chat_id=oid, text=f"🆕 New sub: {name}\n🆔 {uid}", reply_markup=InlineKeyboardMarkup(btns))
        except:pass
    return ConversationHandler.END

async def approve_sub(update, ctx):
    q=update.callback_query; await q.answer()
    if not is_owner(q.from_user.id): return
    sub_uid=q.data.split("_",1)[1]; approved="approve" in q.data
    try:
        ss=get_ss(); info=sub_info(ss,int(sub_uid))
        if info:
            ss.worksheet("Subs").update(f"D{info['row']}",[[("Approved" if approved else "Rejected")]])
            if approved:
                await q.edit_message_text(f"✅ {info['name']} approved!")
                try: await ctx.bot.send_message(chat_id=int(sub_uid),text="✅ Approved! Type /start")
                except:pass
            else:
                await q.edit_message_text(f"❌ {info['name']} rejected.")
    except:await q.edit_message_text("❌ Error.")

async def sub_handler(update, ctx):
    uid=update.effective_user.id; t=update.message.text
    if t=="🟢 Start shift":
        try:
            ss=get_ss(); a=active_shift(ss,uid)
            if a: await update.message.reply_text(f"⚠️ Already on shift!\n📍 {a['po']}\n🕐 {a['start']}", reply_markup=SUB_KB); return SUB_MENU_ST
            projs=active_projects(ss.worksheet("Projects"))
        except: await update.message.reply_text("❌", reply_markup=SUB_KB); return SUB_MENU_ST
        if not projs: await update.message.reply_text("📭 No projects.", reply_markup=SUB_KB); return SUB_MENU_ST
        btns=[[InlineKeyboardButton(f"{p['id']} — {p['po']}",callback_data=f"sshift_{p['id']}")] for p in projs]
        btns.append([InlineKeyboardButton("❌",callback_data="scancel")])
        await update.message.reply_text("📍 Project:", reply_markup=InlineKeyboardMarkup(btns))
        return SUB_SHIFT_SELECT
    elif t=="🔴 End shift":
        try:
            ss=get_ss(); a=active_shift(ss,uid)
            if not a: await update.message.reply_text("❌ No active shift.", reply_markup=SUB_KB); return SUB_MENU_ST
            sh=ss.worksheet("Shifts"); now=datetime.now(); now_s=now.strftime("%Y-%m-%d %H:%M")
            start=datetime.strptime(a["start"],"%Y-%m-%d %H:%M"); hrs=round((now-start).total_seconds()/3600,2)
            sh.update(f"F{a['row']}",[[now_s]]); sh.update(f"G{a['row']}",[[hrs]])
            info=sub_info(ss,uid); name=info["name"] if info else "?"
            # Auto payroll
            rate=info["rate"] if info else 0
            if rate>0:
                pay=round(hrs*rate,2)
                ss.worksheet("Payroll").append_row(["",name,pay,now_s,"auto"], value_input_option="USER_ENTERED")
                await update.message.reply_text(f"🔴 Shift ended!\n👷 {name}\n📍 {a['po']}\n⏱ {hrs}h\n💵 ${pay:,.2f} ({hrs}h × ${rate}/hr)", reply_markup=SUB_KB)
            else:
                await update.message.reply_text(f"🔴 Shift ended!\n👷 {name}\n📍 {a['po']}\n⏱ {hrs}h", reply_markup=SUB_KB)
            for oid in OWNERS:
                try: await ctx.bot.send_message(chat_id=oid,text=f"🔴 {name} ended shift\n📍 {a['po']}\n⏱ {hrs}h")
                except:pass
        except Exception as e: log.error(f"Sub shift end: {e}"); await update.message.reply_text("❌", reply_markup=SUB_KB)
    return SUB_MENU_ST

async def sub_shift_cb(update, ctx):
    q=update.callback_query; await q.answer()
    if q.data=="scancel": await q.edit_message_text("❌"); await q.message.reply_text("Menu:", reply_markup=SUB_KB); return SUB_MENU_ST
    pid=q.data.replace("sshift_",""); uid=q.from_user.id; now_s=datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        ss=get_ss(); info=sub_info(ss,uid); name=info["name"] if info else "?"
        po=proj_po(ss.worksheet("Projects"),pid)
        ss.worksheet("Shifts").append_row([now_s[:10],name,str(uid),pid,now_s,"","",po], value_input_option="USER_ENTERED")
        await q.edit_message_text(f"🟢 Shift started!\n👷 {name}\n📍 {pid} — {po}\n🕐 {now_s}")
        for oid in OWNERS:
            try: await ctx.bot.send_message(chat_id=oid,text=f"🟢 {name} started shift\n📍 {pid} — {po}\n🕐 {now_s}")
            except:pass
    except: await q.edit_message_text("❌")
    await q.message.reply_text("Menu:", reply_markup=SUB_KB); return SUB_MENU_ST

async def cancel_cmd(update, ctx):
    uid=update.effective_user.id
    kb=OWNER_KB if is_owner(uid) else SUB_KB
    await update.message.reply_text("❌ Cancelled.", reply_markup=kb)
    return OWNER_MENU_ST if is_owner(uid) else SUB_MENU_ST

# ============================================================
# MAIN
# ============================================================
def main():
    try: ss=get_ss(); init(ss); log.info("✅ Sheets OK")
    except Exception as e: log.error(f"Init: {e}")

    app=Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(approve_sub, pattern="^(approve_|reject_)"))

    ch=ConversationHandler(
        entry_points=[CommandHandler("start",start)],
        states={
            OWNER_MENU_ST:[MessageHandler(filters.TEXT & ~filters.COMMAND, owner_handler), MessageHandler(filters.PHOTO, photo_received)],
            OWNER_FREE_TEXT:[MessageHandler(filters.TEXT & ~filters.COMMAND, free_text_handler)],
            PHOTO_WAIT_RECEIPT:[CallbackQueryHandler(receipt_proj_select,pattern="^proj_"),CallbackQueryHandler(lambda u,c:scan_confirm(u,c),pattern="^cancel$"),MessageHandler(filters.PHOTO,photo_received)],
            PHOTO_WAIT_INVOICE:[CallbackQueryHandler(invoice_proj_select,pattern="^proj_"),CallbackQueryHandler(lambda u,c:scan_confirm(u,c),pattern="^cancel$"),MessageHandler(filters.PHOTO,photo_received)],
            PHOTO_CONFIRM_RECEIPT:[CallbackQueryHandler(scan_confirm,pattern="^scanc_"),CallbackQueryHandler(scan_confirm,pattern="^cancel$"),MessageHandler(filters.TEXT & ~filters.COMMAND,scan_manual_amt)],
            PHOTO_CONFIRM_INVOICE:[CallbackQueryHandler(scan_confirm,pattern="^scanc_"),CallbackQueryHandler(scan_confirm,pattern="^cancel$"),MessageHandler(filters.TEXT & ~filters.COMMAND,scan_manual_amt)],
            SUB_MENU_ST:[MessageHandler(filters.TEXT & ~filters.COMMAND,sub_handler)],
            SUB_SHIFT_SELECT:[CallbackQueryHandler(sub_shift_cb,pattern="^sshift_"),CallbackQueryHandler(sub_shift_cb,pattern="^scancel$")],
            SUB_REGISTER_NAME:[MessageHandler(filters.TEXT & ~filters.COMMAND,sub_register)],
            CONFIRM_ACTION:[CallbackQueryHandler(oshift_cb,pattern="^oshift_"),CallbackQueryHandler(oshift_cb,pattern="^cancel$")],
        },
        fallbacks=[CommandHandler("cancel",cancel_cmd),CommandHandler("start",start)],
    )
    app.add_handler(ch)
    log.info("🚀 Bot started!")
    app.run_polling()

if __name__=="__main__": main()
