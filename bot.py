"""
Wood & Stone Construction LLC — Telegram Project Tracker Bot v4
💰 Задаток/Приход = платёж вручную или скан инвойса
💸 Записать расход = расход вручную или скан чека
"""

import os
import json
import logging
import base64
import urllib.request
from datetime import datetime, timedelta
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, ReplyKeyboardRemove
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters
)
import gspread
from google.oauth2.service_account import Credentials

# ============================================================
# НАСТРОЙКИ
# ============================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "YOUR_SPREADSHEET_ID_HERE")
GOOGLE_CREDS_FILE = os.environ.get("GOOGLE_CREDS_FILE", "credentials.json")
RECEIPTS_CHANNEL_ID = int(os.environ.get("RECEIPTS_CHANNEL_ID", "-1003389113880"))

ALLOWED_USERS = {
    76341596: "Jeremy",
}

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================
# СОСТОЯНИЯ
# ============================================================
(
    MAIN_MENU,
    NEW_PROJECT_ADDRESS, NEW_PROJECT_DESCRIPTION, NEW_PROJECT_PRICE,
    PAY_SELECT_PROJECT, PAY_METHOD, PAY_AMOUNT, PAY_PHOTO, PAY_CONFIRM,
    EXP_SELECT_PROJECT, EXP_CATEGORY, EXP_METHOD, EXP_AMOUNT, EXP_PHOTO, EXP_CONFIRM, EXP_DESCRIPTION,
    STATUS_DESC_SELECT_PROJECT, STATUS_DESC_TEXT,
    CHANGE_STATUS_SELECT, CHANGE_STATUS_VALUE,
    VIEW_STATUS_SELECT,
    ADD_SUB_NAME, SUB_PAY_SELECT_SUB, SUB_PAY_AMOUNT,
) = range(24)

# ============================================================
# GOOGLE SHEETS
# ============================================================

SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]

def get_google_creds():
    creds_json = os.environ.get("GOOGLE_CREDS_JSON", "")
    if creds_json:
        return Credentials.from_service_account_info(json.loads(creds_json), scopes=SCOPES)
    return Credentials.from_service_account_file(GOOGLE_CREDS_FILE, scopes=SCOPES)

def get_spreadsheet():
    client = gspread.authorize(get_google_creds())
    return client.open_by_key(SPREADSHEET_ID)

def get_or_create_sheet(ss, title, headers):
    try:
        return ss.worksheet(title)
    except gspread.exceptions.WorksheetNotFound:
        sh = ss.add_worksheet(title=title, rows=1000, cols=len(headers))
        sh.append_row(headers, value_input_option="USER_ENTERED")
        return sh

def init_sheets(ss):
    p = get_or_create_sheet(ss, "Проекты", ["Project ID","Адрес","Описание","Цена","Статус","Получено","Расходы","Баланс","Дата создания","Создал"])
    get_or_create_sheet(ss, "Платежи", ["Project ID","Адрес","Сумма","Дата","Кто записал"])
    get_or_create_sheet(ss, "Расходы", ["Project ID","Адрес","Категория","Сумма","Описание","Дата","Кто записал"])
    get_or_create_sheet(ss, "Обновления", ["Project ID","Адрес","Текст","Дата","Кто записал"])
    get_or_create_sheet(ss, "Сабы", ["Имя","Дата добавления","Кто добавил"])
    get_or_create_sheet(ss, "ЗП", ["Субподрядчик","Сумма","Дата","Кто записал"])
    get_or_create_sheet(ss, "Сводка", ["Показатель","Значение","Примечание"])
    return p

# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ
# ============================================================

def check_access(update): return update.effective_user.id in ALLOWED_USERS
def get_user_name(update): return ALLOWED_USERS.get(update.effective_user.id, "Unknown")
def get_user_name_by_id(uid): return ALLOWED_USERS.get(uid, "Unknown")

def get_next_project_id(ps):
    recs = ps.get_all_values()
    if len(recs) <= 1: return "0001"
    mx = 0
    for r in recs[1:]:
        try:
            v = int(r[0])
            if v > mx: mx = v
        except: pass
    return str(mx+1).zfill(4)

def get_active_projects(ps):
    recs = ps.get_all_values()
    if len(recs) <= 1: return []
    out = []
    for r in recs[1:]:
        try:
            st = r[4] if len(r)>4 else "Новый"
            if st != "Завершён": out.append({"id":r[0],"address":r[1],"status":st})
        except: pass
    return out

def get_all_projects(ps):
    recs = ps.get_all_values()
    if len(recs) <= 1: return []
    out = []
    for r in recs[1:]:
        try: out.append({"id":r[0],"address":r[1],"status":r[4] if len(r)>4 else "Новый"})
        except: pass
    return out

def find_project_row(ps, pid):
    for i, r in enumerate(ps.get_all_values()):
        if r[0] == pid: return i+1
    return -1

def update_project_totals(ss, pid):
    ps = ss.worksheet("Проекты")
    pays = ss.worksheet("Платежи")
    exps = ss.worksheet("Расходы")
    tp = sum(float(r[2]) for r in pays.get_all_values()[1:] if r[0]==pid and r[2])
    te = sum(float(r[3]) for r in exps.get_all_values()[1:] if r[0]==pid and r[3])
    rn = find_project_row(ps, pid)
    if rn == -1: return
    ps.update(f"F{rn}", [[tp]])
    ps.update(f"G{rn}", [[te]])
    ps.update(f"H{rn}", [[tp - te]])

def get_project_address(ps, pid):
    for r in ps.get_all_values()[1:]:
        if r[0] == pid: return r[1]
    return ""

