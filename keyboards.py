"""
Reply keyboards for owners and subs.
"""

from telegram import ReplyKeyboardMarkup

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
