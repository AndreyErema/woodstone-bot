"""
Owner menu, free-text AI-driven actions, and entry point (/start, /cancel).

Write actions parsed from free text (create_project, payment, expense, ...)
are staged and shown to the owner as a preview before anything is written to
Sheets — see CONFIRM_ACTIONS / describe_action / apply_write_action.
"""

from datetime import datetime, timedelta
from telegram import ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler

from config import (
    log, is_owner, owner_name, OWNERS,
    OWNER_MENU_ST, OWNER_FREE_TEXT, PHOTO_WAIT_RECEIPT, PHOTO_WAIT_INVOICE,
    SUB_MENU_ST, SUB_REGISTER_NAME, AI_CONFIRM_ST, AI_EDIT_ST,
)
from keyboards import OWNER_KB, SUB_KB
from sheets import (
    get_ss, sub_info, next_pid, next_cid, active_projects, all_projects,
    find_proj_row, proj_po, update_totals, approved_subs, update_summary_sheet, build_summary,
    next_reminder_id, pending_reminders,
)
from ai import ai_parse
from handlers_scan import show_proj_btns
from handlers_shifts import owner_shift_start, owner_shift_end
from sheet_deploy import deploy_all

# Actions that change data and must be confirmed by the owner before writing.
CONFIRM_ACTIONS = {
    "create_project", "payment", "expense", "change_status", "journal",
    "pay_sub", "set_rate", "record_hours", "add_customer",
    "update_project", "update_customer", "create_reminder", "update_reminder",
}

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

async def cancel_cmd(update, ctx):
    uid=update.effective_user.id
    kb=OWNER_KB if is_owner(uid) else SUB_KB
    ctx.user_data.pop("pending_action",None); ctx.user_data.pop("pending_text",None)
    await update.message.reply_text("❌ Cancelled.", reply_markup=kb)
    return OWNER_MENU_ST if is_owner(uid) else SUB_MENU_ST

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
    if t=="📅 Кто где завтра": return await do_tomorrow(update, ctx)

    # Free text → AI parse
    return await process_free_text(update, ctx, t)

async def free_text_handler(update, ctx):
    return await process_free_text(update, ctx, update.message.text)

# ============================================================
# AI PARSE → PREVIEW → CONFIRM → WRITE
# ============================================================
def describe_action(ss, action):
    """Human-readable preview of a parsed write action, shown before it's saved."""
    act=action.get("action")
    if act=="create_project":
        return (f"📋 *Новый проект*\nPO: {action.get('po') or '—'}\n"
                f"Клиент: {action.get('customer') or '—'}\n"
                f"Телефон: {action.get('phone') or '—'}\n"
                f"Адрес: {action.get('address') or '—'}\n"
                f"Описание: {action.get('description') or '—'}\n"
                f"Цена: ${float(action.get('price',0) or 0):,.2f}")
    if act=="payment":
        pid=action.get("project_id",""); po=proj_po(ss.worksheet("Projects"),pid)
        return f"💰 *Платёж*\nПроект: {pid} ({po or '?'})\nСумма: ${float(action.get('amount',0) or 0):,.2f}\nПримечание: {action.get('note') or '—'}"
    if act=="expense":
        pid=action.get("project_id",""); po=proj_po(ss.worksheet("Projects"),pid)
        return f"💸 *Расход*\nПроект: {pid} ({po or '?'})\nКатегория: {action.get('category','Materials')}\nСумма: ${float(action.get('amount',0) or 0):,.2f}\nОписание: {action.get('description') or '—'}"
    if act=="change_status":
        pid=action.get("project_id",""); po=proj_po(ss.worksheet("Projects"),pid)
        return f"🔄 *Смена статуса*\nПроект: {pid} ({po or '?'})\nНовый статус: {action.get('status')}"
    if act=="journal":
        pid=action.get("project_id",""); po=proj_po(ss.worksheet("Projects"),pid)
        return f"📝 *Запись в журнал*\nПроект: {pid} ({po or '?'})\n{action.get('description')}"
    if act=="pay_sub":
        return f"💵 *Выплата сабу*\nСаб: {action.get('sub_name')}\nСумма: ${float(action.get('amount',0) or 0):,.2f}"
    if act=="set_rate":
        return f"💲 *Ставка*\nСаб: {action.get('sub_name')}\nСтавка: ${float(action.get('rate',0) or 0)}/ч"
    if act=="record_hours":
        pid=action.get("project_id","")
        po=proj_po(ss.worksheet("Projects"),pid) if pid else ""
        proj_line=f"{pid} ({po})" if pid else "—"
        return f"⏱ *Часы*\nСаб: {action.get('sub_name')}\nЧасы: {action.get('hours')}\nПроект: {proj_line}\nДата: {action.get('date') or 'сегодня'}"
    if act=="add_customer":
        return (f"👤 *Новый клиент*\nИмя: {action.get('name')}\nАдрес: {action.get('address') or '—'}\n"
                f"Телефон: {action.get('phone') or '—'}\nEmail: {action.get('email') or '—'}\nСвязь: {action.get('communication') or '—'}")
    if act=="update_project":
        pid=action.get("project_id",""); po=proj_po(ss.worksheet("Projects"),pid)
        return f"✏️ *Изменение проекта*\nПроект: {pid} ({po or '?'})\nПоле: {action.get('field')}\nНовое значение: {action.get('value')}"
    if act=="update_customer":
        return f"✏️ *Изменение клиента*\nИмя: {action.get('name')}\nПоле: {action.get('field')}\nНовое значение: {action.get('value')}"
    if act=="create_reminder":
        pid=action.get("project_id","")
        po=proj_po(ss.worksheet("Projects"),pid) if pid else ""
        who=", ".join(action.get("assigned_to") or []) or "—"
        return (f"⏰ *Напоминание*\nДата: {action.get('date') or '—'}\nВремя: {action.get('time') or '(без времени, только дайджест)'}\n"
                f"Кому: {who}\nЧто: {action.get('description') or '—'}\nПроект: {f'{pid} ({po})' if pid else '—'}")
    if act=="update_reminder":
        return f"✏️ *Изменение напоминания*\nПоиск: {action.get('match')}\nПоле: {action.get('field')}\nНовое значение: {action.get('value')}"
    return "…"

