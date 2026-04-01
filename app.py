from update_live_activity import swift_date, send_push
from datetime import datetime, timezone, timedelta
from pocketbase import PocketBase
from dotenv import load_dotenv

import requests
import asyncio
import os

load_dotenv()

client = PocketBase(os.environ.get("DB_URL"))
admin_data = client.admins.auth_with_password(os.environ.get("DB_EMAIL"), os.environ.get("DB_PASSWD"))

tz_taiwan = timezone(timedelta(hours=8))
now = datetime.now(tz_taiwan)
weekday_chinese = ['一', '二', '三', '四', '五',"六","日"]
weekday = weekday_chinese[now.weekday()]
year = now.year
month = now.month
day = now.day



def to_todaySlots(data):
    schedules = []
    for name in data:
        for schedule in data[name]["schedule"]:
            if schedule["weekday"] == weekday:
                schedules.append(
                    {
                        "period": schedule["period"],
                        "subject": name,
                        "startTime": swift_date(
                            year,
                            month,
                            day,
                            int(schedule["start"].split(":")[0]),
                            int(schedule["start"].split(":")[1]),
                        ),
                        "endTime": swift_date(
                            year,
                            month,
                            day,
                            int(schedule["end"].split(":")[0]),
                            int(schedule["end"].split(":")[1]),
                        ),
                        "room": schedule.get("room", ""),
                        "teacher": schedule.get("teacher", ""),
                    }
                )
    return schedules


def get_action(curriculum):
    """
    根據目前時間決定要送出的 action，或 None（不推通知）。
    - notify_start：距第一節上課 <= 20 分鐘
    - update：目前在下課時間（兩節課之間）
    - end：放學後超過 10 分鐘
    """
    slots = []
    for name in curriculum:
        for schedule in curriculum[name]["schedule"]:
            if schedule["weekday"] == weekday:
                sh, sm = map(int, schedule["start"].split(":"))
                eh, em = map(int, schedule["end"].split(":"))
                start_dt = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
                end_dt   = now.replace(hour=eh, minute=em, second=0, microsecond=0)
                slots.append((start_dt, end_dt))

    if not slots:
        return None

    slots.sort(key=lambda x: x[0])
    first_start = slots[0][0]
    last_end    = slots[-1][1]

    # 距第一節上課 <= 20 分鐘（且尚未上課）
    if first_start - timedelta(minutes=20) <= now < first_start:
        return "notify_start"

    # 放學後超過 10 分鐘
    if now > last_end + timedelta(minutes=10):
        return "end"

    # 檢查是否在下課時間（兩節課之間）
    for i in range(len(slots) - 1):
        class_end  = slots[i][1]
        next_start = slots[i + 1][0]
        if class_end <= now <= next_start:
            return "update"

    # 上課中或其他時段 → 不推通知
    return None


# Ping
r = requests.get(os.environ.get("status"))

all_data = client.collection("notify").get_full_list()

for i in all_data:
    if not i.is_open:
        continue
    curriculum = i.curriculum
    if not curriculum:
        continue
    
    action = get_action(curriculum)
    if action is None:
        continue

    todaySlots = to_todaySlots(curriculum)
    asyncio.run(send_push(
        action=action,
        push_to_start_token=i.start_token,
        push_token=i.update_token,
        apns_device_token=i.device_token,
        notify_title="hello",
        notify_body="sb",
        today_slots=todaySlots,
    ))