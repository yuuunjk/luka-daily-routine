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
import hashlib
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
SITE = "https://site.api.espn.com/apis/site/v2/sports"
CORE = "https://site.api.espn.com/apis/v2/sports"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"

SPORT_ORDER = ["야구", "축구", "농구", "배구", "해외축구", "미식축구", "국제 대회", "해외야구", "일반"]
SPORT_EMOJI = {"야구": "⚾", "해외야구": "⚾", "축구": "⚽", "해외축구": "⚽", "국제 대회": "🌍",
               "농구": "🏀", "미식축구": "🏈", "배구": "🏐", "일반": "🏅"}

LEAGUES = [
    {"espn": "baseball/mlb", "sport": "야구", "name": "MLB", "season": "2026"},
    {"espn": "basketball/nba", "sport": "농구", "name": "NBA", "season": "2026"},
    {"espn": "football/nfl", "sport": "미식축구", "name": "NFL", "season": "2026"},
    {"espn": "soccer/eng.1", "sport": "해외축구", "name": "프리미어리그", "season": "2026"},
    {"espn": "soccer/esp.1", "sport": "해외축구", "name": "라리가", "season": "2026"},
    {"espn": "soccer/ger.1", "sport": "해외축구", "name": "분데스리가", "season": "2026"},
    {"espn": "soccer/ita.1", "sport": "해외축구", "name": "세리에A", "season": "2026"},
    {"espn": "soccer/tur.1", "sport": "해외축구", "name": "튀르키예 쉬페르리그", "season": "2026"},
    {"espn": "soccer/uefa.champions", "sport": "해외축구", "name": "챔피언스리그", "season": "2026"},
    # ── 국제 대회 (나라 대 나라) ──
    # ESPN에서 soccer/afc.cup 은 'AFC 챔피언스리그2'(클럽 대회)라서, 아시안컵은 afc.asian.cup 을 쓴다.
    {"espn": "soccer/fifa.world", "sport": "국제 대회", "name": "월드컵", "season": "2026", "intl": True},
    {"espn": "soccer/fifa.worldq.afc", "sport": "국제 대회", "name": "월드컵 아시아 예선", "season": "2026", "intl": True},
    {"espn": "soccer/afc.asian.cup", "sport": "국제 대회", "name": "아시안컵", "season": "2026", "intl": True},
    {"espn": "soccer/uefa.nations", "sport": "국제 대회", "name": "네이션스리그", "season": "2026", "intl": True},
    {"espn": "soccer/uefa.euro", "sport": "국제 대회", "name": "유로", "season": "2026", "intl": True},
    {"espn": "soccer/caf.nations", "sport": "국제 대회", "name": "아프리카 네이션스컵", "season": "2026", "intl": True},
    {"espn": "soccer/conmebol.america", "sport": "국제 대회", "name": "코파 아메리카", "season": "2026", "intl": True},
    {"espn": "soccer/fifa.friendly", "sport": "국제 대회", "name": "국가대표 평가전", "season": "2026", "intl": True},
]
INTL_KEYS = {l["espn"] for l in LEAGUES if l.get("intl")}

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

# NFL 32팀 — 약어가 NBA·MLB와 겹쳐서(CHI·DEN·LAC·SF) 리그별 사전으로 따로 쓴다
NFL_TEAMS_KO = {
    "BUF": "버팔로", "MIA": "마이애미", "NE": "뉴잉글랜드", "NYJ": "뉴욕제츠",
    "BAL": "볼티모어", "CIN": "신시내티", "CLE": "클리블랜드", "PIT": "피츠버그",
    "HOU": "휴스턴", "IND": "인디애나폴리스", "JAX": "잭슨빌", "TEN": "테네시",
    "DEN": "덴버", "KC": "캔자스시티", "LV": "라스베이거스", "LAC": "LA차저스",
    "DAL": "댈러스", "NYG": "뉴욕자이언츠", "PHI": "필라델피아", "WAS": "워싱턴",
    "CHI": "시카고", "DET": "디트로이트", "GB": "그린베이", "MIN": "미네소타",
    "ATL": "애틀랜타", "CAR": "캐롤라이나", "NO": "뉴올리언스", "TB": "탬파베이",
    "ARI": "애리조나", "LAR": "LA램스", "SF": "샌프란시스코", "SEA": "시애틀",
}

# 튀르키예 쉬페르리그 18팀 (GEN은 세리에A 제노아와 겹침)
TUR_TEAMS_KO = {
    "GAL": "갈라타사라이", "FEN": "페네르바체", "BES": "베식타스", "TRAB": "트라브존스포르",
    "IBFK": "바샥셰히르", "KAS": "카슴파샤", "KOC": "코자엘리스포르", "AMED": "아메드스포르",
    "COR": "초룸", "GOZ": "괴즈테페", "CAY": "차이쿠르리제스포르", "ERZ": "에르주룸",
    "ALA": "알라냐스포르", "GEN": "겐츨레르비를리이", "KNY": "코니아스포르", "GAZ": "가즈안테프",
    "SAM": "삼순스포르", "EYU": "아이윕스포르",
}