CONFIRM_BTNS = InlineKeyboardMarkup([
    [InlineKeyboardButton("✅ Подтвердить",callback_data="aiok"), InlineKeyboardButton("✏️ Изменить",callback_data="aiedit")],
    [InlineKeyboardButton("❌ Отмена",callback_data="aicancel")],
])

async def process_free_text(update, ctx, text):
    try: ss=get_ss(); projs=all_projects(ss.worksheet("Projects")); subs=approved_subs(ss)
    except Exception as e:
        log.error(f"DB: {e}"); await update.message.reply_text("❌ Database error.", reply_markup=OWNER_KB); return OWNER_MENU_ST

    await update.message.reply_text("⏳ Processing...")
    sender=owner_name(update.effective_user.id)
    action=ai_parse(text, projs, subs, sender_name=sender, owners=list(OWNERS.values()))
    act=action.get("action","unknown")

    if act in CONFIRM_ACTIONS:
        ctx.user_data["pending_action"]=action
        ctx.user_data["pending_text"]=text
        await update.message.reply_text(f"{describe_action(ss,action)}\n\nВсё верно?", parse_mode="Markdown", reply_markup=CONFIRM_BTNS)
        return AI_CONFIRM_ST

    return await run_readonly_action(update, ctx, ss, action)

async def run_readonly_action(update, ctx, ss, action):
    """Actions that don't change data — executed immediately, no confirmation needed."""
    act=action.get("action")
    try:
        if act=="show_summary":
            return await do_summary(update, ctx)
        if act=="show_project":
            return await do_show_project(update, ctx, ss, action.get("project_id",""))
        if act=="list_projects":
            projs=active_projects(ss.worksheet("Projects"))
            if not projs: await update.message.reply_text("📭 No active projects.", reply_markup=OWNER_KB)
            else:
                t="📋 *Active projects:*\n\n"
                for p in projs: t+=f"• {p['id']} — {p['po']} [{p['status']}]\n"
                await update.message.reply_text(t, parse_mode="Markdown", reply_markup=OWNER_KB)
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

