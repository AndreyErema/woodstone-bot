"""
Sub registration, approval, and sub-side shift tracking.
"""

from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler

from config import log, is_owner, OWNERS, SUB_MENU_ST, SUB_SHIFT_SELECT
from keyboards import SUB_KB
from sheets import get_ss, sub_info, active_shift, active_projects, proj_po, update_timesheet_sheet, update_project_hours_sheet

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
            update_timesheet_sheet(ss); update_project_hours_sheet(ss)
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