# 국가대표 — CHI(칠레)·DEN(덴마크)·COL(콜롬비아)처럼 클럽 약어와 겹치는 게 많아 리그별 사전으로 쓴다
INT_TEAMS_KO = {
    "KOR": "대한민국", "PRK": "북한", "JPN": "일본", "CHN": "중국", "AUS": "호주",
    "IRN": "이란", "IRQ": "이라크", "KSA": "사우디아라비아", "QAT": "카타르", "UAE": "아랍에미리트",
    "UZB": "우즈베키스탄", "JOR": "요르단", "SYR": "시리아", "LIB": "레바논", "KUW": "쿠웨이트",
    "BHR": "바레인", "PLE": "팔레스타인", "YEM": "예멘", "OMA": "오만", "THA": "태국",
    "VIE": "베트남", "IDN": "인도네시아", "MAS": "말레이시아", "SIN": "싱가포르", "IND": "인도",
    "KGZ": "키르기스스탄", "KAZ": "카자흐스탄", "TAJ": "타지키스탄", "TKM": "투르크메니스탄", "HKG": "홍콩",
    "BRA": "브라질", "ARG": "아르헨티나", "URU": "우루과이", "COL": "콜롬비아", "ECU": "에콰도르",
    "PER": "페루", "CHI": "칠레", "PAR": "파라과이", "BOL": "볼리비아", "VEN": "베네수엘라",
    "MEX": "멕시코", "USA": "미국", "CAN": "캐나다", "CRC": "코스타리카", "HON": "온두라스",
    "PAN": "파나마", "JAM": "자메이카", "DOM": "도미니카공화국", "HAI": "아이티", "SLV": "엘살바도르",
    "GER": "독일", "FRA": "프랑스", "ESP": "스페인", "ENG": "잉글랜드", "ITA": "이탈리아",
    "POR": "포르투갈", "NED": "네덜란드", "BEL": "벨기에", "CRO": "크로아티아", "SUI": "스위스",
    "DEN": "덴마크", "SWE": "스웨덴", "NOR": "노르웨이", "FIN": "핀란드", "ISL": "아이슬란드",
    "POL": "폴란드", "CZE": "체코", "SVK": "슬로바키아", "SVN": "슬로베니아", "HUN": "헝가리",
    "AUT": "오스트리아", "GRE": "그리스", "TUR": "튀르키예", "SRB": "세르비아", "ROU": "루마니아",
    "BUL": "불가리아", "UKR": "우크라이나", "RUS": "러시아", "SCO": "스코틀랜드", "WAL": "웨일스",
    "NIR": "북아일랜드", "IRL": "아일랜드", "ALB": "알바니아", "MKD": "북마케도니아", "BIH": "보스니아",
    "GEO": "조지아", "ISR": "이스라엘", "AZE": "아제르바이잔", "ARM": "아르메니아", "EST": "에스토니아",
    "LVA": "라트비아", "LTU": "리투아니아", "BLR": "벨라루스", "MDA": "몰도바", "LUX": "룩셈부르크",
    "AND": "안도라", "GIB": "지브롤터", "LIE": "리히텐슈타인", "MLT": "몰타", "FRO": "페로제도",
    "KOS": "코소보", "CYP": "키프로스", "MNE": "몬테네그로",
    "MAR": "모로코", "SEN": "세네갈", "NGA": "나이지리아", "EGY": "이집트", "ALG": "알제리",
    "TUN": "튀니지", "CMR": "카메룬", "GHA": "가나", "CIV": "코트디부아르", "MLI": "말리",
    "BFA": "부르키나파소", "RSA": "남아프리카공화국", "COD": "콩고민주공화국", "ZAM": "잠비아", "ANG": "앙골라",
    "ETH": "에티오피아", "KEN": "케냐", "UGA": "우간다", "TAN": "탄자니아", "MOZ": "모잠비크",
    "GAB": "가봉", "GUI": "기니", "GAM": "감비아", "BEN": "베냉", "TOG": "토고",
    "NIG": "니제르", "CPV": "카보베르데", "EQG": "적도기니", "SUD": "수단", "LBY": "리비아",
    "NZL": "뉴질랜드", "TAH": "타히티", "FIJ": "피지", "SOL": "솔로몬제도", "VAN": "바누아투",
    "COK": "쿡제도", "PNG": "파푸아뉴기니", "NCL": "뉴칼레도니아",
}

STATE_KO = {"pre": "예정", "in": "진행 중", "post": "종료"}
GROUP_KO = {
    "American League": "아메리칸리그", "National League": "내셔널리그",
    "Eastern Conference": "동부", "Western Conference": "서부",
    "Eastern": "동부", "Western": "서부",
    "AFC": "AFC", "NFC": "NFC",
    "American Football Conference": "AFC", "National Football Conference": "NFC",
}
DIRTY: set[str] = set()

# 아이 화면에 올리지 않는 주제
BANNED = [
    "sex", "porn", "nude", "rape", "murder", "killed", "dead", "death", "suicide",
    "drug", "cocaine", "arrest", "arrested", "jail", "prison", "lawsuit", "court",
    "gambling", "betting", "scandal", "affair", "divorce", "dating", "rumor",
    "politics", "president", "election", "war", "shooting", "crash", "disaster",
    "성폭력", "성추행", "성희롱", "음란", "도박", "마약", "사망", "숨진", "살해", "자살",
    "구속", "기소", "재판", "소송", "고소", "경찰", "검찰", "압수수색", "논란", "스캔들",
    "이혼", "열애", "정치", "대통령", "음주", "폭행", "폭력", "시위", "강제철거", "징계",
    "사고사", "변사", "시신", "성매매", "불법", "마약류", "협박", "공갈",
    "시의원", "구의원", "도의원", "국회의원", "지원금", "감액", "예산",
]


