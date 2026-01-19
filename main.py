import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta, date
import pytz
import re
import json
import os
from dotenv import load_dotenv

load_dotenv()

# ================= CẤU HÌNH CHUNG =================
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DEVELOPER_CHANNEL_ID = os.getenv("DEVELOPER_CHANNEL_ID")
DAILY_CHANNEL_ID = os.getenv("DAILY_CHANNEL_ID")

# --- CẤU HÌNH ROLE: LỌC VÀ SẮP XẾP ---
# Điền ID các Role được phép báo cáo.
# Thứ tự trong list này quyết định thứ tự hiển thị trong báo cáo tổng.
REPORT_ROLE_ORDER = [
    os.getenv("DEV_CV_ROLE_ID"),
    os.getenv("DEV_LLM_ROLE_ID"),
    os.getenv("DEV_FULLSTACK_ROLE_ID"),
    os.getenv("BA_ROLE_ID"),
    os.getenv("TESTER_ROLE_ID"),
]

# --- CẤU HÌNH THỨ 7 CÁCH TUẦN ---
ANCHOR_WORK_SATURDAY = "2024-01-24" 

# ================= HỆ THỐNG =================
STATE_FILE = "bot_state.json"
VN_TZ = pytz.timezone('Asia/Ho_Chi_Minh')
DATE_PATTERN = r"^\s*(\d{1,2})\s*[./\-]\s*(\d{1,2})\s*$"

intents = discord.Intents.default()
intents.message_content = True
intents.members = True 

bot = commands.Bot(command_prefix='!', intents=intents)

