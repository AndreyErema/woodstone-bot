"""
Wood & Stone Construction LLC — Telegram Project Tracker Bot v5
Роли: Владельцы (полное меню) | Сабы (учёт смен)
Сабы регистрируются сами, владелец одобряет.
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

# Владельцы (ID: Имя)
OWNERS = {
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
    SUB_MENU, SUB_SHIFT_START_SELECT, SUB_SHIFT_END_CONFIRM,
    SUB_REGISTER_NAME,
    OWNER_SHIFT_START_SELECT,
) = range(29)

# ============================================================
# GOOGLE SHEETS
# ============================================================

SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]

def get_google_creds():
    cj = os.environ.get("GOOGLE_CREDS_JSON", "")
    if cj: return Credentials.from_service_account_info(json.loads(cj), scopes=SCOPES)
    return Credentials.from_service_account_file(GOOGLE_CREDS_FILE, scopes=SCOPES)

def get_spreadsheet():
    return gspread.authorize(get_google_creds()).open_by_key(SPREADSHEET_ID)

def get_or_create_sheet(ss, title, headers):
    try: return ss.worksheet(title)
    except gspread.exceptions.WorksheetNotFound:
        sh = ss.add_worksheet(title=title, rows=1000, cols=len(headers))
        sh.append_row(headers, value_input_option="USER_ENTERED"); return sh

def init_sheets(ss):
    p = get_or_create_sheet(ss, "Проекты", ["Project ID","Адрес","Описание","Цена","Статус","Получено","Расходы","Баланс","Дата создания","Создал"])
    get_or_create_sheet(ss, "Платежи", ["Project ID","Адрес","Сумма","Дата","Кто записал"])
    get_or_create_sheet(ss, "Расходы", ["Project ID","Адрес","Категория","Сумма","Описание","Дата","Кто записал"])
    get_or_create_sheet(ss, "Обновления", ["Project ID","Адрес","Текст","Дата","Кто записал"])
    get_or_create_sheet(ss, "Сабы", ["Имя","Telegram ID","Статус","Дата регистрации"])
    get_or_create_sheet(ss, "ЗП", ["Субподрядчик","Сумма","Дата","Кто записал"])
    get_or_create_sheet(ss, "Смены", ["Субподрядчик","Telegram ID","Project ID","Адрес","Начало","Конец","Часы","Дата"])
    get_or_create_sheet(ss, "Сводка", ["Показатель","Значение","Примечание"])
    return p

# ============================================================
# РОЛИ И ДОСТУП
# ============================================================

def is_owner(uid): return uid in OWNERS
def get_owner_name(uid): return OWNERS.get(uid, "Unknown")

def get_sub_info(ss, uid):
    """Найти саба по Telegram ID. Вернуть {name, status, row} или None."""
    try:
        sh = ss.worksheet("Сабы")
        for i, r in enumerate(sh.get_all_values()[1:], start=2):
            try:
                if str(r[1]).strip() == str(uid):
                    return {"name": r[0], "status": r[2], "row": i}
            except: pass
    except: pass
    return None

def get_user_display(update):
    """Имя пользователя для записи."""
    uid = update.effective_user.id
    if is_owner(uid): return get_owner_name(uid)
    try:
        ss = get_spreadsheet()
        info = get_sub_info(ss, uid)
        if info: return info["name"]
    except: pass
    return update.effective_user.first_name or "Unknown"

# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ
# ============================================================

def get_next_project_id(ps):
    recs = ps.get_all_values()
    if len(recs) <= 1: return "0001"
    mx = max((int(r[0]) for r in recs[1:] if r[0].isdigit()), default=0)
    return str(mx+1).zfill(4)

def get_active_projects(ps):
    recs = ps.get_all_values()
    if len(recs) <= 1: return []
    return [{"id":r[0],"address":r[1],"status":r[4] if len(r)>4 else "Новый"} for r in recs[1:] if (r[4] if len(r)>4 else "Новый") != "Завершён"]

def get_all_projects(ps):
    recs = ps.get_all_values()
    if len(recs) <= 1: return []
    return [{"id":r[0],"address":r[1],"status":r[4] if len(r)>4 else "Новый"} for r in recs[1:]]

def find_project_row(ps, pid):
    for i, r in enumerate(ps.get_all_values()):
        if r[0] == pid: return i+1
    return -1

def update_project_totals(ss, pid):
    ps = ss.worksheet("Проекты"); pays = ss.worksheet("Платежи"); exps = ss.worksheet("Расходы")
    tp = sum(float(r[2]) for r in pays.get_all_values()[1:] if r[0]==pid and r[2])
    te = sum(float(r[3]) for r in exps.get_all_values()[1:] if r[0]==pid and r[3])
    rn = find_project_row(ps, pid)
    if rn == -1: return
    ps.update(f"F{rn}", [[tp]]); ps.update(f"G{rn}", [[te]]); ps.update(f"H{rn}", [[tp-te]])

def get_project_address(ps, pid):
    for r in ps.get_all_values()[1:]:
        if r[0] == pid: return r[1]
    return ""

def get_approved_subs(ss):
    try:
        recs = ss.worksheet("Сабы").get_all_values()
        return [r[0] for r in recs[1:] if r[0] and len(r)>2 and r[2]=="Одобрен"] if len(recs)>1 else []
    except: return []

def get_active_shift(ss, uid):
    """Найти активную смену (без конца) для саба."""
    try:
        sh = ss.worksheet("Смены")
        recs = sh.get_all_values()
        for i, r in enumerate(recs[1:], start=2):
            if str(r[1]).strip() == str(uid) and len(r) > 5 and r[4] and not r[5]:
                return {"row": i, "pid": r[2], "address": r[3], "start": r[4]}
    except: pass
    return None

# ============================================================
# OCR
# ============================================================

def scan_receipt_amount(fp):
    return _claude_read(fp, "This is a photo of a store receipt. Find the TOTAL amount paid (not subtotal, not tax). Return ONLY the number. Example: 2004.14")

def scan_invoice_amount(fp):
    return _claude_read(fp, "This is a photo of a handwritten invoice, receipt, or check from a client. Find the TOTAL amount paid or due. Return ONLY the number. Example: 5000.00")

def _claude_read(fp, prompt):
    try:
        ak = os.environ.get("ANTHROPIC_API_KEY","")
        if not ak: return 0.0
        with open(fp,"rb") as f: img = base64.b64encode(f.read()).decode("utf-8")
        body = json.dumps({"model":"claude-sonnet-4-20250514","max_tokens":100,
            "messages":[{"role":"user","content":[
                {"type":"image","source":{"type":"base64","media_type":"image/jpeg","data":img}},
                {"type":"text","text":prompt}]}]})
        req = urllib.request.Request("https://api.anthropic.com/v1/messages",data=body.encode("utf-8"),
            headers={"Content-Type":"application/json","x-api-key":ak,"anthropic-version":"2023-06-01"})
        with urllib.request.urlopen(req,timeout=30) as resp:
            res = json.loads(resp.read().decode("utf-8"))
        ans = "".join(b.get("text","") for b in res.get("content",[]) if b.get("type")=="text")
        logger.info(f"Claude OCR: {ans}")
        return float(ans.strip().replace("$","").replace(",",""))
    except Exception as e:
        logger.error(f"OCR error: {e}"); return 0.0

async def send_photo_to_channel(context, fid, caption):
    try:
        msg = await context.bot.send_photo(chat_id=RECEIPTS_CHANNEL_ID, photo=fid, caption=caption)
        if msg: return f"https://t.me/c/{str(RECEIPTS_CHANNEL_ID).replace('-100','')}/{msg.message_id}"
    except Exception as e: logger.error(f"Channel error: {e}")
    return ""

# ============================================================
# СВОДКА
# ============================================================

def build_summary(ss):
    now=datetime.now(); wa=now-timedelta(days=7); was=wa.strftime("%Y-%m-%d")
    ps=ss.worksheet("Проекты").get_all_values()
    tp=tr=te_all=0; ac=cc=0
    for r in ps[1:]:
        try:
            p=float(r[3]) if r[3] else 0; rc=float(r[5]) if len(r)>5 and r[5] else 0
            ex=float(r[6]) if len(r)>6 and r[6] else 0; st=r[4] if len(r)>4 else ""
            tp+=p; tr+=rc; te_all+=ex
            if st=="Завершён": cc+=1
            else: ac+=1
        except: pass
    wpay=wexp=wzp=0; ebc={}; webc={}; zbs={}; tzp=0
    for r in ss.worksheet("Платежи").get_all_values()[1:]:
        try:
            if r[3][:10]>=was: wpay+=float(r[2])
        except: pass
    for r in ss.worksheet("Расходы").get_all_values()[1:]:
        try:
            cat=r[2];amt=float(r[3]);ds=r[5][:10]
            ebc[cat]=ebc.get(cat,0)+amt
            if ds>=was: wexp+=amt; webc[cat]=webc.get(cat,0)+amt
        except: pass
    for r in ss.worksheet("ЗП").get_all_values()[1:]:
        try:
            s=r[0];a=float(r[1]);ds=r[2][:10]
            tzp+=a; zbs[s]=zbs.get(s,0)+a
            if ds>=was: wzp+=a
        except: pass
    bal=tr-te_all-tzp; co=tp-tr
    t=f"📊 *СВОДКА*\n📅 {wa.strftime('%m/%d')} — {now.strftime('%m/%d/%Y')}\n\n"
    t+=f"*За неделю:*\n💰 Получено: ${wpay:,.2f}\n💸 Расходы: ${wexp:,.2f}\n👷 ЗП: ${wzp:,.2f}\n"
    t+=f"\n*Всего:*\n🏗 Активных: {ac}\n💵 Стоимость: ${tp:,.2f}\n✅ Получено: ${tr:,.2f}\n"
    t+=f"💸 Расходы: ${te_all:,.2f}\n👷 ЗП: ${tzp:,.2f}\n📈 Должны: ${co:,.2f}\n💰 Баланс: ${bal:,.2f}\n"
    if ebc:
        t+="\n*По категориям:*\n"
        for c,a in sorted(ebc.items()): t+=f"  • {c}: ${a:,.2f}\n"
    if zbs:
        t+="\n*ЗП по сабам:*\n"
        for s,a in sorted(zbs.items()): t+=f"  • {s}: ${a:,.2f}\n"
    return t

def update_summary_sheet(ss):
    try:
        now=datetime.now()
        ps_d=ss.worksheet("Проекты").get_all_values()
        pay_d=ss.worksheet("Платежи").get_all_values()
        exp_d=ss.worksheet("Расходы").get_all_values()
        zp_d=ss.worksheet("ЗП").get_all_values()
        tp=tr=te=0;ac=cc=0
        for r in ps_d[1:]:
            try:
                p=float(r[3]) if r[3] else 0;rc=float(r[5]) if len(r)>5 and r[5] else 0
                ex=float(r[6]) if len(r)>6 and r[6] else 0;st=r[4] if len(r)>4 else ""
                tp+=p;tr+=rc;te+=ex
                if st=="Завершён":cc+=1
                else:ac+=1
            except:pass
        ebc={};zbs={};tzp=0
        for r in exp_d[1:]:
            try:cat=r[2];amt=float(r[3]);ebc[cat]=ebc.get(cat,0)+amt
            except:pass
        for r in zp_d[1:]:
            try:s=r[0];a=float(r[1]);tzp+=a;zbs[s]=zbs.get(s,0)+a
            except:pass
        bal=tr-te-tzp;co=tp-tr
        all_dates=[]
        for r in pay_d[1:]:
            try:all_dates.append(r[3][:10])
            except:pass
        for r in exp_d[1:]:
            try:all_dates.append(r[5][:10])
            except:pass
        for r in zp_d[1:]:
            try:all_dates.append(r[2][:10])
            except:pass
        if not all_dates:all_dates=[now.strftime("%Y-%m-%d")]
        try:min_dt=datetime.strptime(min(all_dates),"%Y-%m-%d")
        except:min_dt=now
        def ws(dt):return dt-timedelta(days=dt.weekday())
        weeks=[];w=ws(now)
        while w>=ws(min_dt):weeks.append((w,w+timedelta(days=6)));w-=timedelta(days=7)
        def inw(ds,s,e):
            try:return s.strftime("%Y-%m-%d")<=ds[:10]<=e.strftime("%Y-%m-%d")
            except:return False
        cats=sorted(ebc.keys());subs=sorted(zbs.keys())
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
                    if inw(r[2],s,e):a=float(r[1]);sn=r[0];wz+=a;wzs[sn]=wzs.get(sn,0)+a
                except:pass
            wdata.append({"l":f"{s.strftime('%m/%d')}-{e.strftime('%m/%d')}","p":wp,"e":we,"z":wz,"ec":wec,"zs":wzs})
        labels=["СВОДКА","","--- ИТОГИ ---","Активных","Завершённых","Стоимость","Получено (всего)","Расходы (всего)","ЗП (всего)","Должны","БАЛАНС","","--- ЗА НЕДЕЛЮ ---","Получено","Расходы","ЗП","","Расходы по категориям:"]
        for c in cats:labels.append(f"  {c}")
        labels+=["","ЗП по сабам:"]
        for s in subs:labels.append(f"  {s}")
        nc=2+len(weeks);mx=[]
        for lb in labels:
            row=[lb]
            if lb=="СВОДКА":row.append(f"Обновлено: {now.strftime('%Y-%m-%d %H:%M')}")
            elif lb=="Активных":row.append(ac)
            elif lb=="Завершённых":row.append(cc)
            elif lb=="Стоимость":row.append(tp)
            elif lb=="Получено (всего)":row.append(tr)
            elif lb=="Расходы (всего)":row.append(te)
            elif lb=="ЗП (всего)":row.append(tzp)
            elif lb=="Должны":row.append(co)
            elif lb=="БАЛАНС":row.append(bal)
            elif lb=="--- ЗА НЕДЕЛЮ ---":row.append("Актуальное")
            elif lb.startswith("  ") and lb.strip() in ebc:row.append(ebc[lb.strip()])
            elif lb.startswith("  ") and lb.strip() in zbs:row.append(zbs[lb.strip()])
            else:row.append("")
            for wd in wdata:
                if lb=="--- ЗА НЕДЕЛЮ ---":row.append(wd["l"])
                elif lb=="Получено":row.append(wd["p"] if wd["p"] else "")
                elif lb=="Расходы":row.append(wd["e"] if wd["e"] else "")
                elif lb=="ЗП":row.append(wd["z"] if wd["z"] else "")
                elif lb.startswith("  ") and lb.strip() in ebc:row.append(wd["ec"].get(lb.strip(),""))
                elif lb.startswith("  ") and lb.strip() in zbs:row.append(wd["zs"].get(lb.strip(),""))
                else:row.append("")
            mx.append(row)
        try:sh=ss.worksheet("Сводка");sh.clear()
        except:sh=ss.add_worksheet(title="Сводка",rows=50,cols=nc)
        if sh.col_count<nc:sh.resize(cols=nc)
        sh.update("A1",mx,value_input_option="USER_ENTERED")
        logger.info("✅ Сводка обновлена")
    except Exception as e:logger.error(f"Summary error: {e}")

# ============================================================
# МЕНЮ
# ============================================================

OWNER_MENU = ReplyKeyboardMarkup([
    ["📋 Новый проект", "💰 Задаток/Приход"],
    ["💸 Записать расход", "📝 Добавить описание"],
    ["🔄 Изменить статус", "📊 Статус проекта"],
    ["📈 Сводка", "💵 Оплата сабу"],
    ["🟢 Начать смену", "🔴 Закончить смену"],
    ["📁 Архив"],
], resize_keyboard=True)

SUB_MENU_KB = ReplyKeyboardMarkup([
    ["🟢 Начать смену"],
    ["🔴 Закончить смену"],
], resize_keyboard=True)

# ============================================================
# /START — РОУТЕР
# ============================================================

async def start(update, context):
    uid = update.effective_user.id

    # Владелец
    if is_owner(uid):
        await update.message.reply_text(f"👋 {get_owner_name(uid)}!\n🏗 Wood & Stone Tracker", reply_markup=OWNER_MENU)
        return MAIN_MENU

    # Проверяем — зарегистрированный саб?
    try:
        ss = get_spreadsheet()
        info = get_sub_info(ss, uid)
        if info:
            if info["status"] == "Одобрен":
                await update.message.reply_text(f"👋 {info['name']}!\n👷 Меню субподрядчика:", reply_markup=SUB_MENU_KB)
                return SUB_MENU
            elif info["status"] == "Ожидает":
                await update.message.reply_text("⏳ Твоя заявка на рассмотрении. Подожди одобрения от владельца.")
                return ConversationHandler.END
            else:
                await update.message.reply_text("⛔ Доступ отклонён.")
                return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error checking sub: {e}")

    # Незнакомый — предложить регистрацию
    await update.message.reply_text(
        "👋 Привет! Ты не зарегистрирован.\n\n"
        "Если ты субподрядчик Wood & Stone — введи своё имя для регистрации:",
        reply_markup=ReplyKeyboardRemove(),
    )
    return SUB_REGISTER_NAME

# ============================================================
# РЕГИСТРАЦИЯ САБА
# ============================================================

async def sub_register_name(update, context):
    name = update.message.text.strip()
    uid = update.effective_user.id
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    try:
        ss = get_spreadsheet()
        subs_sh = ss.worksheet("Сабы")
        subs_sh.append_row([name, str(uid), "Ожидает", now], value_input_option="USER_ENTERED")
    except Exception as e:
        logger.error(f"Registration error: {e}")
        await update.message.reply_text("❌ Ошибка регистрации. Попробуй позже.")
        return ConversationHandler.END

    await update.message.reply_text(
        f"✅ Заявка отправлена!\n\n👷 Имя: {name}\n⏳ Ожидай одобрения от владельца."
    )

    # Отправить уведомление всем владельцам
    for owner_id in OWNERS:
        try:
            btns = [
                [InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{uid}")],
                [InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{uid}")],
            ]
            await context.bot.send_message(
                chat_id=owner_id,
                text=f"🆕 Новый саб хочет зарегистрироваться!\n\n👷 Имя: {name}\n🆔 ID: {uid}",
                reply_markup=InlineKeyboardMarkup(btns),
            )
        except Exception as e:
            logger.error(f"Notify owner error: {e}")

    return ConversationHandler.END

# ============================================================
# ОДОБРЕНИЕ/ОТКЛОНЕНИЕ САБА (владельцем)
# ============================================================

async def approve_sub(update, context):
    q = update.callback_query; await q.answer()
    if not is_owner(q.from_user.id): return

    sub_uid = q.data.replace("approve_","").replace("reject_","")
    approved = q.data.startswith("approve_")

    try:
        ss = get_spreadsheet()
        info = get_sub_info(ss, int(sub_uid))
        if info:
            sh = ss.worksheet("Сабы")
            new_status = "Одобрен" if approved else "Отклонён"
            sh.update(f"C{info['row']}", [[new_status]])

            if approved:
                await q.edit_message_text(f"✅ {info['name']} одобрен!")
                # Уведомить саба
                try:
                    await context.bot.send_message(
                        chat_id=int(sub_uid),
                        text=f"✅ Ты одобрен! Напиши /start чтобы начать."
                    )
                except: pass
            else:
                await q.edit_message_text(f"❌ {info['name']} отклонён.")
                try:
                    await context.bot.send_message(chat_id=int(sub_uid), text="❌ Твоя заявка отклонена.")
                except: pass
        else:
            await q.edit_message_text("❌ Саб не найден.")
    except Exception as e:
        logger.error(f"Approve error: {e}")
        await q.edit_message_text("❌ Ошибка.")

# ============================================================
# МЕНЮ ВЛАДЕЛЬЦА
# ============================================================

async def owner_menu(update, context):
    if not is_owner(update.effective_user.id):
        return MAIN_MENU
    t = update.message.text
    if t == "📋 Новый проект":
        await update.message.reply_text("📋 Адрес проекта:", reply_markup=ReplyKeyboardRemove())
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
    elif t == "💵 Оплата сабу":
        return await show_subs(update, context)
    elif t == "📈 Сводка":
        return await show_summary(update, context)
    elif t == "📁 Архив":
        return await show_archive(update, context)
    elif t == "🟢 Начать смену":
        return await owner_shift_start(update, context)
    elif t == "🔴 Закончить смену":
        return await owner_shift_end(update, context)
    return MAIN_MENU

# ============================================================
# МЕНЮ САБА — СМЕНЫ
# ============================================================

async def sub_menu_handler(update, context):
    uid = update.effective_user.id
    t = update.message.text

    if t == "🟢 Начать смену":
        # Проверить нет ли активной смены
        try:
            ss = get_spreadsheet()
            active = get_active_shift(ss, uid)
            if active:
                await update.message.reply_text(
                    f"⚠️ У тебя уже есть активная смена!\n"
                    f"📍 Проект: {active['pid']} — {active['address']}\n"
                    f"🕐 Начало: {active['start']}\n\n"
                    f"Сначала заверши текущую смену.",
                    reply_markup=SUB_MENU_KB
                )
                return SUB_MENU
        except: pass
        return await show_projects_for_sub(update, context)

    elif t == "🔴 Закончить смену":
        try:
            ss = get_spreadsheet()
            active = get_active_shift(ss, uid)
            if not active:
                await update.message.reply_text("❌ Нет активной смены.", reply_markup=SUB_MENU_KB)
                return SUB_MENU

            # Закончить смену
            sh = ss.worksheet("Смены")
            now = datetime.now()
            now_str = now.strftime("%Y-%m-%d %H:%M")
            start_time = datetime.strptime(active["start"], "%Y-%m-%d %H:%M")
            hours = round((now - start_time).total_seconds() / 3600, 2)

            sh.update(f"F{active['row']}", [[now_str]])
            sh.update(f"G{active['row']}", [[hours]])

            info = get_sub_info(ss, uid)
            name = info["name"] if info else "?"

            await update.message.reply_text(
                f"✅ Смена завершена!\n\n"
                f"👷 {name}\n"
                f"📍 {active['pid']} — {active['address']}\n"
                f"🕐 {active['start']} → {now_str}\n"
                f"⏱ {hours} ч.",
                reply_markup=SUB_MENU_KB
            )

            # Уведомить владельцев
            for oid in OWNERS:
                try:
                    await context.bot.send_message(
                        chat_id=oid,
                        text=f"👷 {name} завершил смену\n📍 {active['pid']} — {active['address']}\n⏱ {hours} ч."
                    )
                except: pass

        except Exception as e:
            logger.error(f"Shift end error: {e}")
            await update.message.reply_text("❌ Ошибка.", reply_markup=SUB_MENU_KB)
        return SUB_MENU

    return SUB_MENU

async def show_projects_for_sub(update, context):
    try:
        ss = get_spreadsheet()
        projects = get_active_projects(ss.worksheet("Проекты"))
    except:
        await update.message.reply_text("❌ Ошибка.", reply_markup=SUB_MENU_KB); return SUB_MENU
    if not projects:
        await update.message.reply_text("📭 Нет проектов.", reply_markup=SUB_MENU_KB); return SUB_MENU
    btns = [[InlineKeyboardButton(f"{p['id']} — {p['address']}", callback_data=f"sshift_{p['id']}")] for p in projects]
    btns.append([InlineKeyboardButton("❌ Отмена", callback_data="scancel")])
    await update.message.reply_text("📍 На каком проекте работаешь?", reply_markup=InlineKeyboardMarkup(btns))
    return SUB_SHIFT_START_SELECT

async def sub_shift_start(update, context):
    q = update.callback_query; await q.answer()
    if q.data == "scancel":
        await q.edit_message_text("❌ Отменено.")
        await q.message.reply_text("Меню:", reply_markup=SUB_MENU_KB)
        return SUB_MENU

    pid = q.data.replace("sshift_","")
    uid = q.from_user.id
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    try:
        ss = get_spreadsheet()
        info = get_sub_info(ss, uid)
        name = info["name"] if info else "?"
        addr = get_project_address(ss.worksheet("Проекты"), pid)

        # Записать начало смены
        sh = ss.worksheet("Смены")
        sh.append_row([name, str(uid), pid, addr, now, "", "", now[:10]], value_input_option="USER_ENTERED")

        await q.edit_message_text(
            f"🟢 Смена начата!\n\n"
            f"👷 {name}\n"
            f"📍 {pid} — {addr}\n"
            f"🕐 {now}"
        )

        # Уведомить владельцев
        for oid in OWNERS:
            try:
                await context.bot.send_message(
                    chat_id=oid,
                    text=f"🟢 {name} начал смену\n📍 {pid} — {addr}\n🕐 {now}"
                )
            except: pass

    except Exception as e:
        logger.error(f"Shift start error: {e}")
        await q.edit_message_text("❌ Ошибка.")

    await q.message.reply_text("Меню:", reply_markup=SUB_MENU_KB)
    return SUB_MENU

# ============================================================
# СМЕНЫ ВЛАДЕЛЬЦА
# ============================================================

async def owner_shift_start(update, context):
    uid = update.effective_user.id
    try:
        ss = get_spreadsheet()
        active = get_active_shift(ss, uid)
        if active:
            await update.message.reply_text(
                f"⚠️ Уже есть смена!\n📍 {active['pid']} — {active['address']}\n🕐 {active['start']}\n\nСначала заверши.",
                reply_markup=OWNER_MENU)
            return MAIN_MENU
    except: pass
    try:
        ss = get_spreadsheet()
        projects = get_active_projects(ss.worksheet("Проекты"))
    except:
        await update.message.reply_text("❌ Ошибка.", reply_markup=OWNER_MENU); return MAIN_MENU
    if not projects:
        await update.message.reply_text("📭 Нет проектов.", reply_markup=OWNER_MENU); return MAIN_MENU
    btns = [[InlineKeyboardButton(f"{p['id']} — {p['address']}", callback_data=f"oshift_{p['id']}")] for p in projects]
    btns.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    await update.message.reply_text("📍 Проект:", reply_markup=InlineKeyboardMarkup(btns))
    return OWNER_SHIFT_START_SELECT

async def owner_shift_start_cb(update, context):
    q = update.callback_query; await q.answer()
    if q.data == "cancel": return await cancel_cb(update, context)
    pid = q.data.replace("oshift_","")
    uid = q.from_user.id; name = get_owner_name(uid)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        ss = get_spreadsheet(); addr = get_project_address(ss.worksheet("Проекты"), pid)
        ss.worksheet("Смены").append_row([name, str(uid), pid, addr, now, "", "", now[:10]], value_input_option="USER_ENTERED")
        await q.edit_message_text(f"🟢 Смена начата!\n👤 {name}\n📍 {pid} — {addr}\n🕐 {now}")
    except Exception as e:
        logger.error(f"Error: {e}"); await q.edit_message_text("❌ Ошибка.")
    await q.message.reply_text("Меню:", reply_markup=OWNER_MENU); return MAIN_MENU

async def owner_shift_end(update, context):
    uid = update.effective_user.id; name = get_owner_name(uid)
    try:
        ss = get_spreadsheet()
        active = get_active_shift(ss, uid)
        if not active:
            await update.message.reply_text("❌ Нет активной смены.", reply_markup=OWNER_MENU)
            return MAIN_MENU
        sh = ss.worksheet("Смены")
        now = datetime.now(); now_str = now.strftime("%Y-%m-%d %H:%M")
        start_time = datetime.strptime(active["start"], "%Y-%m-%d %H:%M")
        hours = round((now - start_time).total_seconds() / 3600, 2)
        sh.update(f"F{active['row']}", [[now_str]])
        sh.update(f"G{active['row']}", [[hours]])
        await update.message.reply_text(
            f"🔴 Смена завершена!\n👤 {name}\n📍 {active['pid']} — {active['address']}\n🕐 {active['start']} → {now_str}\n⏱ {hours} ч.",
            reply_markup=OWNER_MENU)
    except Exception as e:
        logger.error(f"Error: {e}"); await update.message.reply_text("❌ Ошибка.", reply_markup=OWNER_MENU)
    return MAIN_MENU

# ============================================================
# ОБЩИЕ ФУНКЦИИ ВЛАДЕЛЬЦА
# ============================================================

async def show_projects(update, context, state, all_=False):
    try:
        ss = get_spreadsheet()
        projects = get_all_projects(ss.worksheet("Проекты")) if all_ else get_active_projects(ss.worksheet("Проекты"))
    except:
        await update.message.reply_text("❌ Ошибка.", reply_markup=OWNER_MENU); return MAIN_MENU
    if not projects:
        await update.message.reply_text("📭 Нет проектов.", reply_markup=OWNER_MENU); return MAIN_MENU
    btns = []
    for p in projects:
        lb = f"{p['id']} — {p['address']}"
        if all_: lb += f" [{p['status']}]"
        btns.append([InlineKeyboardButton(lb, callback_data=f"proj_{p['id']}")])
    btns.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    await update.message.reply_text("Проект:", reply_markup=InlineKeyboardMarkup(btns))
    return state

async def show_archive(update, context):
    try:
        arch = [p for p in get_all_projects(get_spreadsheet().worksheet("Проекты")) if p["status"]=="Завершён"]
    except: arch = []
    if not arch:
        await update.message.reply_text("📁 Пуст.", reply_markup=OWNER_MENU); return MAIN_MENU
    t = "📁 *Архив:*\n" + "\n".join(f"• {p['id']} — {p['address']}" for p in arch)
    await update.message.reply_text(t, parse_mode="Markdown", reply_markup=OWNER_MENU); return MAIN_MENU

async def cancel_cb(update, context):
    q = update.callback_query; await q.answer(); await q.edit_message_text("❌ Отменено.")
    uid = q.from_user.id
    mk = OWNER_MENU if is_owner(uid) else SUB_MENU_KB
    await q.message.reply_text("Меню:", reply_markup=mk); return MAIN_MENU if is_owner(uid) else SUB_MENU

# ============================================================
# НОВЫЙ ПРОЕКТ
# ============================================================

async def np_address(update, context):
    context.user_data["np_addr"] = update.message.text
    await update.message.reply_text("📝 Описание:"); return NEW_PROJECT_DESCRIPTION

async def np_desc(update, context):
    context.user_data["np_desc"] = update.message.text
    await update.message.reply_text("💵 Цена:"); return NEW_PROJECT_PRICE

async def np_price(update, context):
    try: price = float(update.message.text.replace(",","").replace("$",""))
    except: await update.message.reply_text("❌ Число!"); return NEW_PROJECT_PRICE
    user = get_owner_name(update.effective_user.id); now = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        ss = get_spreadsheet(); ps = init_sheets(ss); pid = get_next_project_id(ps)
        ps.append_row([pid, context.user_data["np_addr"], context.user_data["np_desc"], price, "Новый", 0, 0, 0, now, user], value_input_option="USER_ENTERED")
        await update.message.reply_text(f"✅ {pid}\n📍 {context.user_data['np_addr']}\n💵 ${price:,.2f}\n👤 {user}", reply_markup=OWNER_MENU)
    except Exception as e:
        logger.error(f"Error: {e}"); await update.message.reply_text("❌ Ошибка.", reply_markup=OWNER_MENU)
    return MAIN_MENU

# ============================================================
# ЗАДАТОК/ПРИХОД
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
    await q.edit_message_text(f"💰 Проект {context.user_data['pay_pid']}.\nКак?", reply_markup=InlineKeyboardMarkup(btns))
    return PAY_METHOD

async def pay_method(update, context):
    q = update.callback_query; await q.answer()
    if q.data == "cancel": return await cancel_cb(update, context)
    if q.data == "pay_manual": await q.edit_message_text("💰 Сумма:"); return PAY_AMOUNT
    if q.data == "pay_photo": await q.edit_message_text("📄 Фото инвойса:"); return PAY_PHOTO

async def pay_amount(update, context):
    try: amt = float(update.message.text.replace(",","").replace("$",""))
    except: await update.message.reply_text("❌ Число!"); return PAY_AMOUNT
    return await _save_pay_msg(update, context, amt, "")

async def pay_photo(update, context):
    if not update.message.photo: await update.message.reply_text("❌ Фото!"); return PAY_PHOTO
    await update.message.reply_text("⏳ Читаю...")
    ph = update.message.photo[-1]; f = await context.bot.get_file(ph.file_id)
    fp = f"/tmp/inv_{ph.file_id}.jpg"; await f.download_to_drive(fp)
    context.user_data["pay_fp"] = fp; context.user_data["pay_fid"] = ph.file_id
    total = scan_invoice_amount(fp); context.user_data["pay_amt"] = total
    if total > 0:
        btns = [[InlineKeyboardButton(f"✅ ${total:,.2f}", callback_data="payc_yes")],
                [InlineKeyboardButton("✏️ Вручную", callback_data="payc_manual")],
                [InlineKeyboardButton("❌", callback_data="cancel")]]
        await update.message.reply_text(f"📄 *${total:,.2f}*\nВерно?", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))
    else:
        btns = [[InlineKeyboardButton("✏️ Вручную", callback_data="payc_manual")],[InlineKeyboardButton("❌", callback_data="cancel")]]
        await update.message.reply_text("📄 Не распознал.\nСумма:", reply_markup=InlineKeyboardMarkup(btns))
    return PAY_CONFIRM

async def pay_confirm(update, context):
    q = update.callback_query; await q.answer()
    if q.data == "cancel":
        try: os.remove(context.user_data.get("pay_fp",""))
        except: pass
        return await cancel_cb(update, context)
    if q.data == "payc_manual": await q.edit_message_text("✏️ Сумма:"); return PAY_CONFIRM
    if q.data == "payc_yes":
        return await _save_pay_cb(q, context, context.user_data.get("pay_amt",0), context.user_data.get("pay_fid",""))

async def pay_confirm_text(update, context):
    try: amt = float(update.message.text.replace(",","").replace("$",""))
    except: await update.message.reply_text("❌ Число!"); return PAY_CONFIRM
    return await _save_pay_msg(update, context, amt, context.user_data.get("pay_fid",""))

async def _save_pay_msg(update, context, amt, fid):
    pid = context.user_data.get("pay_pid",""); user = get_user_display(update); now = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        ss = get_spreadsheet(); ps = ss.worksheet("Проекты"); addr = get_project_address(ps, pid)
        link = ""
        if fid: link = await send_photo_to_channel(context, fid, f"📄 ИНВОЙС — {pid} — {addr}\n💰 ${amt:,.2f}\n📅 {now}\n👤 {user}")
        note = user + (f" (инвойс: {link})" if link else "")
        ss.worksheet("Платежи").append_row([pid, addr, amt, now, note], value_input_option="USER_ENTERED")
        update_project_totals(ss, pid); update_summary_sheet(ss)
        await update.message.reply_text(f"✅ Платёж!\n🆔 {pid}\n💰 ${amt:,.2f}\n👤 {user}", reply_markup=OWNER_MENU)
    except Exception as e: logger.error(f"Error: {e}"); await update.message.reply_text("❌", reply_markup=OWNER_MENU)
    try: os.remove(context.user_data.get("pay_fp",""))
    except: pass
    for k in ["pay_pid","pay_fp","pay_fid","pay_amt"]: context.user_data.pop(k,None)
    return MAIN_MENU

async def _save_pay_cb(q, context, amt, fid):
    pid = context.user_data.get("pay_pid",""); user = get_owner_name(q.from_user.id); now = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        ss = get_spreadsheet(); ps = ss.worksheet("Проекты"); addr = get_project_address(ps, pid)
        link = ""
        if fid: link = await send_photo_to_channel(context, fid, f"📄 ИНВОЙС — {pid} — {addr}\n💰 ${amt:,.2f}\n📅 {now}\n👤 {user}")
        note = user + (f" (инвойс: {link})" if link else "")
        ss.worksheet("Платежи").append_row([pid, addr, amt, now, note], value_input_option="USER_ENTERED")
        update_project_totals(ss, pid); update_summary_sheet(ss)
        await q.edit_message_text(f"✅ Платёж!\n🆔 {pid}\n💰 ${amt:,.2f}\n👤 {user}")
    except Exception as e: logger.error(f"Error: {e}"); await q.edit_message_text("❌")
    try: os.remove(context.user_data.get("pay_fp",""))
    except: pass
    for k in ["pay_pid","pay_fp","pay_fid","pay_amt"]: context.user_data.pop(k,None)
    await q.message.reply_text("Меню:", reply_markup=OWNER_MENU); return MAIN_MENU

# ============================================================
# РАСХОД
# ============================================================

ECATS = ["Материалы","Субподрядчик","Аренда оборудования","Прочее"]

async def exp_select(update, context):
    q = update.callback_query; await q.answer()
    if q.data == "cancel": return await cancel_cb(update, context)
    context.user_data["exp_pid"] = q.data.replace("proj_","")
    btns = [[InlineKeyboardButton(c, callback_data=f"ecat_{c}")] for c in ECATS]
    await q.edit_message_text(f"💸 {context.user_data['exp_pid']}.\nКатегория:", reply_markup=InlineKeyboardMarkup(btns))
    return EXP_CATEGORY

async def exp_cat(update, context):
    q = update.callback_query; await q.answer()
    context.user_data["exp_cat"] = q.data.replace("ecat_","")
    btns = [[InlineKeyboardButton("✏️ Сумму", callback_data="exp_manual")],
            [InlineKeyboardButton("🧾 Фото чека", callback_data="exp_photo")],
            [InlineKeyboardButton("❌", callback_data="cancel")]]
    await q.edit_message_text(f"💸 {context.user_data['exp_cat']}.\nКак?", reply_markup=InlineKeyboardMarkup(btns))
    return EXP_METHOD

async def exp_method(update, context):
    q = update.callback_query; await q.answer()
    if q.data == "cancel": return await cancel_cb(update, context)
    if q.data == "exp_manual": await q.edit_message_text("💸 Сумма:"); return EXP_AMOUNT
    if q.data == "exp_photo": await q.edit_message_text("🧾 Фото чека:"); return EXP_PHOTO

async def exp_amount(update, context):
    try: amt = float(update.message.text.replace(",","").replace("$",""))
    except: await update.message.reply_text("❌ Число!"); return EXP_AMOUNT
    context.user_data["exp_amt"] = amt
    await update.message.reply_text("📝 Описание (или «-»):"); return EXP_DESCRIPTION

async def exp_photo(update, context):
    if not update.message.photo: await update.message.reply_text("❌ Фото!"); return EXP_PHOTO
    await update.message.reply_text("⏳ Сканирую...")
    ph = update.message.photo[-1]; f = await context.bot.get_file(ph.file_id)
    fp = f"/tmp/r_{ph.file_id}.jpg"; await f.download_to_drive(fp)
    context.user_data["exp_fp"] = fp; context.user_data["exp_fid"] = ph.file_id
    total = scan_receipt_amount(fp); context.user_data["exp_amt"] = total
    if total > 0:
        btns = [[InlineKeyboardButton(f"✅ ${total:,.2f}", callback_data="expc_yes")],
                [InlineKeyboardButton("✏️ Вручную", callback_data="expc_manual")],
                [InlineKeyboardButton("❌", callback_data="cancel")]]
        await update.message.reply_text(f"🧾 *${total:,.2f}*\nВерно?", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))
    else:
        btns = [[InlineKeyboardButton("✏️ Вручную", callback_data="expc_manual")],[InlineKeyboardButton("❌", callback_data="cancel")]]
        await update.message.reply_text("🧾 Не распознал.\nСумма:", reply_markup=InlineKeyboardMarkup(btns))
    return EXP_CONFIRM

async def exp_confirm(update, context):
    q = update.callback_query; await q.answer()
    if q.data == "cancel":
        try: os.remove(context.user_data.get("exp_fp",""))
        except: pass
        return await cancel_cb(update, context)
    if q.data == "expc_manual": await q.edit_message_text("✏️ Сумма:"); return EXP_CONFIRM
    if q.data == "expc_yes":
        context.user_data["exp_ok"] = True
        await q.edit_message_text("📝 Описание (или «-»):"); return EXP_DESCRIPTION

async def exp_confirm_text(update, context):
    try: amt = float(update.message.text.replace(",","").replace("$",""))
    except: await update.message.reply_text("❌ Число!"); return EXP_CONFIRM
    context.user_data["exp_amt"] = amt; context.user_data["exp_ok"] = True
    await update.message.reply_text("📝 Описание (или «-»):"); return EXP_DESCRIPTION

async def exp_desc(update, context):
    desc = update.message.text if update.message.text != "-" else ""
    pid = context.user_data.get("exp_pid",""); cat = context.user_data.get("exp_cat","")
    amt = context.user_data.get("exp_amt",0); fid = context.user_data.get("exp_fid","")
    user = get_user_display(update); now = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        ss = get_spreadsheet(); addr = get_project_address(ss.worksheet("Проекты"), pid)
        link = ""
        if fid: link = await send_photo_to_channel(context, fid, f"🧾 ЧЕК — {pid} — {addr}\n💸 ${amt:,.2f} [{cat}]\n📅 {now}\n👤 {user}")
        fd = desc
        if link: fd = f"{desc} (чек: {link})" if desc else f"Чек: {link}"
        ss.worksheet("Расходы").append_row([pid, addr, cat, amt, fd, now, user], value_input_option="USER_ENTERED")
        update_project_totals(ss, pid); update_summary_sheet(ss)
        await update.message.reply_text(f"✅ Расход!\n🆔 {pid}\n📂 {cat}: ${amt:,.2f}\n👤 {user}", reply_markup=OWNER_MENU)
    except Exception as e: logger.error(f"Error: {e}"); await update.message.reply_text("❌", reply_markup=OWNER_MENU)
    try: os.remove(context.user_data.get("exp_fp",""))
    except: pass
    for k in ["exp_pid","exp_cat","exp_amt","exp_fp","exp_fid","exp_ok"]: context.user_data.pop(k,None)
    return MAIN_MENU

# ============================================================
# ОПИСАНИЕ / СТАТУС / ПРОСМОТР
# ============================================================

async def desc_select(update, context):
    q = update.callback_query; await q.answer()
    if q.data == "cancel": return await cancel_cb(update, context)
    context.user_data["desc_pid"] = q.data.replace("proj_","")
    await q.edit_message_text(f"📝 {context.user_data['desc_pid']}.\nОписание:"); return STATUS_DESC_TEXT

async def desc_text(update, context):
    txt = update.message.text; pid = context.user_data["desc_pid"]
    user = get_user_display(update); now = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        ss = get_spreadsheet()
        ss.worksheet("Обновления").append_row([pid, get_project_address(ss.worksheet("Проекты"),pid), txt, now, user], value_input_option="USER_ENTERED")
        await update.message.reply_text(f"✅ {pid}\n📝 {txt}\n👤 {user}", reply_markup=OWNER_MENU)
    except: await update.message.reply_text("❌", reply_markup=OWNER_MENU)
    return MAIN_MENU

STATUSES = ["Новый","В работе","Приостановлен","Завершён"]

async def st_select(update, context):
    q = update.callback_query; await q.answer()
    if q.data == "cancel": return await cancel_cb(update, context)
    context.user_data["st_pid"] = q.data.replace("proj_","")
    btns = [[InlineKeyboardButton(s, callback_data=f"st_{s}")] for s in STATUSES]
    await q.edit_message_text(f"🔄 {context.user_data['st_pid']}:", reply_markup=InlineKeyboardMarkup(btns))
    return CHANGE_STATUS_VALUE

async def st_value(update, context):
    q = update.callback_query; await q.answer()
    ns = q.data.replace("st_",""); pid = context.user_data["st_pid"]
    user = get_owner_name(q.from_user.id); now = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        ss = get_spreadsheet(); ps = ss.worksheet("Проекты")
        rn = find_project_row(ps, pid)
        if rn != -1: ps.update(f"E{rn}", [[ns]])
        ss.worksheet("Обновления").append_row([pid, get_project_address(ps,pid), f"→ {ns}", now, user], value_input_option="USER_ENTERED")
        await q.edit_message_text(f"✅ {pid} → {ns}")
    except: await q.edit_message_text("❌")
    await q.message.reply_text("Меню:", reply_markup=OWNER_MENU); return MAIN_MENU

async def view_select(update, context):
    q = update.callback_query; await q.answer()
    if q.data == "cancel": return await cancel_cb(update, context)
    pid = q.data.replace("proj_","")
    try:
        ss = get_spreadsheet(); ps = ss.worksheet("Проекты")
        rn = find_project_row(ps, pid)
        if rn == -1: await q.edit_message_text("❌"); return MAIN_MENU
        r = ps.row_values(rn)
        price=float(r[3]) if r[3] else 0; tp=float(r[5]) if len(r)>5 and r[5] else 0
        te=float(r[6]) if len(r)>6 and r[6] else 0; bal=float(r[7]) if len(r)>7 and r[7] else 0
        t = f"📊 *{pid}*\n📍 {r[1]}\n📝 {r[2]}\n🔄 {r[4]}\n\n💵 ${price:,.2f}\n✅ Получено: ${tp:,.2f}\n💸 Расходы: ${te:,.2f}\n📈 Должен: ${price-tp:,.2f}\n💰 Баланс: ${bal:,.2f}\n"
        pp = [x for x in ss.worksheet("Платежи").get_all_values()[1:] if x[0]==pid]
        if pp:
            t += "\n*Платежи:*\n"
            for p in pp[-5:]: t += f"  • {p[3]} — ${float(p[2]):,.2f} ({p[4]})\n"
        pe = [x for x in ss.worksheet("Расходы").get_all_values()[1:] if x[0]==pid]
        if pe:
            t += "\n*Расходы:*\n"
            for e in pe[-5:]: t += f"  • {e[5]} — ${float(e[3]):,.2f} [{e[2]}] ({e[6]})\n"
        # Смены по проекту
        psh = [x for x in ss.worksheet("Смены").get_all_values()[1:] if x[2]==pid and x[5]]
        if psh:
            t += "\n*Смены:*\n"
            for s in psh[-5:]: t += f"  • {s[0]} — {s[4][:10]} — {s[6]}ч.\n"
        await q.edit_message_text(t, parse_mode="Markdown")
    except Exception as e: logger.error(f"Error: {e}"); await q.edit_message_text("❌")
    await q.message.reply_text("Меню:", reply_markup=OWNER_MENU); return MAIN_MENU

# ============================================================
# ОПЛАТА САБУ
# ============================================================

async def show_subs(update, context):
    try: subs = get_approved_subs(get_spreadsheet())
    except: subs = []
    if not subs:
        await update.message.reply_text("📭 Нет сабов.", reply_markup=OWNER_MENU); return MAIN_MENU
    btns = [[InlineKeyboardButton(s, callback_data=f"sub_{s}")] for s in subs]
    btns.append([InlineKeyboardButton("❌", callback_data="cancel")])
    await update.message.reply_text("👷 Саб:", reply_markup=InlineKeyboardMarkup(btns))
    return SUB_PAY_SELECT_SUB

async def sub_select(update, context):
    q = update.callback_query; await q.answer()
    if q.data == "cancel": return await cancel_cb(update, context)
    context.user_data["sub_name"] = q.data.replace("sub_","")
    await q.edit_message_text(f"💵 {context.user_data['sub_name']}.\nСумма:"); return SUB_PAY_AMOUNT

async def sub_amount(update, context):
    try: amt = float(update.message.text.replace(",","").replace("$",""))
    except: await update.message.reply_text("❌ Число!"); return SUB_PAY_AMOUNT
    name = context.user_data["sub_name"]; user = get_user_display(update); now = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        ss = get_spreadsheet()
        ss.worksheet("ЗП").append_row([name, amt, now, user], value_input_option="USER_ENTERED")
        update_summary_sheet(ss)
        await update.message.reply_text(f"✅ {name}\n💵 ${amt:,.2f}\n👤 {user}", reply_markup=OWNER_MENU)
    except: await update.message.reply_text("❌", reply_markup=OWNER_MENU)
    return MAIN_MENU

async def show_summary(update, context):
    try:
        t = build_summary(get_spreadsheet())
        await update.message.reply_text(t, parse_mode="Markdown", reply_markup=OWNER_MENU)
    except: await update.message.reply_text("❌", reply_markup=OWNER_MENU)
    return MAIN_MENU

async def cancel(update, context):
    uid = update.effective_user.id
    mk = OWNER_MENU if is_owner(uid) else SUB_MENU_KB
    await update.message.reply_text("❌", reply_markup=mk)
    return MAIN_MENU if is_owner(uid) else SUB_MENU

# ============================================================
# ЗАПУСК
# ============================================================

def main():
    try: ss = get_spreadsheet(); init_sheets(ss); logger.info("✅ Листы ок.")
    except Exception as e: logger.error(f"Init: {e}")

    app = Application.builder().token(BOT_TOKEN).build()

    # Обработчик одобрения/отклонения (вне ConversationHandler)
    app.add_handler(CallbackQueryHandler(approve_sub, pattern="^(approve_|reject_)"))

    ch = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, owner_menu)],
            SUB_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, sub_menu_handler)],
            SUB_REGISTER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, sub_register_name)],
            SUB_SHIFT_START_SELECT: [CallbackQueryHandler(sub_shift_start, pattern="^sshift_"), CallbackQueryHandler(cancel_cb, pattern="^scancel$")],
            NEW_PROJECT_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, np_address)],
            NEW_PROJECT_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, np_desc)],
            NEW_PROJECT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, np_price)],
            PAY_SELECT_PROJECT: [CallbackQueryHandler(pay_select, pattern="^proj_"), CallbackQueryHandler(cancel_cb, pattern="^cancel$")],
            PAY_METHOD: [CallbackQueryHandler(pay_method, pattern="^pay_"), CallbackQueryHandler(cancel_cb, pattern="^cancel$")],
            PAY_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, pay_amount)],
            PAY_PHOTO: [MessageHandler(filters.PHOTO, pay_photo)],
            PAY_CONFIRM: [CallbackQueryHandler(pay_confirm, pattern="^payc_"), CallbackQueryHandler(cancel_cb, pattern="^cancel$"), MessageHandler(filters.TEXT & ~filters.COMMAND, pay_confirm_text)],
            EXP_SELECT_PROJECT: [CallbackQueryHandler(exp_select, pattern="^proj_"), CallbackQueryHandler(cancel_cb, pattern="^cancel$")],
            EXP_CATEGORY: [CallbackQueryHandler(exp_cat, pattern="^ecat_")],
            EXP_METHOD: [CallbackQueryHandler(exp_method, pattern="^exp_"), CallbackQueryHandler(cancel_cb, pattern="^cancel$")],
            EXP_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, exp_amount)],
            EXP_PHOTO: [MessageHandler(filters.PHOTO, exp_photo)],
            EXP_CONFIRM: [CallbackQueryHandler(exp_confirm, pattern="^expc_"), CallbackQueryHandler(cancel_cb, pattern="^cancel$"), MessageHandler(filters.TEXT & ~filters.COMMAND, exp_confirm_text)],
            EXP_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, exp_desc)],
            STATUS_DESC_SELECT_PROJECT: [CallbackQueryHandler(desc_select, pattern="^proj_"), CallbackQueryHandler(cancel_cb, pattern="^cancel$")],
            STATUS_DESC_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, desc_text)],
            CHANGE_STATUS_SELECT: [CallbackQueryHandler(st_select, pattern="^proj_"), CallbackQueryHandler(cancel_cb, pattern="^cancel$")],
            CHANGE_STATUS_VALUE: [CallbackQueryHandler(st_value, pattern="^st_")],
            VIEW_STATUS_SELECT: [CallbackQueryHandler(view_select, pattern="^proj_"), CallbackQueryHandler(cancel_cb, pattern="^cancel$")],
            SUB_PAY_SELECT_SUB: [CallbackQueryHandler(sub_select, pattern="^sub_"), CallbackQueryHandler(cancel_cb, pattern="^cancel$")],
            SUB_PAY_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, sub_amount)],
            OWNER_SHIFT_START_SELECT: [CallbackQueryHandler(owner_shift_start_cb, pattern="^oshift_"), CallbackQueryHandler(cancel_cb, pattern="^cancel$")],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
    )
    app.add_handler(ch)
    logger.info("🚀 Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
