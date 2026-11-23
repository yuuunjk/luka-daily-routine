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
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
SITE = "https://site.api.espn.com/apis/site/v2/sports"
CORE = "https://site.api.espn.com/apis/v2/sports"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"

SPORT_ORDER = ["야구", "해외야구", "축구", "해외축구", "미식축구", "농구", "해외농구", "배구", "국제대회", "기타"]
SPORT_EMOJI = {"야구": "⚾", "해외야구": "⚾", "축구": "⚽", "해외축구": "⚽", "국제대회": "🌍",
               "농구": "🏀", "해외농구": "🏀", "미식축구": "🏈", "배구": "🏐", "기타": "🏅"}

LEAGUES = [
    {"espn": "baseball/mlb", "sport": "해외야구", "name": "MLB", "season": "2026"},
    {"espn": "basketball/nba", "sport": "해외농구", "name": "NBA", "season": "2026"},
    {"espn": "football/nfl", "sport": "미식축구", "name": "NFL", "season": "2026"},
    {"espn": "soccer/eng.1", "sport": "해외축구", "name": "프리미어리그", "season": "2026"},
    {"espn": "soccer/esp.1", "sport": "해외축구", "name": "라리가", "season": "2026"},
    {"espn": "soccer/ger.1", "sport": "해외축구", "name": "분데스리가", "season": "2026"},
    {"espn": "soccer/ita.1", "sport": "해외축구", "name": "세리에A", "season": "2026"},
    {"espn": "soccer/tur.1", "sport": "해외축구", "name": "튀르키예 쉬페르리그", "season": "2026"},
    {"espn": "soccer/uefa.champions", "sport": "해외축구", "name": "챔피언스리그", "season": "2026"},
    # ── 국제 대회 (나라 대 나라) ──
    # ESPN에서 soccer/afc.cup 은 'AFC 챔피언스리그2'(클럽 대회)라서, 아시안컵은 afc.asian.cup 을 쓴다.
    {"espn": "soccer/fifa.world", "sport": "국제대회", "name": "월드컵", "season": "2026", "intl": True},
    {"espn": "soccer/fifa.worldq.afc", "sport": "국제대회", "name": "월드컵 아시아 예선", "season": "2026", "intl": True},
    {"espn": "soccer/afc.asian.cup", "sport": "국제대회", "name": "아시안컵", "season": "2026", "intl": True},
    {"espn": "soccer/uefa.nations", "sport": "국제대회", "name": "네이션스리그", "season": "2026", "intl": True},
    {"espn": "soccer/uefa.euro", "sport": "국제대회", "name": "유로", "season": "2026", "intl": True},
    {"espn": "soccer/caf.nations", "sport": "국제대회", "name": "아프리카 네이션스컵", "season": "2026", "intl": True},
    {"espn": "soccer/conmebol.america", "sport": "국제대회", "name": "코파 아메리카", "season": "2026", "intl": True},
    {"espn": "soccer/fifa.friendly", "sport": "국제대회", "name": "국가대표 평가전", "season": "2026", "intl": True},
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
    # 튀르키예 — 챔피언스리그 등 다른 대회에서도 같은 이름이 나오게 통일
    "FEN": "페네르바흐체",
    # 세리에A
    "JUV": "유벤투스", "INT": "인터밀란", "ACM": "AC밀란", "NAP": "나폴리",
    "ROM": "로마", "LAZ": "라치오", "ATA": "아탈란타", "FIO": "피오렌티나",
    "BOL": "볼로냐", "UDI": "우디네세", "GEN": "제노아",
    # 분데스리가
    "BAY": "바이에른", "BVB": "도르트문트", "LEV": "레버쿠젠", "RBL": "라이프치히",
    "SGE": "프랑크푸르트", "VFB": "슈투트가르트", "WOB": "볼프스부르크",
    "BMG": "묀헨글라트바흐", "SCF": "프라이부르크", "M05": "마인츠",
    "B04": "레버쿠젠", "FCA": "아우크스부르크", "HSV": "함부르크", "KOE": "쾰른",
    "FCU": "우니온 베를린", "S04": "샬케", "SVW": "브레멘", "TSG": "호펜하임",
    "ELV": "엘버스베르크",
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
    "GAL": "갈라타사라이", "FEN": "페네르바흐체", "BES": "베식타스", "TRAB": "트라브존스포르",
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
# ESPN은 분데스리가 바이에른을 MUN, 프리미어리그 맨유도 MUN으로 준다 → 리그별 사전이 없으면
# '바이에른'이 '맨유'로 나온다. 라리가 레반테(LEV)도 분데스리가 레버쿠젠과 겹친다.
GER_TEAMS_KO = {
    "B04": "레버쿠젠", "BMG": "묀헨글라트바흐", "DOR": "도르트문트", "ELV": "엘버스베르크",
    "FCA": "아우크스부르크", "FCU": "우니온 베를린", "HSV": "함부르크", "KOE": "쾰른",
    "M05": "마인츠", "MUN": "바이에른", "RBL": "라이프치히", "S04": "샬케",
    "SCF": "프라이부르크", "SCP": "파더보른", "SGE": "프랑크푸르트", "SVW": "브레멘",
    "TSG": "호펜하임", "VFB": "슈투트가르트",
}
ESP_TEAMS_KO = {
    "ALA": "알라베스", "ATH": "아틀레틱", "ATM": "아틀레티코", "BAR": "바르셀로나",
    "BET": "베티스", "CEL": "셀타", "DEP": "데포르티보", "ELC": "엘체", "ESP": "에스파뇰",
    "GET": "헤타페", "LEV": "레반테", "MCF": "말라가", "OSA": "오사수나", "RAC": "라싱",
    "RAY": "라요", "RMA": "레알마드리드", "RSO": "레알소시에다드", "SEV": "세비야",
    "VAL": "발렌시아", "VIL": "비야레알",
}
ENG_TEAMS_KO = {
    "ARS": "아스널", "AVL": "애스턴빌라", "BHA": "브라이턴", "BOU": "본머스",
    "BRE": "브렌트퍼드", "CHE": "첼시", "COV": "코번트리", "CRY": "크리스털팰리스",
    "EVE": "에버턴", "FUL": "풀럼", "HUL": "헐", "IPS": "입스위치", "LEE": "리즈",
    "LIV": "리버풀", "MAN": "맨체스터 유나이티드", "MNC": "맨체스터 시티", "NEW": "뉴캐슬",
    "NFO": "노팅엄", "SUN": "선덜랜드", "TOT": "토트넘",
}
FRA_TEAMS_KO = {   # 리그1 (프랑스)
    "PSG": "파리 생제르맹", "LILL": "릴", "LYON": "리옹", "REN": "렌", "TRY": "트루아",
    "AUX": "오세르", "MON": "모나코", "RCL": "랑스", "OLM": "마르세유", "TOU": "툴루즈",
    "STR": "스트라스부르", "LOR": "로리앙", "NICE": "니스", "MNS": "르망", "HAC": "르아브르",
    "PAR": "파리 FC", "BRE": "브레스트", "ANG": "앙제",
}
UCL_TEAMS_KO = {
    "MIL": "AC밀란", "TOR": "토리노", "ROM": "로마", "ROMA": "로마", "INT": "인터밀란", "JUV": "유벤투스",
    "NAP": "나폴리", "LAZ": "라치오", "ATA": "아탈란타", "MUN": "바이에른", "SCP": "스포르팅",
    "PSG": "파리 생제르맹", "PSV": "PSV 에인트호번", "DOR": "도르트문트", "VFB": "슈투트가르트",
    "RBL": "라이프치히", "FEN": "페네르바흐체",
}
KO_BY_LEAGUE = {
    "soccer/ger.1": GER_TEAMS_KO,
    "soccer/esp.1": ESP_TEAMS_KO,
    "soccer/eng.1": ENG_TEAMS_KO,
    "soccer/ita.1": {"MIL": "AC밀란", "TOR": "토리노", "ROM": "로마", "ROMA": "로마", "INT": "인터밀란",
                     "JUV": "유벤투스", "NAP": "나폴리", "LAZ": "라치오", "ATA": "아탈란타",
                     "FIO": "피오렌티나", "BOL": "볼로냐", "UDI": "우디네세", "GEN": "제노아",
                     "SAS": "사수올로", "LEC": "레체", "PAR": "파르마", "COMO": "코모",
                     "CAG": "칼리아리", "MON": "몬차", "FRO": "프로시논", "VEN": "베네치아"},
    "soccer/fra.1": FRA_TEAMS_KO,
    "soccer/uefa.champions": UCL_TEAMS_KO,
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
    elif sport in ("해외축구", "축구", "국제대회"):
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

# ══ 국내 팀 로고 — 앱과 같은 곳(sports/assets/logos/)에 내려둔 파일 ══════════
# 외부 CDN 핫링크가 느리거나 막혀도 깨지지 않게 로컬 SVG·PNG 를 상대경로로 연결한다.
LOGO_BASE = "./assets/logos"
KBO_LOGO = {   # KBO 구단 약칭 → 엠블럼 (koreabaseball.com 공식)
    "KIA": "kbo-HT.png", "KT": "kbo-KT.png", "LG": "kbo-LG.png", "롯데": "kbo-LT.png",
    "NC": "kbo-NC.png", "삼성": "kbo-SS.png", "키움": "kbo-WO.png", "SSG": "kbo-SK.png",
    "한화": "kbo-HH.png", "두산": "kbo-OB.png",
}


def kbo_logo(name: str) -> str:
    f = KBO_LOGO.get((name or "").strip())
    return f"{LOGO_BASE}/{f}" if f else ""


def kbl_logo(logo_class: str) -> str:
    """KBL 경기·순위의 teamLogoClass(kgc, kcc …) → 로컬 엠블럼"""
    c = (logo_class or "").strip()
    return f"{LOGO_BASE}/kbl-{c}.svg" if c and c != "kbl" else ""


def kleague_logo(team_id: str) -> str:
    """kleague.com 구단 코드(K01 …) → 로컬 엠블럼"""
    t = (team_id or "").strip().upper()
    return f"{LOGO_BASE}/kleague-{t}.png" if t.startswith("K") else ""


def kovo_logo(url: str) -> str:
    """cdn.kovo.co.kr/emblems/xxx.svg → 로컬 ./assets/logos/kovo-xxx.svg"""
    base = os.path.basename(str(url or "").split("?")[0])
    return f"{LOGO_BASE}/kovo-{base}" if base else ""


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
                    kleague_logo(m.get("homeTeam")), kleague_logo(m.get("awayTeam")),
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
                "emblem": kleague_logo(r.get("teamId")),
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
            "rank": 0, "team": cells[1], "emblem": kbo_logo(cells[1]), "games": games, "w": w, "d": d, "l": l,
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
            kbl_logo(m.get("logoH")), kbl_logo(m.get("logoA")),
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
            "emblem": kbl_logo(r.get("teamLogoClass")), "games": win + loss, "w": win, "d": int(r.get("draw") or 0), "l": loss,
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
        # 배구는 3세트를 먼저 따낸 3:0·3:1·3:2 로만 끝난다. KOVO API는 경기 중에도
        # result 를 채우고 중간 세트 스코어(0 : 1)를 주기 때문에, 그 값으로 '종료' 처리하면
        # 앱의 승부예측이 진 팀을 이긴 팀으로 채점해 버린다(11-21 삼성화재 0:1 → 3:1 사고).
        # 최종 세트 스코어 모양일 때만 종료로 본다.
        finished = len(nums) >= 2 and max(hscore, ascore) == 3 and min(hscore, ascore) <= 2
        started = bool(time_text) and f"{date} {time_text}" <= now.strftime("%Y-%m-%d %H:%M")
        if finished:
            state, status = "done", "종료"
        elif started:
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
            (m.get("place") or "").strip(), kovo_logo(m.get("homeEmblemUrl")), kovo_logo(m.get("awayEmblemUrl")),
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
            "emblem": kovo_logo(r.get("emblemUrl")), "games": win + loss, "w": win, "d": 0, "l": loss,
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



# ══ 선수 개인 순위 (leaders) ═════════════════════════════════
# 리그별 개인 기록 순위를 모아 data/leaders.json 으로 저장한다.
#  · ESPN (MLB·NBA·NFL)       — statistics/byathlete
#  · ESPN (해외축구 7개 리그) — core /leaders + 선수·팀 참조
#  · K리그1·2                 — kleague.com /record/rankSort.do
#  · KBL                      — api.kbl.or.kr /league/season-leader
# 자료가 없는 리그(KBO·V리그 등)는 아무 것도 만들지 않는다. 앱이 안내 문구를 보여 준다.
# ══ 선수 개인 순위 (leaders) ═════════════════════════════════
# 리그별 개인 기록 순위를 모아 data/leaders.json 으로 저장한다.
#  · ESPN (MLB·NBA·NFL)       — statistics/byathlete
#  · ESPN (해외축구 7개 리그) — core /leaders + 선수·팀 참조
#  · K리그1·2                 — kleague.com /record/rankSort.do
#  · KBL                      — api.kbl.or.kr /league/season-leader
# 자료가 없는 리그(KBO·V리그 등)는 아무 것도 만들지 않는다. 앱이 안내 문구를 보여 준다.
#
# 2026-11-21 — 선수 카드 팝업(1안 트레이딩 카드)용 값 추가.
#   행 하나에 rank/player/team/emblem/value 에 더해
#   id·photo·jersey·position·posShort·height·weight·age·citizenship·flag·color·stats4 를 넣는다.
#   카드에 쓰는 신상 정보는 leaders-cache.json(profiles / teams / sstats / photos / kleague)에
#   남겨 두어 5분마다 도는 수집기가 매번 다시 받지 않게 한다.
LEADER_CACHE = "leaders-cache.json"

CORE_SEASON = "https://sports.core.api.espn.com/v2/sports"
CARD_TTL = 14 * 86400          # 등번호·키·몸무게·나이·국적
STATS_TTL = 24 * 3600          # 시즌 기록(해외축구 선수 상세)
PHOTO_TTL = 30 * 86400         # 유럽축구 위키백과 사진 매칭 결과 (실패도 30일 기억)
TEAM_TTL = 30 * 86400          # 팀 이름·엠블럼·팀 색
KLEAGUE_CDN = "https://d2tfp74nsbbrkr.cloudfront.net"
KLEAGUE_IMG_DIR = os.path.join(DATA_DIR, "img", "kleague")

WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
WIKI_UA = ("LUKA-sports-dashboard/1.0 (personal kids sports page; "
           "https://github.com/yuuunjk/clean-sports) python-urllib")
WIKI_GAP = 0.7                 # 위키미디어 API 요청 간 최소 간격(초) — 429 예방
WIKI_MAX_SEARCH = 30           # 한 실행에서 이름 검색으로 넘길 최대 인원(예산 보호)
FOOTBALLER_QID = "Q937857"     # 위키데이터 P106 = association football player
HUMAN_QID = "Q5"

_wiki_lock = threading.Lock()
_wiki_last = [0.0]

# 이름 비교용 — NFKD 로 안 풀리는 글자(ø·æ·그리스 동형 문자)를 라틴 문자로
_TRANSLIT = str.maketrans({
    "ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe",
    "å": "a", "Å": "a", "đ": "d", "Đ": "d", "ð": "d", "Ð": "d",
    "ł": "l", "Ł": "l", "ß": "ss", "þ": "th", "ı": "i", "İ": "i",
    "α": "a", "Α": "a", "β": "b", "Β": "b", "ε": "e", "Ε": "e",
    "ζ": "z", "Ζ": "z", "η": "i", "Η": "i", "ι": "i", "Ι": "i",
    "κ": "k", "Κ": "k", "μ": "m", "Μ": "m", "ν": "n", "Ν": "n",
    "ο": "o", "Ο": "o", "ρ": "p", "Ρ": "p", "τ": "t", "Τ": "t",
    "υ": "u", "Υ": "u", "χ": "x", "Χ": "x",
})

BYATHLETE_CFG = [
    {"id": "mlb", "espn": "baseball/mlb", "sport": "해외야구", "name": "MLB",
     "cats": [("batting.avg", "타율", "avg3"), ("batting.homeRuns", "홈런", "int"),
              ("batting.RBIs", "타점", "int"), ("pitching.ERA", "평균자책", "avg2"),
              ("pitching.wins", "다승", "int"), ("pitching.strikeouts", "탈삼진", "int")]},
    {"id": "nba", "espn": "basketball/nba", "sport": "해외농구", "name": "NBA",
     "cats": [("offensive.avgPoints", "득점", "avg1"), ("general.avgRebounds", "리바운드", "avg1"),
              ("offensive.avgAssists", "어시스트", "avg1"), ("defensive.avgSteals", "스틸", "avg1")]},
    {"id": "nfl", "espn": "football/nfl", "sport": "미식축구", "name": "NFL",
     "cats": [("passing.passingYards", "패싱야드", "int"), ("rushing.rushingYards", "러싱야드", "int"),
              ("receiving.receivingYards", "리시빙야드", "int")]},
]

SOCCER_CFG = [
    ("eng.1", "프리미어리그"), ("esp.1", "라리가"), ("ger.1", "분데스리가"),
    ("ita.1", "세리에A"), ("fra.1", "리그1"), ("tur.1", "튀르키예 쉬페르리그"),
    ("uefa.champions", "챔피언스리그"),
]
SOCCER_CATS = [("goals", "득점"), ("assists", "도움")]

# 카드 하단 스탯 3칸에 쓸 기록 — 리그(그리고 MLB·NFL은 부문)별로 고른다.
STAT4_CFG = {
    "mlb": {
        "batting": [("avg", "타율"), ("homeRuns", "홈런"), ("RBIs", "타점"), ("OPS", "OPS")],
        "pitching": [("ERA", "평균자책"), ("strikeouts", "탈삼진"), ("wins", "다승"), ("WHIP", "WHIP")],
    },
    "nba": {"*": [("avgPoints", "득점"), ("avgRebounds", "리바운드"),
                  ("avgAssists", "어시스트"), ("avgSteals", "스틸")]},
    "nfl": {
        "passing": [("passingYards", "패싱야드"), ("passingTouchdowns", "터치다운"),
                    ("QBRating", "패서평점"), ("passing.interceptions", "인터셉트")],
        "rushing": [("rushingYards", "러싱야드"), ("rushingTouchdowns", "터치다운"),
                    ("rushingAttempts", "시도"), ("yardsPerRushAttempt", "평균야드")],
        "receiving": [("receivingYards", "리시빙야드"), ("receptions", "리셉션"),
                      ("receivingTouchdowns", "터치다운"), ("yardsPerReception", "평균야드")],
    },
}

# ESPN 포지션 약어 → 한국어 (종목마다 같은 약어가 다른 뜻이라 종목별로 둔다)
POS_KO = {
    "baseball": {"SP": "선발투수", "RP": "구원투수", "P": "투수", "CP": "마무리", "C": "포수",
                 "1B": "1루수", "2B": "2루수", "3B": "3루수", "SS": "유격수", "LF": "좌익수",
                 "CF": "중견수", "RF": "우익수", "OF": "외야수", "IF": "내야수", "DH": "지명타자",
                 "EH": "지명타자", "TWP": "투타겸업", "UT": "유틸리티"},
    "basketball": {"PG": "포인트가드", "SG": "슈팅가드", "SF": "스몰포워드", "PF": "파워포워드",
                   "C": "센터", "G": "가드", "F": "포워드", "GF": "가드·포워드", "FC": "포워드·센터",
                   "FD": "포워드", "SG-PG": "가드", "GF-SF": "포워드"},
    "football": {"QB": "쿼터백", "RB": "러닝백", "FB": "풀백", "WR": "와이드리시버", "TE": "타이트엔드",
                 "OT": "오펜시브태클", "OG": "오펜시브가드", "C": "센터", "DE": "디펜시브엔드",
                 "DT": "디펜시브태클", "NT": "노즈태클", "LB": "라인배커", "ILB": "인사이드라인배커",
                 "OLB": "아웃사이드라인배커", "CB": "코너백", "S": "세이프티", "FS": "프리세이프티",
                 "SS": "스트롱세이프티", "K": "킥커", "P": "펀터", "LS": "롱스내퍼",
                 "KR": "킥리터너", "PR": "펀트리터너", "DB": "디펜시브백", "OL": "오펜시브라인",
                 "DL": "디펜시브라인"},
    "soccer": {"F": "공격수", "FW": "공격수", "ST": "스트라이커", "CF": "센터포워드",
               "W": "윙어", "LW": "레프트윙", "RW": "라이트윙", "M": "미드필더", "MF": "미드필더",
               "CM": "중앙 미드필더", "DM": "수비형 미드필더", "AM": "공격형 미드필더",
               "LM": "레프트 미드필더", "RM": "라이트 미드필더", "D": "수비수", "DF": "수비수",
               "CB": "센터백", "LB": "레프트백", "RB": "라이트백", "WB": "윙백",
               "GK": "골키퍼", "G": "골키퍼"},
}

# 국가 이름 → ESPN 국기 코드 (a.espncdn.com/i/teamlogos/countries/500/{코드}.png)
COUNTRY_CODE = {
    "대한민국": "kor", "한국": "kor", "South Korea": "kor", "Korea Republic": "kor", "Korea": "kor",
    "일본": "jpn", "Japan": "jpn", "중국": "chn", "China": "chn", "대만": "tpe", "Taiwan": "tpe",
    "미국": "usa", "USA": "usa", "United States": "usa", "캐나다": "can", "Canada": "can",
    "멕시코": "mex", "Mexico": "mex", "쿠바": "cub", "Cuba": "cub",
    "도미니카공화국": "dom", "Dominican Republic": "dom", "푸에르토리코": "pur", "Puerto Rico": "pur",
    "베네수엘라": "ven", "Venezuela": "ven", "콜롬비아": "col", "Colombia": "col",
    "파나마": "pan", "Panama": "pan", "니카라과": "nic", "Nicaragua": "nic",
    "브라질": "bra", "Brazil": "bra", "아르헨티나": "arg", "Argentina": "arg",
    "우루과이": "uru", "Uruguay": "uru", "페루": "per", "Peru": "per",
    "에콰도르": "ecu", "Ecuador": "ecu", "파라과이": "par", "Paraguay": "par",
    "볼리비아": "bol", "Bolivia": "bol", "칠레": "chi", "Chile": "chi",
    "호주": "aus", "Australia": "aus", "뉴질랜드": "nzl", "New Zealand": "nzl",
    "독일": "ger", "Germany": "ger", "프랑스": "fra", "France": "fra",
    "스페인": "esp", "Spain": "esp", "이탈리아": "ita", "Italy": "ita",
    "잉글랜드": "eng", "England": "eng", "스코틀랜드": "sco", "Scotland": "sco",
    "웨일스": "wal", "Wales": "wal", "아일랜드": "irl", "Ireland": "irl",
    "포르투갈": "por", "Portugal": "por", "네덜란드": "ned", "Netherlands": "ned",
    "벨기에": "bel", "Belgium": "bel", "크로아티아": "cro", "Croatia": "cro",
    "스위스": "sui", "Switzerland": "sui", "덴마크": "den", "Denmark": "den",
    "스웨덴": "swe", "Sweden": "swe", "노르웨이": "nor", "Norway": "nor",
    "핀란드": "fin", "Finland": "fin", "아이슬란드": "isl", "Iceland": "isl",
    "폴란드": "pol", "Poland": "pol", "체코": "cze", "Czechia": "cze", "Czech Republic": "cze",
    "슬로바키아": "svk", "Slovakia": "svk", "슬로베니아": "svn", "Slovenia": "svn",
    "세르비아": "srb", "Serbia": "srb", "그리스": "gre", "Greece": "gre",
    "튀르키예": "tur", "Turkey": "tur", "Türkiye": "tur", "우크라이나": "ukr", "Ukraine": "ukr",
    "러시아": "rus", "Russia": "rus", "오스트리아": "aut", "Austria": "aut",
    "헝가리": "hun", "Hungary": "hun", "루마니아": "rou", "Romania": "rou",
    "불가리아": "bul", "Bulgaria": "bul", "이스라엘": "isr", "Israel": "isr",
    "모로코": "mar", "Morocco": "mar", "세네갈": "sen", "Senegal": "sen",
    "나이지리아": "nga", "Nigeria": "nga", "가나": "gha", "Ghana": "gha",
    "카메룬": "cmr", "Cameroon": "cmr", "코트디부아르": "civ", "Ivory Coast": "civ",
    "알제리": "alg", "Algeria": "alg", "이집트": "egy", "Egypt": "egy",
    "튀니지": "tun", "Tunisia": "tun", "남아프리카공화국": "rsa", "South Africa": "rsa",
    "콩고민주공화국": "cod", "DR Congo": "cod", "앙골라": "ang", "Angola": "ang",
    "말리": "mli", "Mali": "mli", "부르키나파소": "bfa", "Burkina Faso": "bfa",
    "가봉": "gab", "Gabon": "gab", "기니": "gui", "Guinea": "gui",
    "감비아": "gam", "Gambia": "gam", "베냉": "ben", "Benin": "ben",
    "토고": "tog", "Togo": "tog", "니제르": "nig", "Niger": "nig",
    "카보베르데": "cpv", "Cape Verde": "cpv", "적도기니": "eqg", "Equatorial Guinea": "eqg",
    "수단": "sud", "Sudan": "sud", "리비아": "lby", "Libya": "lby",
    "에티오피아": "eth", "Ethiopia": "eth", "케냐": "ken", "Kenya": "ken",
    "우간다": "uga", "Uganda": "uga", "탄자니아": "tan", "Tanzania": "tan",
    "모잠비크": "moz", "Mozambique": "moz", "잠비아": "zmb", "Zambia": "zmb",
    "이란": "irn", "Iran": "irn", "이라크": "irq", "Iraq": "irq",
    "사우디아라비아": "ksa", "Saudi Arabia": "ksa", "카타르": "qat", "Qatar": "qat",
    "아랍에미리트": "uae", "United Arab Emirates": "uae", "우즈베키스탄": "uzb", "Uzbekistan": "uzb",
    "요르단": "jor", "Jordan": "jor", "시리아": "syr", "Syria": "syr",
    "레바논": "lib", "Lebanon": "lib", "쿠웨이트": "kuw", "Kuwait": "kuw",
    "바레인": "bhr", "Bahrain": "bhr", "팔레스타인": "ple", "Palestine": "ple",
    "예멘": "yem", "Yemen": "yem", "오만": "oma", "Oman": "oma",
    "태국": "tha", "Thailand": "tha", "베트남": "vie", "Vietnam": "vie",
    "인도네시아": "idn", "Indonesia": "idn", "말레이시아": "mas", "Malaysia": "mas",
    "싱가포르": "sin", "Singapore": "sin", "인도": "ind", "India": "ind",
    "키르기스스탄": "kgz", "Kyrgyzstan": "kgz", "카자흐스탄": "kaz", "Kazakhstan": "kaz",
    "타지키스탄": "taj", "Tajikistan": "taj", "투르크메니스탄": "tkm", "Turkmenistan": "tkm",
    "홍콩": "hkg", "Hong Kong": "hkg", "북한": "prk", "North Korea": "prk",
    "북마케도니아": "mkd", "North Macedonia": "mkd", "보스니아": "bih", "Bosnia": "bih",
    "알바니아": "alb", "Albania": "alb", "몬테네그로": "mne", "Montenegro": "mne",
    "조지아": "geo", "Georgia": "geo", "아제르바이잔": "aze", "Azerbaijan": "aze",
    "아르메니아": "arm", "Armenia": "arm", "에스토니아": "est", "Estonia": "est",
    "라트비아": "lva", "Latvia": "lva", "리투아니아": "ltu", "Lithuania": "ltu",
    "벨라루스": "blr", "Belarus": "blr", "몰도바": "mda", "Moldova": "mda",
    "룩셈부르크": "lux", "Luxembourg": "lux", "안도라": "and", "Andorra": "and",
    "지브롤터": "gib", "Gibraltar": "gib", "리히텐슈타인": "lie", "Liechtenstein": "lie",
    "몰타": "mlt", "Malta": "mlt", "페로제도": "fro", "Faroe Islands": "fro",
    "코소보": "kos", "Kosovo": "kos", "키프로스": "cyp", "Cyprus": "cyp",
    "피지": "fij", "Fiji": "fij", "솔로몬제도": "sol", "Solomon Islands": "sol",
    "바누아투": "van", "Vanuatu": "van", "타히티": "tah", "Tahiti": "tah",
}

# 국내 리그 팀 색 — 카드 배경 그라데이션에 쓴다 (ESPN 이 팀 색을 주지 않는 리그)
KLEAGUE_COLOR = {
    "K01": "0B4EA2", "K02": "1E3A8A", "K03": "C8102E", "K04": "F26522", "K05": "0B6E4F",
    "K06": "C8102E", "K07": "F2A900", "K08": "0E4C92", "K09": "C8102E", "K10": "0F7B3C",
    "K17": "0B4EA2", "K18": "0B4EA2", "K20": "1D4E89", "K21": "E03A2F", "K22": "F5C518",
    "K26": "1D4E89", "K27": "B31B34", "K29": "0B4EA2", "K31": "C8102E", "K32": "1B5E20",
    "K35": "C8102E",
}
KBL_COLOR = {
    "서울 SK": "E1002A", "수원 KT": "1A1A1A", "울산 현대모비스": "E4002B",
    "안양 정관장": "C8102E", "창원 LG": "A6192E", "원주 DB": "00A651",
    "대구 한국가스공사": "F5A623", "고양 소노": "E8B00F", "서울 삼성": "00A0E9",
    "부산 KCC": "0B3C8C",
}


def _ts() -> int:
    return int(time.time())


def _fresh(entry, ttl: int) -> bool:
    return isinstance(entry, dict) and (_ts() - int(entry.get("t") or 0)) < ttl


def _inch_cm(value):
    try:
        return int(round(float(value) * 2.54))
    except Exception:  # noqa: BLE001
        return None


def _lb_kg(value):
    try:
        return int(round(float(value) * 0.4536))
    except Exception:  # noqa: BLE001
        return None


def pos_ko(sport_type: str, abbr: str, name: str = "") -> str:
    table = POS_KO.get(sport_type) or {}
    key = (abbr or "").upper()
    if key in table:
        return table[key]
    return (name or abbr or "").strip()


def flag_url(country: str) -> str:
    code = COUNTRY_CODE.get((country or "").strip())
    return f"https://a.espncdn.com/i/teamlogos/countries/500/{code}.png" if code else ""


def country_ko(name: str) -> str:
    """국가 이름을 한국어로 (COUNTRY_CODE 안의 한국어 표기를 재사용)."""
    raw = (name or "").strip()
    code = COUNTRY_CODE.get(raw)
    if code:
        for key, val in COUNTRY_CODE.items():
            if val == code and not key.isascii():
                return key
    return raw


def team_ko_cached(team: dict, league_key: str = "") -> str:
    """팀 이름을 사전에서만 찾는다 (번역 호출 없음 — leaders 수집용)."""
    abbr = (team.get("abbreviation") or "").upper()
    override = (KO_BY_LEAGUE.get(league_key) or {}).get(abbr)
    if override:
        return override
    if abbr and abbr in TEAMS_KO:
        return TEAMS_KO[abbr]
    return (team.get("shortDisplayName") or team.get("displayName") or team.get("name") or "").strip()


def stat_text(value, kind="int"):
    if value in (None, "", "-"):
        return "-"
    try:
        num = float(str(value).replace(",", ""))
    except Exception:  # noqa: BLE001
        return str(value)
    if kind == "avg3":
        return f"{num:.3f}"
    if kind == "avg2":
        return f"{num:.2f}"
    if kind == "avg1":
        return f"{num:.1f}"
    return str(int(round(num)))


def espn_byathlete(sport_path: str, sort: str, season: int, limit: int = 10):
    """ESPN byathlete — 한 번의 호출로 선수·팀·기록을 모두 준다.

    카드용으로 id·등번호·포지션·팀 id·머리 사진과, 그 선수의 모든 부문 기록(totals)을 함께 담는다.
    totals 는 "2,838" 같은 표시용 문자열이라 그대로 카드에 쓴다.
    """
    url = (f"https://site.web.api.espn.com/apis/common/v3/sports/{sport_path}/statistics/byathlete"
           f"?region=us&lang=en&contentorigin=espn&isqualified=true&page=1&limit={limit}"
           f"&sort={urllib.parse.quote(sort + ':desc')}&season={season}&seasontype=2")
    payload = get_json(url)
    names = {(c.get("name") or ""): (c.get("names") or []) for c in payload.get("categories") or []}
    group, _, stat = sort.partition(".")
    keys = names.get(group) or []
    idx = keys.index(stat) if stat in keys else None
    out = []
    for pos, entry in enumerate(payload.get("athletes") or [], start=1):
        athlete = entry.get("athlete") or {}
        value = None
        if idx is not None:
            for cat in entry.get("categories") or []:
                if cat.get("name") == group:
                    totals = cat.get("totals") or []
                    if idx < len(totals):
                        value = totals[idx]
        stats = {}
        for cat in entry.get("categories") or []:
            cname = cat.get("name") or ""
            cnames = names.get(cname) or []
            for i, total in enumerate(cat.get("totals") or []):
                if i >= len(cnames):
                    break
                stats[f"{cname}.{cnames[i]}"] = total
                stats.setdefault(cnames[i], total)
        team = {"abbreviation": athlete.get("teamShortName") or "",
                "displayName": athlete.get("teamName") or "",
                "name": athlete.get("teamName") or ""}
        logos = athlete.get("teamLogos") or []
        position = athlete.get("position") or {}
        out.append({
            "rank": pos,
            "player": athlete.get("displayName") or "",
            "team": team_ko_cached(team, sport_path) if athlete.get("teamName") else "",
            "emblem": (logos[0].get("href") if logos else ""),
            "value": value,
            "id": str(athlete.get("id") or ""),
            "teamId": str(athlete.get("teamId") or ""),
            "jersey": str(athlete.get("jersey") or ""),
            "posShort": position.get("abbreviation") or "",
            "position": position.get("displayName") or "",
            "photo": ((athlete.get("headshot") or {}).get("href") or ""),
            "stats": stats,
        })
    return out


def _core_ref(url: str):
    try:
        return get_json(url)
    except Exception:  # noqa: BLE001
        return {}


def espn_core_leaders(sport: str, league: str, season: int, stype: int, limit: int = 12):
    """ESPN core /leaders — 해외축구는 byathlete 가 비어 있어 이 경로를 쓴다."""
    url = (f"{CORE_SEASON}/{sport}/leagues/{league}"
           f"/seasons/{season}/types/{stype}/leaders?limit={limit}")
    payload = get_json(url)
    return {c.get("name"): c.get("leaders") or [] for c in payload.get("categories") or []}


def _ref_id(ref: str):
    match = re.search(r"/(\d+)(?:\?|$)", ref or "")
    return match.group(1) if match else ""


# ── 선수 신상 (ESPN core athlete) ─────────────────────────────
def _athlete_profile(sport: str, league: str, season: int, aid: str, profiles: dict) -> dict:
    key = f"{sport}:{league}:{aid}"
    hit = profiles.get(key)
    if _fresh(hit, CARD_TTL):
        return hit
    data = _core_ref(f"{CORE_SEASON}/{sport}/leagues/{league}/seasons/{season}/athletes/{aid}")
    prof = hit if isinstance(hit, dict) else {}
    if data.get("displayName"):
        position = data.get("position") or {}
        birth = data.get("birthPlace") or {}
        country = (data.get("citizenship") or birth.get("country") or "").strip()
        prof = {
            "name": data.get("displayName") or "",
            "jersey": str(data.get("jersey") or ""),
            "posShort": position.get("abbreviation") or "",
            "pos": pos_ko(sport, position.get("abbreviation"), position.get("displayName")),
            "height": _inch_cm(data.get("height")),
            "weight": _lb_kg(data.get("weight")),
            "age": data.get("age"),
            "citizenship": country_ko(country),
            "flag": ((data.get("flag") or {}).get("href") or flag_url(country)),
            "photo": ((data.get("headshot") or {}).get("href") or ""),
            "t": _ts(),
        }
    else:
        prof = dict(prof, t=_ts(), miss=True)   # 없는 선수 — 14일 동안 다시 부르지 않는다
    profiles[key] = prof
    return prof


def _fetch_profiles(sport: str, league: str, season: int, aids: list[str], profiles: dict, workers: int = 8):
    need = [a for a in aids if a and not _fresh(profiles.get(f"{sport}:{league}:{a}"), CARD_TTL)]
    if not need or not budget_ok(START, 250):
        return
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda a: _athlete_profile(sport, league, season, a, profiles), need))