def budget_ok(start, limit=420):
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
    "football/nfl": NFL_TEAMS_KO,
    "soccer/tur.1": TUR_TEAMS_KO,
}
for _intl in INTL_KEYS:
    KO_BY_LEAGUE[_intl] = INT_TEAMS_KO


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
    elif sport in ("해외축구", "축구", "국제 대회"):
        label = "후반" if period >= 2 else "전반"
        if clock:
            label += f" {clock}"
    elif sport == "미식축구":
        words = f"{stype.get('description') or ''} {detail}".lower()
        if "half" in words:
            label = "하프타임"
        elif period and period > 4:
            label = "연장"
        elif period:
            label = f"{period}쿼터" + (f" {clock}" if clock else "")
        else:
            label = "진행 중"
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


# ══ 한국 리그 (K리그·KBO·KBL·V리그) ═════════════════════════╗
# ESPN에는 KBO·KBL·V리그가 없고, K리그는 코드가 없어서(400) 공식 사이트 JSON을 직접 받는다.
# KBO는 경기 API가 막혀 있어(비시즌이라 지금은 경기가 없다) 순위·소식만 담는다.
KLEAGUE = "https://www.kleague.com"
KBL_API = "https://api.kbl.or.kr"
KOVO_API = "https://user-api.kovo.co.kr"
KBO_RANK = "https://www.koreabaseball.com/Record/TeamRank/TeamRankDaily.aspx"
KR_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9",
}
KBL_HEADERS = {"Channel": "WEB", "TeamCode": "", "lang": "ko", "X-Requested-With": "XMLHttpRequest"}

V_TEAMS_KO = {
    "1001": "대한항공", "1002": "삼성화재", "1004": "KB손해보험", "1005": "현대캐피탈",
    "1006": "한국전력", "1008": "OK저축은행", "1009": "우리카드",
    "2001": "현대건설", "2002": "한국도로공사", "2003": "정관장", "2004": "흥국생명",
    "2005": "GS칼텍스", "2006": "IBK기업은행", "2008": "SOOP",
}


def kr_get_json(url: str, extra_headers: dict | None = None) -> dict | list:
    headers = dict(KR_HEADERS)
    headers.update(extra_headers or {})
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as res:
        return json.loads(res.read().decode("utf-8", "replace"))


def kr_post_json(url: str, payload: dict) -> dict | list:
    headers = dict(KR_HEADERS)
    headers["Content-Type"] = "application/json"
    headers["Referer"] = KLEAGUE + "/schedule.do"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=20) as res:
        return json.loads(res.read().decode("utf-8", "replace"))


def kr_game(gid, sport, league, league_name, date, time, state, status,
            home, hscore, away, ascore, stadium="", hemblem="", aemblem="") -> dict:
    return {
        "id": gid, "sport": sport, "emoji": SPORT_EMOJI.get(sport, "🏅"),
        "league": league, "leagueName": league_name, "time": time, "date": date,
        "state": state, "status": status,
        "home": {"name": home, "code": "", "emblem": hemblem, "score": hscore},
        "away": {"name": away, "code": "", "emblem": aemblem, "score": ascore},
        "winner": ("HOME" if (hscore or 0) > (ascore or 0) else "AWAY" if (ascore or 0) > (hscore or 0) else "DRAW"),
        "stadium": stadium, "channel": "", "onAir": state == "live",
    }


def game_window(now: datetime) -> set[str]:
    return {(now + timedelta(days=d)).strftime("%Y-%m-%d") for d in (-1, 0, 1)}


def kleague_games(now: datetime) -> list[dict]:
    """K리그1·2 — kleague.com 공식 JSON."""
    out: list[dict] = []
    months = set()
    for d in (-1, 0, 1):
        day = now + timedelta(days=d)
        months.add((day.year, day.month))
    window = game_window(now)
    for (year, month) in sorted(months):
        for lid, lname in ((1, "K리그1"), (2, "K리그2")):
            try:
                data = kr_post_json(f"{KLEAGUE}/getScheduleList.do", {"year": year, "month": month, "leagueId": lid})
            except Exception as exc:  # noqa: BLE001
                print(f"  {lname} 일정 실패: {exc}", flush=True)
                continue
            for m in ((data or {}).get("data") or {}).get("scheduleList") or []:
                date = (m.get("gameDate") or "").replace(".", "-")
                if date not in window:
                    continue
                raw = (m.get("gameStatus") or "").strip().upper()
                if raw.startswith("F"):
                    state, status = "done", "종료"
                elif raw == "HF":
                    state, status = "live", "하프타임"
                elif raw in ("1H", "2H"):
                    state, status = "live", ("전반" if raw == "1H" else "후반")
                elif raw:
                    state, status = "live", "진행 중"
                else:
                    state, status = "upcoming", ""
                hg = m.get("homeGoal")
                ag = m.get("awayGoal")
                hscore = hg if state != "upcoming" else None
                ascore = ag if state != "upcoming" else None
                out.append(kr_game(
                    f"kleague-{m.get('gameId')}", "축구", f"kleague.{lid}", lname,
                    date, (m.get("gameTime") or "").strip(), state, status,
                    (m.get("homeTeamName") or "").strip(), hscore,
                    (m.get("awayTeamName") or "").strip(), ascore,
                    (m.get("fieldName") or "").strip(),
                ))
    return out


