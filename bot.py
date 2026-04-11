"""
Wood & Stone Construction LLC — Telegram Project Tracker Bot v3
Листы Google Sheets:
  - Проекты — сводка (1 строка = 1 проект)
  - Платежи — все входящие платежи от клиентов
  - Расходы — все расходы по проектам
  - Обновления — описания статуса
  - Сабы — список субподрядчиков
  - ЗП — оплаты субподрядчикам (без привязки к проекту)
  - Сводка — общая статистика
"""

import os
import json
import logging
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
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import base64
import re

# ============================================================
# НАСТРОЙКИ
# ============================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "YOUR_SPREADSHEET_ID_HERE")
DRIVE_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID", "YOUR_DRIVE_FOLDER_ID_HERE")
GOOGLE_CREDS_FILE = os.environ.get("GOOGLE_CREDS_FILE", "credentials.json")

ALLOWED_USERS = {
    76341596: "Jeremy",
    139580832: "Serge",
    1173624685: "Kastet",
    # 444555666: "Partner 4",
}

# ============================================================
# ЛОГИРОВАНИЕ
# ============================================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============================================================
# СОСТОЯНИЯ РАЗГОВОРА
# ============================================================
(
    MAIN_MENU,
    NEW_PROJECT_ADDRESS,
    NEW_PROJECT_DESCRIPTION,
    NEW_PROJECT_PRICE,
    PAYMENT_SELECT_PROJECT,
    PAYMENT_AMOUNT,
    EXPENSE_SELECT_PROJECT,
    EXPENSE_CATEGORY,
    EXPENSE_AMOUNT,
    EXPENSE_DESCRIPTION,
    STATUS_DESC_SELECT_PROJECT,
    STATUS_DESC_TEXT,
    CHANGE_STATUS_SELECT_PROJECT,
    CHANGE_STATUS_VALUE,
    VIEW_STATUS_SELECT_PROJECT,
    ADD_SUB_NAME,
    SUB_PAY_SELECT_SUB,
    SUB_PAY_AMOUNT,
    RECEIPT_SELECT_PROJECT,
    RECEIPT_PHOTO,
    RECEIPT_CONFIRM,
) = range(21)

# ============================================================
# GOOGLE SHEETS — ПОДКЛЮЧЕНИЕ
# ============================================================

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_google_creds():
    creds_json = os.environ.get("GOOGLE_CREDS_JSON", "")
    if creds_json:
        creds_info = json.loads(creds_json)
        creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    else:
        creds = Credentials.from_service_account_file(GOOGLE_CREDS_FILE, scopes=SCOPES)
    return creds


def get_spreadsheet():
    creds = get_google_creds()
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    return spreadsheet


def get_drive_service():
    creds = get_google_creds()
    return build("drive", "v3", credentials=creds)


def upload_receipt_to_drive(file_path: str) -> str:
    """Загрузить фото чека в Google Drive в папку Чеки/YYYY-MM."""
    service = get_drive_service()
    now = datetime.now()
    month_folder_name = f"Чеки {now.strftime('%Y-%m')}"

    # Найти папку месяца внутри основной папки
    query = (
        f"name='{month_folder_name}' and "
        f"'{DRIVE_FOLDER_ID}' in parents and "
        f"mimeType='application/vnd.google-apps.folder' and "
        f"trashed=false"
    )
    results = service.files().list(
        q=query, fields="files(id)",
        supportsAllDrives=True, includeItemsFromAllDrives=True
    ).execute()
    folders = results.get("files", [])

    if folders:
        folder_id = folders[0]["id"]
    else:
        # Создать папку месяца внутри основной папки (владелец = основная папка)
        folder_metadata = {
            "name": month_folder_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [DRIVE_FOLDER_ID],
        }
        folder = service.files().create(
            body=folder_metadata, fields="id",
            supportsAllDrives=True
        ).execute()
        folder_id = folder["id"]

    # Загрузить фото
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    file_metadata = {
        "name": f"receipt_{timestamp}.jpg",
        "parents": [folder_id],
    }
    media = MediaFileUpload(file_path, mimetype="image/jpeg")
    uploaded = service.files().create(
        body=file_metadata, media_body=media, fields="id, webViewLink",
        supportsAllDrives=True
    ).execute()

    # Сделать доступным по ссылке
    service.permissions().create(
        fileId=uploaded["id"],
        body={"type": "anyone", "role": "reader"},
        supportsAllDrives=True
    ).execute()

    return uploaded.get("webViewLink", "")


def extract_total_from_receipt(file_path: str) -> float:
    """Распознать сумму с фото чека через Claude API."""
    try:
        import urllib.request

        ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
        if not ANTHROPIC_API_KEY:
            logger.error("ANTHROPIC_API_KEY not set")
            return 0.0

        with open(file_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

        request_body = json.dumps({
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 100,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": "This is a photo of a receipt. Find the TOTAL amount (the final amount paid, not subtotal, not tax). Return ONLY the number, nothing else. Example: 2004.14"
                    }
                ],
            }],
        })

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=request_body.encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
            },
        )

        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        # Извлечь текст ответа
        answer = ""
        for block in result.get("content", []):
            if block.get("type") == "text":
                answer += block.get("text", "")

        logger.info(f"Claude receipt answer: {answer}")

        # Парсим число
        answer = answer.strip().replace("$", "").replace(",", "")
        total = float(answer)
        return total

    except Exception as e:
        logger.error(f"Receipt OCR error: {e}")
        return 0.0


def get_or_create_sheet(spreadsheet, title, headers):
    try:
        sheet = spreadsheet.worksheet(title)
    except gspread.exceptions.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(title=title, rows=1000, cols=len(headers))
        sheet.append_row(headers, value_input_option="USER_ENTERED")
    return sheet


def init_sheets(spreadsheet):
    projects = get_or_create_sheet(spreadsheet, "Проекты", [
        "Project ID", "Адрес", "Описание", "Цена",
        "Статус", "Получено", "Расходы", "Баланс",
        "Дата создания", "Создал"
    ])
    get_or_create_sheet(spreadsheet, "Платежи", [
        "Project ID", "Адрес", "Сумма", "Дата", "Кто записал"
    ])
    get_or_create_sheet(spreadsheet, "Расходы", [
        "Project ID", "Адрес", "Категория", "Сумма",
        "Описание", "Дата", "Кто записал"
    ])
    get_or_create_sheet(spreadsheet, "Обновления", [
        "Project ID", "Адрес", "Текст", "Дата", "Кто записал"
    ])
    get_or_create_sheet(spreadsheet, "Сабы", [
        "Имя", "Дата добавления", "Кто добавил"
    ])
    get_or_create_sheet(spreadsheet, "ЗП", [
        "Субподрядчик", "Сумма", "Дата", "Кто записал"
    ])
    get_or_create_sheet(spreadsheet, "Сводка", [
        "Показатель", "Значение", "Примечание"
    ])
    return projects


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def check_access(update: Update) -> bool:
    return update.effective_user.id in ALLOWED_USERS


