#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 콘텐츠 지원사업/공모 자동 수집 봇
- 한국: 공공데이터포털 KOCCA OpenAPI + 주요 기관 게시판 스크래핑
- 글로벌: RSS/검색 기반 그랜트·공모 수집
- AI/콘텐츠 키워드로 필터링 후 HTML 이메일 발송

매일 GitHub Actions(09:30 KST)로 실행됨.
"""

import os
import re
import sys
import html
import smtplib
import datetime as dt
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
import feedparser

KST = dt.timezone(dt.timedelta(hours=9))
TODAY = dt.datetime.now(KST).date()
UA = {"User-Agent": "Mozilla/5.0 (compatible; GrantBot/1.0)"}
TIMEOUT = 20

# ── 관심 키워드 (이 중 하나라도 제목/내용에 있으면 채택) ─────────────
KEYWORDS = [
    # AI / 기술
    "AI", "인공지능", "생성형", "생성 AI", "버추얼", "가상", "메타버스",
    "실감", "VP", "버추얼프로덕션", "딥러닝", "LLM",
    # 콘텐츠 / 영상
    "콘텐츠", "영상", "방송", "다큐", "애니메이션", "웹툰", "OTT",
    "숏폼", "스토리", "IP", "제작지원", "크리에이터", "미디어",
    # 영어 글로벌
    "artificial intelligence", "generative", "AI film", "AI video",
    "media art", "content", "creator", "documentary", "animation",
    "immersive", "XR", "virtual production",
]

# 제외 키워드 (게임 전용·채용 등 노이즈 줄이기 — 필요시 비우세요)
EXCLUDE = ["채용", "병역지정", "산업기능요원"]


def matches(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    if any(x.lower() in low for x in EXCLUDE):
        # 제외어가 있어도 핵심 AI어가 있으면 살림
        if not any(k.lower() in low for k in ["ai", "인공지능", "생성"]):
            return False
    return any(k.lower() in low for k in KEYWORDS)


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


# ════════════════════════════════════════════════════════════════
# 1. 한국 — 공공데이터포털 KOCCA 지원사업공고 OpenAPI
#    서비스키는 환경변수 DATA_GO_KR_KEY 로 주입 (data.go.kr 무료 발급)
# ════════════════════════════════════════════════════════════════
def fetch_kocca_api():
    key = os.environ.get("DATA_GO_KR_KEY", "").strip()
    if not key:
        return [("KOCCA OpenAPI", "⚠️ DATA_GO_KR_KEY 미설정 — 건너뜀", "", "")]
    out = []
    url = "http://api.data.go.kr/openapi/tn_pubr_public_kocca_supt_busi_anno_api"
    params = {
        "serviceKey": key,
        "pageNo": 1,
        "numOfRows": 50,
        "type": "json",
    }
    try:
        r = requests.get(url, params=params, headers=UA, timeout=TIMEOUT)
        data = r.json()
        items = (data.get("response", {}).get("body", {}).get("items", []) or [])
        for it in items:
            title = clean(it.get("titles") or it.get("title") or "")
            link = it.get("rfrncUrl") or it.get("link") or "https://www.kocca.kr"
            period = f"{it.get('reqstBeginDe','')}~{it.get('reqstEndDe','')}"
            if matches(title):
                out.append(("KOCCA(콘진원)", title, link, period))
    except Exception as e:
        out.append(("KOCCA OpenAPI", f"⚠️ API 오류: {e}", "", ""))
    return out


# ════════════════════════════════════════════════════════════════
# 2. 한국 — 기관 게시판 스크래핑 (구조 바뀌면 여기만 고치면 됨)
# ════════════════════════════════════════════════════════════════
def fetch_kocca_board():
    """콘진원 지원공고 게시판 (API 실패 대비 백업)"""
    out = []
    url = "https://www.kocca.kr/kocca/pims/list.do?menuNo=204104"
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a"):
            t = clean(a.get_text())
            href = a.get("href", "")
            if len(t) > 8 and matches(t) and ("view" in href or "Detail" in href):
                out.append(("콘진원 게시판", t, urljoin(url, href), ""))
    except Exception as e:
        out.append(("콘진원 게시판", f"⚠️ {e}", "", ""))
    return out[:15]


def fetch_nipa():
    """정보통신산업진흥원(NIPA) 사업공고"""
    out = []
    url = "https://www.nipa.kr/home/2-2"
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a"):
            t = clean(a.get_text())
            href = a.get("href", "")
            if len(t) > 8 and matches(t):
                out.append(("NIPA", t, urljoin(url, href), ""))
    except Exception as e:
        out.append(("NIPA", f"⚠️ {e}", "", ""))
    return out[:15]


def fetch_kobaco_etc_rss():
    """bizinfo(기업마당) 등 RSS 제공처 — AI/콘텐츠 키워드 필터"""
    out = []
    feeds = [
        # 기업마당 분야별 RSS가 바뀔 수 있으니 검색 백업과 병행
        "https://www.bizinfo.go.kr/web/lay1/program/S1T122C128/rss/rssList.do",
    ]
    for f in feeds:
        try:
            d = feedparser.parse(f)
            for e in d.entries[:40]:
                t = clean(e.get("title", ""))
                if matches(t):
                    out.append(("기업마당", t, e.get("link", ""), ""))
        except Exception:
            pass
    return out[:15]


# ════════════════════════════════════════════════════════════════
# 3. 글로벌 — AI/미디어아트 그랜트·공모 RSS
# ════════════════════════════════════════════════════════════════
def fetch_global_rss():
    out = []
    feeds = [
        ("Rhizome/AI art", "https://rhizome.org/feed/"),
        ("CreativeApplications", "https://www.creativeapplications.net/feed/"),
        ("ResArtis(레지던시)", "https://resartis.org/feed/"),
    ]
    for name, f in feeds:
        try:
            d = feedparser.parse(f)
            for e in d.entries[:30]:
                t = clean(e.get("title", ""))
                summ = clean(e.get("summary", ""))
                if matches(t + " " + summ):
                    out.append((name, t, e.get("link", ""), ""))
        except Exception:
            pass
    return out[:20]


# ════════════════════════════════════════════════════════════════
# 메일 빌드 & 발송
# ════════════════════════════════════════════════════════════════
def build_html(sections):
    total = sum(len(v) for v in sections.values())
    parts = [f"""<div style="font-family:-apple-system,'Apple SD Gothic Neo',sans-serif;
      max-width:680px;margin:0 auto;color:#1a1a1a">
      <h2 style="border-bottom:3px solid #FF5533;padding-bottom:8px">
        🤖 AI 콘텐츠 지원사업 브리핑</h2>
      <p style="color:#666">{TODAY} (KST) · 총 {total}건 수집</p>"""]

    for sec, items in sections.items():
        parts.append(f'<h3 style="margin-top:28px;color:#FF5533">{html.escape(sec)} '
                     f'<span style="color:#999;font-size:13px">({len(items)})</span></h3>')
        if not items:
            parts.append('<p style="color:#aaa">새 공고 없음</p>')
            continue
        parts.append('<ul style="line-height:1.7;padding-left:18px">')
        for src, title, link, period in items:
            p = f' <span style="color:#888;font-size:12px">[{html.escape(period)}]</span>' if period else ""
            ln = (f'<a href="{html.escape(link)}" style="color:#1a1a1a;text-decoration:none;'
                  f'border-bottom:1px solid #ddd">{html.escape(title)}</a>') if link else html.escape(title)
            parts.append(f'<li><b style="color:#FF5533;font-size:12px">{html.escape(src)}</b> · {ln}{p}</li>')
        parts.append('</ul>')

    parts.append('<p style="margin-top:32px;color:#bbb;font-size:11px">'
                 '자동 생성 · 소스 구조 변경 시 collect.py 수정 필요</p></div>')
    return "\n".join(parts)


def send_email(html_body):
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    pw = os.environ["SMTP_PASS"]
    to = os.environ.get("MAIL_TO", user)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🤖 AI 콘텐츠 지원사업 브리핑 — {TODAY}"
    msg["From"] = f"AI 지원사업 봇 <{user}>"
    msg["To"] = to
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    recipients = [x.strip() for x in to.split(",") if x.strip()]
    if port == 465:
        # SSL 방식 (네이버가 STARTTLS를 거부할 때 대비)
        with smtplib.SMTP_SSL(host, port) as s:
            s.login(user, pw)
            s.sendmail(user, recipients, msg.as_string())
    else:
        with smtplib.SMTP(host, port) as s:
            s.starttls()
            s.login(user, pw)
            s.sendmail(user, recipients, msg.as_string())
    print(f"메일 발송 완료 → {to}")


def main():
    sections = {
        "🇰🇷 한국 — 콘진원/공식 API": fetch_kocca_api(),
        "🇰🇷 한국 — 기관 게시판": fetch_kocca_board() + fetch_nipa() + fetch_kobaco_etc_rss(),
        "🌍 글로벌 — AI/미디어아트": fetch_global_rss(),
    }

    # 중복 제거 (제목 기준)
    for sec in sections:
        seen, dedup = set(), []
        for row in sections[sec]:
            k = row[1][:40]
            if k not in seen:
                seen.add(k)
                dedup.append(row)
        sections[sec] = dedup

    html_body = build_html(sections)

    # 로컬 미리보기 저장
    with open("preview.html", "w", encoding="utf-8") as f:
        f.write(html_body)

    if os.environ.get("SMTP_HOST"):
        send_email(html_body)
    else:
        print("SMTP 미설정 — preview.html 만 생성")


if __name__ == "__main__":
    main()
