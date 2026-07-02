"""
Receipt / invoice photo scanning flow.
"""

import os
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import (
    log, owner_name, RECEIPTS_CHANNEL_ID,
    OWNER_MENU_ST, PHOTO_WAIT_RECEIPT, PHOTO_CONFIRM_RECEIPT, PHOTO_WAIT_INVOICE, PHOTO_CONFIRM_INVOICE,
)
from keyboards import OWNER_KB
from sheets import get_ss, active_projects, proj_po, update_totals, update_summary_sheet
from ai import scan_amount

async def send_to_channel(ctx, fid, caption):
    if not RECEIPTS_CHANNEL_ID: return ""
    try:
        msg=await ctx.bot.send_photo(chat_id=RECEIPTS_CHANNEL_ID,photo=fid,caption=caption)
        if msg: return f"https://t.me/c/{str(RECEIPTS_CHANNEL_ID).replace('-100','')}/{msg.message_id}"
    except Exception as e: log.error(f"Channel: {e}")
    return ""

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
