#!/usr/bin/env python3
"""
VocPass — Android 課程動態常駐通知 FCM 推送模組
==========================================

由 app.py 在 iOS 發送完成後呼叫，透過 FCM **data message** 把當天課表批量推給
Android 裝置，Android 端的 VocPassFirebaseMessagingService 收到後會解析出
「這節課 / 下節課 / 倒數」並重建常駐通知。

Kotlin 端（VocPassFirebaseMessagingService.parseCurriculumToClassStatus）預期的
payload 是 data message，key 為 "curriculum"，value 為 **扁平 JSON 陣列字串**，
每個元素欄位如下（時間為 24 小時制 "HH:MM"）：

    {
      "period":    "第六節",
      "subject":   "數位電子實習",
      "room":      "電子二甲",
      "startTime": "14:10",
      "endTime":   "15:00"
    }

Firebase 服務帳戶金鑰固定讀取同目錄下的 fcm-account.json。
"""

from __future__ import annotations

import json
import os
import sys
import warnings
from typing import Any, Iterable

SERVICE_ACCOUNT_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fcm-account.json"
)

REQUIRED_FIELDS = ("period", "subject", "room", "startTime", "endTime")


def _import_firebase():
    try:
        import firebase_admin
        from firebase_admin import credentials, messaging
    except ImportError:
        sys.stderr.write(
            "找不到 firebase-admin，請先安裝：\n    pip install firebase-admin\n"
        )
        sys.exit(1)
    return firebase_admin, credentials, messaging


def init_firebase() -> None:
    """初始化 Firebase Admin SDK。"""
    firebase_admin, credentials, _ = _import_firebase()
    if firebase_admin._apps:
        return

    if not os.path.isfile(SERVICE_ACCOUNT_FILE):
        raise FileNotFoundError(f"找不到服務帳戶金鑰檔：{SERVICE_ACCOUNT_FILE}")

    try:
        with open(SERVICE_ACCOUNT_FILE, "r", encoding="utf-8") as fh:
            project_id = json.load(fh).get("project_id")
    except (OSError, ValueError):
        project_id = None

    cred = credentials.Certificate(SERVICE_ACCOUNT_FILE)
    options = {"projectId": project_id} if project_id else None
    firebase_admin.initialize_app(cred, options)


def normalize_curriculum(raw: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    """把課表整理成 Kotlin 端預期的扁平陣列，欄位齊全且皆為字串。"""
    normalized: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        entry = {field: str(item.get(field, "")).strip() for field in REQUIRED_FIELDS}

        if not entry["subject"] or not entry["startTime"] or not entry["endTime"]:
            continue
        normalized.append(entry)

    normalized.sort(key=lambda e: _minutes(e["startTime"]))
    return normalized


def _minutes(hhmm: str) -> int:
    try:
        hour, minute = hhmm.split(":")[:2]
        return int(hour) * 60 + int(minute)
    except (ValueError, IndexError):
        return 0


def send_batch(targets: list[tuple[str, list[dict[str, str]]]]) -> list[tuple[bool, str]]:
    """批量發送 data message。

    targets: [(fcm_token, 扁平課表), ...]
    回傳與 targets 等長的 [(是否成功, message_id 或錯誤訊息), ...]。
    """
    if not targets:
        return []

    _, _, messaging = _import_firebase()

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", category=DeprecationWarning, module="firebase_admin.*"
        )
        messages = [
            messaging.Message(
                token=token,
                data={"curriculum": json.dumps(curriculum, ensure_ascii=False)},
                android=messaging.AndroidConfig(priority="high"),
            )
            for token, curriculum in targets
        ]
        batch = messaging.send_each(messages)

    results: list[tuple[bool, str]] = []
    for resp in batch.responses:
        if resp.success:
            results.append((True, resp.message_id))
        else:
            results.append((False, str(resp.exception)))
    return results
