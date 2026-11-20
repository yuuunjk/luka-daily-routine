#!/usr/bin/env python3
"""LUKA 스포츠 데이터 수집기 — ESPN 공개 API 기반 (네이버 대체).

네이버 스포츠 API는 해외 데이터센터 IP를 막아 GitHub Actions에서 쓸 수 없었다.
ESPN 공개 API는 Actions에서도 열리고, 브라우저에는 CORS(`access-control-allow-origin: *`)를
열어 주므로 앱이 실시간으로 직접 호출할 수도 있다.

만드는 파일
  data/today.json      오늘(±하루) 경기 — 상태·스코어·로고
  data/standings.json  리그별 순위
  data/news.json       주요 소식 (한국어 제목·3줄 요약·원문 링크)
  data/teams-ko.json   팀 약어 → 한국어 이름 (앱이 직접 호출할 때도 사용)

원칙
  * 기사 전문은 저장하지 않는다. 제목·요약·링크만.
  * 어린이에게 부적절한 기사는 버린다.
  * 영어는 한국어로 옮긴다(팀 사전 + MyMemory 번역, 실패하면 영어 그대로).
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
SITE = "https://site.api.espn.com/apis/site/v2/sports"
CORE = "https://site.api.espn.com/apis/v2/sports"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"

SPORT_ORDER = ["야구", "해외야구", "축구", "해외축구", "농구", "배구", "골프", "일반"]
SPORT_EMOJI = {"야구": "⚾", "해외야구": "⚾", "축구": "⚽", "해외축구": "⚽",
               "농구": "🏀", "배구": "🏐", "골프": "⛳", "일반": "🏅"}

LEAGUES = [
    {"espn": "baseball/mlb", "sport": "야구", "name": "MLB", "season": "2026"},
    {"espn": "basketball/nba", "sport": "농구", "name": "NBA", "season": "2026"},
    {"espn": "soccer/eng.1", "sport": "해외축구", "name": "프리미어리그", "season": "2026"},
    {"espn": "soccer/esp.1", "sport": "해외축구", "name": "라리가", "season": "2026"},
    {"espn": "soccer/ger.1", "sport": "해외축구", "name": "분데스리가", "season": "2026"},
    {"espn": "soccer/ita.1", "sport": "해외축구", "name": "세리에A", "season": "2026"},
    {"espn": "soccer/uefa.champions", "sport": "해외축구", "name": "챔피언스리그", "season": "2026"},
    {"espn": "golf/pga", "sport": "골프", "name": "PGA", "season": "2026"},
]

TEAMS_KO = {
    # NBA
    "ATL": "애틀랜타", "BOS": "보스턴", "BKN": "브루클린", "CHA": "샬럿", "CHI": "시카고",
    "CLE": "클리블랜드", "DAL": "댈러스", "DEN": "덴버", "DET": "디트로이트",
    "GS": "골든스테이트", "HOU": "휴스턴", "IND": "인디애나", "LAC": "LA클리퍼스",
    "LAL": "LA레이커스", "MEM": "멤피스", "MIA": "마이애미", "MIL": "밀워키",
    "MIN": "미네소타", "NO": "뉴올리언스", "NOP": "뉴올리언스", "NY": "뉴욕", "NYK": "뉴욕",
    "OKC": "오클라호마", "ORL": "올랜도", "PHI": "필라델피아", "PHX": "피닉스",
    "POR": "포틀랜드", "SAC": "새크라멘토", "SA": "샌안토니오", "SAS": "샌안토니오",
    "TOR": "토론토", "UTA": "유타", "WAS": "워싱턴", "WSH": "워싱턴",
    # MLB
    "ARI": "애리조나", "BAL": "볼티모어", "CHC": "시카고컵스", "CWS": "시카고화이트삭스",
    "CHW": "시카고화이트삭스", "CIN": "신시내티", "COL": "콜로라도", "KC": "캔자스시티",
    "KCR": "캔자스시티", "LAA": "LA에인절스", "LAD": "LA다저스", "MIA": "마이애미",
    "MIL": "밀워키", "MIN": "미네소타", "NYM": "뉴욕메츠", "NYY": "뉴욕양키스",
    "ATH": "애슬레틱스", "OAK": "애슬레틱스", "PHI": "필라델피아", "PIT": "피츠버그",
    "SD": "샌디에이고", "SDP": "샌디에이고", "SF": "샌프란시스코", "SFG": "샌프란시스코",
    "SEA": "시애틀", "STL": "세인트루이스", "TB": "탬파베이", "TBR": "탬파베이",
    "TEX": "텍사스", "TOR": "토론토", "WSH": "워싱턴",
    # 프리미어리그
    "ARS": "아스널", "AVL": "애스턴빌라", "BOU": "본머스", "BRE": "브렌트퍼드",
    "BHA": "브라이턴", "CHE": "첼시", "CRY": "크리스털팰리스", "EVE": "에버턴",
    "FUL": "풀럼", "IPS": "입스위치", "LEI": "레스터", "LIV": "리버풀",
    "MCI": "맨시티", "MUN": "맨유", "NEW": "뉴캐슬",
    "NFO": "노팅엄", "SOU": "사우샘프턴", "TOT": "토트넘", "WHU": "웨스트햄",
    "WOL": "울버햄프턴", "BUR": "번리", "SHU": "셰필드", "LUT": "루턴",
    # 라리가
    "RMA": "레알마드리드", "BAR": "바르셀로나", "ATM": "아틀레티코", "SEV": "세비야",
    "ATH": "아틀레틱", "RSO": "레알소시에다드", "VIL": "비야레알", "BET": "베티스",
    "VAL": "발렌시아", "GIR": "지로나", "CEL": "셀타", "OSA": "오사수나",
    "RAY": "라요", "MLL": "마요르카", "GET": "헤타페", "ALV": "알라베스",
    # 세리에A
    "JUV": "유벤투스", "INT": "인터밀란", "ACM": "AC밀란", "NAP": "나폴리",
    "ROM": "로마", "LAZ": "라치오", "ATA": "아탈란타", "FIO": "피오렌티나",
    "BOL": "볼로냐", "UDI": "우디네세", "GEN": "제노아",
    # 분데스리가
    "BAY": "바이에른", "BVB": "도르트문트", "LEV": "레버쿠젠", "RBL": "라이프치히",
    "SGE": "프랑크푸르트", "VFB": "슈투트가르트", "WOB": "볼프스부르크",
    "BMG": "묀헨글라트바흐", "SCF": "프라이부르크", "M05": "마인츠",
}

STATE_KO = {"pre": "예정", "in": "진행 중", "post": "종료"}
DIRTY: set[str] = set()

# 아이 화면에 올리지 않는 주제
BANNED = [
    "sex", "porn", "nude", "rape", "murder", "killed", "dead", "death", "suicide",
    "drug", "cocaine", "arrest", "arrested", "jail", "prison", "lawsuit", "court",
    "gambling", "betting", "scandal", "affair", "divorce", "dating", "rumor",
    "politics", "president", "election", "war", "shooting", "crash", "disaster",
    "성폭력", "성추행", "음란", "도박", "마약", "사망", "숨진", "살해", "자살",
    "구속", "기소", "재판", "소송", "논란", "스캔들", "이혼", "열애", "정치", "대통령",
]


def budget_ok(start, limit=280):
    return (time.time() - start) < limit


def http_get(url: str, timeout: int = 15, retries: int = 1) -> bytes:
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json,*/*"})
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return res.read()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < retries:
                time.sleep(1.0)
    raise RuntimeError(f"{type(last).__name__}: {last} @ {url[:80]}")


def get_json(url: str) -> dict:
    return json.loads(http_get(url).decode("utf-8", "replace"))


def write_json(name: str, obj) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write("\n")
    print(f"  → {name} ({os.path.getsize(path) / 1024:.1f} KB)", flush=True)


def read_json(name: str, default=None):
    try:
        with open(os.path.join(DATA_DIR, name), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return default


# 약어가 리그마다 겹치는 경우(예: MIL = 밀워키 벅스 / AC밀란) 리그별로 먼저 본다
KO_BY_LEAGUE = {
    "soccer/ita.1": {"MIL": "AC밀란", "TOR": "토리노", "ROM": "로마", "INT": "인터밀란",
                     "JUV": "유벤투스", "NAP": "나폴리", "LAZ": "라치오", "ATA": "아탈란타",
                     "FIO": "피오렌티나", "BOL": "볼로냐", "UDI": "우디네세", "GEN": "제노아"},
    "soccer/uefa.champions": {"MIL": "AC밀란", "TOR": "토리노", "ROM": "로마", "INT": "인터밀란",
                              "JUV": "유벤투스", "NAP": "나폴리", "LAZ": "라치오", "ATA": "아탈란타"},
}


def team_ko(team: dict, league_key: str = "") -> str:
    abbr = (team.get("abbreviation") or "").upper()
    override = (KO_BY_LEAGUE.get(league_key) or {}).get(abbr)
    if override:
        return override
    if abbr and abbr in TEAMS_KO:
        return TEAMS_KO[abbr]
    name = team.get("shortDisplayName") or team.get("name") or team.get("displayName") or ""
    if not name:
        return ""
    # 모르는 팀은 한 번만 번역해서 사전에 저장한다 (다음 실행부터는 바로 사용)
    ko = translate(name)
    if ko and ko != name and abbr:
        TEAMS_KO[abbr] = ko
        DIRTY.add(abbr)
    return ko


def logo_of(team: dict) -> str:
    logos = team.get("logos") or []
    if logos:
        return logos[0].get("href") or ""
    if team.get("logo"):
        return team["logo"]
    return ""


def status_ko(comp: dict, sport: str) -> tuple[str, str]:
    st = comp.get("status") or {}
    stype = (st.get("type") or {})
    state = stype.get("state") or "pre"
    period = st.get("period") or 0
    clock = (st.get("displayClock") or "").strip()
    detail = stype.get("shortDetail") or stype.get("detail") or ""
    if state == "pre":
        return "upcoming", ""
    if state == "post":
        return "done", "종료"
    # 진행 중
    if sport == "농구":
        label = f"{period}쿼터" if period else "진행 중"
    elif sport == "야구":
        label = f"{period}회" if period else "진행 중"
    elif sport in ("해외축구", "축구"):
        label = "후반" if period >= 2 else "전반"
        if clock:
            label += f" {clock}"
    elif sport == "골프":
        label = f"{period}라운드" if period else "진행 중"
    else:
        label = "진행 중"
    return "live", label


def to_game(event: dict, league: dict) -> dict | None:
    comps = event.get("competitions") or []
    if not comps:
        # 골프처럼 대회 하나가 이벤트인 경우
        name = event.get("name") or event.get("shortName") or ""
        if not name:
            return None
        st = event.get("status") or {}
        state = ((st.get("type") or {}).get("state")) or "pre"
        return {
            "id": event.get("id") or name,
            "sport": league["sport"], "emoji": SPORT_EMOJI[league["sport"]],
            "league": league["espn"], "leagueName": league["name"],
            "time": "", "date": "", "state": {"pre": "upcoming", "in": "live", "post": "done"}.get(state, "upcoming"),
            "status": "", "home": {"name": name, "code": "", "emblem": "", "score": None},
            "away": {"name": "", "code": "", "emblem": "", "score": None},
            "winner": "DRAW", "stadium": "", "channel": "", "onAir": False,
        }
    comp = comps[0]
    competitors = comp.get("competitors") or []
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if not home or not away:
        return None
    state, status_text = status_ko(comp, league["sport"])
    dt_raw = event.get("date") or comp.get("date") or ""
    time_text = ""
    date_text = ""
    if dt_raw:
        try:
            dt = datetime.fromisoformat(dt_raw.replace("Z", "+00:00")).astimezone(KST)
            time_text = dt.strftime("%H:%M")
            date_text = dt.strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            pass
    home_team = home.get("team") or {}
    away_team = away.get("team") or {}
    home_score = home.get("score")
    away_score = away.get("score")
    try:
        home_score = int(float(home_score)) if home_score not in (None, "") else None
        away_score = int(float(away_score)) if away_score not in (None, "") else None
    except Exception:  # noqa: BLE001
        pass
    venue = ((comp.get("venue") or {}).get("fullName")) or ""
    return {
        "id": str(event.get("id") or comp.get("id") or ""),
        "sport": league["sport"], "emoji": SPORT_EMOJI[league["sport"]],
        "league": league["espn"], "leagueName": league["name"],
        "time": time_text, "date": date_text, "state": state, "status": status_text,
        "home": {"name": team_ko(home_team, league["espn"]), "code": home_team.get("abbreviation") or "",
                 "emblem": logo_of(home_team), "score": home_score},
        "away": {"name": team_ko(away_team, league["espn"]), "code": away_team.get("abbreviation") or "",
                 "emblem": logo_of(away_team), "score": away_score},
        "winner": "HOME" if home.get("winner") else ("AWAY" if away.get("winner") else "DRAW"),
        "stadium": venue, "channel": "", "onAir": state == "live",
    }


def fetch_scoreboard(league: dict, date_str: str) -> list[dict]:
    url = f"{SITE}/{league['espn']}/scoreboard?dates={date_str}&limit=50"
    payload = get_json(url)
    out = []
    for ev in payload.get("events") or []:
        game = to_game(ev, league)
        if game:
            out.append(game)
    return out


def build_today(now: datetime) -> None:
    games, seen = [], set()
    for offset in (0, -1):
        date_str = (now + timedelta(days=offset)).strftime("%Y%m%d")
        for league in LEAGUES:
            try:
                for game in fetch_scoreboard(league, date_str):
                    if offset == -1 and game["state"] != "done":
                        continue
                    key = game["id"] or f"{game['home']['name']}-{game['away']['name']}"
                    if key in seen:
                        continue
                    seen.add(key)
                    if offset == -1:
                        game["yesterday"] = True
                    games.append(game)
            except Exception as exc:  # noqa: BLE001
                print(f"  {league['name']} {date_str} 실패: {exc}", flush=True)

    order = {"live": 0, "upcoming": 1, "done": 2, "cancel": 3}
    games.sort(key=lambda g: (order.get(g["state"], 4), g.get("date") or "", g.get("time") or ""))
    counts = {}
    for g in games:
        counts[g["sport"]] = counts.get(g["sport"], 0) + 1
    if not games:
        print("  경기 0건 — today.json 은 그대로 둡니다", flush=True)
        return
    write_json("today.json", {
        "updated": now.isoformat(timespec="seconds"),
        "date": now.strftime("%Y-%m-%d"),
        "total": len(games),
        "counts": counts,
        "sports": SPORT_ORDER,
        "source": "ESPN",
        "games": games,
    })
    print(f"  경기 {len(games)}건 {counts}", flush=True)


def stat_value(entry: dict, *names):
    for s in entry.get("stats") or []:
        if s.get("name") in names:
            return s.get("displayValue") or s.get("value")
    return None


def build_standings(now: datetime) -> None:
    leagues = []
    for league in LEAGUES:
        if league["espn"].startswith("golf"):
            continue
        url = f"{CORE}/{league['espn']}/standings?season={league['season']}"
        try:
            payload = get_json(url)
        except Exception as exc:  # noqa: BLE001
            print(f"  {league['name']} 순위 실패: {exc}", flush=True)
            continue
        groups = payload.get("children") or []
        rows = []
        for group in groups:
            group_name = (group.get("name") or "").replace("2026-27 ", "").replace("2026 ", "")
            entries = ((group.get("standings") or {}).get("entries")) or []
            for entry in entries:
                team = entry.get("team") or {}
                rank = stat_value(entry, "rank") or entry.get("rank")
                if rank is None:
                    rank = len(rows) + 1
                points = stat_value(entry, "points")
                win_pct = stat_value(entry, "winPercent") or stat_value(entry, "percentage")
                rows.append({
                    "rank": int(float(rank)) if str(rank).replace(".", "").isdigit() else len(rows) + 1,
                    "team": team_ko(team, league["espn"]),
                    "emblem": logo_of(team),
                    "games": stat_value(entry, "gamesPlayed"),
                    "w": stat_value(entry, "wins"),
                    "d": stat_value(entry, "ties"),
                    "l": stat_value(entry, "losses"),
                    "rate": win_pct,
                    "points": points if points not in ("", None) else None,
                    "last5": "",
                    "group": group_name if len(groups) > 1 else "",
                })
        if not rows:
            print(f"  {league['name']} 순위 없음", flush=True)
            continue
        soccer = league["espn"].startswith("soccer")
        metric = "pts" if soccer and all(r["points"] not in (None, "") for r in rows[:3]) else "pct"
        for r in rows:
            if metric == "pct":
                r["points"] = None
        rows.sort(key=lambda r: (r["group"] or "", r["rank"]))
        # 승률 문자열 정리
        for r in rows:
            if metric == "pct" and isinstance(r["rate"], str) and r["rate"].startswith("."):
                r["rate"] = "0" + r["rate"]
        leagues.append({
            "id": league["espn"].split("/")[-1], "sport": league["sport"], "name": league["name"],
            "season": str(league["season"]), "metric": metric, "teamCount": len(rows), "rows": rows,
        })
        print(f"  {league['name']}: {len(rows)}팀 ({metric})", flush=True)

    if not leagues:
        print("  순위 0개 — 그대로 둡니다", flush=True)
        return
    order = {s: i for i, s in enumerate(SPORT_ORDER)}
    leagues.sort(key=lambda l: (order.get(l["sport"], 9), l["name"]))
    write_json("standings.json", {
        "updated": now.isoformat(timespec="seconds"),
        "sports": SPORT_ORDER, "source": "ESPN", "leagues": leagues,
    })


def is_safe(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    return not any(word in low for word in BANNED)


def translate(text: str) -> str:
    """MyMemory 무료 번역. 실패하면 원문을 그대로 돌려준다."""
    if not text or not re.search(r"[A-Za-z]", text):
        return text
    if len(text) > 480:
        text = text[:480]
    url = ("https://api.mymemory.translated.net/get?q="
           + urllib.parse.quote(text, safe="") + "&langpair=en|ko")
    try:
        payload = get_json(url)
        out = ((payload.get("responseData") or {}).get("translatedText")) or ""
        if out and payload.get("responseStatus") == 200:
            return html.unescape(out)
    except Exception as exc:  # noqa: BLE001
        print(f"    번역 실패: {exc}", flush=True)
    return text


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?。])\s+", text.strip())
    return [p.strip(" ,") for p in parts if len(p.strip()) > 8]


def build_news(now: datetime) -> None:
    cache = read_json("news_cache.json", {}) or {}
    new_cache = {}
    sections = []
    total = 0
    for league in LEAGUES:
        if not budget_ok(START):
            break
        url = f"{SITE}/{league['espn']}/news?limit=6"
        try:
            payload = get_json(url)
        except Exception as exc:  # noqa: BLE001
            print(f"  {league['name']} 소식 실패: {exc}", flush=True)
            continue
        items = []
        for art in (payload.get("articles") or []):
            headline = (art.get("headline") or "").strip()
            desc = re.sub(r"<[^>]+>", " ", art.get("description") or "").strip()
            link = ""
            links = art.get("links") or {}
            if isinstance(links, dict):
                link = ((links.get("web") or {}).get("href")) or ""
            aid = str(art.get("id") or headline[:20])
            if not headline or not is_safe(headline) or not is_safe(desc):
                continue
            key = aid
            if key in cache:
                items.append(cache[key])
                new_cache[key] = cache[key]
            else:
                ko_title = translate(headline)
                if not is_safe(ko_title):
                    continue
                ko_desc = translate(desc)
                lines = split_sentences(ko_desc)[:3]
                if not lines:
                    continue
                item = {
                    "id": key, "title": ko_title, "summary": lines,
                    "source": "ESPN", "time": art.get("published") or "",
                    "link": link or "https://www.espn.com/",
                }
                items.append(item)
                new_cache[key] = item
            if len(items) >= 3:
                break
        if items:
            sections.append({"id": league["espn"], "label": league["name"], "items": items})
            total += len(items)
            print(f"  {league['name']}: {len(items)}건", flush=True)

    if not total:
        print("  소식 0건 — 그대로 둡니다", flush=True)
        return
    for k in list(new_cache)[:400]:
        pass
    write_json("news_cache.json", dict(list(new_cache.items())[:400]))
    write_json("news.json", {
        "updated": now.isoformat(timespec="seconds"),
        "total": total,
        "note": "제목·3줄 요약·원문 링크만 담습니다. 기사 전문과 댓글은 저장하지 않습니다.",
        "source": "ESPN (한국어 번역)",
        "sections": sections,
    })


START = time.time()


def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass
    now = datetime.now(KST)
    print(f"[{now.isoformat(timespec='seconds')}] ESPN 수집 시작", flush=True)
    cached = read_json("teams-ko.json", {}) or {}
    for k, v in cached.items():
        TEAMS_KO.setdefault(k, v)
    write_json("teams-ko.json", dict(sorted(TEAMS_KO.items())))
    for name, fn in (("today", build_today), ("standings", build_standings), ("news", build_news)):
        try:
            fn(now)
        except Exception as exc:  # noqa: BLE001
            print(f"!! {name} 실패: {exc}", flush=True)
    if DIRTY:
        write_json("teams-ko.json", dict(sorted(TEAMS_KO.items())))
        print(f"  팀 이름 {len(DIRTY)}개 번역 추가", flush=True)
    print(f"끝 ({time.time() - START:.0f}s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
