from update_live_activity import *
from datetime import datetime, timezone, timedelta
from pocketbase import PocketBase
from dotenv import load_dotenv

import requests
import asyncio
import time
import os

load_dotenv()

client = PocketBase(os.environ.get("DB_URL"))
admin_data = client.admins.auth_with_password(
    os.environ.get("DB_EMAIL"), os.environ.get("DB_PASSWD")
)

jwt_token = make_jwt_token()
tz_taiwan = timezone(timedelta(hours=8))
now = datetime.now(tz_taiwan)
weekday_chinese = ["一", "二", "三", "四", "五", "六", "日"]
weekday = weekday_chinese[now.weekday()]
year = now.year
month = now.month
day = now.day

logs = ""
if os.path.isfile("logs.txt"):
    with open("logs.txt", "r") as f:
        logs = f.read()


def get_time_str(schedule, key, fallback):
    val = schedule.get(key, "")
    return val if val and ":" in val else fallback


time_template = {
    "一": ("08:10", "09:00"),
    "二": ("09:10", "10:00"),
    "三": ("10:10", "11:00"),
    "四": ("11:10", "12:00"),
    "五": ("13:10", "14:00"),
    "六": ("14:10", "15:00"),
    "七": ("15:10", "16:00"),
    "八": ("16:10", "17:00"),
    "九": ("17:10", "18:00"),
    "十": ("18:10", "19:00"),
    "十一": ("19:10", "20:00"),
    "十二": ("20:10", "21:00"),
    "十三": ("21:10", "22:00"),
    "十四": ("22:10", "23:00"),
    "無": ("00:00", "00:00"),
}


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
                            *map(
                                int,
                                get_time_str(
                                    schedule,
                                    "start",
                                    time_template.get(
                                        schedule["period"], ("00:00", "00:00")
                                    )[0],
                                ).split(":"),
                            ),
                        ),
                        "endTime": swift_date(
                            year,
                            month,
                            day,
                            *map(
                                int,
                                get_time_str(
                                    schedule,
                                    "end",
                                    time_template.get(
                                        schedule["period"], ("00:00", "00:00")
                                    )[1],
                                ).split(":"),
                            ),
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

                tpl = time_template.get(schedule["period"], ("00:00", "00:00"))
                sh, sm = map(int, get_time_str(schedule, "start", tpl[0]).split(":"))
                eh, em = map(int, get_time_str(schedule, "end", tpl[1]).split(":"))
                start_dt = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
                end_dt = now.replace(hour=eh, minute=em, second=0, microsecond=0)
                period = schedule.get("period", "?")
                slots.append((start_dt, end_dt, period))

    if not slots:
        return None

    slots.sort(key=lambda x: x[0])
    first_start = slots[0][0]
    last_end = slots[-1][1]

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
        class_end = slots[idx][1]
        next_start = slots[idx + 1][0]
        if class_end <= now <= next_start:
            return ("update", f"第{slots[idx][2]}節下課")

    # 上課中
    for start_dt, end_dt, period in slots:
        if start_dt <= now <= end_dt:
            return ("update", f"第{period}節")

    return None


all_data = client.collection("notify").get_full_list()

t1 = time.time()
sended = 0
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
        last_send_dt = datetime.fromisoformat(
            i.last_send.replace("Z", "+00:00")
        ).astimezone(tz_taiwan)
        last_action = getattr(i, "last_action", None)
        if last_action == label and last_send_dt.date() == now.date():
            continue

    todaySlots = to_todaySlots(curriculum)
    asyncio.run(
        send_push(
            action=action,
            push_to_start_token=i.start_token,
            push_token=i.update_token,
            apns_device_token=i.apns_token,
            notify_title="打開App來啟動動態島吧！",
            notify_body="我也不知道為什麼Apple不讓我自動啟動...然後只能存在8小時",
            today_slots=todaySlots,
            jwt_token=jwt_token,
            db_client=client,
            db_id=i.id,
        )
    )
    sended += 1

    client.collection("notify").update(
        i.id, {"last_send": now.isoformat(), "last_action": label}
    )

t2 = time.time()


logs += f"{now.isoformat()}: {t2-t1:.2f}s -> {sended}\n"
with open("logs.txt", "w+") as f:
    f.write(logs)
# Ping
r = requests.get(os.environ.get("status"))