def kleague_standings() -> list[dict]:
    data = kr_post_json(f"{KLEAGUE}/api/clubRank.do", {"year": datetime.now(KST).year})
    body = (data or {}).get("data") or {}
    out = []
    for lid, key, name in ((1, "league1", "K리그1"), (2, "league2", "K리그2")):
        rows = []
        for r in body.get(key) or []:
            rows.append({
                "rank": int(r.get("rank") or len(rows) + 1),
                "team": (r.get("teamName") or "").strip(),
                "emblem": "",
                "games": r.get("gameCount"),
                "w": r.get("winCnt"), "d": r.get("tieCnt"), "l": r.get("lossCnt"),
                "rate": None, "points": r.get("gainPoint"), "last5": "", "group": "",
            })
        if not rows:
            continue
        rows.sort(key=lambda x: (-float(x.get("points") or 0), -float(x.get("w") or 0)))
        for i, r in enumerate(rows, start=1):
            r["rank"] = i
        out.append({"id": f"kleague.{lid}", "sport": "축구", "name": name,
                    "season": str(datetime.now(KST).year), "metric": "pts",
                    "teamCount": len(rows), "rows": rows})
    return out


def kbo_standings() -> list[dict]:
    """KBO — 공식 기록 페이지 HTML 표 파싱 (경기 API는 막혀 있음)."""
    raw = http_get(KBO_RANK).decode("utf-8", "replace")
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", raw, re.S):
        cells = [html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c))).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        if len(cells) < 7 or not cells[0].isdigit():
            continue
        try:
            games, w, l, d = int(cells[2]), int(cells[3]), int(cells[4]), int(cells[5])
        except Exception:  # noqa: BLE001
            continue
        rate = (w / (w + l)) if (w + l) else 0.0
        rows.append({
            "rank": 0, "team": cells[1], "emblem": "", "games": games, "w": w, "d": d, "l": l,
            "rate": f"{rate:.3f}", "points": None, "last5": "", "group": "",
        })
    if not rows:
        return []
    rows.sort(key=lambda x: (-float(x["rate"]), -float(x["w"])))
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    return [{"id": "kbo", "sport": "야구", "name": "KBO 리그", "season": str(datetime.now(KST).year),
             "metric": "pct", "teamCount": len(rows), "rows": rows}]


def kbl_games(now: datetime) -> list[dict]:
    lo = (now - timedelta(days=1)).strftime("%Y%m%d")
    hi = (now + timedelta(days=1)).strftime("%Y%m%d")
    data = kr_get_json(f"{KBL_API}/match/list?fromDate={lo}&toDate={hi}&tcodeList=all", KBL_HEADERS)
    out = []
    for m in data or []:
        date_raw = str(m.get("gameDate") or "")
        if len(date_raw) != 8:
            continue
        date = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:]}"
        start = str(m.get("gameStart") or "")
        time_text = f"{start[:2]}:{start[2:4]}" if len(start) == 4 else ""
        if int(m.get("isEnded") or 0) == 1:
            state, status = "done", "종료"
        elif time_text and date == now.strftime("%Y-%m-%d") and time_text <= now.strftime("%H:%M"):
            state, status = "live", "진행 중"
        else:
            state, status = "upcoming", ""
        hscore = m.get("scoreH") if state != "upcoming" else None
        ascore = m.get("scoreA") if state != "upcoming" else None
        out.append(kr_game(
            f"kbl-{m.get('gmkey')}", "농구", "kbl", "KBL", date, time_text, state, status,
            (m.get("tnameH") or "").strip(), hscore, (m.get("tnameA") or "").strip(), ascore,
            (m.get("stadiumname") or m.get("place") or "").strip() if isinstance(m.get("stadiumname"), str) else "",
        ))
    return out


def kbl_standings() -> list[dict]:
    data = kr_get_json(f"{KBL_API}/league/rank/team", KBL_HEADERS)
    rows = []
    for r in data or []:
        win, loss = int(r.get("win") or 0), int(r.get("loss") or 0)
        rate = (win / (win + loss)) if (win + loss) else 0.0
        rows.append({
            "rank": int(r.get("rank") or len(rows) + 1),
            "team": (r.get("tname") or r.get("tnameF") or "").strip(),
            "emblem": "", "games": win + loss, "w": win, "d": int(r.get("draw") or 0), "l": loss,
            "rate": f"{rate:.3f}", "points": None, "last5": "", "group": "",
        })
    if not rows:
        return []
    rows.sort(key=lambda x: (-float(x["rate"]), -float(x["w"])))
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    return [{"id": "kbl", "sport": "농구", "name": "KBL", "season": "2026-27",
             "metric": "pct", "teamCount": len(rows), "rows": rows}]


def kovo_season() -> str:
    rank = kr_get_json(f"{KOVO_API}/main/game/league/team-rank", {"Referer": "https://www.kovo.co.kr/"})
    payload = (rank or {}).get("payload") or []
    return str(payload[0].get("season")) if payload else "023"


def kovo_games(now: datetime) -> list[dict]:
    season = kovo_season()
    data = kr_get_json(f"{KOVO_API}/stat/game-schedule?seasonCode={season}", {"Referer": "https://www.kovo.co.kr/"})
    content = ((data or {}).get("payload") or {}).get("content") or []
    window = game_window(now)
    out = []
    for m in content:
        date = str(m.get("gdate") or "")
        if date not in window:
            continue
        score = str(m.get("score") or "")
        nums = re.findall(r"\d+", score)
        hscore = int(nums[0]) if len(nums) >= 2 else None
        ascore = int(nums[1]) if len(nums) >= 2 else None
        time_text = str(m.get("gstime") or "")[:5]
        if m.get("result"):
            state, status = "done", "종료"
        elif nums and date == now.strftime("%Y-%m-%d") and time_text and time_text <= now.strftime("%H:%M"):
            state, status = "live", "진행 중"
        else:
            state, status = "upcoming", ""
        gender = str(m.get("gender") or "1")
        lname = "V리그 남자부" if gender == "1" else "V리그 여자부"
        out.append(kr_game(
            f"kovo-{m.get('gnum')}-{date}", "배구", "kovo.m" if gender == "1" else "kovo.w", lname,
            date, time_text, state, status,
            V_TEAMS_KO.get(str(m.get("hcode") or ""), (m.get("hname") or "").strip()), hscore,
            V_TEAMS_KO.get(str(m.get("acode") or ""), (m.get("aname") or "").strip()), ascore,
            (m.get("place") or "").strip(), m.get("homeEmblemUrl") or "", m.get("awayEmblemUrl") or "",
        ))
    return out


