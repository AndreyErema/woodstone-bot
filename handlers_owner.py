"""
Owner menu, free-text AI-driven actions, and entry point (/start, /cancel).
"""

from datetime import datetime
from telegram import ReplyKeyboardRemove
from telegram.ext import ConversationHandler

from config import (
    log, is_owner, owner_name,
    OWNER_MENU_ST, OWNER_FREE_TEXT, PHOTO_WAIT_RECEIPT, PHOTO_WAIT_INVOICE,
    SUB_MENU_ST, SUB_REGISTER_NAME,
)
from keyboards import OWNER_KB, SUB_KB
from sheets import (
    get_ss, sub_info, next_pid, next_cid, active_projects, all_projects,
    find_proj_row, proj_po, update_totals, approved_subs, update_summary_sheet, build_summary,
)
from ai import ai_parse
from handlers_scan import show_proj_btns
from handlers_shifts import owner_shift_start, owner_shift_end

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
            dt=action.get("date","")
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