async def apply_write_action(ss, action, uname, now_s):
    """Executes a confirmed write action against Sheets. Returns the result message text."""
    act=action.get("action")
    try:
        if act=="create_project":
            ps=ss.worksheet("Projects"); pid=next_pid(ps)
            po=action.get("po",""); cust=action.get("customer",""); phone=action.get("phone",""); addr=action.get("address",""); desc=action.get("description",""); price=float(action.get("price",0))
            if not po and addr: po=addr[:30]
            ps.append_row([pid,po,cust,addr,desc,price,"New",0,0,0,now_s,uname], value_input_option="USER_ENTERED")
            update_totals(ss,pid)
            if cust:
                try:
                    cs=ss.worksheet("Customers"); cid=next_cid(cs)
                    cs.append_row([cid,cust,addr,phone,"",po,"","",now_s,uname], value_input_option="USER_ENTERED")
                except:pass
            return f"✅ Project created!\n🆔 {pid}\n📋 PO: {po}\n👤 Customer: {cust or '—'}\n📞 {phone or '—'}\n📍 {addr}\n📝 {desc or '—'}\n💵 ${price:,.2f}"

        if act=="payment":
            pid=action.get("project_id",""); amt=float(action.get("amount",0)); note=action.get("note","")
            po=proj_po(ss.worksheet("Projects"),pid)
            ss.worksheet("Payments").append_row([pid,po,amt,now_s,f"{uname} {note}".strip(),""], value_input_option="USER_ENTERED")
            update_totals(ss,pid); update_summary_sheet(ss)
            return f"✅ Payment recorded!\n🆔 {pid} ({po})\n💰 ${amt:,.2f}"

        if act=="expense":
            pid=action.get("project_id",""); cat=action.get("category","Materials"); amt=float(action.get("amount",0)); desc=action.get("description","")
            po=proj_po(ss.worksheet("Projects"),pid)
            ss.worksheet("Expenses").append_row([pid,po,cat,amt,desc,now_s,uname], value_input_option="USER_ENTERED")
            update_totals(ss,pid); update_summary_sheet(ss)
            return f"✅ Expense recorded!\n🆔 {pid} ({po})\n📂 {cat}: ${amt:,.2f}\n📝 {desc or '—'}"

        if act=="change_status":
            pid=action.get("project_id",""); ns=action.get("status","")
            ps=ss.worksheet("Projects"); rn=find_proj_row(ps,pid)
            if rn<1: return "❌ Project not found."
            ps.update(f"G{rn}",[[ns]])
            po=proj_po(ps,pid)
            ss.worksheet("Journal").append_row([pid,po,f"Status → {ns}",now_s,uname], value_input_option="USER_ENTERED")
            return f"✅ {pid} ({po}) → {ns}"

        if act=="journal":
            pid=action.get("project_id",""); desc=action.get("description","")
            po=proj_po(ss.worksheet("Projects"),pid)
            ss.worksheet("Journal").append_row([pid,po,desc,now_s,uname], value_input_option="USER_ENTERED")
            return f"✅ Journal entry added!\n🆔 {pid} ({po})\n📝 {desc}"

        if act=="pay_sub":
            sn=action.get("sub_name",""); amt=float(action.get("amount",0))
            ss.worksheet("Payroll").append_row(["",sn,amt,now_s,uname], value_input_option="USER_ENTERED")
            update_summary_sheet(ss)
            return f"✅ Paid!\n👷 {sn}: ${amt:,.2f}"

        if act=="set_rate":
            sn=action.get("sub_name",""); rate=float(action.get("rate",0))
            sh=ss.worksheet("Subs")
            for i,r in enumerate(sh.get_all_values()[1:],2):
                if r[1].lower().strip()==sn.lower().strip():
                    sh.update(f"F{i}",[[rate]])
                    return f"✅ {sn} rate → ${rate}/hr"
            return f"❌ Sub '{sn}' not found."

        if act=="record_hours":
            sn=action.get("sub_name",""); hrs=float(action.get("hours",0)); pid=action.get("project_id","")
            dt=action.get("date","")
            if not dt or dt=="today": dt=datetime.now().strftime("%Y-%m-%d")
            po=proj_po(ss.worksheet("Projects"),pid) if pid else ""
            sub_tid=""
            for r in ss.worksheet("Subs").get_all_values()[1:]:
                if r[1].lower().strip()==sn.lower().strip(): sub_tid=r[0]; break
            ss.worksheet("Shifts").append_row([dt,sn,sub_tid,pid,f"{dt} 09:00",f"{dt} {9+hrs:.0f}:00",hrs,po], value_input_option="USER_ENTERED")
            rate=0
            for s in approved_subs(ss):
                if s["name"].lower().strip()==sn.lower().strip(): rate=s["rate"]; break
            update_summary_sheet(ss)
            if rate>0:
                pay=round(hrs*rate,2)
                ss.worksheet("Payroll").append_row(["",sn,pay,f"{dt} (manual)",uname], value_input_option="USER_ENTERED")
                return f"✅ {sn}: {hrs}h on {dt}\n💵 Auto-pay: ${pay:,.2f} ({hrs}h × ${rate}/hr)"
            return f"✅ {sn}: {hrs}h on {dt}\n⚠️ No rate set — payroll not calculated."

        if act=="add_customer":
            cs=ss.worksheet("Customers"); cid=next_cid(cs)
            cs.append_row([cid,action.get("name",""),action.get("address",""),action.get("phone",""),action.get("email",""),"",action.get("communication",""),"",now_s,uname], value_input_option="USER_ENTERED")
            return f"✅ Customer added: {action.get('name','')}"

        if act=="update_project":
            pid=action.get("project_id",""); field=action.get("field",""); value=action.get("value","")
            ps=ss.worksheet("Projects"); rn=find_proj_row(ps,pid)
            if rn<1: return "❌ Project not found."
            col_map={"po":"B","customer":"C","address":"D","description":"E","price":"F"}
            col=col_map.get(field)
            if not col: return f"❌ Unknown field: {field}"
            ps.update(f"{col}{rn}",[[value]], value_input_option="USER_ENTERED")
            po=proj_po(ps,pid)
            ss.worksheet("Journal").append_row([pid,po,f"{field} → {value}",now_s,uname], value_input_option="USER_ENTERED")
            return f"✅ Project {pid} updated: {field} = {value}"

        if act=="update_customer":
            name=action.get("name",""); field=action.get("field",""); value=action.get("value","")
            cs=ss.worksheet("Customers")
            col_map={"phone":"D","email":"E","communication":"G","address":"C","description":"H"}
            col=col_map.get(field)
            if not col: return f"❌ Unknown field: {field}"
            for i,r in enumerate(cs.get_all_values()[1:],2):
                if len(r)>1 and r[1].lower().strip()==name.lower().strip():
                    cs.update(f"{col}{i}",[[value]], value_input_option="USER_ENTERED")
                    return f"✅ Customer {name} updated: {field} = {value}"
            return f"❌ Customer '{name}' not found."

        if act=="create_reminder":
            rs=ss.worksheet("Reminders"); rid=next_reminder_id(rs)
            date=action.get("date",""); time_=action.get("time",""); who=action.get("assigned_to") or []
            desc=action.get("description",""); pid=action.get("project_id","")
            po=proj_po(ss.worksheet("Projects"),pid) if pid else ""
            rs.append_row([rid,date,time_,", ".join(who),desc,pid,po,"Pending",uname,now_s,""], value_input_option="USER_ENTERED")
            return f"✅ Напоминание создано!\n📅 {date} {time_}\n👤 {', '.join(who) or '—'}\n📝 {desc}"

        if act=="update_reminder":
            match=(action.get("match","") or "").lower().strip()
            field=action.get("field",""); value=action.get("value","")
            col_map={"date":"B","time":"C","status":"H"}
            col=col_map.get(field)
            if not col: return f"❌ Unknown field: {field}"
            rs=ss.worksheet("Reminders")
            for i,r in enumerate(rs.get_all_values()[1:],2):
                if len(r)>7 and r[7]=="Pending" and match and match in (r[4] if len(r)>4 else "").lower():
                    rs.update(f"{col}{i}",[[value]], value_input_option="USER_ENTERED")
                    return f"✅ Напоминание обновлено: {field} = {value}"
            return "❌ Напоминание не найдено."

        return "❌ Unknown action."
    except Exception as e:
        log.error(f"Apply action error: {e}")
        return f"❌ Error: {e}"