def get_user_name(update: Update) -> str:
    return ALLOWED_USERS.get(update.effective_user.id, "Unknown")


def get_user_name_by_id(user_id: int) -> str:
    return ALLOWED_USERS.get(user_id, "Unknown")


def get_next_project_id(projects_sheet) -> str:
    all_records = projects_sheet.get_all_values()
    if len(all_records) <= 1:
        return "0001"
    max_id = 0
    for row in all_records[1:]:
        try:
            pid = int(row[0])
            if pid > max_id:
                max_id = pid
        except (ValueError, IndexError):
            continue
    return str(max_id + 1).zfill(4)


def get_active_projects(projects_sheet) -> list:
    all_records = projects_sheet.get_all_values()
    if len(all_records) <= 1:
        return []
    projects = []
    for row in all_records[1:]:
        try:
            status = row[4] if len(row) > 4 else "Новый"
            if status != "Завершён":
                projects.append({"id": row[0], "address": row[1], "status": status})
        except (IndexError, ValueError):
            continue
    return projects


def get_all_projects(projects_sheet) -> list:
    all_records = projects_sheet.get_all_values()
    if len(all_records) <= 1:
        return []
    projects = []
    for row in all_records[1:]:
        try:
            projects.append({
                "id": row[0], "address": row[1],
                "status": row[4] if len(row) > 4 else "Новый",
            })
        except (IndexError, ValueError):
            continue
    return projects


def find_project_row(projects_sheet, project_id: str) -> int:
    all_records = projects_sheet.get_all_values()
    for i, row in enumerate(all_records):
        if row[0] == project_id:
            return i + 1
    return -1


def update_project_totals(spreadsheet, project_id: str):
    projects_sheet = spreadsheet.worksheet("Проекты")
    payments_sheet = spreadsheet.worksheet("Платежи")
    expenses_sheet = spreadsheet.worksheet("Расходы")

    total_paid = 0
    for row in payments_sheet.get_all_values()[1:]:
        if row[0] == project_id:
            try:
                total_paid += float(row[2])
            except (ValueError, IndexError):
                continue

    total_expenses = 0
    for row in expenses_sheet.get_all_values()[1:]:
        if row[0] == project_id:
            try:
                total_expenses += float(row[3])
            except (ValueError, IndexError):
                continue

    row_num = find_project_row(projects_sheet, project_id)
    if row_num == -1:
        return

    balance = total_paid - total_expenses
    projects_sheet.update(f"F{row_num}", [[total_paid]])
    projects_sheet.update(f"G{row_num}", [[total_expenses]])
    projects_sheet.update(f"H{row_num}", [[balance]])


def get_project_address(projects_sheet, project_id: str) -> str:
    for row in projects_sheet.get_all_values()[1:]:
        if row[0] == project_id:
            return row[1]
    return ""


def get_subs_list(spreadsheet) -> list:
    try:
        subs_sheet = spreadsheet.worksheet("Сабы")
        all_records = subs_sheet.get_all_values()
        if len(all_records) <= 1:
            return []
        return [row[0] for row in all_records[1:] if row[0]]
    except Exception:
        return []


def build_summary(spreadsheet) -> str:
    """Собрать общую сводку: за неделю, по категориям, баланс, долги клиентов."""
    now = datetime.now()
    week_ago = now - timedelta(days=7)
    week_ago_str = week_ago.strftime("%Y-%m-%d")

    # --- Все проекты ---
    projects_sheet = spreadsheet.worksheet("Проекты")
    all_projects = projects_sheet.get_all_values()
    total_price = 0
    total_received = 0
    total_expenses_all = 0
    active_count = 0
    for row in all_projects[1:]:
        try:
            price = float(row[3]) if row[3] else 0
            received = float(row[5]) if len(row) > 5 and row[5] else 0
            expenses = float(row[6]) if len(row) > 6 and row[6] else 0
            status = row[4] if len(row) > 4 else ""
            total_price += price
            total_received += received
            total_expenses_all += expenses
            if status != "Завершён":
                active_count += 1
        except (ValueError, IndexError):
            continue

    clients_owe = total_price - total_received

    # --- Платежи за неделю ---
    payments_sheet = spreadsheet.worksheet("Платежи")
    week_payments = 0
    for row in payments_sheet.get_all_values()[1:]:
        try:
            date_str = row[3][:10] if len(row) > 3 else ""
            if date_str >= week_ago_str:
                week_payments += float(row[2])
        except (ValueError, IndexError):
            continue

    # --- Расходы за неделю + по категориям ---
    expenses_sheet = spreadsheet.worksheet("Расходы")
    week_expenses = 0
    expenses_by_category = {}
    week_expenses_by_category = {}
    for row in expenses_sheet.get_all_values()[1:]:
        try:
            category = row[2] if len(row) > 2 else "Прочее"
            amount = float(row[3]) if len(row) > 3 else 0
            date_str = row[5][:10] if len(row) > 5 else ""

            # Всего по категориям
            expenses_by_category[category] = expenses_by_category.get(category, 0) + amount

            # За неделю
            if date_str >= week_ago_str:
                week_expenses += amount
                week_expenses_by_category[category] = week_expenses_by_category.get(category, 0) + amount
        except (ValueError, IndexError):
            continue

    # --- ЗП за неделю + всего ---
    zp_sheet = spreadsheet.worksheet("ЗП")
    total_zp = 0
    week_zp = 0
    zp_by_sub = {}
    for row in zp_sheet.get_all_values()[1:]:
        try:
            sub_name = row[0] if row[0] else "?"
            amount = float(row[1]) if len(row) > 1 else 0
            date_str = row[2][:10] if len(row) > 2 else ""
            total_zp += amount
            zp_by_sub[sub_name] = zp_by_sub.get(sub_name, 0) + amount
            if date_str >= week_ago_str:
                week_zp += amount
        except (ValueError, IndexError):
            continue

    # --- Баланс ---
    balance = total_received - total_expenses_all - total_zp

    # --- Формируем текст ---
    text = "📊 *СВОДКА*\n"
    text += f"📅 Период: {week_ago.strftime('%m/%d')} — {now.strftime('%m/%d/%Y')}\n\n"

    text += "*— За неделю —*\n"
    text += f"💰 Получено от клиентов: ${week_payments:,.2f}\n"
    text += f"💸 Расходы по проектам: ${week_expenses:,.2f}\n"
    text += f"👷 ЗП сабам: ${week_zp:,.2f}\n"

    if week_expenses_by_category:
        text += "\n*Расходы за неделю по категориям:*\n"
        for cat, amt in sorted(week_expenses_by_category.items()):
            text += f"  • {cat}: ${amt:,.2f}\n"

    text += "\n*— Всего (все время) —*\n"
    text += f"🏗 Активных проектов: {active_count}\n"
    text += f"💵 Общая стоимость проектов: ${total_price:,.2f}\n"
    text += f"✅ Получено от клиентов: ${total_received:,.2f}\n"
    text += f"💸 Расходы по проектам: ${total_expenses_all:,.2f}\n"
    text += f"👷 ЗП сабам (всего): ${total_zp:,.2f}\n"
    text += f"📈 Клиенты должны: ${clients_owe:,.2f}\n"
    text += f"💰 Баланс (получено - расходы - ЗП): ${balance:,.2f}\n"

    if expenses_by_category:
        text += "\n*Все расходы по категориям:*\n"
        for cat, amt in sorted(expenses_by_category.items()):
            text += f"  • {cat}: ${amt:,.2f}\n"

    if zp_by_sub:
        text += "\n*ЗП по субподрядчикам:*\n"
        for sub, amt in sorted(zp_by_sub.items()):
            text += f"  • {sub}: ${amt:,.2f}\n"

    return text


