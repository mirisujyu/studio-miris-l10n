#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
번역 대상 선별 + 번역 덤프/반영

strings.json 은 v2 포맷(`scn.py extract`, VNEG 구조적 추출)이라 오퍼랜드 오탐은
거의 없지만, 여는 괄호 조각(`「z`)·기호만 있는 조각 등 번역이 필요 없는 레코드는
여전히 있다. 일본어 표시문자가 있는 미번역 조각만 골라 dump/apply 한다.

사용
----
  ujyu filter stats              분류 통계
  ujyu filter dump [start] [n]   번역용 최소 포맷 덤프 (id<TAB>화자<TAB>원문)
  ujyu filter context            씬 전체를 문맥과 함께 덤프 (앞뒤 대사가 보인다)
  ujyu filter propagate          같은 원문에 기번역 전파
  ujyu filter apply <tsv>        번역 결과(id<TAB>번역) 반영
  ujyu filter review             검수용 TSV (config.REVIEW_TSV)
"""
import sys, os, json, io, re, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ujyu.titleconfig import config as C
STRINGS = C.STRINGS

# 표시용 일본어 문자: 히라가나 / 전각가타카나 / 한자 / 장음부호
JP_CHAR = re.compile(r"[぀-ゟ゠-ヿ一-鿿々〆]")
# 반각 가나·기호 (제어 오퍼랜드)
HALFWIDTH = re.compile(r"^[｡-ﾟ！-～ -~]+$")
# 리소스 이름 패턴
RESOURCE = re.compile(C.RESOURCE_RE, re.IGNORECASE)


# 텍스트처럼 디코드되지만 실제론 마커인 코드들.
# `钁` = SJIS E8 76 (트레일이 'v'). 331회 반복되며 대사 문맥이 전혀 없다.
MARKER = set(C.MARKERS)

# 검수 표시 (config). 비어 있으면 이 프로젝트는 표시를 쓰지 않는다.
REVIEW_MARK = getattr(C, "REVIEW_MARK", None) or ""
# 표시를 안 쓰는 프로젝트에서 번역자가 습관적으로 붙여 보낸 것을 떼어내기 위한 후보.
# REVIEW_MARK 가 설정돼 있으면 이 목록은 쓰이지 않는다.
_MARK_CANDIDATES = ("♠", "♣", "★", "◆")


def classify(jp):
    """조각을 분류한다. 반환: 'text' | 'marker' | 'halfwidth' | 'resource' | 'nojp' | 'empty'"""
    s = (jp or "").strip()
    if not s:
        return "empty"
    if s in MARKER:
        return "marker"
    if RESOURCE.match(s) and not JP_CHAR.search(s):
        return "resource"
    if not JP_CHAR.search(s):
        # 일본어 표시문자가 없음 → 반각 오퍼랜드거나 ASCII 식별자
        return "halfwidth" if HALFWIDTH.match(s) else "nojp"
    return "text"


def load():
    return json.load(open(STRINGS, encoding="utf-8"))


def save(S):
    """strings.json 을 원자적으로 쓴다.

    번역 배치를 병렬로 돌리면 다른 프로세스가 `load()` 하는 도중에 여기서 쓰게 된다.
    제자리 덮어쓰기는 그 순간 반쪽짜리 JSON 을 읽히므로(실제로 `filter check` 가
    파싱 오류로 죽은 적이 있다) 임시 파일에 다 쓴 뒤 os.replace 로 갈아끼운다.
    """
    tmp = STRINGS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(S, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STRINGS)


def split_tail(jp):
    """(본문, 꼬리제어코드). 끝에 연속된 제어문자(<0x30)를 꼬리로 분리한다.

    대사 대부분이 `…」%` 처럼 끝나는데 `%`(0x25)는 대기/종료 제어코드다.
    번역기에는 본문만 주고, 반영할 때 꼬리를 그대로 다시 붙인다.
    """
    n = len(jp)
    k = n
    while k > 0 and ord(jp[k - 1]) < 0x30:
        k -= 1
    return jp[:k], jp[k:]


def has_mid_ctrl(jp):
    """꼬리를 뗀 본문에 아직 제어문자가 남아 있는가 (수동 처리 대상)."""
    body, _ = split_tail(jp)
    return any(ord(c) < 0x30 for c in body)


def targets(S, include_done=False, skip_mid=True):
    """번역 대상 [(id, 본문, 꼬리)] 목록."""
    out = []
    for i, r in enumerate(S):
        jp = r.get("jp")
        if classify(jp) != "text":
            continue
        if not include_done and (r.get("kr") or "").strip():
            continue
        if skip_mid and has_mid_ctrl(jp):
            continue
        body, tail = split_tail(jp)
        out.append((i, body, tail))
    return out


def cmd_stats():
    S = load()
    from collections import Counter
    c = Counter(classify(r.get("jp")) for r in S)
    done = sum(1 for r in S if (r.get("kr") or "").strip())
    tgt = targets(S)
    # 문자수 합계 (번역 분량 가늠)
    chars = sum(len(t) for _, t, _x in tgt)
    lines = [
        "전체 조각: %d" % len(S),
        "",
        "분류:",
        "  text       %6d  ← 번역 대상" % c["text"],
        "  halfwidth  %6d  (반각가나 제어 오퍼랜드)" % c["halfwidth"],
        "  resource   %6d  (리소스 이름)" % c["resource"],
        "  nojp       %6d  (일본어 없음)" % c["nojp"],
        "  empty      %6d" % c["empty"],
        "",
        "이미 번역됨: %d" % done,
        "남은 번역 대상: %d조각 / %d자" % (len(tgt), chars),
    ]
    # 파일별 남은 분량 상위
    from collections import defaultdict
    per = defaultdict(int)
    idx = {i: r["file"] for i, r in enumerate(S)}
    for i, t, _x in tgt:
        per[idx[i]] += 1
    lines.append("")
    lines.append("파일별 남은 조각 상위 15:")
    for f, n in sorted(per.items(), key=lambda kv: -kv[1])[:15]:
        lines.append("  %-22s %5d" % (f, n))
    txt = "\n".join(lines)
    io.open("_filter_stats.txt", "w", encoding="utf-8").write(txt)
    print("-> _filter_stats.txt (%d조각 대상)" % len(tgt))


# 문자 영역 — (이름, 시작, 끝) 앞에서부터 먼저 맞는 것으로 분류한다.
# 폰트에 어떤 글리프를 넣어야 하는지, 주입이 CP949 로 나가는지 가늠하는 데 쓴다.
BLOCKS = [
    ("제어",              0x0000, 0x001F),
    ("ASCII 공백·기호",    0x0020, 0x002F),
    ("ASCII 숫자",         0x0030, 0x0039),
    ("ASCII 기호",         0x003A, 0x0040),
    ("ASCII 대문자",       0x0041, 0x005A),
    ("ASCII 기호2",        0x005B, 0x0060),
    ("ASCII 소문자",       0x0061, 0x007A),
    ("ASCII 기호3",        0x007B, 0x007E),
    ("라틴 확장",          0x00A0, 0x024F),
    ("그리스",             0x0370, 0x03FF),
    ("키릴",               0x0400, 0x04FF),
    ("한글 자모",          0x1100, 0x11FF),
    ("일반 구두점",        0x2000, 0x206F),
    ("첨자·통화·기호",     0x2070, 0x218F),
    ("화살표",             0x2190, 0x21FF),
    ("수학 연산",          0x2200, 0x22FF),
    ("괘선",               0x2500, 0x257F),
    ("블록·기하 도형",     0x2580, 0x25FF),
    ("기타 기호",          0x2600, 0x27BF),
    ("CJK 기호·구두점",    0x3000, 0x303F),
    ("히라가나",           0x3040, 0x309F),
    ("가타카나",           0x30A0, 0x30FF),
    ("한글 호환 자모",     0x3130, 0x318F),
    ("CJK 괄호 문자",      0x3200, 0x33FF),
    ("한자 확장 A",        0x3400, 0x4DBF),
    ("한자",               0x4E00, 0x9FFF),
    ("한글 음절",          0xAC00, 0xD7A3),
    ("사유 영역",          0xE000, 0xF8FF),
    ("CJK 호환 한자",      0xF900, 0xFAFF),
    ("반각·전각",          0xFF00, 0xFFEF),
]


def _block_of(ch):
    o = ord(ch)
    for name, lo, hi in BLOCKS:
        if lo <= o <= hi:
            return name
    return "기타(U+%04X~)" % (o & ~0xFF)


def cmd_chars(out=None, kinds=None, jp_only=False):
    """원문(jp)·번역문(kr) 의 문자 영역별 통계 + 사용 문자 목록.

    폰트에 넣을 글리프 집합과 CP949 주입 가능 여부를 한눈에 본다. `본문`은
    표시되는 글자만 센 것이고, 대사 끝 제어코드(`」%` 의 `%` 등)는 뺐다.

    `jp_only` 면 번역문(kr) 쪽 통계와 문자 목록을 빼고 원문만 낸다. 번역문의
    사용 문자 목록은 번역 결과물이라 공개 리포에 올릴 수 없는 경우가 있는데,
    폰트 작업에 필요한 것은 대개 원문 쪽이다.
    """
    from collections import Counter
    S = load()
    want = set(kinds) if kinds else None
    cj, ck = Counter(), Counter()          # 문자 -> 빈도
    nj = nk = 0                            # 조각 수
    for r in S:
        if want and r.get("kind") not in want:
            continue
        jp = split_tail(r.get("jp") or "")[0]
        kr = split_tail(r.get("kr") or "")[0]
        if jp:
            cj.update(jp); nj += 1
        if kr:
            ck.update(kr); nk += 1

    def table(cnt):
        per = {}
        for ch, n in cnt.items():
            b = _block_of(ch)
            g = per.setdefault(b, [0, 0])   # [종수, 총빈도]
            g[0] += 1; g[1] += n
        return per

    def enc_bad(cnt):
        bad = []
        for ch in cnt:
            try:
                ch.encode("cp949")
            except UnicodeEncodeError:
                bad.append(ch)
        return sorted(bad)

    sets = [("jp", cj)] if jp_only else [("jp", cj), ("kr", ck)]
    L = []
    L.append("# 문자 통계 — 원문(jp)" if jp_only
             else "# 문자 통계 — 원문(jp) vs 번역문(kr)")
    L.append("")
    L.append("**원작 시나리오가 실제로 쓰는 글자의 목록과 빈도.** 게임에서 추출한 원문을")
    L.append("그대로 센 것이고, 번역 결과와는 무관하다.")
    L.append("")
    L.append("쓸 데:")
    L.append("")
    L.append("- **폰트에 어떤 글리프를 넣어야 하는지** — 여기 없는 글자는 준비할 필요가 없다.")
    L.append("- **CP949 로 옮길 수 있는지** — 아래 \"인코딩 불가\" 목록이 그대로 `ujyu jpmap` 의")
    L.append("  대상이다(사용자 정의 영역에 실어 나른다).")
    L.append("- 번역하지 않고 원문으로 남길 대목이 있을 때 그 글자들이 화면에 나오는지 가늠.")
    L.append("")
    L.append("`ujyu filter chars --jp-only` 로 다시 만든다. 손으로 고치지 말 것.")
    L.append("본문은 표시되는 글자만 세고, 대사 끝 제어코드(`」%` 의 `%` 등)는 뺐다.")
    L.append("")
    L.append("대상 kind: %s" % (",".join(sorted(want)) if want else "전체"))
    if jp_only:
        L.append("조각 수: %d" % nj)
        L.append("총 문자 수: %d" % sum(cj.values()))
        L.append("고유 문자 수: %d" % len(cj))
    else:
        L.append("조각 수: jp %d / kr %d" % (nj, nk))
        L.append("총 문자 수: jp %d / kr %d" % (sum(cj.values()), sum(ck.values())))
        L.append("고유 문자 수: jp %d / kr %d" % (len(cj), len(ck)))
    L.append("")
    tj, tk = table(cj), (None if jp_only else table(ck))
    L.append("## 문자 영역별")
    L.append("")
    if jp_only:
        L.append("| 영역 | 종 | 빈도 |")
        L.append("|---|---:|---:|")
    else:
        L.append("| 영역 | jp 종 | jp 빈도 | kr 종 | kr 빈도 |")
        L.append("|---|---:|---:|---:|---:|")
    known = set(b[0] for b in BLOCKS)
    order = [b[0] for b in BLOCKS] + sorted((set(tj) | set(tk or {})) - known)
    seen = set()
    for b in order:
        if b in seen or (b not in tj and b not in (tk or {})):
            continue
        seen.add(b)
        a = tj.get(b, [0, 0])
        if jp_only:
            L.append("| %s | %d | %d |" % (b, a[0], a[1]))
        else:
            c = tk.get(b, [0, 0])
            L.append("| %s | %d | %d | %d | %d |" % (b, a[0], a[1], c[0], c[1]))
    for tag, cnt in sets:
        bad = enc_bad(cnt)
        L.append("")
        L.append("CP949 로 인코딩 불가%s: %d종%s"
                 % ("" if jp_only else " (%s)" % tag, len(bad),
                    ("  " + " ".join(bad)) if bad else ""))

    for tag, cnt in ([("원문 jp", cj)] if jp_only
                     else [("원문 jp", cj), ("번역문 kr", ck)]):
        L.append("")
        L.append("## 사용 문자 목록 — %s (빈도순)" % tag)
        by = {}
        for ch, n in cnt.items():
            by.setdefault(_block_of(ch), []).append((n, ch))
        for b in [x[0] for x in BLOCKS] + sorted(set(by) - set(x[0] for x in BLOCKS)):
            if b not in by:
                continue
            items = sorted(by[b], key=lambda t: (-t[0], t[1]))
            L.append("")
            L.append("### %s (%d종)" % (b, len(items)))
            L.append("".join(ch for _n, ch in items))
    path = out or "_char_stats.md"
    io.open(path, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("-> %s (%s)" % (path, "jp %d종" % len(cj) if jp_only
                           else "jp %d종 / kr %d종" % (len(cj), len(ck))))


def cmd_context(fname=None, kind=None, out=None):
    """씬 전체를 **문맥과 함께** 덤프한다 — 이미 번역된 줄도 싣고 미번역만 표시.

    `dump` 는 미번역 조각만 주므로 앞뒤 대사가 보이지 않는다. VN 번역은 말투·존칭·
    지시대명사가 앞뒤에 걸리므로, 번역할 씬 전체를 순서대로 보여 주는 편이 낫다.
    미번역 줄에 `◆미번역` 표시가 붙는다.

    본문에 제어문자가 낀 조각(`*部屋から…` 처럼)도 **싣는다**. `dump` 는 `{n}` 토큰으로
    감싸지 못해 빼지만, 사람이 보고 옮길 때는 제어문자를 그 자리에 그대로 두면 그만이고
    실제로 그렇게 번역한 40조각이 전부 정상 주입된다. 빼 두면 씬이 조용히 미완성으로
    남으므로 `◆미번역(제어문자)` 로 따로 표시해 준다.
    """
    S = load()
    tgt_ids = {i for i, _b, _t in targets(S, include_done=True)}
    mid_ids = {i for i, r in enumerate(S)
               if classify(r.get("jp")) == "text" and has_mid_ctrl(r["jp"])}
    tgt_ids |= mid_ids
    kinds = set(kind.split(",")) if kind else None
    per = {}
    for i, r in enumerate(S):
        if fname and r.get("file") != fname:
            continue
        if kinds and r.get("kind") not in kinds:
            continue
        per.setdefault(r.get("file"), []).append(i)
    if not per:
        raise SystemExit("해당하는 조각이 없다 (--file 이름을 확인하라)")
    out = out or "_tl_context.txt"
    f = io.open(out, "w", encoding="utf-8")
    n_scene = n_todo = 0
    for scn in sorted(per):
        ids = sorted(per[scn], key=lambda i: S[i].get("off", 0))
        todo = [i for i in ids if i in tgt_ids and not (S[i].get("kr") or "").strip()]
        if not todo:
            continue                        # 다 번역된 씬은 싣지 않는다
        n_scene += 1; n_todo += len(todo)
        f.write("===== %s  (미번역 %d)\n" % (scn, len(todo)))
        for i in ids:
            r = S[i]
            kr = (r.get("kr") or "").strip()
            if i not in tgt_ids and not kr:
                continue                    # 오프너·빈 조각은 문맥에서도 뺀다
            mark = ""
            if i in tgt_ids and not kr:
                mark = "  ◆미번역(제어문자 그대로 유지)" if i in mid_ids else "  ◆미번역"
            f.write("%d [%s|%s]%s\n  jp: %s\n  kr: %s\n"
                    % (i, r.get("kind", ""), r.get("speaker", "") or "", mark,
                       r.get("jp") or "", kr))
        f.write("\n")
    f.close()
    print("씬 %d개 / 미번역 %d조각 -> %s" % (n_scene, n_todo, out))
    print("  번역한 줄을 `id<TAB>번역` TSV 로 만들어 `ujyu filter apply` 하면 된다.")


def cmd_fix(dry_run=True):
    """이미 반영된 번역의 **기계적으로 고칠 수 있는 표기 결함**을 손본다.

    지금 고치는 것: 나레이션 선두 들여쓰기. 원문이 전각공백으로 시작하는데 번역문이
    아닌 경우(=들여쓰기가 빠져 화면에서 왼쪽에 붙는다), 반대로 원문에 없는데 번역문에
    붙은 경우. 둘 다 원문에 맞춘다 — STYLE.md 의 규칙이 "원문을 따른다" 이기 때문이다.

    뜻을 바꾸는 수정은 하지 않는다. 사람이 판단할 것은 `filter check` 로 보고만 한다.
    """
    S = load()
    add = rm = 0
    for r in S:
        if r.get("kind") != "narr" or classify(r.get("jp")) != "text":
            continue
        kr = r.get("kr") or ""
        if not kr.strip():
            continue
        jp = r["jp"]
        if jp.startswith("　") and not kr.startswith("　"):
            if not dry_run:
                r["kr"] = "　" + kr
            add += 1
        elif not jp.startswith("　") and kr.startswith("　"):
            if not dry_run:
                r["kr"] = kr.lstrip("　")
            rm += 1
    if dry_run:
        print("나레이션 들여쓰기: 넣을 것 %d건 / 뺄 것 %d건 (--write 로 반영)" % (add, rm))
        return
    save(S)
    print("나레이션 들여쓰기 보정: 넣음 %d건 / 뺌 %d건" % (add, rm))


def cmd_propagate(dry_run=False):
    """같은 원문(jp)에 이미 있는 번역(kr)을 전파한다.

    VN 은 분기마다 같은 대사가 반복되므로, 한 번 번역하면 나머지가 따라온다.
    **이미 kr 이 있는 조각은 건드리지 않는다.** 같은 jp 에 서로 다른 kr 이 있으면
    (분기별로 다르게 번역한 경우) 애매하므로 전파하지 않고 보고만 한다.
    """
    S = load()
    # skip_mid=False: 본문 중간에 제어문자가 있는 조각도 전파 대상이다. 덤프/수동
    # 번역에서는 그런 조각을 빼지만, 전파는 **jp 가 완전히 같은 것에 kr 을 그대로
    # 복사**하는 것뿐이라 제어문자가 있어도 안전하다. 빼 두면 회상 사본(`sc_*`)의
    # `…」*t` 계열이 원본과 글자까지 같은데도 영원히 안 채워진다.
    ids = {i for i, _b, _t in targets(S, include_done=True, skip_mid=False)}
    by = {}                                  # jp -> {kr, ...}
    for i in ids:
        kr = (S[i].get("kr") or "").strip()
        if kr:
            by.setdefault(S[i]["jp"], set()).add(kr)
    ambiguous = {jp for jp, ks in by.items() if len(ks) > 1}
    n = 0
    for i in ids:
        r = S[i]
        if (r.get("kr") or "").strip():
            continue
        ks = by.get(r["jp"])
        if not ks or r["jp"] in ambiguous:
            continue
        if not dry_run:
            r["kr"] = next(iter(ks))
        n += 1
    if dry_run:
        print("전파 가능: %d조각 (dry-run, 쓰지 않았다)" % n)
    else:
        save(S)
        print("전파: %d조각 -> %s" % (n, STRINGS))
    if ambiguous:
        print("같은 원문에 번역이 갈려 전파하지 않은 것: %d종" % len(ambiguous))


def cmd_dump(start=0, n=200, fname=None, kind=None):
    """번역기에 넣을 최소 포맷: `id<TAB>화자<TAB>본문` 한 줄씩.

      dump [start] [n]          전체 대상에서 start 부터 n 개
      dump --file <name.scn>    그 파일의 미번역 대상 전부 (스토리 순 작업용)
      dump --file <name> --kind dlg,narr   종류 한정
    """
    S = load()
    if fname:
        kinds = set(kind.split(",")) if kind else None
        tgt = [t for t in targets(S)
               if S[t[0]]["file"] == fname and (kinds is None or S[t[0]].get("kind") in kinds)]
        chunk = tgt
    else:
        tgt = targets(S)
        chunk = tgt[start:start + n]
    path = "_tl_in.tsv"
    out = io.open(path, "w", encoding="utf-8", newline="")
    import inject_text as cp949
    for i, _body, _tail in chunk:
        # 화자를 같이 실어야 상대별 말투를 맞출 수 있다 (없으면 나레이션)
        sp = (S[i].get("speaker") or "").strip()
        # 커맨드는 {n} 토큰으로 나간다. 번역자는 토큰을 그대로 두면 된다.
        tpl, _cmds = cp949.to_template(S[i]["jp"])
        out.write("%d\t%s\t%s\n" % (i, sp, tpl.replace("\t", " ")))
    out.close()
    print("%d조각 -> _tl_in.tsv (전체대상 %d)" % (len(chunk), len(tgt)))


def cmd_review(out=None):
    """검수용 TSV 를 뽑는다 — `id / 시나리오 / 화자 / JP / KR / 검토`.

    스프레드시트로 열어 JP↔KR 을 나란히 본다. 번역문이 통째로 들어가는 파일이라
    `config.REVIEW_TSV = None` 이면 만들지 않는다(공개 리포에 올릴 수 없는 경우).

    `검토` 열에는 `config.REVIEW_MARK` 가 붙은 줄에 표시가 선다 — 번역자가
    판단이 갈린다고 남긴 것이라 그 줄부터 보면 된다.
    """
    path = out or getattr(C, "REVIEW_TSV", None)
    if not path:
        print("config.REVIEW_TSV 가 비어 있다 — 검수 TSV 를 뽑지 않는다.")
        print("  뽑으려면 config 에 경로를 넣거나 `-o 경로` 를 주면 된다.")
        return 1
    TAB, NL = chr(9), chr(10)
    S = load()
    rows = [TAB.join(["id", "시나리오", "화자", "JP", "KR", "검토"])]
    n = marked = 0
    for i, r in enumerate(S):
        if classify(r.get("jp")) != "text":
            continue
        jp = (r.get("jp") or "").replace(TAB, " ")
        kr = (r.get("kr") or "").replace(TAB, " ")
        flag = "검토" if (REVIEW_MARK and REVIEW_MARK in kr) else ""
        if flag:
            marked += 1
        rows.append(TAB.join([str(i), r["file"], r.get("speaker") or "", jp, kr, flag]))
        n += 1
    io.open(path, "w", encoding="utf-8-sig", newline="").write(NL.join(rows) + NL)
    print("-> %s (%d행%s)"
          % (path, n, ", 검토 표시 %d행" % marked if marked else ""))


def cmd_apply(path, skip_done=False):
    """`id<TAB>번역본문` TSV를 kr에 반영. **원문 꼬리 제어코드를 자동으로 다시 붙인다.**

    `skip_done` 이면 이미 kr 이 있는 id 는 건드리지 않는다. 병렬 번역처럼 덤프와
    반영 사이에 `propagate` 가 끼면 같은 원문이 이미 채워져 있을 수 있는데,
    거기에 다른 문장을 덮어쓰면 **같은 원문의 번역이 갈린다**.
    """
    S = load()
    n = skip = done = 0
    for line in io.open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line.strip() or "\t" not in line:
            if line.strip():
                skip += 1
            continue
        sid, kr = line.split("\t", 1)
        try:
            i = int(sid)
        except ValueError:
            skip += 1
            continue
        kr = kr.strip()
        # 검수 표시: config.REVIEW_MARK 가 비어 있으면 이 프로젝트는 표시를 안 쓴다는
        # 뜻이므로, 번역자가 붙여 보냈더라도 떼고 넣는다(게임 화면에 그대로 보인다).
        if not REVIEW_MARK:
            for m in _MARK_CANDIDATES:
                kr = kr.replace(m, "")
            kr = kr.strip()
        if not kr:
            continue
        if skip_done and (S[i].get("kr") or "").strip():
            done += 1
            continue
        # 커맨드는 kr 안의 {n} 토큰이 담당한다 (cp949.encode_piece 가 복원).
        # 토큰이 없는 구 번역문도 하위호환 경로로 처리된다.
        S[i]["kr"] = kr
        n += 1
    save(S)
    print("반영 %d조각 (스킵 %d%s)"
          % (n, skip, ", 기번역 유지 %d" % done if done else ""))


# ─────────────────────────────────────────── 번역 TSV 검사
BAD_ASCII = re.compile(r"[!?~.,()\[\]<>;:\"']")     # 엔진이 커맨드로 먹는 반각 기호
OPENERS = ("「", "（", "『")
TAILS = ("」%", "』%", "）", "」", "』")


def cmd_check(paths):
    """반영 **전에** 번역 TSV 를 전수 검사한다 (strings.json 은 건드리지 않는다).

    `inject check` 는 이미 반영된 것만 본다. 병렬 번역처럼 여러 곳에서 TSV 가
    올라올 때는 넣기 전에 걸러야 한다. 실제 주입 인코더를 그대로 태우고, 그 위에
    표기 규칙(STYLE.md)을 검사한다.
    """
    import inject_text as cp949
    S = load()
    bad = []; ok = 0; seen = {}
    for path in paths:
        if not os.path.exists(path):
            print("없음: %s" % path); continue
        n = 0
        for ln, line in enumerate(io.open(path, encoding="utf-8"), 1):
            t = line.rstrip("\n").rstrip("\r")
            if not t.strip():
                continue

            def err(why, s=""):
                bad.append((os.path.basename(path), ln, why, s))

            if "\t" not in t:
                err("탭 없음", t[:40]); continue
            sid, kr = t.split("\t", 1)
            if "\t" in kr:
                err("번역문에 탭", kr[:40]); continue
            try:
                i = int(sid)
            except ValueError:
                err("id 아님", sid[:20]); continue
            if not (0 <= i < len(S)):
                err("id 범위 밖", sid); continue
            if i in seen:
                err("id 중복 (%s:%d)" % seen[i], sid); continue
            seen[i] = (os.path.basename(path), ln)
            r = S[i]; jp = r.get("jp") or ""
            if classify(jp) != "text":
                err("번역 대상이 아님(%s)" % classify(jp), jp[:20])
            try:
                cp949.encode_piece(kr, jp)
            except Exception as e:
                err("인코딩 실패: %s" % e, kr[:30]); continue
            # 반각 ASCII 는 **허용**한다 — 숫자·마침표·쉼표·하이픈 등은 ASCII 로 쓰고
            # 주입기(`normalize`)가 전각으로 바꾼다. 번역자가 전각을 직접 치지 않아도
            # 되게 하는 쪽이 오타가 적다. 대신 커맨드 바이트가 유실되지 않는지는
            # 아래 `verify` 가 바이트 단위로 본다.
            # 괄호류(「」『』【】（））는 원문 형태를 그대로 쓴다 — 아래 여는 괄호·
            # 종결부 검사가 그걸 본다.
            if kr[:1] in OPENERS and jp[:1] not in OPENERS:
                err("없던 여는 괄호로 시작", kr[:20])
            for suf in TAILS:
                if jp.rstrip().endswith(suf):
                    if not kr.rstrip().endswith(suf):
                        err("종결부 불일치 (원문 %r)" % suf, kr[-14:])
                    break
            if r.get("kind") == "narr":
                if jp.startswith("　") != kr.startswith("　"):
                    err("narr 선두 전각공백 불일치", kr[:20])
            for e in cp949.verify(jp, kr, r.get("kind")):
                err(e, kr[:30])
            n += 1; ok += 1
        print("  %-30s %5d행" % (os.path.basename(path), n))
    print("\n통과 %d / 문제 %d" % (ok, len(bad)))
    for f, ln, why, s in bad[:60]:
        print("  %s:%d  %s  %r" % (f, ln, why, s))
    if len(bad) > 60:
        print("  … 외 %d건" % (len(bad) - 60))
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(
        prog="ujyu filter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="번역 대상 선별 + 번역 덤프/반영",
        epilog="예:\n"
               "  ujyu filter dump 0 200            # 앞에서부터 200조각 덤프\n"
               "  ujyu filter dump --file 01.scn    # 그 파일의 미번역 대상 전부\n")
    sub = ap.add_subparsers(dest="cmd", metavar="<명령>")

    sub.add_parser("stats", help="분류 통계 (_filter_stats.txt)",
                   description="조각 분류·남은 번역 분량 통계를 _filter_stats.txt 로 쓴다")

    p_dump = sub.add_parser("dump", help="번역용 최소 포맷 덤프 (_tl_in.tsv)",
                            description="번역기에 넣을 `id<TAB>화자<TAB>본문` TSV 를 만든다")
    p_dump.add_argument("start", nargs="?", type=int, default=0,
                        help="전체 대상에서 시작 인덱스 (기본: 0)")
    p_dump.add_argument("n", nargs="?", type=int, default=200,
                        help="덤프할 조각 수 (기본: 200)")
    p_dump.add_argument("--file", dest="fname", default=None,
                        help="이 .scn 파일의 미번역 대상 전부 (지정 시 start/n 무시)")
    p_dump.add_argument("--kind", default=None,
                        help="--file 과 함께: 종류 한정, 쉼표 구분 (예: dlg,narr)")

    p_ch = sub.add_parser("chars", help="문자 영역별 통계 + 사용 문자 목록 (_char_stats.md)",
                          description="원문·번역문에 쓰인 문자를 영역별로 세고 목록을 낸다. "
                                      "폰트 글리프 집합·CP949 인코딩 가능 여부 확인용")
    p_ch.add_argument("--kind", default=None,
                      help="종류 한정, 쉼표 구분 (예: dlg,narr). 생략하면 전체")
    p_ch.add_argument("-o", "--out", default=None,
                      help="출력 경로 (기본: _char_stats.md)")
    p_rv = sub.add_parser("review", help="검수용 TSV (id/시나리오/화자/JP/KR/검토)")
    p_rv.add_argument("-o", "--out", default=None,
                      help="출력 경로 (기본: config.REVIEW_TSV)")

    p_ch.add_argument("--jp-only", action="store_true",
                      help="번역문(kr) 통계·문자 목록을 빼고 원문만 낸다")

    p_ctx = sub.add_parser("context", help="씬 전체를 문맥과 함께 덤프 (_tl_context.txt)",
                           description="이미 번역된 줄도 실어 앞뒤 문맥을 보여 준다. "
                                       "미번역 줄에 표시가 붙는다")
    p_ctx.add_argument("--file", dest="fname", default=None,
                       help="이 .scn 만 (생략하면 미번역이 남은 씬 전부)")
    p_ctx.add_argument("--kind", default=None,
                       help="종류 한정, 쉼표 구분 (예: dlg,narr)")
    p_ctx.add_argument("-o", "--out", default=None,
                       help="출력 경로 (기본: _tl_context.txt)")

    p_fix = sub.add_parser("fix", help="기반영 번역의 기계적 표기 결함 보정",
                           description="나레이션 선두 들여쓰기를 원문에 맞춘다. "
                                       "기본은 보고만 하고, --write 라야 쓴다")
    p_fix.add_argument("--write", action="store_true", help="실제로 반영한다")

    p_prop = sub.add_parser("propagate", help="같은 원문에 기번역 전파",
                            description="같은 jp 에 이미 있는 kr 을 미번역 조각에 전파한다. "
                                        "번역이 갈리는 원문은 전파하지 않는다")
    p_prop.add_argument("--dry-run", action="store_true",
                        help="몇 개가 전파될지만 보고, 쓰지 않는다")

    p_chk = sub.add_parser("check", help="반영 전 번역 TSV 전수 검사",
                           description="실제 주입 인코더 + 표기 규칙으로 번역 TSV 를 검사한다. "
                                       "strings.json 은 건드리지 않는다")
    p_chk.add_argument("tsv", nargs="+", help="검사할 번역 TSV 들")

    p_apply = sub.add_parser("apply", help="번역 결과(id<TAB>번역) 를 strings.json 에 반영",
                             description="번역 TSV 를 strings.json 의 kr 에 반영한다")
    p_apply.add_argument("tsv", help="번역 결과 TSV (`id<TAB>번역본문` 한 줄씩)")
    p_apply.add_argument("--skip-done", action="store_true",
                         help="이미 번역된 id 는 건드리지 않는다 (전파본과 충돌 방지)")

    a = ap.parse_args()
    if not a.cmd:
        ap.print_help()
        return
    if a.cmd == "stats":
        cmd_stats()
    elif a.cmd == "dump":
        cmd_dump(a.start, a.n, a.fname, a.kind)
    elif a.cmd == "chars":
        cmd_chars(a.out, a.kind.split(",") if a.kind else None, a.jp_only)
    elif a.cmd == "review":
        return cmd_review(a.out)
    elif a.cmd == "context":
        cmd_context(a.fname, a.kind, a.out)
    elif a.cmd == "fix":
        cmd_fix(not a.write)
    elif a.cmd == "propagate":
        cmd_propagate(a.dry_run)
    elif a.cmd == "check":
        return cmd_check(a.tsv)
    elif a.cmd == "apply":
        cmd_apply(a.tsv, a.skip_done)


if __name__ == "__main__":
    main()
