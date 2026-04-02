from update_live_activity import send_push
import asyncio

asyncio.run(
    send_push(
        action="notify_start",
        apns_device_token="",
        notify_title="你好",
        notify_body="夯爆了",
        today_slots=[],
    )
)