def update_summary_sheet(spreadsheet):
    """Обновить лист Сводка: актуальные итоги + колонка на каждую неделю (пн-вс)."""
    try:
        now = datetime.now()

        # --- Собираем все данные ---
        projects_sheet = spreadsheet.worksheet("Проекты")
        payments_sheet = spreadsheet.worksheet("Платежи")
        expenses_sheet = spreadsheet.worksheet("Расходы")
        zp_sheet = spreadsheet.worksheet("ЗП")

        all_projects = projects_sheet.get_all_values()
        all_payments = payments_sheet.get_all_values()
        all_expenses = expenses_sheet.get_all_values()
        all_zp = zp_sheet.get_all_values()

        # --- Актуальные итоги ---
        total_price = 0
        total_received = 0
        total_expenses_all = 0
        active_count = 0
        completed_count = 0
        for row in all_projects[1:]:
            try:
                price = float(row[3]) if row[3] else 0
                received = float(row[5]) if len(row) > 5 and row[5] else 0
                expenses = float(row[6]) if len(row) > 6 and row[6] else 0
                status = row[4] if len(row) > 4 else ""
                total_price += price
                total_received += received
                total_expenses_all += expenses
                if status == "Завершён":
                    completed_count += 1
                else:
                    active_count += 1
            except (ValueError, IndexError):
                continue

        total_zp = 0
        zp_by_sub = {}
        for row in all_zp[1:]:
            try:
                sub_name = row[0] if row[0] else "?"
                amount = float(row[1]) if len(row) > 1 else 0
                total_zp += amount
                zp_by_sub[sub_name] = zp_by_sub.get(sub_name, 0) + amount
            except (ValueError, IndexError):
                continue

        expenses_by_category = {}
        for row in all_expenses[1:]:
            try:
                category = row[2] if len(row) > 2 else "Прочее"
                amount = float(row[3]) if len(row) > 3 else 0
                expenses_by_category[category] = expenses_by_category.get(category, 0) + amount
            except (ValueError, IndexError):
                continue

        clients_owe = total_price - total_received
        balance = total_received - total_expenses_all - total_zp

        # --- Собираем все даты чтобы определить диапазон недель ---
        all_dates = []
        for row in all_payments[1:]:
            try:
                all_dates.append(row[3][:10])
            except (IndexError, ValueError):
                continue
        for row in all_expenses[1:]:
            try:
                all_dates.append(row[5][:10])
            except (IndexError, ValueError):
                continue
        for row in all_zp[1:]:
            try:
                all_dates.append(row[2][:10])
            except (IndexError, ValueError):
                continue

        if not all_dates:
            all_dates = [now.strftime("%Y-%m-%d")]

        # Найти самую раннюю дату
        min_date_str = min(all_dates)
        try:
            min_date = datetime.strptime(min_date_str, "%Y-%m-%d")
        except ValueError:
            min_date = now

        # --- Генерируем список недель (пн-вс) от текущей назад ---
        def get_week_start(dt):
            """Получить понедельник недели."""
            return dt - timedelta(days=dt.weekday())

        current_week_start = get_week_start(now)
        first_week_start = get_week_start(min_date)

        weeks = []
        ws = current_week_start
        while ws >= first_week_start:
            we = ws + timedelta(days=6)
            weeks.append((ws, we))
            ws = ws - timedelta(days=7)

        # --- Считаем данные по каждой неделе ---
        def in_week(date_str, week_start, week_end):
            try:
                return week_start.strftime("%Y-%m-%d") <= date_str[:10] <= week_end.strftime("%Y-%m-%d")
            except (ValueError, IndexError):
                return False

        week_data = []
        for ws, we in weeks:
            w_payments = 0
            for row in all_payments[1:]:
                try:
                    if in_week(row[3], ws, we):
                        w_payments += float(row[2])
                except (ValueError, IndexError):
                    continue

            w_expenses = 0
            w_exp_by_cat = {}
            for row in all_expenses[1:]:
                try:
                    if in_week(row[5], ws, we):
                        amt = float(row[3])
                        cat = row[2] if len(row) > 2 else "Прочее"
                        w_expenses += amt
                        w_exp_by_cat[cat] = w_exp_by_cat.get(cat, 0) + amt
                except (ValueError, IndexError):
                    continue

            w_zp = 0
            w_zp_by_sub = {}
            for row in all_zp[1:]:
                try:
                    if in_week(row[2], ws, we):
                        amt = float(row[1])
                        sub = row[0] if row[0] else "?"
                        w_zp += amt
                        w_zp_by_sub[sub] = w_zp_by_sub.get(sub, 0) + amt
                except (ValueError, IndexError):
                    continue

            week_data.append({
                "label": f"{ws.strftime('%m/%d')}-{we.strftime('%m/%d')}",
                "payments": w_payments,
                "expenses": w_expenses,
                "zp": w_zp,
                "exp_by_cat": w_exp_by_cat,
                "zp_by_sub": w_zp_by_sub,
            })

        # --- Собираем все категории и сабов ---
        all_categories = sorted(set(expenses_by_category.keys()))
        all_subs = sorted(set(zp_by_sub.keys()))

        # --- Строим таблицу ---
        # Строки (фиксированные)
        row_labels = [
            "СВОДКА",
            "",
            "--- ИТОГИ ---",
            "Активных проектов",
            "Завершённых проектов",
            "Общая стоимость проектов",
            "Получено от клиентов (всего)",
            "Расходы по проектам (всего)",
            "ЗП сабам (всего)",
            "Клиенты должны",
            "БАЛАНС",
            "",
            "--- ЗА НЕДЕЛЮ ---",
            "Получено от клиентов",
            "Расходы по проектам",
            "ЗП сабам",
            "",
            "Расходы по категориям:",
        ]
        for cat in all_categories:
            row_labels.append(f"  {cat}")

        row_labels.append("")
        row_labels.append("ЗП по субподрядчикам:")
        for sub in all_subs:
            row_labels.append(f"  {sub}")

        # Колонки: Показатель | Актуальное | Неделя1 | Неделя2 | ...
        num_cols = 2 + len(weeks)  # A + Актуальное + недели
        num_rows = len(row_labels)

        # Заполняем матрицу
        matrix = []
        for i, label in enumerate(row_labels):
            row = [label]

            # Колонка "Актуальное"
            if label == "СВОДКА":
                row.append(f"Обновлено: {now.strftime('%Y-%m-%d %H:%M')}")
            elif label == "Активных проектов":
                row.append(active_count)
            elif label == "Завершённых проектов":
                row.append(completed_count)
            elif label == "Общая стоимость проектов":
                row.append(total_price)
            elif label == "Получено от клиентов (всего)":
                row.append(total_received)
            elif label == "Расходы по проектам (всего)":
                row.append(total_expenses_all)
            elif label == "ЗП сабам (всего)":
                row.append(total_zp)
            elif label == "Клиенты должны":
                row.append(clients_owe)
            elif label == "БАЛАНС":
                row.append(balance)
            elif label == "--- ЗА НЕДЕЛЮ ---":
                row.append("Актуальное")
            elif label == "Расходы по категориям:":
                row.append("")
            elif label == "ЗП по субподрядчикам:":
                row.append("")
            elif label.startswith("  ") and label.strip() in expenses_by_category:
                row.append(expenses_by_category[label.strip()])
            elif label.startswith("  ") and label.strip() in zp_by_sub:
                row.append(zp_by_sub[label.strip()])
            else:
                row.append("")

            # Колонки по неделям
            for wd in week_data:
                if label == "--- ЗА НЕДЕЛЮ ---":
                    row.append(wd["label"])
                elif label == "Получено от клиентов":
                    row.append(wd["payments"] if wd["payments"] else "")
                elif label == "Расходы по проектам":
                    row.append(wd["expenses"] if wd["expenses"] else "")
                elif label == "ЗП сабам":
                    row.append(wd["zp"] if wd["zp"] else "")
                elif label.startswith("  ") and label.strip() in expenses_by_category:
                    val = wd["exp_by_cat"].get(label.strip(), "")
                    row.append(val if val else "")
                elif label.startswith("  ") and label.strip() in zp_by_sub:
                    val = wd["zp_by_sub"].get(label.strip(), "")
                    row.append(val if val else "")
                else:
                    row.append("")

            matrix.append(row)

        # --- Записать в лист ---
        try:
            summary_sheet = spreadsheet.worksheet("Сводка")
            summary_sheet.clear()
        except gspread.exceptions.WorksheetNotFound:
            summary_sheet = spreadsheet.add_worksheet(title="Сводка", rows=50, cols=num_cols)

        # Убедиться что хватает колонок
        if summary_sheet.col_count < num_cols:
            summary_sheet.resize(cols=num_cols)

        # Записать одним вызовом
        end_col = chr(ord('A') + num_cols - 1) if num_cols <= 26 else None
        if num_cols <= 26:
            summary_sheet.update(
                f"A1:{end_col}{num_rows}",
                matrix,
                value_input_option="USER_ENTERED"
            )
        else:
            # Для большого количества колонок
            summary_sheet.update(
                f"A1",
                matrix,
                value_input_option="USER_ENTERED"
            )

        logger.info("✅ Лист Сводка обновлён.")
    except Exception as e:
        logger.error(f"Error updating summary sheet: {e}")