def is_work_day(current_date):
    """Kiểm tra ngày làm việc (T2-T6, T7 cách tuần)"""
    weekday = current_date.weekday()
    if weekday == 6: 
        return False
    if weekday < 5: 
        return True
    if weekday == 5:
        try:
            anchor_date = datetime.strptime(ANCHOR_WORK_SATURDAY, "%Y-%m-%d").date()
            delta = current_date - anchor_date
            return (delta.days // 7) % 2 == 0
        except: 
            return True
    return False

def get_role_priority(member):
    """
    Kiểm tra xem Member có role trong danh sách cho phép không.
    Trả về: (Index ưu tiên thấp nhất, True) nếu có.
    Trả về: (9999, False) nếu không có role nào hợp lệ.
    """
    best_index = 9999
    found = False
    
    for role in member.roles:
        if role.id in REPORT_ROLE_ORDER:
            idx = REPORT_ROLE_ORDER.index(role.id)
            if idx < best_index:
                best_index = idx
                found = True
    
    return best_index, found

def load_state():
    if os.path.exists(STATE_FILE):
        try: 
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except: 
            return {}
    return {}

def save_state(data):
    with open(STATE_FILE, "w") as f: 
        json.dump(data, f)

def get_today_str():
    return datetime.now(VN_TZ).strftime("%Y-%m-%d")

def normalize_report(author_display_name, content):
    lines = content.strip().split('\n')
    lines = [line.strip() for line in lines if line.strip()]
    if not lines: 
        return None

    # Xác định tên và ngày
    first_line_date_match = re.match(DATE_PATTERN, lines[0])
    if first_line_date_match:
        name = author_display_name
        start_index = 0
    else:
        name = lines[0].title()
        start_index = 1
    
    formatted_lines = []
    has_date = False

    for i in range(start_index, len(lines)):
        line = lines[i]
        date_match = re.match(DATE_PATTERN, line)
        if date_match:
            d, m = date_match.groups()
            formatted_lines.append(f"{int(d):02d}/{int(m):02d}")
            has_date = True
        else:
            clean = re.sub(r"^[-*+•]\s*", "", line)
            if clean.lower() == name.lower(): 
                continue
            if clean:
                formatted_lines.append(f"- {clean[0].upper() + clean[1:]}")

    if not formatted_lines: 
        return None
    if not has_date:
        formatted_lines.insert(0, datetime.now(VN_TZ).strftime("%d/%m"))

    return f"**{name}**\n" + "\n".join(formatted_lines)

async def get_report_data_sorted():
    """
    Trả về: 
    1. List các dict đã sắp xếp theo Role: [{'text': '...', 'prio': 0}, ...]
    2. Set ID người đã báo cáo
    """
    dev_channel = bot.get_channel(DEVELOPER_CHANNEL_ID)
    if not dev_channel: 
        return [], set()

    now = datetime.now(VN_TZ)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    raw_reports = []
    reported_ids = set()
    
    async for message in dev_channel.history(after=start_of_day, limit=500):
        if message.author.bot: 
            continue
        
        # 1. CHECK ROLE & LẤY ĐỘ ƯU TIÊN
        priority, is_valid_role = get_role_priority(message.author)
        
        # Nếu người này không có role trong danh sách -> Bỏ qua luôn
        if not is_valid_role:
            continue

        content = message.content.strip()
        if not content: 
            continue

        # 2. CHECK CÚ PHÁP (Có dòng ngày tháng riêng biệt)
        has_isolated_date_line = re.search(DATE_PATTERN, content, re.MULTILINE)
        
        if has_isolated_date_line:
            norm = normalize_report(message.author.display_name, content)
            if norm:
                raw_reports.append({
                    'text': norm,
                    'prio': priority,
                    'msg_id': message.id
                })
                reported_ids.add(message.author.id)
            
    # 3. SẮP XẾP
    raw_reports.sort(key=lambda x: (x['prio'], x['msg_id']))
    
    return raw_reports, reported_ids

async def create_final_content():
    sorted_reports, _ = await get_report_data_sorted()
    
    # Chỉ lấy phần text để ghép lại
    reports_text_list = [item['text'] for item in sorted_reports]
    
    now = datetime.now(VN_TZ)
    header = f"__**Daily Report {now.strftime('%d/%m/%Y')}:**__\n\n"
    
    if not reports_text_list:
        return header + "(Hiện tại chưa có báo cáo nào)"
    return header + "\n\n".join(reports_text_list)

# ================= SCHEDULER =================
@tasks.loop(minutes=1)
async def daily_scheduler():
    now = datetime.now(VN_TZ)
    current_date = now.date()
    
    if not is_work_day(current_date): 
        return 

    today_str = get_today_str()
    current_time_str = now.strftime("%H:%M")
    
    state = load_state()
    if state.get("date") != today_str:
        state = {"date": today_str, "msg_id": None, "step_830": False, "step_900": False, "step_930": False}
        save_state(state)

    dev_channel = bot.get_channel(DEVELOPER_CHANNEL_ID)
    daily_channel = bot.get_channel(DAILY_CHANNEL_ID)

    # 1. Nhắc nhở 08:30
    if current_time_str == "08:30" and not state["step_830"]:
        if dev_channel: 
            await dev_channel.send("@everyone Daily mọi người ơi!")
        state["step_830"] = True
        save_state(state)

    # 2. Tag tên 09:00 (TAG THEO ROLE HỢP LỆ)
    elif current_time_str == "09:00" and not state["step_900"]:
        if dev_channel:
            _, reported_ids = await get_report_data_sorted()
            missing_mentions = []
            
            for member in dev_channel.members:
                if member.bot:
                    continue
                if member.id in reported_ids:
                    continue
                
                # --- CHECK: CHỈ TAG NHỮNG NGƯỜI CÓ ROLE TRONG LIST BÁO CÁO ---
                _, is_valid_role = get_role_priority(member)
                
                if is_valid_role:
                    missing_mentions.append(member.mention)
            
            if missing_mentions:
                await dev_channel.send(f"{' '.join(missing_mentions)} Daily mọi người ơi!")
                
        state["step_900"] = True
        save_state(state)

    # 3. Tổng hợp 09:30
    elif current_time_str == "09:30" and not state["step_930"]:
        final_content = await create_final_content()
        if daily_channel:
            sent = await daily_channel.send(final_content)
            state["msg_id"] = sent.id
            state["step_930"] = True
            save_state(state)

    # 4. Update
    is_update = (now.minute == 0 or now.minute == 30)
    is_after = (now.hour > 9) or (now.hour == 9 and now.minute >= 30)

    if is_update and is_after:
        final_content = await create_final_content()
        msg_id = state.get("msg_id")
        
        if daily_channel:
            if msg_id:
                try:
                    msg_edit = await daily_channel.fetch_message(msg_id)
                    if msg_edit.content != final_content:
                        await msg_edit.edit(content=final_content)
                        print(f"[{current_time_str}] Đã cập nhật.")
                except discord.NotFound:
                    sent = await daily_channel.send(final_content)
                    state["msg_id"] = sent.id
                    save_state(state)
            else:
                sent = await daily_channel.send(final_content)
                state["msg_id"] = sent.id
                state["step_930"] = True
                save_state(state)

@bot.event
async def on_ready():
    print(f'Bot đã online: {bot.user}')
    daily_scheduler.start()

bot.run(TOKEN)