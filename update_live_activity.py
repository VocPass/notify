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
    "key_id": os.environ.get("key_id"),
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
                # push-to-start 若不帶 alert，APNs 會回 200 但背景不會把 Live Activity
                # 呈現出來（畫面不跳）。alert 是遠端啟動實際顯示的必要條件。
                "alert": {
                    "title": notify_title or "課表已開始",
                    "body": notify_body or "今日課程動態島已啟動",
                },
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
    push_to_start_token: str = "",
    push_token: str = "",
    apns_device_token: str = "",
    notify_title: str = "",
    notify_body: str = "",
    today_slots: list = [],
    jwt_token: str="",
    is_dev: bool = False,
    db_client=None,
    db_id=None,
    http_client: httpx.AsyncClient = None,
):
    state = {**content_state, "todaySlots": today_slots}

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

    token = jwt_token
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

    hosts = [APNS_HOST_PRODUCTION, APNS_HOST_SANDBOX]

    async def _post(client, host):
        url = f"https://{host}/3/device/{device_token}"
        print(f"→ 推送至: {url}")
        resp = await client.post(url, headers=headers, content=payload_json)
        print(f"← Status: {resp.status_code}  Body: {resp.text}")
        return resp

    async def _run(client):
        last = None
        for host in hosts:
            resp = await _post(client, host)
            last = resp
            if resp.status_code == 200:
                print(f"✅ {action_label}推送成功！（{host}）")
                return True
            if not (resp.status_code == 400 and "BadDeviceToken" in resp.text):
                break
        if db_client is not None and db_id is not None:
            db_client.collection("notify").update(
                db_id, {"error": f"{last.status_code}: {last.text}"}
            )
        print("❌ 推送失敗，請檢查 Token 和設定")
        return False

    if http_client is not None:
        return await _run(http_client)
    async with httpx.AsyncClient(http2=True) as _client:
        return await _run(_client)