# ============================================================
# ГЛАВНОЕ МЕНЮ
# ============================================================

MAIN_MENU_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["📋 Новый проект", "💰 Записать платёж"],
        ["💸 Записать расход", "🧾 Скан чека"],
        ["📝 Добавить описание", "🔄 Изменить статус"],
        ["📊 Статус проекта", "📈 Сводка"],
        ["👷 Добавить саба", "💵 Оплата сабу"],
        ["📁 Архив"],
    ],
    resize_keyboard=True,
)


async def start(update: Update, context) -> int:
    if not check_access(update):
        await update.message.reply_text("⛔ У вас нет доступа к этому боту.")
        return ConversationHandler.END

    name = get_user_name(update)
    await update.message.reply_text(
        f"👋 Привет, {name}!\n\n"
        f"🏗 Wood & Stone Construction — Project Tracker\n\n"
        f"Выбери действие:",
        reply_markup=MAIN_MENU_KEYBOARD,
    )
    return MAIN_MENU


async def main_menu_handler(update: Update, context) -> int:
    if not check_access(update):
        await update.message.reply_text("⛔ Нет доступа.")
        return ConversationHandler.END

    text = update.message.text

    if text == "📋 Новый проект":
        await update.message.reply_text(
            "📋 Создаём новый проект.\n\nВведи адрес проекта:",
            reply_markup=ReplyKeyboardRemove(),
        )
        return NEW_PROJECT_ADDRESS

    elif text == "💰 Записать платёж":
        return await show_project_list(update, context, PAYMENT_SELECT_PROJECT)

    elif text == "💸 Записать расход":
        return await show_project_list(update, context, EXPENSE_SELECT_PROJECT)

    elif text == "🧾 Скан чека":
        return await show_project_list(update, context, RECEIPT_SELECT_PROJECT)

    elif text == "📝 Добавить описание":
        return await show_project_list(update, context, STATUS_DESC_SELECT_PROJECT)

    elif text == "🔄 Изменить статус":
        return await show_project_list(update, context, CHANGE_STATUS_SELECT_PROJECT)

    elif text == "📊 Статус проекта":
        return await show_project_list(update, context, VIEW_STATUS_SELECT_PROJECT, include_all=True)

    elif text == "👷 Добавить саба":
        await update.message.reply_text(
            "👷 Введи имя субподрядчика:",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ADD_SUB_NAME

    elif text == "💵 Оплата сабу":
        return await show_subs_list(update, context)

    elif text == "📈 Сводка":
        return await show_summary(update, context)

    elif text == "📁 Архив":
        return await show_archive(update, context)

    return MAIN_MENU


# ============================================================
# СПИСОК ПРОЕКТОВ
# ============================================================

async def show_project_list(update, context, next_state, include_all=False):
    try:
        spreadsheet = get_spreadsheet()
        projects_sheet = spreadsheet.worksheet("Проекты")
        projects = get_all_projects(projects_sheet) if include_all else get_active_projects(projects_sheet)
    except Exception as e:
        logger.error(f"Error getting projects: {e}")
        await update.message.reply_text("❌ Ошибка подключения.", reply_markup=MAIN_MENU_KEYBOARD)
        return MAIN_MENU

    if not projects:
        await update.message.reply_text("📭 Нет активных проектов.", reply_markup=MAIN_MENU_KEYBOARD)
        return MAIN_MENU

    buttons = []
    for p in projects:
        label = f"{p['id']} — {p['address']}"
        if include_all:
            label += f" [{p['status']}]"
        buttons.append([InlineKeyboardButton(label, callback_data=f"proj_{p['id']}")])
    buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])

    await update.message.reply_text(
        "Выбери проект:", reply_markup=InlineKeyboardMarkup(buttons),
    )
    return next_state