# ── 팀 정보 (ESPN core team — 이름·엠블럼·팀 색) ──────────────
def _team_card(sport: str, league: str, season: int, tid: str, teams: dict) -> dict:
    key = f"{sport}:{league}:{tid}"
    hit = teams.get(key)
    if _fresh(hit, TEAM_TTL) and hit.get("color"):
        return hit
    data = _core_ref(f"{CORE_SEASON}/{sport}/leagues/{league}/seasons/{season}/teams/{tid}")
    logos = data.get("logos") or []
    info = {
        "name": data.get("displayName") or "",
        "abbr": data.get("abbreviation") or "",
        "logo": (logos[0].get("href") if logos else ""),
        "color": (data.get("color") or "").lstrip("#"),
        "alt": (data.get("alternateColor") or "").lstrip("#"),
        "t": _ts(),
    }
    if not info["name"]:
        info = dict(hit or {}, t=_ts(), miss=True)
    teams[key] = info
    return info


def _enrich_byathlete(sport: str, league: str, season: int, rows: list[dict],
                      profiles: dict, teams: dict) -> None:
    _fetch_profiles(sport, league, season, [r.get("id") for r in rows], profiles)
    tids = [t for t in {r.get("teamId") for r in rows} if t and not (
        _fresh(teams.get(f"{sport}:{league}:{t}"), TEAM_TTL) and (teams.get(f"{sport}:{league}:{t}") or {}).get("color"))]
    if tids and budget_ok(START, 250):
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda t: _team_card(sport, league, season, t, teams), tids))