def get_subs_list(ss):
    try:
        recs = ss.worksheet("Сабы").get_all_values()
        return [r[0] for r in recs[1:] if r[0]] if len(recs)>1 else []
    except: return []

# ============================================================
# OCR — CLAUDE API
# ============================================================

def scan_receipt_amount(file_path):
    """Распознать TOTAL с чека (магазин)."""
    return _claude_read_amount(file_path,
        "This is a photo of a store receipt. Find the TOTAL amount paid (not subtotal, not tax). Return ONLY the number. Example: 2004.14")

def scan_invoice_amount(file_path):
    """Распознать сумму с инвойса/рукописного чека от клиента."""
    return _claude_read_amount(file_path,
        "This is a photo of a handwritten invoice, receipt, or check from a client. Find the TOTAL amount paid or due. It may be handwritten. Return ONLY the number. Example: 5000.00")

def _claude_read_amount(file_path, prompt):
    try:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key: return 0.0
        with open(file_path, "rb") as f:
            img = base64.b64encode(f.read()).decode("utf-8")
        body = json.dumps({
            "model": "claude-sonnet-4-20250514", "max_tokens": 100,
            "messages": [{"role":"user","content":[
                {"type":"image","source":{"type":"base64","media_type":"image/jpeg","data":img}},
                {"type":"text","text":prompt}
            ]}]
        })
        req = urllib.request.Request("https://api.anthropic.com/v1/messages",
            data=body.encode("utf-8"),
            headers={"Content-Type":"application/json","x-api-key":api_key,"anthropic-version":"2023-06-01"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        answer = "".join(b.get("text","") for b in result.get("content",[]) if b.get("type")=="text")
        logger.info(f"Claude OCR: {answer}")
        return float(answer.strip().replace("$","").replace(",",""))
    except Exception as e:
        logger.error(f"OCR error: {e}")
        return 0.0

# ============================================================
# ОТПРАВКА ФОТО В КАНАЛ
# ============================================================

async def send_photo_to_channel(context, file_id, caption):
    """Отправить фото в канал чеков, вернуть ссылку."""
    try:
        msg = await context.bot.send_photo(chat_id=RECEIPTS_CHANNEL_ID, photo=file_id, caption=caption)
        if msg and msg.message_id:
            ch = str(RECEIPTS_CHANNEL_ID).replace("-100","")
            return f"https://t.me/c/{ch}/{msg.message_id}"
    except Exception as e:
        logger.error(f"Channel error: {e}")
    return ""

# ============================================================
# СВОДКА (для таблицы и бота)
# ============================================================

def build_summary(ss):
    now = datetime.now()
    wa = now - timedelta(days=7)
    was = wa.strftime("%Y-%m-%d")
    ps = ss.worksheet("Проекты").get_all_values()
    tp=tr=te_all=0; ac=cc=0
    for r in ps[1:]:
        try:
            p=float(r[3]) if r[3] else 0; rc=float(r[5]) if len(r)>5 and r[5] else 0
            ex=float(r[6]) if len(r)>6 and r[6] else 0; st=r[4] if len(r)>4 else ""
            tp+=p; tr+=rc; te_all+=ex
            if st=="Завершён": cc+=1
            else: ac+=1
        except: pass
    co = tp - tr
    wpay=0
    for r in ss.worksheet("Платежи").get_all_values()[1:]:
        try:
            if r[3][:10]>=was: wpay+=float(r[2])
        except: pass
    wexp=0; ebc={}; webc={}
    for r in ss.worksheet("Расходы").get_all_values()[1:]:
        try:
            cat=r[2] if len(r)>2 else "Прочее"; amt=float(r[3]) if len(r)>3 else 0; ds=r[5][:10] if len(r)>5 else ""
            ebc[cat]=ebc.get(cat,0)+amt
            if ds>=was: wexp+=amt; webc[cat]=webc.get(cat,0)+amt
        except: pass
    tzp=wzp=0; zbs={}
    for r in ss.worksheet("ЗП").get_all_values()[1:]:
        try:
            s=r[0] if r[0] else "?"; a=float(r[1]) if len(r)>1 else 0; ds=r[2][:10] if len(r)>2 else ""
            tzp+=a; zbs[s]=zbs.get(s,0)+a
            if ds>=was: wzp+=a
        except: pass
    bal = tr - te_all - tzp
    t = f"📊 *СВОДКА*\n📅 {wa.strftime('%m/%d')} — {now.strftime('%m/%d/%Y')}\n\n"
    t += f"*— За неделю —*\n💰 Получено: ${wpay:,.2f}\n💸 Расходы: ${wexp:,.2f}\n👷 ЗП: ${wzp:,.2f}\n"
    if webc:
        t += "\n*Расходы за неделю:*\n"
        for c,a in sorted(webc.items()): t += f"  • {c}: ${a:,.2f}\n"
    t += f"\n*— Всего —*\n🏗 Активных: {ac}\n💵 Стоимость: ${tp:,.2f}\n✅ Получено: ${tr:,.2f}\n"
    t += f"💸 Расходы: ${te_all:,.2f}\n👷 ЗП: ${tzp:,.2f}\n📈 Должны: ${co:,.2f}\n💰 Баланс: ${bal:,.2f}\n"
    if ebc:
        t += "\n*Расходы по категориям:*\n"
        for c,a in sorted(ebc.items()): t += f"  • {c}: ${a:,.2f}\n"
    if zbs:
        t += "\n*ЗП по сабам:*\n"
        for s,a in sorted(zbs.items()): t += f"  • {s}: ${a:,.2f}\n"
    return t

def update_summary_sheet(ss):
    try:
        now = datetime.now()
        wa = now - timedelta(days=7); was = wa.strftime("%Y-%m-%d")
        ps_data = ss.worksheet("Проекты").get_all_values()
        pay_data = ss.worksheet("Платежи").get_all_values()
        exp_data = ss.worksheet("Расходы").get_all_values()
        zp_data = ss.worksheet("ЗП").get_all_values()

        tp=tr=te_all=0; ac=cc=0
        for r in ps_data[1:]:
            try:
                p=float(r[3]) if r[3] else 0; rc=float(r[5]) if len(r)>5 and r[5] else 0
                ex=float(r[6]) if len(r)>6 and r[6] else 0; st=r[4] if len(r)>4 else ""
                tp+=p; tr+=rc; te_all+=ex
                if st=="Завершён": cc+=1
                else: ac+=1
            except: pass
        co = tp - tr

        # Недели
        all_dates = []
        for r in pay_data[1:]:
            try: all_dates.append(r[3][:10])
            except: pass
        for r in exp_data[1:]:
            try: all_dates.append(r[5][:10])
            except: pass
        for r in zp_data[1:]:
            try: all_dates.append(r[2][:10])
            except: pass
        if not all_dates: all_dates = [now.strftime("%Y-%m-%d")]

        min_d = min(all_dates)
        try: min_dt = datetime.strptime(min_d, "%Y-%m-%d")
        except: min_dt = now

        def ws(dt): return dt - timedelta(days=dt.weekday())
        cws = ws(now); fws = ws(min_dt)
        weeks = []
        w = cws
        while w >= fws:
            weeks.append((w, w+timedelta(days=6))); w -= timedelta(days=7)

        def inw(ds, s, e):
            try: return s.strftime("%Y-%m-%d") <= ds[:10] <= e.strftime("%Y-%m-%d")
            except: return False

        ebc={}; zbs={}; tzp=0
        for r in exp_data[1:]:
            try:
                cat=r[2] if len(r)>2 else "Прочее"; amt=float(r[3])
                ebc[cat]=ebc.get(cat,0)+amt
            except: pass
        for r in zp_data[1:]:
            try:
                s=r[0] if r[0] else "?"; a=float(r[1])
                tzp+=a; zbs[s]=zbs.get(s,0)+a
            except: pass
        bal = tr - te_all - tzp

        wdata = []
        for s,e in weeks:
            wp=we=wz=0; wec={}; wzs={}
            for r in pay_data[1:]:
                try:
                    if inw(r[3],s,e): wp+=float(r[2])
                except: pass
            for r in exp_data[1:]:
                try:
                    if inw(r[5],s,e):
                        a=float(r[3]); c=r[2] if len(r)>2 else "Прочее"
                        we+=a; wec[c]=wec.get(c,0)+a
                except: pass
            for r in zp_data[1:]:
                try:
                    if inw(r[2],s,e):
                        a=float(r[1]); sn=r[0] if r[0] else "?"
                        wz+=a; wzs[sn]=wzs.get(sn,0)+a
                except: pass
            wdata.append({"l":f"{s.strftime('%m/%d')}-{e.strftime('%m/%d')}","p":wp,"e":we,"z":wz,"ec":wec,"zs":wzs})

        cats = sorted(set(ebc.keys())); subs = sorted(set(zbs.keys()))
        labels = ["СВОДКА","","--- ИТОГИ ---","Активных","Завершённых","Стоимость","Получено (всего)",
            "Расходы (всего)","ЗП (всего)","Должны","БАЛАНС","","--- ЗА НЕДЕЛЮ ---",
            "Получено","Расходы","ЗП","","Расходы по категориям:"]
        for c in cats: labels.append(f"  {c}")
        labels += ["","ЗП по сабам:"]
        for s in subs: labels.append(f"  {s}")

        nc = 2+len(weeks)
        mx = []
        for lb in labels:
            row = [lb]
            if lb=="СВОДКА": row.append(f"Обновлено: {now.strftime('%Y-%m-%d %H:%M')}")
            elif lb=="Активных": row.append(ac)
            elif lb=="Завершённых": row.append(cc)
            elif lb=="Стоимость": row.append(tp)
            elif lb=="Получено (всего)": row.append(tr)
            elif lb=="Расходы (всего)": row.append(te_all)
            elif lb=="ЗП (всего)": row.append(tzp)
            elif lb=="Должны": row.append(co)
            elif lb=="БАЛАНС": row.append(bal)
            elif lb=="--- ЗА НЕДЕЛЮ ---": row.append("Актуальное")
            elif lb.startswith("  ") and lb.strip() in ebc: row.append(ebc[lb.strip()])
            elif lb.startswith("  ") and lb.strip() in zbs: row.append(zbs[lb.strip()])
            else: row.append("")
            for wd in wdata:
                if lb=="--- ЗА НЕДЕЛЮ ---": row.append(wd["l"])
                elif lb=="Получено": row.append(wd["p"] if wd["p"] else "")
                elif lb=="Расходы": row.append(wd["e"] if wd["e"] else "")
                elif lb=="ЗП": row.append(wd["z"] if wd["z"] else "")
                elif lb.startswith("  ") and lb.strip() in ebc: row.append(wd["ec"].get(lb.strip(),""))
                elif lb.startswith("  ") and lb.strip() in zbs: row.append(wd["zs"].get(lb.strip(),""))
                else: row.append("")
            mx.append(row)

        try:
            sh = ss.worksheet("Сводка"); sh.clear()
        except: sh = ss.add_worksheet(title="Сводка",rows=50,cols=nc)
        if sh.col_count < nc: sh.resize(cols=nc)
        sh.update("A1", mx, value_input_option="USER_ENTERED")
        logger.info("✅ Сводка обновлена")
    except Exception as e:
        logger.error(f"Summary error: {e}")

# ============================================================
# МЕНЮ
# ============================================================

MENU = ReplyKeyboardMarkup([
    ["📋 Новый проект", "💰 Задаток/Приход"],
    ["💸 Записать расход", "📝 Добавить описание"],
    ["🔄 Изменить статус", "📊 Статус проекта"],
    ["📈 Сводка", "👷 Добавить саба"],
    ["💵 Оплата сабу", "📁 Архив"],
], resize_keyboard=True)

async def start(update, context):
    if not check_access(update):
        await update.message.reply_text("⛔ Нет доступа."); return ConversationHandler.END
    await update.message.reply_text(f"👋 {get_user_name(update)}!\n🏗 Wood & Stone — Tracker\nВыбери:", reply_markup=MENU)
    return MAIN_MENU

async def menu(update, context):
    if not check_access(update):
        await update.message.reply_text("⛔"); return ConversationHandler.END
    t = update.message.text
    if t == "📋 Новый проект":
        await update.message.reply_text("📋 Введи адрес проекта:", reply_markup=ReplyKeyboardRemove())
        return NEW_PROJECT_ADDRESS
    elif t == "💰 Задаток/Приход":
        return await show_projects(update, context, PAY_SELECT_PROJECT)
    elif t == "💸 Записать расход":
        return await show_projects(update, context, EXP_SELECT_PROJECT)
    elif t == "📝 Добавить описание":
        return await show_projects(update, context, STATUS_DESC_SELECT_PROJECT)
    elif t == "🔄 Изменить статус":
        return await show_projects(update, context, CHANGE_STATUS_SELECT)
    elif t == "📊 Статус проекта":
        return await show_projects(update, context, VIEW_STATUS_SELECT, True)
    elif t == "👷 Добавить саба":
        await update.message.reply_text("👷 Имя субподрядчика:", reply_markup=ReplyKeyboardRemove())
        return ADD_SUB_NAME
    elif t == "💵 Оплата сабу":
        return await show_subs(update, context)
    elif t == "📈 Сводка":
        return await show_summary(update, context)
    elif t == "📁 Архив":
        return await show_archive(update, context)
    return MAIN_MENU

# ============================================================
# ОБЩИЕ
# ============================================================

async def show_projects(update, context, state, all_=False):
    try:
        ss = get_spreadsheet(); ps = ss.worksheet("Проекты")
        projects = get_all_projects(ps) if all_ else get_active_projects(ps)
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text("❌ Ошибка.", reply_markup=MENU); return MAIN_MENU
    if not projects:
        await update.message.reply_text("📭 Нет проектов.", reply_markup=MENU); return MAIN_MENU
    btns = []
    for p in projects:
        lb = f"{p['id']} — {p['address']}"
        if all_: lb += f" [{p['status']}]"
        btns.append([InlineKeyboardButton(lb, callback_data=f"proj_{p['id']}")])
    btns.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    await update.message.reply_text("Выбери проект:", reply_markup=InlineKeyboardMarkup(btns))
    return state

async def show_archive(update, context):
    try:
        ss = get_spreadsheet()
        arch = [p for p in get_all_projects(ss.worksheet("Проекты")) if p["status"]=="Завершён"]
    except: arch = []
    if not arch:
        await update.message.reply_text("📁 Архив пуст.", reply_markup=MENU); return MAIN_MENU
    t = "📁 *Архив:*\n\n" + "\n".join(f"• {p['id']} — {p['address']}" for p in arch)
    await update.message.reply_text(t, parse_mode="Markdown", reply_markup=MENU); return MAIN_MENU

async def cancel_cb(update, context):
    q = update.callback_query; await q.answer(); await q.edit_message_text("❌ Отменено.")
    await q.message.reply_text("Меню:", reply_markup=MENU); return MAIN_MENU

# ============================================================
# НОВЫЙ ПРОЕКТ
# ============================================================

async def np_address(update, context):
    context.user_data["np_addr"] = update.message.text
    await update.message.reply_text("📝 Описание проекта:"); return NEW_PROJECT_DESCRIPTION

async def np_desc(update, context):
    context.user_data["np_desc"] = update.message.text
    await update.message.reply_text("💵 Цена проекта (число):"); return NEW_PROJECT_PRICE

async def np_price(update, context):
    try: price = float(update.message.text.replace(",","").replace("$",""))
    except:
        await update.message.reply_text("❌ Число!"); return NEW_PROJECT_PRICE
    user = get_user_name(update); now = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        ss = get_spreadsheet(); ps = init_sheets(ss); pid = get_next_project_id(ps)
        ps.append_row([pid, context.user_data["np_addr"], context.user_data["np_desc"], price, "Новый", 0, 0, 0, now, user], value_input_option="USER_ENTERED")
        await update.message.reply_text(f"✅ Проект создан!\n🆔 {pid}\n📍 {context.user_data['np_addr']}\n💵 ${price:,.2f}\n👤 {user}", reply_markup=MENU)
    except Exception as e:
        logger.error(f"Error: {e}"); await update.message.reply_text("❌ Ошибка.", reply_markup=MENU)
    for k in ["np_addr","np_desc"]: context.user_data.pop(k,None)
    return MAIN_MENU

# ============================================================
# 💰 ЗАДАТОК/ПРИХОД
# ============================================================

async def pay_select(update, context):
    q = update.callback_query; await q.answer()
    if q.data == "cancel": return await cancel_cb(update, context)
    context.user_data["pay_pid"] = q.data.replace("proj_","")
    btns = [
        [InlineKeyboardButton("✏️ Ввести сумму", callback_data="pay_manual")],
        [InlineKeyboardButton("📄 Сфоткать инвойс", callback_data="pay_photo")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
    ]
    await q.edit_message_text(f"💰 Проект {context.user_data['pay_pid']}.\n\nКак записать?", reply_markup=InlineKeyboardMarkup(btns))
    return PAY_METHOD

async def pay_method(update, context):
    q = update.callback_query; await q.answer()
    if q.data == "cancel": return await cancel_cb(update, context)
    if q.data == "pay_manual":
        await q.edit_message_text("💰 Введи сумму:"); return PAY_AMOUNT
    if q.data == "pay_photo":
        await q.edit_message_text("📄 Сфоткай инвойс и отправь:"); return PAY_PHOTO

async def pay_amount(update, context):
    try: amt = float(update.message.text.replace(",","").replace("$",""))
    except: await update.message.reply_text("❌ Число!"); return PAY_AMOUNT
    return await _save_payment(update, context, amt, "")

async def pay_photo(update, context):
    if not update.message.photo:
        await update.message.reply_text("❌ Отправь фото."); return PAY_PHOTO
    await update.message.reply_text("⏳ Читаю инвойс...")
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    fp = f"/tmp/inv_{photo.file_id}.jpg"
    await file.download_to_drive(fp)
    context.user_data["pay_fp"] = fp
    context.user_data["pay_fid"] = photo.file_id
    total = scan_invoice_amount(fp)
    context.user_data["pay_amt"] = total
    if total > 0:
        btns = [
            [InlineKeyboardButton(f"✅ Да, ${total:,.2f}", callback_data="payc_yes")],
            [InlineKeyboardButton("✏️ Вручную", callback_data="payc_manual")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
        ]
        await update.message.reply_text(f"📄 Сумма: *${total:,.2f}*\nВерно?", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))
    else:
        btns = [
            [InlineKeyboardButton("✏️ Вручную", callback_data="payc_manual")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
        ]
        await update.message.reply_text("📄 Не распознал сумму.\nВведи вручную:", reply_markup=InlineKeyboardMarkup(btns))
    return PAY_CONFIRM

async def pay_confirm(update, context):
    q = update.callback_query; await q.answer()
    if q.data == "cancel":
        try: os.remove(context.user_data.get("pay_fp",""))
        except: pass
        return await cancel_cb(update, context)
    if q.data == "payc_manual":
        await q.edit_message_text("✏️ Введи сумму:"); return PAY_CONFIRM
    if q.data == "payc_yes":
        amt = context.user_data.get("pay_amt", 0)
        fid = context.user_data.get("pay_fid", "")
        return await _save_payment_cb(q, context, amt, fid)

async def pay_confirm_text(update, context):
    try: amt = float(update.message.text.replace(",","").replace("$",""))
    except: await update.message.reply_text("❌ Число!"); return PAY_CONFIRM
    fid = context.user_data.get("pay_fid", "")
    return await _save_payment(update, context, amt, fid)

async def _save_payment(update, context, amt, fid):
    pid = context.user_data.get("pay_pid",""); user = get_user_name(update)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        ss = get_spreadsheet(); ps = ss.worksheet("Проекты"); pays = ss.worksheet("Платежи")
        addr = get_project_address(ps, pid)
        link = ""
        if fid:
            link = await send_photo_to_channel(context, fid, f"📄 ИНВОЙС — {pid} — {addr}\n💰 ${amt:,.2f}\n📅 {now}\n👤 {user}")
        note = f"{user}" + (f" (инвойс: {link})" if link else "")
        pays.append_row([pid, addr, amt, now, note], value_input_option="USER_ENTERED")
        update_project_totals(ss, pid); update_summary_sheet(ss)
        await update.message.reply_text(f"✅ Платёж записан!\n🆔 {pid}\n💰 ${amt:,.2f}\n👤 {user}", reply_markup=MENU)
    except Exception as e:
        logger.error(f"Error: {e}"); await update.message.reply_text("❌ Ошибка.", reply_markup=MENU)
    try: os.remove(context.user_data.get("pay_fp",""))
    except: pass
    for k in ["pay_pid","pay_fp","pay_fid","pay_amt"]: context.user_data.pop(k,None)
    return MAIN_MENU

async def _save_payment_cb(q, context, amt, fid):
    pid = context.user_data.get("pay_pid",""); user = get_user_name_by_id(q.from_user.id)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        ss = get_spreadsheet(); ps = ss.worksheet("Проекты"); pays = ss.worksheet("Платежи")
        addr = get_project_address(ps, pid)
        link = ""
        if fid:
            link = await send_photo_to_channel(context, fid, f"📄 ИНВОЙС — {pid} — {addr}\n💰 ${amt:,.2f}\n📅 {now}\n👤 {user}")
        note = f"{user}" + (f" (инвойс: {link})" if link else "")
        pays.append_row([pid, addr, amt, now, note], value_input_option="USER_ENTERED")
        update_project_totals(ss, pid); update_summary_sheet(ss)
        await q.edit_message_text(f"✅ Платёж записан!\n🆔 {pid}\n💰 ${amt:,.2f}\n👤 {user}")
    except Exception as e:
        logger.error(f"Error: {e}"); await q.edit_message_text("❌ Ошибка.")
    try: os.remove(context.user_data.get("pay_fp",""))
    except: pass
    for k in ["pay_pid","pay_fp","pay_fid","pay_amt"]: context.user_data.pop(k,None)
    await q.message.reply_text("Меню:", reply_markup=MENU); return MAIN_MENU

# ============================================================
# 💸 ЗАПИСАТЬ РАСХОД
# ============================================================

EXPENSE_CATS = ["Материалы", "Субподрядчик", "Аренда оборудования", "Прочее"]

async def exp_select(update, context):
    q = update.callback_query; await q.answer()
    if q.data == "cancel": return await cancel_cb(update, context)
    context.user_data["exp_pid"] = q.data.replace("proj_","")
    btns = [[InlineKeyboardButton(c, callback_data=f"ecat_{c}")] for c in EXPENSE_CATS]
    await q.edit_message_text(f"💸 Проект {context.user_data['exp_pid']}.\nКатегория:", reply_markup=InlineKeyboardMarkup(btns))
    return EXP_CATEGORY

async def exp_category(update, context):
    q = update.callback_query; await q.answer()
    context.user_data["exp_cat"] = q.data.replace("ecat_","")
    btns = [
        [InlineKeyboardButton("✏️ Ввести сумму", callback_data="exp_manual")],
        [InlineKeyboardButton("🧾 Сфоткать чек", callback_data="exp_photo")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
    ]
    await q.edit_message_text(f"💸 {context.user_data['exp_cat']}.\nКак записать?", reply_markup=InlineKeyboardMarkup(btns))
    return EXP_METHOD

async def exp_method(update, context):
    q = update.callback_query; await q.answer()
    if q.data == "cancel": return await cancel_cb(update, context)
    if q.data == "exp_manual":
        await q.edit_message_text("💸 Введи сумму:"); return EXP_AMOUNT
    if q.data == "exp_photo":
        await q.edit_message_text("🧾 Сфоткай чек и отправь:"); return EXP_PHOTO

async def exp_amount(update, context):
    try: amt = float(update.message.text.replace(",","").replace("$",""))
    except: await update.message.reply_text("❌ Число!"); return EXP_AMOUNT
    context.user_data["exp_amt"] = amt
    await update.message.reply_text("📝 Описание (или «-» пропустить):"); return EXP_DESCRIPTION

async def exp_photo(update, context):
    if not update.message.photo:
        await update.message.reply_text("❌ Отправь фото."); return EXP_PHOTO
    await update.message.reply_text("⏳ Сканирую чек...")
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    fp = f"/tmp/rcpt_{photo.file_id}.jpg"
    await file.download_to_drive(fp)
    context.user_data["exp_fp"] = fp
    context.user_data["exp_fid"] = photo.file_id
    total = scan_receipt_amount(fp)
    context.user_data["exp_amt"] = total
    if total > 0:
        btns = [
            [InlineKeyboardButton(f"✅ Да, ${total:,.2f}", callback_data="expc_yes")],
            [InlineKeyboardButton("✏️ Вручную", callback_data="expc_manual")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
        ]
        await update.message.reply_text(f"🧾 Сумма: *${total:,.2f}*\nВерно?", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))
    else:
        btns = [
            [InlineKeyboardButton("✏️ Вручную", callback_data="expc_manual")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
        ]
        await update.message.reply_text("🧾 Не распознал.\nВведи вручную:", reply_markup=InlineKeyboardMarkup(btns))
    return EXP_CONFIRM

async def exp_confirm(update, context):
    q = update.callback_query; await q.answer()
    if q.data == "cancel":
        try: os.remove(context.user_data.get("exp_fp",""))
        except: pass
        return await cancel_cb(update, context)
    if q.data == "expc_manual":
        await q.edit_message_text("✏️ Введи сумму:"); return EXP_CONFIRM
    if q.data == "expc_yes":
        context.user_data["exp_confirmed"] = True
        await q.edit_message_text("📝 Описание (или «-» пропустить):"); return EXP_DESCRIPTION

async def exp_confirm_text(update, context):
    """Ручной ввод суммы после фото."""
    try: amt = float(update.message.text.replace(",","").replace("$",""))
    except: await update.message.reply_text("❌ Число!"); return EXP_CONFIRM
    context.user_data["exp_amt"] = amt
    context.user_data["exp_confirmed"] = True
    await update.message.reply_text("📝 Описание (или «-» пропустить):"); return EXP_DESCRIPTION

async def exp_description(update, context):
    desc = update.message.text if update.message.text != "-" else ""
    pid = context.user_data.get("exp_pid","")
    cat = context.user_data.get("exp_cat","")
    amt = context.user_data.get("exp_amt",0)
    fid = context.user_data.get("exp_fid","")
    user = get_user_name(update); now = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        ss = get_spreadsheet(); ps = ss.worksheet("Проекты"); exps = ss.worksheet("Расходы")
        addr = get_project_address(ps, pid)
        link = ""
        if fid:
            link = await send_photo_to_channel(context, fid, f"🧾 ЧЕК — {pid} — {addr}\n💸 ${amt:,.2f} [{cat}]\n📅 {now}\n👤 {user}")
        full_desc = desc
        if link: full_desc = f"{desc} (чек: {link})" if desc else f"Чек: {link}"
        exps.append_row([pid, addr, cat, amt, full_desc, now, user], value_input_option="USER_ENTERED")
        update_project_totals(ss, pid); update_summary_sheet(ss)
        await update.message.reply_text(f"✅ Расход записан!\n🆔 {pid}\n📂 {cat}: ${amt:,.2f}\n📝 {desc or '—'}\n👤 {user}", reply_markup=MENU)
    except Exception as e:
        logger.error(f"Error: {e}"); await update.message.reply_text("❌ Ошибка.", reply_markup=MENU)
    try: os.remove(context.user_data.get("exp_fp",""))
    except: pass
    for k in ["exp_pid","exp_cat","exp_amt","exp_fp","exp_fid","exp_confirmed"]: context.user_data.pop(k,None)
    return MAIN_MENU

# ============================================================
# ОПИСАНИЕ
# ============================================================

async def desc_select(update, context):
    q = update.callback_query; await q.answer()
    if q.data == "cancel": return await cancel_cb(update, context)
    context.user_data["desc_pid"] = q.data.replace("proj_","")
    await q.edit_message_text(f"📝 Проект {context.user_data['desc_pid']}.\nОписание:"); return STATUS_DESC_TEXT

async def desc_text(update, context):
    txt = update.message.text; pid = context.user_data["desc_pid"]
    user = get_user_name(update); now = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        ss = get_spreadsheet(); ps = ss.worksheet("Проекты"); us = ss.worksheet("Обновления")
        us.append_row([pid, get_project_address(ps,pid), txt, now, user], value_input_option="USER_ENTERED")
        await update.message.reply_text(f"✅ Добавлено!\n🆔 {pid}\n📝 {txt}\n👤 {user}", reply_markup=MENU)
    except: await update.message.reply_text("❌ Ошибка.", reply_markup=MENU)
    context.user_data.pop("desc_pid",None); return MAIN_MENU

# ============================================================
# СТАТУС
# ============================================================

STATUSES = ["Новый","В работе","Приостановлен","Завершён"]

async def status_select(update, context):
    q = update.callback_query; await q.answer()
    if q.data == "cancel": return await cancel_cb(update, context)
    context.user_data["st_pid"] = q.data.replace("proj_","")
    btns = [[InlineKeyboardButton(s, callback_data=f"st_{s}")] for s in STATUSES]
    await q.edit_message_text(f"🔄 {context.user_data['st_pid']}.\nСтатус:", reply_markup=InlineKeyboardMarkup(btns))
    return CHANGE_STATUS_VALUE

async def status_value(update, context):
    q = update.callback_query; await q.answer()
    ns = q.data.replace("st_",""); pid = context.user_data["st_pid"]
    user = get_user_name_by_id(q.from_user.id); now = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        ss = get_spreadsheet(); ps = ss.worksheet("Проекты")
        rn = find_project_row(ps, pid)
        if rn != -1: ps.update(f"E{rn}", [[ns]])
        ss.worksheet("Обновления").append_row([pid, get_project_address(ps,pid), f"Статус → {ns}", now, user], value_input_option="USER_ENTERED")
        await q.edit_message_text(f"✅ {pid} → {ns}\n👤 {user}")
    except: await q.edit_message_text("❌ Ошибка.")
    await q.message.reply_text("Меню:", reply_markup=MENU)
    context.user_data.pop("st_pid",None); return MAIN_MENU

# ============================================================
# СТАТУС ПРОЕКТА — СВОДКА
# ============================================================

async def view_select(update, context):
    q = update.callback_query; await q.answer()
    if q.data == "cancel": return await cancel_cb(update, context)
    pid = q.data.replace("proj_","")
    try:
        ss = get_spreadsheet(); ps = ss.worksheet("Проекты")
        rn = find_project_row(ps, pid)
        if rn == -1: await q.edit_message_text("❌ Не найден."); return MAIN_MENU
        r = ps.row_values(rn)
        addr=r[1]; desc=r[2]; price=float(r[3]) if r[3] else 0
        st=r[4] if len(r)>4 else ""; tp=float(r[5]) if len(r)>5 and r[5] else 0
        te=float(r[6]) if len(r)>6 and r[6] else 0; bal=float(r[7]) if len(r)>7 and r[7] else 0
        rem = price - tp
        t = f"📊 *{pid}*\n📍 {addr}\n📝 {desc}\n🔄 {st}\n\n💵 Цена: ${price:,.2f}\n✅ Получено: ${tp:,.2f}\n💸 Расходы: ${te:,.2f}\n📈 Должен: ${rem:,.2f}\n💰 Баланс: ${bal:,.2f}\n"
        pp = [r for r in ss.worksheet("Платежи").get_all_values()[1:] if r[0]==pid]
        if pp:
            t += "\n*Платежи:*\n"
            for p in pp[-5:]: t += f"  • {p[3]} — ${float(p[2]):,.2f} ({p[4]})\n"
        pe = [r for r in ss.worksheet("Расходы").get_all_values()[1:] if r[0]==pid]
        if pe:
            t += "\n*Расходы:*\n"
            for e in pe[-5:]:
                d = f" — {e[4]}" if e[4] else ""
                t += f"  • {e[5]} — ${float(e[3]):,.2f} [{e[2]}]{d} ({e[6]})\n"
        pu = [r for r in ss.worksheet("Обновления").get_all_values()[1:] if r[0]==pid]
        if pu:
            t += "\n*Обновления:*\n"
            for u in pu[-5:]: t += f"  • {u[3]} — {u[2]} ({u[4]})\n"
        await q.edit_message_text(t, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error: {e}"); await q.edit_message_text("❌ Ошибка.")
    await q.message.reply_text("Меню:", reply_markup=MENU); return MAIN_MENU

# ============================================================
# САБЫ
# ============================================================

async def add_sub(update, context):
    name = update.message.text.strip(); user = get_user_name(update); now = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        ss = get_spreadsheet(); ss.worksheet("Сабы").append_row([name, now, user], value_input_option="USER_ENTERED")
        await update.message.reply_text(f"✅ {name} добавлен!\n👤 {user}", reply_markup=MENU)
    except: await update.message.reply_text("❌ Ошибка.", reply_markup=MENU)
    return MAIN_MENU

async def show_subs(update, context):
    try:
        ss = get_spreadsheet(); subs = get_subs_list(ss)
    except: subs = []
    if not subs:
        await update.message.reply_text("📭 Нет сабов.", reply_markup=MENU); return MAIN_MENU
    btns = [[InlineKeyboardButton(s, callback_data=f"sub_{s}")] for s in subs]
    btns.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    await update.message.reply_text("👷 Выбери саба:", reply_markup=InlineKeyboardMarkup(btns))
    return SUB_PAY_SELECT_SUB

async def sub_select(update, context):
    q = update.callback_query; await q.answer()
    if q.data == "cancel": return await cancel_cb(update, context)
    context.user_data["sub_name"] = q.data.replace("sub_","")
    await q.edit_message_text(f"💵 {context.user_data['sub_name']}.\nСумма:"); return SUB_PAY_AMOUNT

async def sub_amount(update, context):
    try: amt = float(update.message.text.replace(",","").replace("$",""))
    except: await update.message.reply_text("❌ Число!"); return SUB_PAY_AMOUNT
    name = context.user_data["sub_name"]; user = get_user_name(update); now = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        ss = get_spreadsheet()
        ss.worksheet("ЗП").append_row([name, amt, now, user], value_input_option="USER_ENTERED")
        update_summary_sheet(ss)
        await update.message.reply_text(f"✅ Оплата!\n👷 {name}\n💵 ${amt:,.2f}\n👤 {user}", reply_markup=MENU)
    except: await update.message.reply_text("❌ Ошибка.", reply_markup=MENU)
    context.user_data.pop("sub_name",None); return MAIN_MENU

# ============================================================
# СВОДКА
# ============================================================

async def show_summary(update, context):
    try:
        ss = get_spreadsheet(); t = build_summary(ss)
        await update.message.reply_text(t, parse_mode="Markdown", reply_markup=MENU)
    except: await update.message.reply_text("❌ Ошибка.", reply_markup=MENU)
    return MAIN_MENU

async def cancel(update, context):
    await update.message.reply_text("❌ Отменено.", reply_markup=MENU); return MAIN_MENU

# ============================================================
# ЗАПУСК
# ============================================================

def main():
    try:
        ss = get_spreadsheet(); init_sheets(ss); logger.info("✅ Листы ок.")
    except Exception as e: logger.error(f"Init error: {e}")

    app = Application.builder().token(BOT_TOKEN).build()
    ch = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, menu)],
            NEW_PROJECT_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, np_address)],
            NEW_PROJECT_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, np_desc)],
            NEW_PROJECT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, np_price)],
            PAY_SELECT_PROJECT: [CallbackQueryHandler(pay_select, pattern="^proj_"), CallbackQueryHandler(cancel_cb, pattern="^cancel$")],
            PAY_METHOD: [CallbackQueryHandler(pay_method, pattern="^pay_"), CallbackQueryHandler(cancel_cb, pattern="^cancel$")],
            PAY_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, pay_amount)],
            PAY_PHOTO: [MessageHandler(filters.PHOTO, pay_photo)],
            PAY_CONFIRM: [CallbackQueryHandler(pay_confirm, pattern="^payc_"), CallbackQueryHandler(cancel_cb, pattern="^cancel$"), MessageHandler(filters.TEXT & ~filters.COMMAND, pay_confirm_text)],
            EXP_SELECT_PROJECT: [CallbackQueryHandler(exp_select, pattern="^proj_"), CallbackQueryHandler(cancel_cb, pattern="^cancel$")],
            EXP_CATEGORY: [CallbackQueryHandler(exp_category, pattern="^ecat_")],
            EXP_METHOD: [CallbackQueryHandler(exp_method, pattern="^exp_"), CallbackQueryHandler(cancel_cb, pattern="^cancel$")],
            EXP_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, exp_amount)],
            EXP_PHOTO: [MessageHandler(filters.PHOTO, exp_photo)],
            EXP_CONFIRM: [CallbackQueryHandler(exp_confirm, pattern="^expc_"), CallbackQueryHandler(cancel_cb, pattern="^cancel$"), MessageHandler(filters.TEXT & ~filters.COMMAND, exp_confirm_text)],
            EXP_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, exp_description)],
            STATUS_DESC_SELECT_PROJECT: [CallbackQueryHandler(desc_select, pattern="^proj_"), CallbackQueryHandler(cancel_cb, pattern="^cancel$")],
            STATUS_DESC_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, desc_text)],
            CHANGE_STATUS_SELECT: [CallbackQueryHandler(status_select, pattern="^proj_"), CallbackQueryHandler(cancel_cb, pattern="^cancel$")],
            CHANGE_STATUS_VALUE: [CallbackQueryHandler(status_value, pattern="^st_")],
            VIEW_STATUS_SELECT: [CallbackQueryHandler(view_select, pattern="^proj_"), CallbackQueryHandler(cancel_cb, pattern="^cancel$")],
            ADD_SUB_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_sub)],
            SUB_PAY_SELECT_SUB: [CallbackQueryHandler(sub_select, pattern="^sub_"), CallbackQueryHandler(cancel_cb, pattern="^cancel$")],
            SUB_PAY_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, sub_amount)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
    )
    app.add_handler(ch)
    logger.info("🚀 Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
