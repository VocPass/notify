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
                            int(schedule.get("start","00:00").split(":")[0]),
                            int(schedule.get("start","00:00").split(":")[1]),
                        ),
                        "endTime": swift_date(
                            year,
                            month,
                            day,
                            int(schedule.get("end","00:00").split(":")[0]),
                            int(schedule.get("end","00:00").split(":")[1]),
                        ),
                        "room": schedule.get("room", ""),
                        "teacher": schedule.get("teacher", ""),
                    }
                )
    return schedules


def get_action(curriculum):
    """
    回傳 (action, label) 或 None。
    - ("notify_start", "課前通知")
    - ("update", "第X節")     ← 上課中
    - ("update", "第X節下課") ← 兩節課之間
    - ("end",    "放學")
    """
    slots = []
    for name in curriculum:
        for schedule in curriculum[name]["schedule"]:
            if schedule["weekday"] == weekday:
                sh, sm = map(int, schedule.get('start','00:00').split(":"))
                eh, em = map(int, schedule.get('end','00:00').split(":"))
                start_dt = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
                end_dt   = now.replace(hour=eh, minute=em, second=0, microsecond=0)
                period   = schedule.get("period", "?")
                slots.append((start_dt, end_dt, period))

    if not slots:
        return None

    slots.sort(key=lambda x: x[0])
    first_start = slots[0][0]
    last_end    = slots[-1][1]

    # 距第一節上課 <= 20 分鐘（且尚未上課）
    if first_start - timedelta(minutes=20) <= now < first_start:
        return ("notify_start", "課前通知")

    # 放學後超過 10 分鐘
    if now > last_end + timedelta(minutes=10):
        return ("end", "放學")

    # 最後一節課剛結束（0–10 分鐘內）→ 先 update 一次
    if now > last_end:
        return ("update", "放學前更新")

    # 下課時間（兩節課之間）
    for idx in range(len(slots) - 1):
        class_end  = slots[idx][1]
        next_start = slots[idx + 1][0]
        if class_end <= now <= next_start:
            return ("update", f"第{slots[idx][2]}節下課")

    # 上課中
    for start_dt, end_dt, period in slots:
        if start_dt <= now <= end_dt:
            return ("update", f"第{period}節")

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

    result = get_action(curriculum)
    if result is None:
        continue
    action, label = result

    # 同 label 今天已送過 → 跳過（每個狀態一天只送一次）
    if i.last_send:
        last_send_dt = datetime.fromisoformat(i.last_send.replace("Z", "+00:00")).astimezone(tz_taiwan)
        last_action = getattr(i, "last_action", None)
        if last_action == label and last_send_dt.date() == now.date():
            continue

    todaySlots = to_todaySlots(curriculum)
    asyncio.run(send_push(
        action=action,
        push_to_start_token=i.start_token,
        push_token=i.update_token,
        apns_device_token=i.apns_token,
        notify_title="打開App看看今天的課表吧！",
        notify_body="愛因斯坦沒有說過：人生最大的遺憾就是沒有在課前收到課表通知。當然這功能我還在測試...",
        today_slots=todaySlots,
        db_client=client,
        db_id=i.id,
    ))

    client.collection("notify").update(i.id, {"last_send": now.isoformat(), "last_action": label})