import asyncio
import json
import time
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

import os
import jwt
import httpx

APPLE_REFERENCE_DATE_OFFSET = 978307200
TW = timezone(timedelta(hours=8))
load_dotenv()

def swift_date(year, month, day, hour, minute=0) -> float:
    """輸入台灣時間，自動轉換為 Swift Codable Date"""
    dt = datetime(year, month, day, hour, minute, tzinfo=TW)
    return dt.timestamp() - APPLE_REFERENCE_DATE_OFFSET


CONFIG = {
    "team_id": os.environ.get("team_id"),
    "key_id":os.environ.get("key_id"),
    "key_path": os.environ.get("key_path"),
    "bundle_id": os.environ.get("bundle_id"),
    "use_sandbox": False,
}

# start        → 使用 push_to_start_token 遠端啟動 Live Activity
# update       → 使用 push_token 遠端更新已啟動的 Live Activity
# end          → 使用 push_token 遠端結束 Live Activity
# notify_start → 發送一般通知，觸發 App 本機建立 Live Activity
# notify_stop  → 發送一般通知，觸發 App 本機結束 Live Activity

now = datetime.now()
content_state = {
    "currentPeriod": "-",
    "currentSubject": "未知",
    "currentStartTime": swift_date(now.year, now.month, now.day, 0, 0),
    "currentEndTime": swift_date(now.year, now.month, now.day, 0, 0),
    "nextPeriod": "-",
    "nextSubject": "未知",
    "nextStartTime": swift_date(now.year, now.month, now.day, 0, 0),
    "todaySlots": [],
}


APNS_HOST_SANDBOX = "api.sandbox.push.apple.com"
APNS_HOST_PRODUCTION = "api.push.apple.com"


def make_jwt_token():
    with open(CONFIG["key_path"], "r") as f:
        private_key = f.read()
    payload = {
        "iss": CONFIG["team_id"],
        "iat": int(time.time()),
    }
    headers = {
        "alg": "ES256",
        "kid": CONFIG["key_id"],
    }
    return jwt.encode(payload, private_key, algorithm="ES256", headers=headers)


def build_payload(
    action: str,
    notify_title: str,
    notify_body: str,
    state: dict,
):
    if action == "start":
        return {
            "aps": {
                "timestamp": int(time.time()),
                "event": "start",
                "content-state": state,
                "attributes-type": "ClassScheduleActivityAttributes",
                "attributes": {},
            }
        }
    elif action == "end":
        return {
            "aps": {
                "timestamp": int(time.time()),
                "event": "end",
                "dismissal-date": int(time.time()),
                "content-state": {
                    "currentPeriod": "",
                    "currentSubject": "",
                    "currentStartTime": None,
                    "currentEndTime": None,
                    "nextPeriod": "",
                    "nextSubject": "",
                    "nextStartTime": None,
                    "todaySlots": [],
                },
            }
        }
    elif action == "notify_start":
        return {
            "aps": {
                "alert": {
                    "title": notify_title,
                    "body": notify_body,
                },
                "sound": "default",
                "content-available": 1,
            },
            "action": "start_live_activity",
        }
    elif action == "notify_stop":
        return {
            "aps": {
                "alert": {
                    "title": notify_title,
                    "body": notify_body,
                },
                "sound": "default",
                "content-available": 1,
            },
            "action": "stop_live_activity",
        }
    else:  # update
        return {
            "aps": {
                "timestamp": int(time.time()),
                "event": "update",
                "content-state": state,
                "alert": {
                    "title": "課表更新",
                    "body": state.get("currentSubject") or state.get("nextSubject", ""),
                },
            }
        }


async def send_push(
    action: str,
    push_to_start_token: str,
    push_token: str,
    apns_device_token: str,
    notify_title: str,
    notify_body: str,
    today_slots: list,
):
    state = {**content_state, "todaySlots": today_slots}

    host = APNS_HOST_SANDBOX if CONFIG["use_sandbox"] else APNS_HOST_PRODUCTION

    is_notify = action in ("notify_start", "notify_stop")

    if is_notify:
        device_token = apns_device_token
    elif action == "start":
        device_token = push_to_start_token
    else:
        device_token = push_token

    if not device_token:
        print("❌ 請先填入對應的 Token")
        return

    url = f"https://{host}/3/device/{device_token}"

    token = make_jwt_token()
    if is_notify:
        headers = {
            "authorization": f"bearer {token}",
            "apns-topic": CONFIG["bundle_id"],
            "apns-push-type": "alert",
            "apns-priority": "10",
        }
    else:
        headers = {
            "authorization": f"bearer {token}",
            "apns-topic": f"{CONFIG['bundle_id']}.push-type.liveactivity",
            "apns-push-type": "liveactivity",
            "apns-priority": "10",
        }

    payload = build_payload(action, notify_title, notify_body, state)
    payload_json = json.dumps(payload, ensure_ascii=False)

    action_label = {
        "start": "啟動(push-to-start)",
        "update": "更新",
        "end": "結束",
        "notify_start": "通知啟動",
        "notify_stop": "通知結束",
    }[action]
    print(f"→ 操作: {action_label} Live Activity")
    print(f"→ 推送至: {url}")

    async with httpx.AsyncClient(http2=True) as client:
        resp = await client.post(url, headers=headers, content=payload_json)

    print(f"\n← Status: {resp.status_code}")
    if resp.text:
        print(f"← Body: {resp.text}")

    if resp.status_code == 200:
        print(f"\n✅ {action_label}推送成功！")
    else:
        print(f"\n❌ 推送失敗，請檢查 Token 和設定")