async def show_archive(update, context):
    try:
        spreadsheet = get_spreadsheet()
        projects_sheet = spreadsheet.worksheet("Проекты")
        all_projects = get_all_projects(projects_sheet)
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text("❌ Ошибка.", reply_markup=MAIN_MENU_KEYBOARD)
        return MAIN_MENU

    archived = [p for p in all_projects if p["status"] == "Завершён"]
    if not archived:
        await update.message.reply_text("📁 Архив пуст.", reply_markup=MAIN_MENU_KEYBOARD)
        return MAIN_MENU

    text = "📁 *Архив проектов:*\n\n"
    for p in archived:
        text += f"• {p['id']} — {p['address']}\n"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=MAIN_MENU_KEYBOARD)
    return MAIN_MENU


async def cancel_callback(update: Update, context) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Отменено.")
    await query.message.reply_text("Главное меню:", reply_markup=MAIN_MENU_KEYBOARD)
    return MAIN_MENU


# ============================================================
# НОВЫЙ ПРОЕКТ
# ============================================================

async def new_project_address(update: Update, context) -> int:
    context.user_data["new_address"] = update.message.text
    await update.message.reply_text("📝 Введи описание проекта:")
    return NEW_PROJECT_DESCRIPTION


async def new_project_description(update: Update, context) -> int:
    context.user_data["new_description"] = update.message.text
    await update.message.reply_text("💵 Введи цену проекта (число):")
    return NEW_PROJECT_PRICE


