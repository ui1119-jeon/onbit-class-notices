#!/usr/bin/env python3
"""Fetch ONLY public notice/homework from teacher Apps Script, build a static JSON snapshot.

Usage:
  ONBIT_SOURCE_URL='https://script.google.com/macros/s/.../exec' python scripts/build_snapshot.py
  python scripts/build_snapshot.py --fixture scripts/test_fixture.json  # offline preview
Never print the source URL; place it in GitHub Actions Secrets, not files.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "web"
PUBLIC.mkdir(exist_ok=True)
KST = ZoneInfo("Asia/Seoul")


def endpoint(raw: str) -> str:
    parsed = urlparse(raw.strip())
    if parsed.scheme != "https" or parsed.hostname != "script.google.com" or not re.fullmatch(r"/macros/s/[A-Za-z0-9_-]+/exec", parsed.path):
        raise ValueError("잘못된 Apps Script 웹앱 URL입니다. /exec까지의 주소를 입력하세요.")
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({"api": "1", "action": "sync.all"})
    if os.environ.get("ONBIT_STAFF_TOKEN", ""):
        query["token"] = os.environ["ONBIT_STAFF_TOKEN"]
    return urlunparse(parsed._replace(query=urlencode(query), fragment=""))


def download(raw: str) -> dict:
    url = endpoint(raw)
    error = None
    for attempt in range(4):
        try:
            request = Request(url, headers={"Accept": "application/json", "User-Agent": "OnbitSnapshotBot/1.0"})
            with urlopen(request, timeout=30) as response:
                body = response.read(2_000_000)
                content_type = response.headers.get("Content-Type", "")
            obj = json.loads(body.decode("utf-8-sig"))
            if not isinstance(obj, dict):
                raise ValueError("응답이 JSON 객체가 아닙니다.")
            if obj.get("ok") is not True:
                raise ValueError("서버가 ok=false를 반환했습니다.")
            return obj
        except Exception as exc:
            # Google ContentService can redirect to an unsettled googleusercontent URL.
            # Retry the ORIGINAL endpoint, not the expiring redirected URL.
            error = type(exc).__name__
            if attempt < 3:
                time.sleep(3 * (attempt + 1))
    raise RuntimeError("공용 API 응답 실패(4회). 권한, URL, API 배포 상태를 점검하세요. 오류 유형: " + str(error))


def txt(value: object, cap: int) -> str:
    if value is None:
        return ""
    return str(value).strip()[:cap]


def bool_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "y", "yes", "예", "수행")


def sanitize(source: dict) -> dict:
    if source.get("ok") is not True:
        raise ValueError("API 응답 ok=true가 아닙니다.")
    if not isinstance(source.get("homework"), list) or not isinstance(source.get("homeroom"), list):
        raise ValueError("homework/homeroom 목록이 없습니다. 잘못된 API를 연결했을 수 있습니다.")
    if len(source["homework"]) > 5000 or len(source["homeroom"]) > 1000:
        raise ValueError("공개 데이터 크기가 예상치를 초과하여 게시를 중단했습니다.")
    today = datetime.now(KST).date()
    end_date = today + timedelta(days=730)
    homework: list[dict] = []
    seen_hw: set[tuple] = set()
    for row in source["homework"]:
        if not isinstance(row, dict):
            continue
        dt = txt(row.get("date"), 10)
        try:
            due = date.fromisoformat(dt)
        except ValueError:
            continue
        if not (today <= due <= end_date):
            continue
        item = {
            "date": dt,
            "subject": txt(row.get("subject"), 80),
            "content": txt(row.get("content"), 700),
            "performance": bool_value(row.get("performance")),
        }
        if not item["subject"] or not item["content"]:
            continue
        key = tuple(item.values())
        if key in seen_hw:
            continue
        seen_hw.add(key)
        homework.append(item)
    homework.sort(key=lambda a: (a["date"], a["subject"], a["content"]))
    homeroom = []
    for i, row in enumerate(source["homeroom"]):
        if not isinstance(row, dict):
            continue
        content = txt(row.get("content"), 500)
        if content:
            try:
                order = int(row.get("display_order", i + 1))
            except (ValueError, TypeError):
                order = i + 1
            homeroom.append({"content": content, "display_order": max(1, order)})
    homeroom.sort(key=lambda x: x["display_order"])
    # Deliberately do not export original IDs, any extra fields, users, or spreadsheet metadata.
    return {
        "version": 3,
        "title": "3학년 4반 학급 안내",
        "updated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "update_times": ["10:00", "13:00", "16:30"],
        "homeroom": homeroom,
        "homework": homework,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, help="local sample API response for testing only")
    args = parser.parse_args()
    if args.fixture:
        source = json.loads(args.fixture.read_text(encoding="utf-8"))
    else:
        original = os.environ.get("ONBIT_SOURCE_URL", "")
        if not original:
            print("GitHub Secret ONBIT_SOURCE_URL이 없습니다.", file=sys.stderr)
            return 1
        source = download(original)
    result = sanitize(source)
    (PUBLIC / "snapshot.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"스냅샷 생성 완료: 조종례 {len(result['homeroom'])}개, 숙제 {len(result['homework'])}개, {result['updated_at']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