async def ai_confirm_cb(update, ctx):
    q=update.callback_query; await q.answer()
    if q.data=="aicancel":
        ctx.user_data.pop("pending_action",None); ctx.user_data.pop("pending_text",None)
        await q.edit_message_text("❌ Отменено.")
        await q.message.reply_text("Menu:", reply_markup=OWNER_KB)
        return OWNER_MENU_ST

    if q.data=="aiedit":
        await q.edit_message_text("✏️ Опиши, что исправить (или пришли заново):")
        return AI_EDIT_ST

    if q.data=="aiok":
        action=ctx.user_data.get("pending_action",{})
        uname=owner_name(q.from_user.id); now_s=datetime.now().strftime("%Y-%m-%d %H:%M")
        try: ss=get_ss()
        except Exception as e:
            log.error(f"DB: {e}")
            await q.edit_message_text("❌ Database error.")
            await q.message.reply_text("Menu:", reply_markup=OWNER_KB)
            return OWNER_MENU_ST
        msg=await apply_write_action(ss, action, uname, now_s)
        ctx.user_data.pop("pending_action",None); ctx.user_data.pop("pending_text",None)
        await q.edit_message_text(msg)
        await q.message.reply_text("Menu:", reply_markup=OWNER_KB)
        return OWNER_MENU_ST