async def new_project_price(update: Update, context) -> int:
    try:
        price = float(update.message.text.replace(",", "").replace("$", ""))
    except ValueError:
        await update.message.reply_text("❌ Введи число. Например: 15000")
        return NEW_PROJECT_PRICE

    user_name = get_user_name(update)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    try:
        spreadsheet = get_spreadsheet()
        projects_sheet = init_sheets(spreadsheet)
        project_id = get_next_project_id(projects_sheet)

        row = [
            project_id, context.user_data["new_address"],
            context.user_data["new_description"], price,
            "Новый", 0, 0, 0, now, user_name,
        ]
        projects_sheet.append_row(row, value_input_option="USER_ENTERED")

        await update.message.reply_text(
            f"✅ Проект создан!\n\n"
            f"🆔 ID: {project_id}\n"
            f"📍 Адрес: {context.user_data['new_address']}\n"
            f"💵 Цена: ${price:,.2f}\n"
            f"👤 Создал: {user_name}",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
    except Exception as e:
        logger.error(f"Error creating project: {e}")
        await update.message.reply_text("❌ Ошибка при создании проекта.", reply_markup=MAIN_MENU_KEYBOARD)

    for key in ["new_address", "new_description", "new_price"]:
        context.user_data.pop(key, None)
    return MAIN_MENU


# ============================================================
# ЗАПИСАТЬ ПЛАТЁЖ
# ============================================================

async def payment_select_project(update: Update, context) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "cancel":
        return await cancel_callback(update, context)
    project_id = query.data.replace("proj_", "")
    context.user_data["payment_project_id"] = project_id
    await query.edit_message_text(f"💰 Проект {project_id}.\n\nВведи сумму платежа:")
    return PAYMENT_AMOUNT


async def payment_amount(update: Update, context) -> int:
    try:
        amount = float(update.message.text.replace(",", "").replace("$", ""))
    except ValueError:
        await update.message.reply_text("❌ Введи число. Например: 5000")
        return PAYMENT_AMOUNT

    project_id = context.user_data["payment_project_id"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    user_name = get_user_name(update)

    try:
        spreadsheet = get_spreadsheet()
        projects_sheet = spreadsheet.worksheet("Проекты")
        payments_sheet = spreadsheet.worksheet("Платежи")
        address = get_project_address(projects_sheet, project_id)

        payments_sheet.append_row(
            [project_id, address, amount, now, user_name],
            value_input_option="USER_ENTERED"
        )
        update_project_totals(spreadsheet, project_id)
        update_summary_sheet(spreadsheet)

        await update.message.reply_text(
            f"✅ Платёж записан!\n\n"
            f"🆔 Проект: {project_id}\n"
            f"💵 Сумма: ${amount:,.2f}\n"
            f"👤 Записал: {user_name}",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
    except Exception as e:
        logger.error(f"Error recording payment: {e}")
        await update.message.reply_text("❌ Ошибка.", reply_markup=MAIN_MENU_KEYBOARD)

    context.user_data.pop("payment_project_id", None)
    return MAIN_MENU


# ============================================================
# ЗАПИСАТЬ РАСХОД
# ============================================================

EXPENSE_CATEGORIES = ["Материалы", "Субподрядчик", "Аренда оборудования", "Прочее"]


async def expense_select_project(update: Update, context) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "cancel":
        return await cancel_callback(update, context)
    project_id = query.data.replace("proj_", "")
    context.user_data["expense_project_id"] = project_id

    buttons = [[InlineKeyboardButton(cat, callback_data=f"expcat_{cat}")] for cat in EXPENSE_CATEGORIES]
    await query.edit_message_text(
        f"💸 Проект {project_id}.\n\nВыбери категорию расхода:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return EXPENSE_CATEGORY


async def expense_category(update: Update, context) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["expense_category"] = query.data.replace("expcat_", "")
    await query.edit_message_text(f"💸 Категория: {context.user_data['expense_category']}.\n\nВведи сумму расхода:")
    return EXPENSE_AMOUNT


async def expense_amount(update: Update, context) -> int:
    try:
        amount = float(update.message.text.replace(",", "").replace("$", ""))
    except ValueError:
        await update.message.reply_text("❌ Введи число.")
        return EXPENSE_AMOUNT
    context.user_data["expense_amount"] = amount
    await update.message.reply_text("📝 Описание расхода (или «-» чтобы пропустить):")
    return EXPENSE_DESCRIPTION


async def expense_description(update: Update, context) -> int:
    description = update.message.text if update.message.text != "-" else ""
    project_id = context.user_data["expense_project_id"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    user_name = get_user_name(update)

    try:
        spreadsheet = get_spreadsheet()
        projects_sheet = spreadsheet.worksheet("Проекты")
        expenses_sheet = spreadsheet.worksheet("Расходы")
        address = get_project_address(projects_sheet, project_id)

        expenses_sheet.append_row(
            [project_id, address, context.user_data["expense_category"],
             context.user_data["expense_amount"], description, now, user_name],
            value_input_option="USER_ENTERED"
        )
        update_project_totals(spreadsheet, project_id)
        update_summary_sheet(spreadsheet)

        await update.message.reply_text(
            f"✅ Расход записан!\n\n"
            f"🆔 Проект: {project_id}\n"
            f"📂 {context.user_data['expense_category']}: ${context.user_data['expense_amount']:,.2f}\n"
            f"📝 {description or '—'}\n"
            f"👤 Записал: {user_name}",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text("❌ Ошибка.", reply_markup=MAIN_MENU_KEYBOARD)

    for key in ["expense_project_id", "expense_category", "expense_amount"]:
        context.user_data.pop(key, None)
    return MAIN_MENU


# ============================================================
# ДОБАВИТЬ ОПИСАНИЕ
# ============================================================

async def status_desc_select_project(update: Update, context) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "cancel":
        return await cancel_callback(update, context)
    project_id = query.data.replace("proj_", "")
    context.user_data["desc_project_id"] = project_id
    await query.edit_message_text(f"📝 Проект {project_id}.\n\nВведи описание текущего состояния:")
    return STATUS_DESC_TEXT


async def status_desc_text(update: Update, context) -> int:
    description = update.message.text
    project_id = context.user_data["desc_project_id"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    user_name = get_user_name(update)

    try:
        spreadsheet = get_spreadsheet()
        projects_sheet = spreadsheet.worksheet("Проекты")
        updates_sheet = spreadsheet.worksheet("Обновления")
        address = get_project_address(projects_sheet, project_id)

        updates_sheet.append_row(
            [project_id, address, description, now, user_name],
            value_input_option="USER_ENTERED"
        )

        await update.message.reply_text(
            f"✅ Описание добавлено!\n\n"
            f"🆔 Проект: {project_id}\n"
            f"📝 {description}\n"
            f"👤 {user_name}",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text("❌ Ошибка.", reply_markup=MAIN_MENU_KEYBOARD)

    context.user_data.pop("desc_project_id", None)
    return MAIN_MENU


# ============================================================
# ИЗМЕНИТЬ СТАТУС
# ============================================================

STATUS_OPTIONS = ["Новый", "В работе", "Приостановлен", "Завершён"]


async def change_status_select_project(update: Update, context) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "cancel":
        return await cancel_callback(update, context)
    project_id = query.data.replace("proj_", "")
    context.user_data["status_project_id"] = project_id

    buttons = [[InlineKeyboardButton(s, callback_data=f"status_{s}")] for s in STATUS_OPTIONS]
    await query.edit_message_text(
        f"🔄 Проект {project_id}.\n\nВыбери новый статус:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return CHANGE_STATUS_VALUE


async def change_status_value(update: Update, context) -> int:
    query = update.callback_query
    await query.answer()
    new_status = query.data.replace("status_", "")
    project_id = context.user_data["status_project_id"]
    user_name = get_user_name_by_id(query.from_user.id)

    try:
        spreadsheet = get_spreadsheet()
        projects_sheet = spreadsheet.worksheet("Проекты")
        row_num = find_project_row(projects_sheet, project_id)
        if row_num != -1:
            projects_sheet.update(f"E{row_num}", [[new_status]])

        updates_sheet = spreadsheet.worksheet("Обновления")
        address = get_project_address(projects_sheet, project_id)
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        updates_sheet.append_row(
            [project_id, address, f"Статус → {new_status}", now, user_name],
            value_input_option="USER_ENTERED"
        )

        await query.edit_message_text(
            f"✅ Статус: {new_status}\n🆔 Проект: {project_id}\n👤 {user_name}"
        )
    except Exception as e:
        logger.error(f"Error: {e}")
        await query.edit_message_text("❌ Ошибка.")

    await query.message.reply_text("Главное меню:", reply_markup=MAIN_MENU_KEYBOARD)
    context.user_data.pop("status_project_id", None)
    return MAIN_MENU


# ============================================================
# СТАТУС ПРОЕКТА — СВОДКА
# ============================================================

async def view_status_select_project(update: Update, context) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "cancel":
        return await cancel_callback(update, context)
    project_id = query.data.replace("proj_", "")

    try:
        spreadsheet = get_spreadsheet()
        projects_sheet = spreadsheet.worksheet("Проекты")
        payments_sheet = spreadsheet.worksheet("Платежи")
        expenses_sheet = spreadsheet.worksheet("Расходы")
        updates_sheet = spreadsheet.worksheet("Обновления")

        row_num = find_project_row(projects_sheet, project_id)
        if row_num == -1:
            await query.edit_message_text("❌ Проект не найден.")
            return MAIN_MENU

        proj = projects_sheet.row_values(row_num)
        address = proj[1] if len(proj) > 1 else ""
        description = proj[2] if len(proj) > 2 else ""
        price = float(proj[3]) if len(proj) > 3 and proj[3] else 0
        status = proj[4] if len(proj) > 4 else "Новый"
        total_paid = float(proj[5]) if len(proj) > 5 and proj[5] else 0
        total_expenses = float(proj[6]) if len(proj) > 6 and proj[6] else 0
        balance = float(proj[7]) if len(proj) > 7 and proj[7] else 0
        remaining = price - total_paid

        text = (
            f"📊 *Проект {project_id}*\n\n"
            f"📍 {address}\n"
            f"📝 {description}\n"
            f"🔄 Статус: {status}\n\n"
            f"💵 Цена: ${price:,.2f}\n"
            f"✅ Получено: ${total_paid:,.2f}\n"
            f"💸 Расходы: ${total_expenses:,.2f}\n"
            f"📈 Клиент должен: ${remaining:,.2f}\n"
            f"💰 Баланс: ${balance:,.2f}\n"
        )

        # Платежи
        proj_payments = [r for r in payments_sheet.get_all_values()[1:] if r[0] == project_id]
        if proj_payments:
            text += "\n*Платежи:*\n"
            for p in proj_payments[-5:]:
                text += f"  • {p[3]} — ${float(p[2]):,.2f} ({p[4]})\n"

        # Расходы
        proj_expenses = [r for r in expenses_sheet.get_all_values()[1:] if r[0] == project_id]
        if proj_expenses:
            text += "\n*Расходы:*\n"
            for e in proj_expenses[-5:]:
                desc = f" — {e[4]}" if e[4] else ""
                text += f"  • {e[5]} — ${float(e[3]):,.2f} [{e[2]}]{desc} ({e[6]})\n"

        # Обновления
        proj_updates = [r for r in updates_sheet.get_all_values()[1:] if r[0] == project_id]
        if proj_updates:
            text += "\n*Обновления:*\n"
            for u in proj_updates[-5:]:
                text += f"  • {u[3]} — {u[2]} ({u[4]})\n"

        await query.edit_message_text(text, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error: {e}")
        await query.edit_message_text("❌ Ошибка.")

    await query.message.reply_text("Главное меню:", reply_markup=MAIN_MENU_KEYBOARD)
    return MAIN_MENU


# ============================================================
# ДОБАВИТЬ САБА
# ============================================================

async def add_sub_name(update: Update, context) -> int:
    sub_name = update.message.text.strip()
    user_name = get_user_name(update)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    try:
        spreadsheet = get_spreadsheet()
        subs_sheet = spreadsheet.worksheet("Сабы")
        subs_sheet.append_row(
            [sub_name, now, user_name],
            value_input_option="USER_ENTERED"
        )

        await update.message.reply_text(
            f"✅ Субподрядчик добавлен!\n\n"
            f"👷 {sub_name}\n"
            f"👤 Добавил: {user_name}",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
    except Exception as e:
        logger.error(f"Error adding sub: {e}")
        await update.message.reply_text("❌ Ошибка.", reply_markup=MAIN_MENU_KEYBOARD)

    return MAIN_MENU


# ============================================================
# ОПЛАТА САБУ
# ============================================================

async def show_subs_list(update: Update, context) -> int:
    try:
        spreadsheet = get_spreadsheet()
        subs = get_subs_list(spreadsheet)
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text("❌ Ошибка.", reply_markup=MAIN_MENU_KEYBOARD)
        return MAIN_MENU

    if not subs:
        await update.message.reply_text(
            "📭 Нет субподрядчиков. Сначала добавь саба.",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
        return MAIN_MENU

    buttons = [[InlineKeyboardButton(s, callback_data=f"sub_{s}")] for s in subs]
    buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])

    await update.message.reply_text(
        "👷 Выбери субподрядчика:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return SUB_PAY_SELECT_SUB


async def sub_pay_select_sub(update: Update, context) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "cancel":
        return await cancel_callback(update, context)

    sub_name = query.data.replace("sub_", "")
    context.user_data["sub_pay_name"] = sub_name
    await query.edit_message_text(f"💵 Оплата: {sub_name}\n\nВведи сумму:")
    return SUB_PAY_AMOUNT


async def sub_pay_amount(update: Update, context) -> int:
    try:
        amount = float(update.message.text.replace(",", "").replace("$", ""))
    except ValueError:
        await update.message.reply_text("❌ Введи число.")
        return SUB_PAY_AMOUNT

    sub_name = context.user_data["sub_pay_name"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    user_name = get_user_name(update)

    try:
        spreadsheet = get_spreadsheet()
        zp_sheet = spreadsheet.worksheet("ЗП")
        zp_sheet.append_row(
            [sub_name, amount, now, user_name],
            value_input_option="USER_ENTERED"
        )
        update_summary_sheet(spreadsheet)

        await update.message.reply_text(
            f"✅ Оплата записана!\n\n"
            f"👷 {sub_name}\n"
            f"💵 Сумма: ${amount:,.2f}\n"
            f"👤 Записал: {user_name}",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text("❌ Ошибка.", reply_markup=MAIN_MENU_KEYBOARD)

    context.user_data.pop("sub_pay_name", None)
    return MAIN_MENU


# ============================================================
# СКАН ЧЕКА
# ============================================================

async def receipt_select_project(update: Update, context) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "cancel":
        return await cancel_callback(update, context)
    project_id = query.data.replace("proj_", "")
    context.user_data["receipt_project_id"] = project_id
    await query.edit_message_text(
        f"🧾 Проект {project_id}.\n\n📸 Сфоткай чек и отправь фото:"
    )
    return RECEIPT_PHOTO


async def receipt_photo(update: Update, context) -> int:
    if not update.message.photo:
        await update.message.reply_text("❌ Отправь фото, не файл.")
        return RECEIPT_PHOTO

    await update.message.reply_text("⏳ Сканирую чек...")

    # Скачать фото
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    file_path = f"/tmp/receipt_{photo.file_id}.jpg"
    await file.download_to_drive(file_path)

    # Распознать сумму
    total = extract_total_from_receipt(file_path)
    context.user_data["receipt_file_path"] = file_path
    context.user_data["receipt_amount"] = total

    if total > 0:
        buttons = [
            [InlineKeyboardButton(f"✅ Да, ${total:,.2f}", callback_data="receipt_yes")],
            [InlineKeyboardButton("✏️ Ввести вручную", callback_data="receipt_manual")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
        ]
        await update.message.reply_text(
            f"🧾 Распознанная сумма: **${total:,.2f}**\n\nВерно?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    else:
        buttons = [
            [InlineKeyboardButton("✏️ Ввести вручную", callback_data="receipt_manual")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
        ]
        await update.message.reply_text(
            "🧾 Не удалось распознать сумму.\n\nВведи вручную:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    return RECEIPT_CONFIRM


async def receipt_confirm(update: Update, context) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        # Удалить temp файл
        try:
            os.remove(context.user_data.get("receipt_file_path", ""))
        except OSError:
            pass
        return await cancel_callback(update, context)

    if query.data == "receipt_manual":
        await query.edit_message_text("✏️ Введи сумму вручную:")
        return RECEIPT_CONFIRM

    if query.data == "receipt_yes":
        amount = context.user_data.get("receipt_amount", 0)
        return await save_receipt(query, context, amount)

    # Если пришло число (ручной ввод) — это не callback, обработаем ниже
    return RECEIPT_CONFIRM


async def receipt_manual_amount(update: Update, context) -> int:
    """Ручной ввод суммы после неудачного OCR."""
    try:
        amount = float(update.message.text.replace(",", "").replace("$", ""))
    except ValueError:
        await update.message.reply_text("❌ Введи число. Например: 127.45")
        return RECEIPT_CONFIRM

    return await save_receipt_from_message(update, context, amount)


async def save_receipt(query, context, amount):
    """Сохранить чек (из callback)."""
    project_id = context.user_data.get("receipt_project_id", "")
    file_path = context.user_data.get("receipt_file_path", "")
    user_name = get_user_name_by_id(query.from_user.id)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    try:
        spreadsheet = get_spreadsheet()
        projects_sheet = spreadsheet.worksheet("Проекты")
        expenses_sheet = spreadsheet.worksheet("Расходы")
        address = get_project_address(projects_sheet, project_id)

        # Загрузить фото в Drive
        drive_link = ""
        try:
            drive_link = upload_receipt_to_drive(file_path)
        except Exception as e:
            logger.error(f"Drive upload error: {e}")
            drive_link = "Ошибка загрузки"

        # Записать расход
        description = f"Чек: {drive_link}" if drive_link else "Чек (фото не загружено)"
        expenses_sheet.append_row(
            [project_id, address, "Материалы", amount, description, now, user_name],
            value_input_option="USER_ENTERED"
        )
        update_project_totals(spreadsheet, project_id)
        update_summary_sheet(spreadsheet)

        await query.edit_message_text(
            f"✅ Чек записан!\n\n"
            f"🆔 Проект: {project_id}\n"
            f"💵 Сумма: ${amount:,.2f}\n"
            f"📂 Категория: Материалы\n"
            f"🔗 Фото: {drive_link or '—'}\n"
            f"👤 Записал: {user_name}"
        )
    except Exception as e:
        logger.error(f"Error saving receipt: {e}")
        await query.edit_message_text("❌ Ошибка при сохранении чека.")

    # Cleanup
    try:
        os.remove(file_path)
    except OSError:
        pass
    for key in ["receipt_project_id", "receipt_file_path", "receipt_amount"]:
        context.user_data.pop(key, None)

    await query.message.reply_text("Главное меню:", reply_markup=MAIN_MENU_KEYBOARD)
    return MAIN_MENU


async def save_receipt_from_message(update, context, amount):
    """Сохранить чек (из текстового ввода суммы)."""
    project_id = context.user_data.get("receipt_project_id", "")
    file_path = context.user_data.get("receipt_file_path", "")
    user_name = get_user_name(update)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    try:
        spreadsheet = get_spreadsheet()
        projects_sheet = spreadsheet.worksheet("Проекты")
        expenses_sheet = spreadsheet.worksheet("Расходы")
        address = get_project_address(projects_sheet, project_id)

        # Загрузить фото
        drive_link = ""
        try:
            drive_link = upload_receipt_to_drive(file_path)
        except Exception as e:
            logger.error(f"Drive upload error: {e}")
            drive_link = "Ошибка загрузки"

        description = f"Чек: {drive_link}" if drive_link else "Чек (фото не загружено)"
        expenses_sheet.append_row(
            [project_id, address, "Материалы", amount, description, now, user_name],
            value_input_option="USER_ENTERED"
        )
        update_project_totals(spreadsheet, project_id)
        update_summary_sheet(spreadsheet)

        await update.message.reply_text(
            f"✅ Чек записан!\n\n"
            f"🆔 Проект: {project_id}\n"
            f"💵 Сумма: ${amount:,.2f}\n"
            f"📂 Категория: Материалы\n"
            f"🔗 Фото: {drive_link or '—'}\n"
            f"👤 Записал: {user_name}",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text("❌ Ошибка.", reply_markup=MAIN_MENU_KEYBOARD)

    try:
        os.remove(file_path)
    except OSError:
        pass
    for key in ["receipt_project_id", "receipt_file_path", "receipt_amount"]:
        context.user_data.pop(key, None)
    return MAIN_MENU


# ============================================================
# СВОДКА
# ============================================================

async def show_summary(update: Update, context) -> int:
    try:
        spreadsheet = get_spreadsheet()
        text = build_summary(spreadsheet)
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=MAIN_MENU_KEYBOARD)
    except Exception as e:
        logger.error(f"Error building summary: {e}")
        await update.message.reply_text("❌ Ошибка при загрузке сводки.", reply_markup=MAIN_MENU_KEYBOARD)
    return MAIN_MENU


# ============================================================
# ОТМЕНА
# ============================================================

async def cancel(update: Update, context) -> int:
    await update.message.reply_text("❌ Отменено.", reply_markup=MAIN_MENU_KEYBOARD)
    return MAIN_MENU


# ============================================================
# ЗАПУСК БОТА
# ============================================================

def main():
    try:
        spreadsheet = get_spreadsheet()
        init_sheets(spreadsheet)
        logger.info("✅ Листы инициализированы.")
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации: {e}")

    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, main_menu_handler),
            ],
            NEW_PROJECT_ADDRESS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, new_project_address),
            ],
            NEW_PROJECT_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, new_project_description),
            ],
            NEW_PROJECT_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, new_project_price),
            ],
            PAYMENT_SELECT_PROJECT: [
                CallbackQueryHandler(payment_select_project, pattern="^proj_"),
                CallbackQueryHandler(cancel_callback, pattern="^cancel$"),
            ],
            PAYMENT_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, payment_amount),
            ],
            EXPENSE_SELECT_PROJECT: [
                CallbackQueryHandler(expense_select_project, pattern="^proj_"),
                CallbackQueryHandler(cancel_callback, pattern="^cancel$"),
            ],
            EXPENSE_CATEGORY: [
                CallbackQueryHandler(expense_category, pattern="^expcat_"),
            ],
            EXPENSE_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, expense_amount),
            ],
            EXPENSE_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, expense_description),
            ],
            STATUS_DESC_SELECT_PROJECT: [
                CallbackQueryHandler(status_desc_select_project, pattern="^proj_"),
                CallbackQueryHandler(cancel_callback, pattern="^cancel$"),
            ],
            STATUS_DESC_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, status_desc_text),
            ],
            CHANGE_STATUS_SELECT_PROJECT: [
                CallbackQueryHandler(change_status_select_project, pattern="^proj_"),
                CallbackQueryHandler(cancel_callback, pattern="^cancel$"),
            ],
            CHANGE_STATUS_VALUE: [
                CallbackQueryHandler(change_status_value, pattern="^status_"),
            ],
            VIEW_STATUS_SELECT_PROJECT: [
                CallbackQueryHandler(view_status_select_project, pattern="^proj_"),
                CallbackQueryHandler(cancel_callback, pattern="^cancel$"),
            ],
            ADD_SUB_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_sub_name),
            ],
            SUB_PAY_SELECT_SUB: [
                CallbackQueryHandler(sub_pay_select_sub, pattern="^sub_"),
                CallbackQueryHandler(cancel_callback, pattern="^cancel$"),
            ],
            SUB_PAY_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, sub_pay_amount),
            ],
            RECEIPT_SELECT_PROJECT: [
                CallbackQueryHandler(receipt_select_project, pattern="^proj_"),
                CallbackQueryHandler(cancel_callback, pattern="^cancel$"),
            ],
            RECEIPT_PHOTO: [
                MessageHandler(filters.PHOTO, receipt_photo),
            ],
            RECEIPT_CONFIRM: [
                CallbackQueryHandler(receipt_confirm, pattern="^receipt_"),
                CallbackQueryHandler(cancel_callback, pattern="^cancel$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receipt_manual_amount),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
    )

    app.add_handler(conv_handler)
    logger.info("🚀 Бот запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()