def _card_row(row: dict, profile: dict, team: dict, stats4: list) -> dict:
    """지금까지 모은 값으로 카드용 필드를 채운 행을 만든다."""
    out = dict(row)
    out["id"] = str(row.get("id") or "")
    out["photo"] = profile.get("photo") or row.get("photo") or ""
    out["jersey"] = str(profile.get("jersey") or row.get("jersey") or "")
    out["posShort"] = profile.get("posShort") or row.get("posShort") or ""
    out["position"] = profile.get("pos") or pos_ko("soccer" if row.get("_soccer") else "", row.get("posShort"), row.get("position") or "") or ""
    out["height"] = profile.get("height")
    out["weight"] = profile.get("weight")
    out["age"] = profile.get("age")
    out["citizenship"] = profile.get("citizenship") or row.get("citizenship") or ""
    out["flag"] = profile.get("flag") or row.get("flag") or ""
    out["color"] = (team.get("color") or row.get("color") or "").lstrip("#")
    out["altColor"] = (team.get("alt") or row.get("altColor") or "").lstrip("#")
    tiles = []
    for s in stats4 or []:
        if not s:
            continue
        val = s.get("v")
        if val in (None, "", "-"):
            continue
        tiles.append({"k": s.get("k") or "", "v": str(val)})
    out["stats4"] = tiles[:4]
    out.pop("_soccer", None)
    out.pop("stats", None)
    return out