def kovo_standings() -> list[dict]:
    data = kr_get_json(f"{KOVO_API}/main/game/league/team-rank", {"Referer": "https://www.kovo.co.kr/"})
    payload = (data or {}).get("payload") or []
    rows = []
    for r in payload:
        win, loss = int(r.get("win") or 0), int(r.get("lost") or 0)
        rate = (win / (win + loss)) if (win + loss) else 0.0
        rows.append({
            "rank": int(r.get("rank") or len(rows) + 1),
            "team": V_TEAMS_KO.get(str(r.get("tcode") or ""), str(r.get("tsname") or "").strip()),
            "emblem": r.get("emblemUrl") or "", "games": win + loss, "w": win, "d": 0, "l": loss,
            "rate": f"{rate:.3f}", "points": None, "last5": "", "group": "",
        })
    if not rows:
        return []
    rows.sort(key=lambda x: (-float(x["rate"]), -float(x["w"])))
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    season = str(payload[0].get("season") or "")
    # 023 같은 코드 대신 "2026-27" 처럼 보여 준다 (배구 시즌은 7월에 시작)
    now = datetime.now(KST)
    start_year = now.year if now.month >= 7 else now.year - 1
    label = f"{start_year}-{str(start_year + 1)[2:]}"
    return [{"id": "kovo", "sport": "배구", "name": "V리그 남자부", "season": label,
             "metric": "pct", "teamCount": len(rows), "rows": rows}]


def build_today(now: datetime) -> None:
    games, seen = [], set()
    for offset in (0, -1, 1):
        date_str = (now + timedelta(days=offset)).strftime("%Y%m%d")
        for league in LEAGUES:
            try:
                for game in fetch_scoreboard(league, date_str):
                    if offset == -1 and game["state"] != "done":
                        continue
                    if offset == 1 and game["state"] != "upcoming":
                        continue
                    key = game["id"] or f"{game['home']['name']}-{game['away']['name']}"
                    if key in seen:
                        continue
                    seen.add(key)
                    if offset == -1:
                        game["yesterday"] = True
                    elif offset == 1:
                        game["tomorrow"] = True
                    games.append(game)
            except Exception as exc:  # noqa: BLE001
                print(f"  {league['name']} {date_str} 실패: {exc}", flush=True)

    # 한국 리그 경기 (K리그·KBL·V리그) — ESPN에 없어서 한국 공식 사이트에서 직접 받는다
    for fn in (kleague_games, kbl_games, kovo_games):
        try:
            for game in fn(now):
                key = game["id"]
                if key in seen:
                    continue
                seen.add(key)
                games.append(game)
        except Exception as exc:  # noqa: BLE001
            print(f"  {fn.__name__} 실패: {exc}", flush=True)

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
        if league.get("intl"):
            # 국제 대회는 조별리그 표가 대회마다 달라서 순위는 넣지 않는다(경기·소식만)
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
                wins = stat_value(entry, "wins")
                draws = stat_value(entry, "ties")
                losses = stat_value(entry, "losses")
                played = stat_value(entry, "gamesPlayed")
                if played in (None, ""):
                    try:
                        played = str(int(float(str(wins or 0))) + int(float(str(draws or 0))) + int(float(str(losses or 0))))
                    except Exception:  # noqa: BLE001
                        played = None
                rows.append({
                    "rank": int(float(rank)) if str(rank).replace(".", "").isdigit() else len(rows) + 1,
                    "team": team_ko(team, league["espn"]),
                    "emblem": logo_of(team),
                    "games": played,
                    "w": wins,
                    "d": draws,
                    "l": losses,
                    "rate": win_pct,
                    "points": points if points not in ("", None) else None,
                    "last5": "",
                    "group": GROUP_KO.get(group_name, group_name) if len(groups) > 1 else "",
                })
        if not rows:
            print(f"  {league['name']} 순위 없음", flush=True)
            continue
        soccer = league["espn"].startswith("soccer")
        metric = "pts" if soccer and all(r["points"] not in (None, "") for r in rows[:3]) else "pct"
        for r in rows:
            if metric == "pct":
                r["points"] = None
        # ESPN이 주는 rank는 그룹·리그 기준이 뒤섞여 있어, 성적순으로 직접 매긴다
        def perf(r):
            if metric == "pts":
                return (-float(str(r["points"] or 0).replace("+", "")), -float(str(r["w"] or 0)))
            rate = r.get("rate")
            try:
                rate = float(str(rate))
            except Exception:  # noqa: BLE001
                w = float(str(r["w"] or 0)); l = float(str(r["l"] or 0))
                rate = w / (w + l) if (w + l) else 0.0
            return (-rate, -float(str(r["w"] or 0)))
        buckets: dict[str, list] = {}
        for r in rows:
            buckets.setdefault(r["group"] or "", []).append(r)
        ordered = []
        for key in buckets:
            group_rows = sorted(buckets[key], key=perf)
            for i, r in enumerate(group_rows, start=1):
                r["rank"] = i
            ordered.extend(group_rows)
        rows = ordered
        # 승률 문자열 정리
        for r in rows:
            if metric == "pct" and isinstance(r["rate"], str) and r["rate"].startswith("."):
                r["rate"] = "0" + r["rate"]
        leagues.append({
            "id": league["espn"].split("/")[-1], "sport": league["sport"], "name": league["name"],
            "season": str(league["season"]), "metric": metric, "teamCount": len(rows), "rows": rows,
        })
        print(f"  {league['name']}: {len(rows)}팀 ({metric})", flush=True)

    for fn in (kleague_standings, kbo_standings, kbl_standings, kovo_standings):
        try:
            for lg in fn():
                leagues.append(lg)
                print(f"  {lg['name']}: {lg['teamCount']}팀 ({lg['metric']})", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  {fn.__name__} 실패: {exc}", flush=True)

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


def _google_translate(text: str) -> str:
    """구글 번역(공개 엔드포인트). MyMemory보다 빠르고 하루 한도가 없다."""
    pieces, buf = [], ""
    for sentence in re.split(r"(?<=[.!?。])\s+", text):
        if len(buf) + len(sentence) > 900 and buf:
            pieces.append(buf)
            buf = sentence
        else:
            buf = (buf + " " + sentence).strip()
    if buf:
        pieces.append(buf)
    outs = []
    for piece in pieces:
        url = ("https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=ko&dt=t&q="
               + urllib.parse.quote(piece, safe=""))
        try:
            payload = get_json(url)
            joined = "".join((seg[0] or "") for seg in (payload[0] or []) if seg)
            if joined.strip():
                outs.append(joined.strip())
        except Exception as exc:  # noqa: BLE001
            print(f"    구글 번역 실패: {exc}", flush=True)
            return ""
    return " ".join(outs)


def translate(text: str) -> str:
    """영어 → 한국어. 구글 번역 우선, 실패하면 MyMemory, 둘 다 실패하면 원문."""
    if not text or not re.search(r"[A-Za-z]", text):
        return text
    if re.search(r"[가-힣]", text):
        return text
    out = _google_translate(text)
    if out and re.search(r"[가-힣]", out):
        return out
    if len(text) > 480:
        text = text[:480]
    url = ("https://api.mymemory.translated.net/get?q="
           + urllib.parse.quote(text, safe="") + "&langpair=en|ko")
    try:
        payload = get_json(url)
        out = ((payload.get("responseData") or {}).get("translatedText")) or ""
        if out and payload.get("responseStatus") == 200 and "MYMEMORY WARNING" not in out.upper():
            return html.unescape(out)
    except Exception as exc:  # noqa: BLE001
        print(f"    번역 실패: {exc}", flush=True)
    return text


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?。])\s+", text.strip())
    return [p.strip(" ,") for p in parts if len(p.strip()) > 8]


