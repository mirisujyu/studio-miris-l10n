# -*- coding: utf-8 -*-
"""common.csv — 아카이브 안 엔진 설정 파일.

포맷: 한 줄 = `<type>,<name>[,<value>...]` (CRLF). type ∈ object/bool/int/string/
file/flag/… (VNEG 심볼 타입과 동일 계열). 예:
    string,title,神無ノ鳥
    int,version,140
    file,save,...

**모든 시나리오 아카이브(scenario.axr/ax2/ax3/ax4)에 각각** 들어 있고 내용이 다를 수 있다
(번호 큰 아카이브가 우선). 한 아카이브 것을 다른 아카이브에 주입하면 그 아카이브 고유
정의(예: ax4의 int,version)가 사라지므로 **각 아카이브의 것을 제자리 편집**해야 한다.

바이트 단위로 다룬다(값이 CP932/CP949 혼재). 이 모듈은 순수 파서 — 아카이브 입출력은
miris.axr, 타이틀별 설정값은 상위 도구가 주입한다.
"""
import re


def _line_pat(typ, name):
    return re.compile((r"%s,%s(,[^\r\n]*)?(?=\r?\n|$)"
                       % (re.escape(typ), re.escape(name))).encode())


def fields(buf):
    """common.csv 바이트 → [(type, name, rest_bytes)] (rest = 첫 콤마 뒤 원문)."""
    out = []
    for line in bytes(buf).splitlines():
        if not line:
            continue
        parts = line.split(b",", 2)
        typ = parts[0].decode("latin1")
        name = parts[1].decode("latin1") if len(parts) > 1 else ""
        rest = parts[2] if len(parts) > 2 else b""
        out.append((typ, name, rest))
    return out


def get_field(buf, typ, name):
    """`<typ>,<name>,<value>` 의 value 바이트 반환(없으면 None; 값 없는 항목은 b'')."""
    m = _line_pat(typ, name).search(bytes(buf))
    if not m:
        return None
    return (m.group(1) or b"")[1:]


def set_field(buf, typ, name, value):
    """`<typ>,<name>` 줄의 값을 value(bytes)로 교체. 항목 없으면 원본 그대로 반환.

    구조/길이 보존을 위해 **해당 줄만** 치환한다(다른 정의·순서 불변)."""
    b = bytes(buf)
    m = _line_pat(typ, name).search(b)
    if not m:
        return b
    new = typ.encode() + b"," + name.encode() + b"," + value
    if b[m.start():m.end()] == new:
        return b
    return b[:m.start()] + new + b[m.end():]


def has_field(buf, typ, name):
    return _line_pat(typ, name).search(bytes(buf)) is not None