async def ai_edit_text(update, ctx):
    correction=update.message.text
    original=ctx.user_data.get("pending_text","")
    try: ss=get_ss(); projs=all_projects(ss.worksheet("Projects")); subs=approved_subs(ss)
    except Exception as e:
        log.error(f"DB: {e}"); await update.message.reply_text("❌ Database error.", reply_markup=OWNER_KB); return OWNER_MENU_ST

    await update.message.reply_text("⏳ Processing...")
    merged=f"{original}\n\nCORRECTION: {correction}"
    sender=owner_name(update.effective_user.id)
    action=ai_parse(merged, projs, subs, sender_name=sender, owners=list(OWNERS.values()))
    act=action.get("action","unknown")

    if act not in CONFIRM_ACTIONS:
        ctx.user_data.pop("pending_action",None); ctx.user_data.pop("pending_text",None)
        reply=action.get("reply","Не удалось разобрать правку. Попробуй ещё раз с нуля.")
        await update.message.reply_text(reply, reply_markup=OWNER_KB)
        return OWNER_MENU_ST

    ctx.user_data["pending_action"]=action
    ctx.user_data["pending_text"]=merged
    await update.message.reply_text(f"{describe_action(ss,action)}\n\nВсё верно?", parse_mode="Markdown", reply_markup=CONFIRM_BTNS)
    return AI_CONFIRM_ST

# ============================================================
# SHOW PROJECT / SUMMARY / ARCHIVE
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
        await update.message.reply_text(t, reply_markup=OWNER_KB)
    except: await update.message.reply_text("❌ Error.", reply_markup=OWNER_KB)
    return OWNER_MENU_ST

async def do_tomorrow(update, ctx):
    """Кто где завтра: all Pending reminders dated tomorrow, across all owners."""
    try:
        ss=get_ss()
        tomorrow=(datetime.now()+timedelta(days=1)).strftime("%Y-%m-%d")
        rems=[r for r in pending_reminders(ss) if r["date"]==tomorrow]
        if not rems:
            await update.message.reply_text("📅 На завтра напоминаний нет.", reply_markup=OWNER_KB)
            return OWNER_MENU_ST
        rems.sort(key=lambda r: r["time"] or "99:99")
        t=f"📅 Завтра ({tomorrow}):\n\n"
        for r in rems:
            who=", ".join(r["assigned_to"]) or "—"
            time_part=f"{r['time']} — " if r["time"] else ""
            proj_part=f" [{r['customer']}]" if r["customer"] else ""
            t+=f"• {time_part}{who}: {r['description']}{proj_part}\n"
        await update.message.reply_text(t, reply_markup=OWNER_KB)
    except Exception as e:
        log.error(f"do_tomorrow: {e}")
        await update.message.reply_text("❌ Error.", reply_markup=OWNER_KB)
    return OWNER_MENU_ST

async def deploy_sheet_cmd(update, ctx):
    uid=update.effective_user.id
    if not is_owner(uid): return
    await update.message.reply_text("⏳ Deploying Timesheet / Project Hours structure...")
    try:
        ss=get_ss(); deploy_all(ss)
        await update.message.reply_text("✅ Done. Timesheet and Project Hours are set up — pick a period in their A2/B2 cells.", reply_markup=OWNER_KB)
    except Exception as e:
        log.error(f"deploy_sheet: {e}")
        await update.message.reply_text(f"❌ Error: {e}", reply_markup=OWNER_KB)