# ══ 기사 본문 청소 ══════════════════════════════════════
# 앱이 기사 원문 페이지로 나가지 않고 앱 안에서 읽는다(광고·댓글·추천기사 없이).
# 그래서 본문 문단을 여기서 깨끗하게 뽑아 두고, 제목·요약·본문만 저장한다.
NOISE = re.compile(
    r"무단 전재|재배포|저작권|ⓒ|©|구독|기사제보|카카오톡|텔레그램|▶|광고|사진=|자료사진|"
    r"오피니언|사설|All rights reserved|Sign up|Watch on ESPN|Advertisement|Read more|"
    r"관련기사|많이 본|추천기사|클릭|구독하기", re.I)


ESPN_STORY = "https://content.core.api.espn.com/v1/sports/news/"


def text_paragraphs(raw: str, limit: int = 8) -> list[str]:
    """HTML에서 읽을 만한 문단만 뽑는다(목록·내비·이메일·저작권 문구 제거)."""
    if not raw:
        return []
    body = re.sub(r"<(script|style|figure|picture|iframe|noscript|svg)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    paras: list[str] = []
    for p in re.findall(r"<p[^>]*>(.*?)</p>", body, re.S | re.I):
        clean = html.unescape(re.sub(r"<[^>]+>", " ", p))
        clean = re.sub(r"\s+", " ", clean).strip()
        if len(clean) < 40 or NOISE.search(clean) or not is_safe(clean):
            continue
        if "@" in clean and len(clean) < 90:  # 기자 이메일 줄
            continue
        if clean not in paras:
            paras.append(clean)
    return paras[:limit]


def espn_body(article_id: str, limit: int = 6) -> list[str]:
    try:
        payload = get_json(f"{ESPN_STORY}{article_id}")
    except Exception:  # noqa: BLE001
        return []
    head = (payload.get("headlines") or [{}])[0]
    return text_paragraphs(head.get("story") or "", limit)


# ══ 한국 스포츠 소식 (연합뉴스·동아일보·한겨레 RSS) ═════════════════
KR_FEEDS = [
    ("연합뉴스", "https://www.yna.co.kr/rss/sports.xml"),
    ("동아일보", "https://rss.donga.com/sports.xml"),
    ("한겨레", "https://www.hani.co.kr/rss/sports/"),
]
KR_LABELS = {"야구": "KBO", "축구": "K리그", "농구": "KBL", "배구": "V리그", "일반": "한국 스포츠"}
# 종목에서 빀 골프는 소식에도 올리지 않는다
KR_DROP = ["골프", "KPGA", "KLPGA", "PGA", "LPGA"]
SPORT_WORDS = {
    "야구": ["프로야구", "KBO", "한국시리즈", "야구", "타자", "타율", "홈런", "투수", "삼진", "세이브",
             "MLB", "메이저리그", "한화", "두산", "롯데", "KIA", "기아", "SSG", "키움", "LG 트윈스",
             "트윈스", "라이온즈", "타이거즈", "자이언츠", "히어로즈", "이글스", "다이노스", "위즈", "랜더스",
             "베어스", "한신", "오릭스", "소프트뱅크", "요미우리", "불펜", "타점"],
    "축구": ["축구", "K리그", "손흥민", "이강인", "김민재", "월드컵", "프리미어리그", "EPL",
             "라리가", "UEFA", "ACL", "코리아컵", "챔피언스리그", "FC", "골키퍼", "해트트릭", "북중미",
             "해외축구", "분데스리가", "세리에", "유로파", "공격수", "수비수", "미드필더", "오프사이드",
             "페널티킥", "프리킥", "코너킥", "슈팅", "득점왕", "리그1", "울산 HD", "전북 현대"],
    "농구": ["농구", "KBL", "WKBL", "NBA", "드래프트", "리바운드", "3점슛", "모비스", "LG", "SK",
             "정관장", "DB", "KT", "한국가스공사", "KCC", "소노", "삼성 썬더스", "퀵"] ,
    "배구": ["배구", "V리그", "대한항공", "현대캐피탈", "흥국생명", "GS칼텍스", "IBK", "도로공사",
             "우리카드", "KB손해보험", "OK저축은행", "세터", "리베로", "블로킹", "한국전력"],
    "일반": ["쇼트트랙", "스피드스케이팅", "피겨", "컬링", "아이스하키", "수영", "육상", "마라톤",
             "탁구", "배드민턴", "테니스", "양공", "펜싱", "유도", "태권도", "역도", "바둑",
             "당구", "핸드볼", "사이클", "e스포츠", "체조", "클라이밍", "서핑", "스쿼시", "보치아"],
}


def classify_sport(text: str) -> str:
    if "핸드볼" in text and not any(w in text for w in ("축구", "K리그", "손흥민", "월드컵")):
        return "일반"
    best, best_score = "일반", 0
    for sport, words in SPORT_WORDS.items():
        score = sum(1 for w in words if w in text)
        if score > best_score:
            best, best_score = sport, score
    return best


def cdata(s: str) -> str:
    return re.sub(r"^\s*<!\[CDATA\[|\]\]>\s*$", "", (s or "").strip())


def tidy_kr(text: str) -> str:
    """연합뉴스 머리글((서울=연합뉴스) 홍길동 기자 = ) 같은 것을 다듬는다."""
    text = re.sub(r"^\s*[\(（][^\)）]{1,20}[=＝][^\)）]{1,20}[\)）]\s*", "", text)
    text = re.sub(r"^\s*[가-힣]{2,4}\s*기자\s*=\s*", "", text)
    return text.strip()


def rss_items(url: str, limit: int = 6) -> list[dict]:
    try:
        raw = http_get(url).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        print(f"  RSS 실패 {url[:40]}: {exc}", flush=True)
        return []
    out = []
    for block in re.findall(r"<item>(.*?)</item>", raw, re.S):
        title = cdata((re.search(r"<title>(.*?)</title>", block, re.S) or [None, ""])[1])
        link = cdata((re.search(r"<link>(.*?)</link>", block, re.S) or [None, ""])[1])
        desc = cdata((re.search(r"<description>(.*?)</description>", block, re.S) or [None, ""])[1])
        desc = html.unescape(re.sub(r"<[^>]+>", " ", desc))
        desc = re.sub(r"\s+", " ", desc).strip()
        pub = ((re.search(r"<pubDate>(.*?)</pubDate>", block, re.S) or [None, ""])[1] or "").strip()
        if not title or not link.startswith("http"):
            continue
        if "중간순위" in title or not is_safe(title) or (desc and not is_safe(desc)):
            continue
        if any(w in title for w in KR_DROP):
            continue
        if re.search(r"^\[.*(전적|순위|일정|중계|결과)\]|\(전적\)|프로배구 전적", title):
            continue
        if re.match(r"^\[(표|그래픽|포토|영상|카드|일지|속보)\]", title):
            continue
        when = ""
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(pub)
            when = dt.astimezone(KST).isoformat(timespec="seconds")
        except Exception:  # noqa: BLE001
            when = ""
        out.append({"title": title, "link": link, "desc": desc, "time": when})
        if len(out) >= limit:
            break
    return out


def build_kr_news(now: datetime, cache: dict, new_cache: dict, max_body: int = 16) -> tuple[list[dict], int]:
    """한국 스포츠 기사 — 제목·요약·본문 문단을 모두 한국어로 그대로 담는다."""
    seen_titles: set[str] = set()
    buckets: dict[str, list[dict]] = {}
    used = 0
    for source, feed in KR_FEEDS:
        for it in rss_items(feed, limit=10):
            norm = re.sub(r"[^\w가-힣]", "", it["title"])[:24]
            if norm in seen_titles:
                continue
            seen_titles.add(norm)
            sport = classify_sport(it["title"] + " " + it["desc"])
            key = "kr:" + hashlib.md5(it["link"].encode()).hexdigest()[:10]
            if key in cache:
                item = cache[key]
                if any(w in (item.get("title") or "") for w in KR_DROP):
                    continue
                new_cache[key] = item
                buckets.setdefault(item.get("sport") or sport, []).append(item)
                continue
            body: list[str] = []
            if used < max_body:
                used += 1
                try:
                    page = http_get(it["link"], timeout=15).decode("utf-8", "replace")
                    body = text_paragraphs(page, limit=8)
                except Exception as exc:  # noqa: BLE001
                    print(f"    본문 실패 {source}: {exc}", flush=True)
            summary = [tidy_kr(s) for s in split_sentences(it["desc"])[:3]]
            summary = [s for s in summary if s]
            body = [tidy_kr(p) for p in body]
            body = [p for p in body if len(p) > 20]
            if not body:
                body = summary[:] or split_sentences(it["title"])[:1]
            if not summary:
                summary = split_sentences(body[0])[:2] if body else [it["title"]]
            item = {
                "id": key, "title": it["title"], "summary": summary, "body": body[:8],
                "source": source, "time": it["time"], "sport": sport,
                "label": KR_LABELS.get(sport, "한국 스포츠"), "lang": "ko",
            }
            if not is_safe(item["title"] + " " + " ".join(item["body"])):
                continue
            new_cache[key] = item
            buckets.setdefault(sport, []).append(item)
    sections = []
    order = ["야구", "축구", "농구", "배구", "일반"]
    for sport in order:
        items = (buckets.get(sport) or [])[:6]
        if not items:
            continue
        sections.append({"id": f"kr.{sport}", "label": KR_LABELS.get(sport, "한국 스포츠"),
                         "sport": sport, "lang": "ko", "items": items})
    return sections, sum(len(s["items"]) for s in sections)


def build_news(now: datetime) -> None:
    cache = read_json("news_cache.json", {}) or {}
    # 예전 형식(본문 없음) 캐시는 버린다
    cache = {k: v for k, v in cache.items() if isinstance(v, dict) and "body" in v}
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
        translated_body = False
        for art in (payload.get("articles") or []):
            headline = (art.get("headline") or "").strip()
            desc = re.sub(r"<[^>]+>", " ", art.get("description") or "").strip()
            aid = str(art.get("id") or headline[:20])
            if not headline or not is_safe(headline) or not is_safe(desc):
                continue
            if "fantasy" in headline.lower() or "betting" in headline.lower():
                continue  # 판타지·배팅 기사는 아이 화면에 올리지 않는다
            if aid in cache:
                items.append(cache[aid])
                new_cache[aid] = cache[aid]
            else:
                ko_title = translate(headline)
                if not is_safe(ko_title) or not re.search(r"[가-힣]", ko_title):
                    continue  # 번역이 안 되면 영어 제목을 내보내지 않는다(다음 실행에 다시 시도)
                ko_desc = translate(desc)
                if not re.search(r"[가-힣]", ko_desc):
                    continue
                body: list[str] = []
                if not translated_body and budget_ok(START, 380):
                    # 본문까지 한글로: 리그당 한 기사만
                    paras = espn_body(aid, limit=3)
                    body = [translate(p) for p in paras[:2]]
                    body = [b for b in body if b and len(b) > 20 and re.search(r"[가-힣]", b)]
                    translated_body = bool(body)
                lines = split_sentences(ko_desc)[:3]
                item = {
                    "id": aid, "title": ko_title, "summary": lines, "body": body or lines,
                    "source": "ESPN", "time": art.get("published") or "",
                    "sport": league["sport"], "label": league["name"], "lang": "en",
                }
                items.append(item)
                new_cache[aid] = item
            if len(items) >= 2:
                break
        if items:
            sections.append({"id": league["espn"], "label": league["name"],
                             "sport": league["sport"], "intl": bool(league.get("intl")),
                             "lang": "en", "items": items})
            total += len(items)
            print(f"  {league['name']}: {len(items)}건", flush=True)

    # 한국 소식은 앞에 둔다 (아이가 먼저 보는 뉴스)
    kr_sections, kr_total = ([], 0)
    try:
        kr_sections, kr_total = build_kr_news(now, cache, new_cache)
        for s in kr_sections:
            print(f"  {s['label']} (한국): {len(s['items'])}건", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  한국 소식 실패: {exc}", flush=True)

    total += kr_total
    sections = kr_sections + sections
    if not total:
        print("  소식 0건 — 그대로 둡니다", flush=True)
        return
    write_json("news_cache.json", dict(list(new_cache.items())[:400]))
    write_json("news.json", {
        "updated": now.isoformat(timespec="seconds"),
        "total": total,
        "korean": kr_total,
        "note": "제목·요약·본문 문단만 담습니다. 광고·댓글·추천기사는 저장하지 않고, 앱 안에서만 읽습니다.",
        "source": "ESPN · 연합뉴스 · 동아일보 · 한겨레 (한국어 번역)",
        "sections": sections,
    })


def write_ko_maps() -> None:
    """앱이 ESPN을 직접 부를 때 쓰는 리그별 한국어 이름표.

    약어가 리그끼리 겹쳐서(CHI=시카고/칠레, GEN=제노아/겐츨레르비를리이) 한 장으로는
    맞출 수 없다. 수집기가 원본을 쥐고 이 파일로 내려 준다.
    """
    write_json("ko-teams.json", {
        "updated": datetime.now(KST).isoformat(timespec="seconds"),
        "byLeague": {
            "football/nfl": NFL_TEAMS_KO,
            "soccer/tur.1": TUR_TEAMS_KO,
            "soccer/ita.1": KO_BY_LEAGUE["soccer/ita.1"],
            "soccer/uefa.champions": KO_BY_LEAGUE["soccer/uefa.champions"],
        },
        "intl": INT_TEAMS_KO,
        "intlLeagues": sorted(INTL_KEYS),
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
    write_ko_maps()
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
