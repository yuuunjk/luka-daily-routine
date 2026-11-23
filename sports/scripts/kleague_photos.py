#!/usr/bin/env python3
"""K리그 선수 사진을 '원본 그대로' 다시 받아 카드용 WebP 로 저장한다.

K리그 공식 사진은 투명 배경 PNG 다. 이 스크립트는
  · 배경을 지우지도, 흰색/회색으로 채우지도 않고
  · 크기만 560px 로 줄여서
data/img/kleague/{playerId}.webp 로 저장한다(알파 채널 유지).

예전 파이프라인이 `.convert("RGB")` 로 알파를 버리는 바람에, 투명 영역 RGB 에
남아 있던 회색 스튜디오 배경이 사진 안의 회색 사각형으로 드러났다.
이 스크립트로 한 번 돌려 두면 그 흔적이 사라진다.

사용:
    python3 scripts/kleague_photos.py            # 없는 사진만 받는다
    python3 scripts/kleague_photos.py --force    # data/leaders.json 의 K리그 선수 전부 다시 받는다
    python3 scripts/kleague_photos.py --ids 20240227 20180294
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
IMG_DIR = os.path.join(DATA_DIR, "img", "kleague")
LEADERS = os.path.join(DATA_DIR, "leaders.json")
KLEAGUE = "https://www.kleague.com"
KLEAGUE_CDN = "https://d2tfp74nsbbrkr.cloudfront.net"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
MAX_W = 560


def http_get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ko-KR"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


def source_url(pid: str) -> str:
    """선수 상세 페이지에서 공식 사진 주소(img1)를 찾는다."""
    html = http_get(f"{KLEAGUE}/record/playerDetail.do?playerId={pid}", timeout=30).decode("utf-8", "replace")
    m = re.search(r'https://d2tfp74nsbbrkr\.cloudfront\.net/[^"\']+\.png', html)
    return m.group(0) if m else ""


def save_webp(pid: str, src: str) -> tuple[bool, str]:
    from PIL import Image  # noqa: PLC0415
    import io  # noqa: PLC0415

    raw = http_get(src, timeout=60)
    image = Image.open(io.BytesIO(raw)).convert("RGBA")   # 알파 그대로
    w, h = image.size
    if w > MAX_W:
        image = image.resize((MAX_W, max(1, int(h * MAX_W / w))), Image.LANCZOS)
    os.makedirs(IMG_DIR, exist_ok=True)
    path = os.path.join(IMG_DIR, f"{pid}.webp")
    image.save(path, "WEBP", quality=82, method=4)
    return True, f"{pid} {w}x{h} -> {image.size[0]}x{image.size[1]} {os.path.getsize(path)//1024}KB"


def kleague_targets() -> dict[str, str]:
    """leaders.json 에 나오는 K리그 선수 → 사진 주소(로컬 경로면 빈 값)."""
    out: dict[str, str] = {}
    try:
        with open(LEADERS, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:  # noqa: BLE001
        print(f"leaders.json 읽기 실패: {exc}")
        return out
    for lg in data.get("leagues", []):
        if not str(lg.get("id", "")).startswith("kleague"):
            continue
        for cat in lg.get("categories", []):
            for row in cat.get("rows", []):
                pid = str(row.get("id") or "").strip()
                photo = row.get("photo") or ""
                if not pid:
                    continue
                if photo.startswith("http"):
                    out[pid] = photo
                else:
                    out.setdefault(pid, "")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="저장된 사진도 다시 받는다")
    ap.add_argument("--ids", nargs="*", default=None, help="특정 선수 id 만")
    args = ap.parse_args()

    targets = kleague_targets()
    if args.ids:
        targets = {pid: targets.get(pid, "") for pid in args.ids}

    ok = fail = skip = 0
    for pid, known in sorted(targets.items()):
        path = os.path.join(IMG_DIR, f"{pid}.webp")
        if os.path.exists(path) and os.path.getsize(path) > 2000 and not args.force:
            skip += 1
            continue
        try:
            src = known or source_url(pid)
            if not src:
                print(f"{pid} 사진 주소 없음")
                fail += 1
                continue
            _, msg = save_webp(pid, src)
            print(msg, flush=True)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"{pid} 실패: {str(exc)[:90]}", flush=True)
            fail += 1
    print(f"\n저장 {ok} · 건너뜀 {skip} · 실패 {fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