# ── 해외축구 시즌 기록 ──────────────────────────────────────
def _soccer_stats(league: str, season: int, aid: str, sstats: dict) -> dict:
    key = f"{league}:{aid}"
    hit = sstats.get(key)
    if _fresh(hit, STATS_TTL):
        return hit.get("v") or {}
    data = _core_ref(f"{CORE_SEASON}/soccer/leagues/{league}/seasons/{season}"
                     f"/types/1/athletes/{aid}/statistics")
    ref = data.get("$ref") or ""
    if ref and "/statistics/" not in ref:
        ref += "/0"
    split = _core_ref(ref) if ref else {}
    values = {}
    for cat in ((split.get("splits") or {}).get("categories") or []):
        for st in cat.get("stats") or []:
            if st.get("name"):
                values[st["name"]] = st.get("displayValue")
    sstats[key] = {"v": values, "t": _ts()}
    return values


# ── 유럽축구 사진: Wikipedia / Wikimedia Commons ────────────
def _wiki_call(host: str, params: dict, tries: int = 3) -> dict:
    """위키미디어 API POST — 요청 간 최소 간격을 지키고 429 면 쉬었다 다시 한다."""
    body = urllib.parse.urlencode(params).encode()
    for attempt in range(tries):
        with _wiki_lock:
            wait = WIKI_GAP - (time.time() - _wiki_last[0])
            if wait > 0:
                time.sleep(wait)
            _wiki_last[0] = time.time()
        try:
            req = urllib.request.Request(host, data=body, headers={
                "User-Agent": WIKI_UA, "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded"})
            with urllib.request.urlopen(req, timeout=25) as res:
                return json.loads(res.read().decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001
            code = getattr(exc, "code", 0)
            if code == 429 or code >= 500:
                time.sleep(6 * (attempt + 1))
                continue
            return {}
    return {}


def _norm_name(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.translate(_TRANSLIT)
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


def _wiki_pages(titles: list[str]) -> dict:
    """문서 제목 후보 → {요청 제목: page}. 넘겨주기(redirect)·표제어 정규화를 따라간다."""
    out = {}
    for i in range(0, len(titles), 40):
        chunk = [t for t in titles[i:i + 40] if t]
        if not chunk:
            continue
        data = _wiki_call(WIKI_API, {
            "action": "query", "titles": "|".join(chunk), "redirects": "1",
            "prop": "pageprops", "ppprop": "wikibase_item", "format": "json", "formatversion": "2"})
        query = data.get("query") or {}
        pages = {p.get("title"): p for p in (query.get("pages") or []) if p.get("title")}
        alias = {}
        for n in query.get("normalized") or []:
            alias[n.get("from")] = n.get("to")
        for r in query.get("redirects") or []:
            alias[r.get("from")] = r.get("to")
        for title in chunk:
            cur = title
            for _ in range(3):
                cur = alias.get(cur, cur)
            page = pages.get(cur)
            if page is not None:
                out[title] = page
    return out


def _wiki_search(name: str, limit: int = 4) -> list[str]:
    """이름으로 문서 찾기 — 'footballer' 를 붙여 동명이인(정치인·배우)을 피한다."""
    data = _wiki_call(WIKI_API, {
        "action": "query", "list": "search", "srsearch": f"{name} footballer",
        "srlimit": str(limit), "format": "json"})
    return [h.get("title") for h in ((data.get("query") or {}).get("search") or []) if h.get("title")]


def _wd_entities(qids: list[str]) -> dict:
    out = {}
    for i in range(0, len(qids), 40):
        chunk = [q for q in qids[i:i + 40] if q]
        if not chunk:
            continue
        data = _wiki_call(WIKIDATA_API, {
            "action": "wbgetentities", "ids": "|".join(chunk),
            "props": "claims|labels|aliases|descriptions", "languages": "en", "format": "json"})
        out.update(data.get("entities") or {})
    return out


def _claim_values(entity: dict, prop: str) -> list:
    vals = []
    for claim in ((entity.get("claims") or {}).get(prop) or []):
        value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(value, dict) and value.get("id"):
            vals.append(value["id"])
        elif isinstance(value, str):
            vals.append(value)
        elif isinstance(value, dict) and value.get("time"):
            vals.append(value["time"])
    return vals


def _commons_thumbs(files: list[str], width: int = 500) -> dict:
    out = {}
    for i in range(0, len(files), 40):
        chunk = [f for f in files[i:i + 40] if f]
        if not chunk:
            continue
        data = _wiki_call(COMMONS_API, {
            "action": "query", "titles": "|".join(f"File:{f}" for f in chunk),
            "prop": "imageinfo", "iiprop": "url|mime|size", "iiurlwidth": str(width),
            "format": "json", "formatversion": "2"})
        for page in ((data.get("query") or {}).get("pages") or []):
            title = (page.get("title") or "").replace("File:", "")
            infos = page.get("imageinfo") or []
            if infos:
                out[title] = infos[0].get("thumburl") or infos[0].get("url") or ""
    return out


def resolve_soccer_photos(players: list[dict], cache: dict) -> dict:
    """유럽축구 선수 사진 찾기.

    영문 이름 → enwiki 문서 → 위키데이터 항목이 '축구선수(P106)'·'사람(P31)'인지 확인 →
    이름·출생연도가 맞는지 확인 → P18(사진) → Commons 500px 썸네일.
    확인이 안 되면 사진을 넣지 않는다(null → 앱이 유니폼 카드로 그린다).
    결과는 30일 동안 leaders-cache.json 에 남긴다.
    """
    photos = cache.get("photos")
    if not isinstance(photos, dict):
        photos = cache["photos"] = {}
    todo = [p for p in players if p.get("id") and not _fresh(photos.get(p["id"]), PHOTO_TTL)]
    if not todo:
        return photos
    names = {p["id"]: (p.get("name") or "").strip() for p in todo}

    def put(pid, **kw):
        entry = {"t": _ts(), "photo": None}
        entry.update(kw)
        photos[pid] = entry

    def _judge(ent: dict, wanted: set[str], age) -> tuple[bool, str]:
        """위키데이터 항목이 이 선수가 맞는지 확인한다."""
        inst = _claim_values(ent, "P31")
        occ = _claim_values(ent, "P106")
        if "Q4167410" in inst or "Q22808320" in inst:   # 동음이의 문서
            return False, "disambig"
        if FOOTBALLER_QID not in occ:
            return False, "not_footballer"
        if inst and HUMAN_QID not in inst:
            return False, "not_human"
        label = _norm_name((((ent.get("labels") or {}).get("en") or {}).get("value")) or "")
        aliases = {_norm_name(a.get("value")) for a in (((ent.get("aliases") or {}).get("en")) or [])}
        if not (wanted & ({label} | aliases)):
            return False, "name_mismatch"
        births = _claim_values(ent, "P569")
        if age and births:
            try:
                year = int(births[0][1:5])
                if abs(int(age) - (datetime.now(KST).year - year)) > 1:
                    return False, "age_mismatch"
            except Exception:  # noqa: BLE001
                pass
        return True, "ok"

    ages = {p["id"]: p.get("age") for p in todo}
    accepted: dict[str, tuple] = {}   # aid -> (qid, P18 파일명)
    reasons: dict[str, str] = {}

    def judge_candidates(cands: dict):
        """{aid: [(title, qid), ...]} → 통과한 것만 accepted 에."""
        all_qids = [q for lst in cands.values() for _, q in lst]
        ents = _wd_entities(all_qids) if all_qids else {}
        for pid, lst in cands.items():
            if pid in accepted:
                continue
            wanted = {_norm_name(names.get(pid) or "")}
            for title, qid in lst:
                ent = ents.get(qid) or {}
                if not ent:
                    reasons.setdefault(pid, "no_entity")
                    continue
                ok, why = _judge(ent, wanted | {_norm_name(title)}, ages.get(pid))
                if not ok:
                    reasons[pid] = why
                    continue
                files = _claim_values(ent, "P18")
                if not files:
                    reasons[pid] = "no_image"
                    continue
                accepted[pid] = (qid, files[0])
                reasons.pop(pid, None)
                break

    # 1) 이름 그대로 문서 찾기
    pages = _wiki_pages([n for n in names.values() if n])
    exact = {}
    for pid, name in names.items():
        page = pages.get(name) or {}
        qid = (page.get("pageprops") or {}).get("wikibase_item") or ""
        if qid:
            exact[pid] = [(page.get("title") or name, qid)]
    judge_candidates(exact)

    # 2) 확인이 안 된 선수는 '이름 + footballer' 검색 결과로 (한 실행에 정해 둔 인원까지)
    rest = [p for p in todo if p["id"] not in accepted]
    searched = {}
    for p in rest[:WIKI_MAX_SEARCH]:
        if not budget_ok(START, 240):
            break
        name = names.get(p["id"]) or ""
        if not name:
            continue
        titles = _wiki_search(name, limit=4)
        if titles:
            searched[p["id"]] = titles
    if searched:
        found = _wiki_pages([t for lst in searched.values() for t in lst])
        cands = {}
        for pid, titles in searched.items():
            lst = []
            for title in titles:
                page = found.get(title) or {}
                qid = (page.get("pageprops") or {}).get("wikibase_item") or ""
                if qid:
                    lst.append((page.get("title") or title, qid))
            if lst:
                cands[pid] = lst
        judge_candidates(cands)

    # 3) Commons 썸네일
    thumbs = _commons_thumbs([f for _, f in accepted.values()])
    checked = set(exact) | set(searched)
    for pid in {p["id"] for p in todo}:
        if pid in accepted:
            qid, fname = accepted[pid]
            url = thumbs.get(fname) or ""
            put(pid, photo=url or None, reason="ok" if url else "no_thumb", qid=qid)
        elif pid in checked:
            put(pid, reason=reasons.get(pid) or "no_match")
        # 검색 차례가 오지 않은 선수는 기록을 남기지 않는다 → 다음 실행에서 이어서 본다
    return photos


# ── K리그 상세 (키·몸무게·생일·국적) ────────────────────────
def _kleague_detail(pid: str, kleague_cache: dict) -> dict:
    hit = kleague_cache.get(pid)
    if _fresh(hit, CARD_TTL):
        return hit
    info = {"t": _ts()}
    try:
        req = urllib.request.Request(f"{KLEAGUE}/record/playerDetail.do?playerId={pid}",
                                     headers={"User-Agent": UA, "Accept-Language": "ko-KR",
                                              "Accept": "text/html,*/*"})
        with urllib.request.urlopen(req, timeout=25) as res:
            html_text = res.read().decode("utf-8", "replace")
        info["html"] = html_text
        table = {}
        for th, td in re.findall(r"<th>(.*?)</th>\s*<td[^>]*>(.*?)</td>", html_text, re.S):
            key = re.sub(r"<[^>]+>", "", th).strip()
            val = re.sub(r"<[^>]+>", "", td).replace("&nbsp;", " ").strip()
            if key and val:
                table[key] = val
        info.update({
            "name": table.get("이름", ""),
            "en": table.get("영문명", ""),
            "team": table.get("소속구단", ""),
            "posShort": table.get("포지션", ""),
            "num": table.get("배번", ""),
            "country": table.get("국적", ""),
            "height": int(table["키"]) if table.get("키", "").isdigit() else None,
            "weight": int(table["몸무게"]) if table.get("몸무게", "").isdigit() else None,
            "birth": table.get("생년월일", ""),
        })
        birth = table.get("생년월일", "")
        if re.match(r"^\d{4}/\d{2}/\d{2}$", birth):
            y, m, d = (int(x) for x in birth.split("/"))
            today = datetime.now(KST).date()
            age = today.year - y - ((today.month, today.day) < (m, d))
            info["age"] = age
    except Exception as exc:  # noqa: BLE001
        print(f"  K리그 선수 상세 실패 {pid}: {exc}", flush=True)
        info["miss"] = True
    info.pop("html", None)
    kleague_cache[pid] = info
    return info


def _kleague_photo(img1: str, pid: str, photo_cache: dict | None = None) -> str:
    """K리그 공식 사진(4~5MB PNG)을 카드용 560px WebP 로 줄여 data/img/kleague 에 둔다.

    K리그 공식 사진은 **투명 배경 PNG** 다(구단 배경 위에 얹어 쓰는 누끼 원본).
    그래서 배경을 지우거나 다른 색으로 채우지 않고 **투명도까지 원본 그대로** 줄여 저장한다.
    예전에는 `.convert("RGB")` 로 알파를 버렸는데, 투명 영역의 RGB 에 남아 있던 회색
    스튜디오 배경이 그대로 드러나 사진 안에 회색 사각형이 겹쳐 보였다(하정우 카드).
    Pillow 가 없는 환경에서는 원본 URL 을 쓰되, 7일 동안은 다시 받지 않는다(4~5MB).
    """
    rel = f"img/kleague/{pid}.webp"
    path = os.path.join(DATA_DIR, rel)
    if os.path.exists(path) and os.path.getsize(path) > 2000:
        return "./data/" + rel
    src = f"{KLEAGUE_CDN}{img1}" if img1.startswith("/") else img1
    if not src:
        return ""
    if photo_cache is not None:
        hit = photo_cache.get(pid)
        if _fresh(hit, 7 * 86400) and hit.get("src"):
            return hit["src"]
    try:
        raw = http_get(src, timeout=40)
        from PIL import Image  # noqa: PLC0415 — Pillow 가 없으면 원본 URL 로 넘어간다
        import io  # noqa: PLC0415
        image = Image.open(io.BytesIO(raw))
        image = image.convert("RGBA")      # 알파 보존 — 원본 배경을 건드리지 않는다
        width, height = image.size
        if width > 560:
            image = image.resize((560, max(1, int(height * 560 / width))), Image.LANCZOS)
        os.makedirs(KLEAGUE_IMG_DIR, exist_ok=True)
        image.save(path, "WEBP", quality=82, method=4)
        return "./data/" + rel
    except Exception as exc:  # noqa: BLE001
        print(f"  K리그 사진 압축 불가 {pid}: {str(exc)[:50]}", flush=True)
        if photo_cache is not None:
            photo_cache[pid] = {"src": src, "t": _ts()}
        return src


def build_leaders(now: datetime) -> None:
    cache = read_json(LEADER_CACHE, {}) or {}
    athletes = cache.get("athletes") or {}
    teams = cache.get("teams") or {}
    profiles = cache.get("profiles") or {}
    sstats = cache.get("sstats") or {}
    kleague_cache = cache.get("kleague") or {}
    kphoto = cache.get("kphoto") or {}
    year = now.year
    nba_season = year + 1 if now.month >= 9 else year
    leagues = []
    soccer_rows: list[dict] = []
    soccer_players: dict[str, dict] = {}

    # ── ESPN byathlete: MLB · NBA · NFL ──
    for cfg in BYATHLETE_CFG:
        season = nba_season if cfg["id"] == "nba" else year
        sport_type, league_slug = cfg["espn"].split("/")
        cats = []
        for sort, label, kind in cfg["cats"]:
            rows, used_season = [], season
            for attempt_season in (season, season - 1):
                try:
                    rows = espn_byathlete(cfg["espn"], sort, attempt_season, limit=10)
                except Exception as exc:  # noqa: BLE001
                    print(f"  {cfg['name']} {label} 실패: {exc}", flush=True)
                    rows = []
                if rows:
                    used_season = attempt_season
                    break
            if not rows:
                continue
            _enrich_byathlete(sport_type, league_slug, used_season, rows, profiles, teams)
            group = sort.split(".")[0]
            table = STAT4_CFG[cfg["id"]].get(group) or STAT4_CFG[cfg["id"]].get("*") or []
            out_rows = []
            for r in rows:
                prof = profiles.get(f"{sport_type}:{league_slug}:{r['id']}") or {}
                team = teams.get(f"{sport_type}:{league_slug}:{r.get('teamId') or ''}") or {}
                stats = r.get("stats") or {}
                tiles = [{"k": k, "v": stats.get(key)} for key, k in table]
                base = {k: r[k] for k in ("rank", "player", "team", "emblem")}
                base.update({"id": r.get("id"), "photo": r.get("photo"), "jersey": r.get("jersey"),
                             "posShort": r.get("posShort"), "position": r.get("position")})
                base["value"] = stat_text(r["value"], kind)
                out_rows.append(_card_row(base, dict(prof, pos=prof.get("pos") or pos_ko(
                    sport_type, r.get("posShort"), r.get("position"))), team, tiles))
            cats.append({"key": sort, "name": label, "season": str(used_season), "rows": out_rows})
        if cats:
            label = f"{season - 1}-{str(season)[2:]}" if cfg["id"] == "nba" else str(season)
            leagues.append({"id": cfg["id"], "sport": cfg["sport"], "name": cfg["name"],
                            "season": label, "source": "ESPN", "categories": cats})
            print(f"  {cfg['name']} 선수 순위 {len(cats)}부문", flush=True)

    # ── ESPN core: 해외축구 (참조 → 이름·팀·로고·팀 색, 캐시 사용) ──
    def team_info(team_id: str, league_key: str):
        if not team_id:
            return "", "", {}
        key = f"soccer:{league_key}:{team_id}"
        hit = teams.get(key) or teams.get(team_id)
        if not _fresh(hit, TEAM_TTL) or not (hit or {}).get("color"):
            data = _core_ref(f"{CORE_SEASON}/soccer/leagues/{league_key}"
                             f"/seasons/{year}/teams/{team_id}")
            logos = data.get("logos") or []
            if data.get("displayName"):
                hit = {"name": data.get("displayName") or "",
                       "abbr": data.get("abbreviation") or "",
                       "logo": logos[0].get("href") if logos else "",
                       "color": (data.get("color") or "").lstrip("#"),
                       "alt": (data.get("alternateColor") or "").lstrip("#"),
                       "t": _ts()}
                teams[key] = hit
        hit = hit or {}
        ko = team_ko_cached({"abbreviation": hit.get("abbr", ""), "displayName": hit.get("name", ""),
                             "name": hit.get("name", "")}, f"soccer/{league_key}")
        return ko or hit.get("name", ""), hit.get("logo", ""), hit

    for slug, name in SOCCER_CFG:
        try:
            cats_raw = espn_core_leaders("soccer", slug, year, 1, limit=12)
        except Exception as exc:  # noqa: BLE001
            print(f"  {name} 선수 순위 실패: {exc}", flush=True)
            continue
        recent = {k: (cats_raw.get(k) or [])[:10] for k, _ in SOCCER_CATS}
        ids = []
        for entries in recent.values():
            for entry in entries:
                aid = _ref_id(((entry.get("athlete") or {}).get("$ref")) or "")
                if aid and aid not in ids:
                    ids.append(aid)
        _fetch_profiles("soccer", slug, year, ids, profiles)
        for aid in ids:
            prof = profiles.get(f"soccer:{slug}:{aid}") or {}
            if prof.get("name"):
                athletes[aid] = prof["name"]
                soccer_players[aid] = {"id": aid, "name": prof["name"], "age": prof.get("age")}
        # 이 리그에서 그 선수가 올린 득점·도움 (카드 스탯에 그대로 사용)
        own = {}
        for key, _ in SOCCER_CATS:
            for entry in recent.get(key) or []:
                aid = _ref_id(((entry.get("athlete") or {}).get("$ref")) or "")
                if aid:
                    own.setdefault(aid, {})[key] = stat_text(entry.get("value"), "int")
        picked = []
        for key, label in SOCCER_CATS:
            leaders = recent.get(key) or []
            rows = []
            for pos, entry in enumerate(leaders, start=1):
                aid = _ref_id(((entry.get("athlete") or {}).get("$ref")) or "")
                tid = _ref_id(((entry.get("team") or {}).get("$ref")) or "")
                team_name, logo, team = team_info(tid, slug)
                prof = profiles.get(f"soccer:{slug}:{aid}") or {}
                stats = _soccer_stats(slug, year, aid, sstats) if aid else {}
                tiles = [
                    {"k": "득점", "v": (own.get(aid) or {}).get("goals") or stats.get("totalGoals")},
                    {"k": "도움", "v": (own.get(aid) or {}).get("assists") or stats.get("goalAssists")},
                    {"k": "슈팅", "v": stats.get("totalShots")},
                    {"k": "경기", "v": stats.get("appearances")},
                ]
                row = _card_row({
                    "rank": pos, "player": prof.get("name") or athletes.get(aid, ""),
                    "team": team_name, "emblem": logo, "value": stat_text(entry.get("value"), "int"),
                    "id": aid, "_soccer": True,
                }, prof, team, tiles)
                rows.append(row)
                soccer_rows.append(row)
            rows = [r for r in rows if r["player"]]
            if rows:
                picked.append({"key": key, "name": label, "rows": rows})
        if picked:
            leagues.append({"id": slug, "sport": "해외축구", "name": name,
                            "season": f"{year}-{str(year + 1)[2:]}", "source": "ESPN",
                            "categories": picked})
            print(f"  {name} 선수 순위 {len(picked)}부문", flush=True)

    # ── K리그1·2 ──
    for lid, lname in ((1, "K리그1"), (2, "K리그2")):
        cats = []
        for code, label, field in (("GOAL", "득점", "goalQty"), ("ASSIST", "도움", "assistQty"),
                                   ("AP", "공격포인트", "apQty")):
            try:
                data = kr_post_json(f"{KLEAGUE}/record/rankSort.do",
                                    {"year": str(year), "leagueId": str(lid), "recordType": code})
            except Exception as exc:  # noqa: BLE001
                print(f"  {lname} {label} 실패: {exc}", flush=True)
                continue
            items = (((data or {}).get("data") or {}).get("list") or [])[:10]
            need = [str(it.get("playerId")) for it in items
                    if it.get("playerId") and not _fresh(kleague_cache.get(str(it.get("playerId"))), CARD_TTL)]
            if need and budget_ok(START, 240):
                with ThreadPoolExecutor(max_workers=6) as pool:
                    list(pool.map(lambda pid: _kleague_detail(pid, kleague_cache), need))
            rows = []
            for pos, item in enumerate(items, start=1):
                pid = str(item.get("playerId") or "")
                det = kleague_cache.get(pid) or {}
                team_id = (item.get("teamId") or "").strip().upper()
                team_name = (item.get("teamName") or "").strip()
                photo = _kleague_photo(item.get("img1") or "", pid, kphoto) if item.get("img1") else ""
                position = det.get("posShort") or ""
                tiles = [
                    {"k": "득점", "v": item.get("goalQty")},
                    {"k": "도움", "v": item.get("assistQty")},
                    {"k": "공격포인트", "v": item.get("apQty")},
                    {"k": "경기", "v": item.get("gameQty")},
                ]
                rows.append({
                    "rank": pos, "player": (item.get("name") or "").strip(), "team": team_name,
                    "emblem": kleague_logo(item.get("teamId")),
                    "value": str(item.get(field) if item.get(field) is not None else "-"),
                    "id": pid, "photo": photo,
                    "jersey": str(item.get("backNo") or det.get("num") or ""),
                    "position": pos_ko("soccer", position), "posShort": position,
                    "height": det.get("height"), "weight": det.get("weight"), "age": det.get("age"),
                    "citizenship": det.get("country") or "", "flag": flag_url(det.get("country") or ""),
                    "color": KLEAGUE_COLOR.get(team_id, ""), "altColor": "",
                    "en": det.get("en") or "",
                    "stats4": [t for t in tiles if t["v"] not in (None, "", "-")][:4],
                })
            if rows:
                cats.append({"key": code, "name": label, "rows": rows})
        if cats:
            leagues.append({"id": f"kleague.{lid}", "sport": "축구", "name": lname,
                            "season": str(year), "source": "K리그", "categories": cats})
            print(f"  {lname} 선수 순위 {len(cats)}부문", flush=True)

    # ── KBL ──
    try:
        parts = kr_get_json(f"{KBL_API}/league/season-leader", KBL_HEADERS)
        emblem_by_team = {}
        try:
            for row in kbl_standings()[0]["rows"]:
                emblem_by_team[row["team"]] = row["emblem"]
        except Exception:  # noqa: BLE001
            pass
        labels = {"score": "득점", "rb": "리바운드", "ast": "어시스트", "bs": "블록", "stl": "스틸"}
        # 선수별로 5부문 값을 모아 카드 스탯 4칸을 만든다
        by_player: dict[str, dict] = {}
        for part in parts or []:
            key = part.get("part")
            if key not in labels:
                continue
            for item in (part.get("list") or [])[:10]:
                player = item.get("player") or {}
                pcode = str(player.get("pcode") or "")
                if not pcode:
                    continue
                slot = by_player.setdefault(pcode, {"player": player, "values": {}})
                slot["values"][key] = stat_text(item.get("value"), "avg1")
        cats = []
        for part in parts or []:
            key = part.get("part")
            if key not in labels:
                continue
            rows = []
            for pos, item in enumerate((part.get("list") or [])[:10], start=1):
                player = item.get("player") or {}
                pcode = str(player.get("pcode") or "")
                team_name = (player.get("tname") or "").strip()
                merged = (by_player.get(pcode) or {}).get("values") or {}
                tiles = [{"k": labels[k], "v": merged.get(k)} for k in ("score", "rb", "ast", "bs", "stl")]
                tiles = [t for t in tiles if t["v"] not in (None, "", "-")]
                if len(tiles) < 3 and item.get("gameCount"):
                    tiles.append({"k": "경기", "v": str(item.get("gameCount"))})
                rows.append({
                    "rank": pos, "player": (player.get("pname") or "").strip(),
                    "team": team_name, "emblem": emblem_by_team.get(team_name, ""),
                    "value": stat_text(item.get("value"), "avg1"),
                    "id": pcode, "photo": player.get("img") or "",
                    "jersey": str(player.get("backNum") or ""),
                    "position": pos_ko("basketball", player.get("pos") or "", ""),
                    "posShort": player.get("pos") or "",
                    "height": None, "weight": None, "age": None,
                    "citizenship": "", "flag": "",
                    "color": KBL_COLOR.get(team_name, ""), "altColor": "",
                    "en": player.get("ename") or "",
                    "stats4": tiles[:4],
                })
            if rows:
                cats.append({"key": key, "name": labels[key], "rows": rows})
        if cats:
            leagues.append({"id": "kbl", "sport": "농구", "name": "KBL",
                            "season": "2026-27", "source": "KBL", "categories": cats})
            print(f"  KBL 선수 순위 {len(cats)}부문", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  KBL 선수 순위 실패: {exc}", flush=True)

    # ── 유럽축구 사진 (위키백과·위키미디어) — 모든 리그를 모아 한 번에 ──
    if soccer_players:
        photos = {}
        try:
            photos = resolve_soccer_photos(list(soccer_players.values()), cache)
        except Exception as exc:  # noqa: BLE001
            print(f"  유럽축구 사진 매칭 실패: {exc}", flush=True)
        hit = 0
        for row in soccer_rows:
            entry = photos.get(row.get("id") or "") or {}
            row["photo"] = entry.get("photo") or ""
            if row["photo"]:
                hit += 1
        total = len({r.get("id") for r in soccer_rows if r.get("id")})
        got = len({r["id"] for r in soccer_rows if r.get("photo") and r.get("id")})
        print(f"  유럽축구 사진 {got}/{total}명 ({100 * got / total:.0f}%)" if total else "", flush=True)

    cache["athletes"] = athletes
    cache["teams"] = teams
    cache["profiles"] = profiles
    cache["sstats"] = sstats
    cache["kleague"] = kleague_cache
    cache["kphoto"] = kphoto
    write_json(LEADER_CACHE, cache)
    if not leagues:
        print("  선수 순위 0개 — 그대로 둡니다", flush=True)
        return
    order = {s: i for i, s in enumerate(SPORT_ORDER)}
    leagues.sort(key=lambda l: (order.get(l["sport"], 9), l["name"]))
    write_json("leaders.json", {
        "updated": now.isoformat(timespec="seconds"),
        "sports": SPORT_ORDER,
        "source": "ESPN · K리그 · KBL",
        "leagues": leagues,
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
KR_LABELS = {"야구": "KBO", "축구": "K리그", "농구": "KBL", "배구": "V리그", "기타": "한국 스포츠"}
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
    "기타": ["쇼트트랙", "스피드스케이팅", "피겨", "컬링", "아이스하키", "수영", "육상", "마라톤",
             "탁구", "배드민턴", "테니스", "양공", "펜싱", "유도", "태권도", "역도", "바둑",
             "당구", "핸드볼", "사이클", "e스포츠", "체조", "클라이밍", "서핑", "스쿼시", "보치아"],
}


def classify_sport(text: str) -> str:
    if "핸드볼" in text and not any(w in text for w in ("축구", "K리그", "손흥민", "월드컵")):
        return "기타"
    best, best_score = "기타", 0
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
    order = ["야구", "축구", "농구", "배구", "기타"]
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
    used_ids: set[str] = set()
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
            if aid in used_ids:
                continue  # 같은 기사가 두 리그 소식에 같이 올라오는 경우가 있어 한 번만
            if not headline or not is_safe(headline) or not is_safe(desc):
                continue
            if "fantasy" in headline.lower() or "betting" in headline.lower():
                continue  # 판타지·배팅 기사는 아이 화면에 올리지 않는다
            if aid in cache:
                items.append(cache[aid])
                new_cache[aid] = cache[aid]
                used_ids.add(aid)
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
                used_ids.add(aid)
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
            "soccer/ger.1": GER_TEAMS_KO,
            "soccer/esp.1": ESP_TEAMS_KO,
            "soccer/eng.1": ENG_TEAMS_KO,
            "soccer/ita.1": KO_BY_LEAGUE["soccer/ita.1"],
            "soccer/uefa.champions": UCL_TEAMS_KO,
        },
        "intl": INT_TEAMS_KO,
        "intlLeagues": sorted(INTL_KEYS),
    })


def good_cached_name(value) -> bool:
    """예전 실행이 남긴 엉터리 이름을 걸러 낸다.

    기계번역이 'LeverkusenCity in Germany', 'Hamburg\n' 같은 값을 사전에 넣어 두었다.
    한글이 없거나, 'City in' 이 섮여 있거나, 줄바꿈·양끝 공백이 있으면 버린다.
    """
    if not isinstance(value, str):
        return False
    if not re.search(r"[가-힣]", value):
        return False
    if "City in" in value or "\n" in value or value != value.strip():
        return False
    return True


START = time.time()


def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass
    now = datetime.now(KST)
    print(f"[{now.isoformat(timespec='seconds')}] ESPN 수집 시작", flush=True)
    cached = read_json("teams-ko.json", {}) or {}
    dropped = 0
    for k, v in cached.items():
        if good_cached_name(v):
            TEAMS_KO.setdefault(k, v)
        else:
            dropped += 1
    if dropped:
        print(f"  팀 이름 사전에서 엉터리 이름 {dropped}개 제거", flush=True)
    write_json("teams-ko.json", dict(sorted(TEAMS_KO.items())))
    write_ko_maps()
    for name, fn in (("today", build_today), ("standings", build_standings), ("leaders", build_leaders), ("news", build_news)):
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
