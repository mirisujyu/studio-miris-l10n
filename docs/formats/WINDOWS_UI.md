# Windows UI 지역화 (PE 리소스 · 인라인 ANSI)

인게임 텍스트(엔진이 직접 그리는 것, [TEXT_RENDER.md](TEXT_RENDER.md))와 **별개로**,
**OS 가 그리는 UI** — 우클릭 메뉴, 설정 다이얼로그, 버전 정보, MessageBox — 를
한글 패치하는 구조. 도구: [ujyu exe ui](../../ujyu exe ui), 번역표:
`config.UI_STRINGS`(JSON, 양식 [samples/ui_strings.sample.json](../../samples/ui_strings.sample.json)).
검증 타이틀: 神無ノ鳥(2002).

## 1. 요소별 저장 위치와 인코딩

| 요소 | 저장 형식 | 인코딩 | 처리 방식 |
|---|---|---|---|
| MENU | PE 리소스 (클래식 MENUTEMPLATE, type 4) | **UTF-16** | `UpdateResourceW` 로 교체 |
| DIALOG | PE 리소스 (클래식 DLGTEMPLATE, type 5) | UTF-16 | 〃 + 다이얼로그 폰트 교체 |
| STRINGTABLE | PE 리소스 (길이접두 블록 16개, type 6) | UTF-16 | 〃 |
| MessageBox 등 인라인 문자열 | `.data`/`.rdata` 코드 인라인 | **ANSI(→CP949)** | exe 바이트 **제자리 치환** |

핵심 구분: **리소스는 UTF-16 이라 시스템 코드페이지와 무관하게 정상 표시**되고,
ANSI API(`MessageBoxA` 등) 경로만 CP949 바이트가 필요하다(한국어 Windows, ACP=949).

## 2. 리소스 교체 (UpdateResourceW)

`pefile` 로 기존 리소스를 읽어 각 템플릿을 파싱·재작성한 뒤
`BeginUpdateResourceW` → `UpdateResourceW` → `EndUpdateResourceW` 로 써넣는다.

- **MENU**: `flags`(POPUP=`0x10`, 마지막=`0x80`) 트리를 재귀 순회하며 라벨을 교체한다.
  라벨의 `\t` 뒤는 단축키 표시이므로 **앞부분만** 번역한다.
- **DIALOG**: 헤더(style/exstyle/개수/좌표) → menu/class → 타이틀 → (`DS_SETFONT`=`0x40`
  이면) 폰트 크기+face → 컨트롤 배열. 컨트롤은 **4바이트 정렬**이며, class/텍스트 필드는
  `0xFFFF`+ordinal 이거나 널종단 WCHAR 문자열이다. 컨트롤 **class 명은 그대로 두고**
  캡션만 번역한다.
- **STRINGTABLE**: `<len:2><WCHAR×len>` 이 16개 연속.
- **다이얼로그 폰트**를 한글 폰트(예 `맑은 고딕`)로 바꿔야 한글 글리프가 보장된다
  (`config.DLGFONT`).

주의:
- **메뉴 리소스는 numeric ID 가 아니라 문자열 이름**(`MYMENU`·`DBGMENU` 등)으로 참조될 수
  있다 → `UpdateResourceW` 에 `LPCWSTR` 로 넘긴다.
- **DLGTEMPLATE**와 **DLGTEMPLATEEX**(선두가 `01 00 FF FF`)는 레이아웃이 다르다.
  EX 형식은 보이는 텍스트가 없는 경우가 많아 건너뛰어도 무방하다.
- 번역표 매칭은 정확일치 → `strip()` 일치(앞뒤 공백 보존) → 원문 유지 순으로 한다.

## 3. 인라인 ANSI 문자열 제자리 치환

MessageBox 문구 등은 리소스가 아니라 코드에 박힌 ANSI 문자열이다.
원문(SJIS)을 메모리 매핑 이미지에서 찾아 → VA→파일오프셋 변환 → **CP949 바이트로 덮고
남는 자리를 널로 채운다**.

- 문자열 길이가 **원문 바이트 수를 넘으면 안 된다** (제자리 치환이라 확장 불가).
  넘치면 그 항목은 건너뛰고 경고한다.
- 널 종단을 반드시 유지한다(치환 바이트 뒤에 `\0` + 나머지 `\0` 패딩).
- 원문(일본어)을 기준으로 찾으므로 **무패치 원본 exe 에서 바로** 적용된다.

## 4. 번역 대상 (神無ノ鳥 실측 범위)

- **메뉴**: 파일(세이브/로드/타이틀로/종료), 표시(창모드/전체화면/화면전환),
  조작(자동모드/다음 선택지/텍스트창/지난 로그), 설정(시스템·문자·소리/버전정보),
  디버그(시나리오 실행/변수·플래그/읽음 체크/메모리).
- **다이얼로그**: 시스템·소리·문자 설정, 이름 설정, 버전 정보, 예/아니오 확인,
  디버그(시나리오 호출·변수 참조·읽음 설정).
- **MessageBox**: 이름 입력 검증 오류(미입력·너무 김·반각 사용) 등.
- 누락을 막으려면 **바이너리 전수 SJIS 스캔**으로 사용자 노출 문자열을 훑는다
  (폰트 GDI 이름처럼 번역하면 안 되는 것은 제외).

## 5. 한계·주의

- **타이틀바**(`SetWindowTextA`)는 인라인 문자열이 아니라 동적으로 구성되는 경우가 있다
  (버전 문자열 등 참조) — 별도 확인이 필요하다. 창 제목 자체는 보통 `common.csv` 의
  설정값이다([COMMON_CSV.md](COMMON_CSV.md)).
- CP949 인라인 치환은 **한국어 Windows(ACP=949) 전제**다. 일본어 로케일로 강제 실행하면
  MessageBox 가 깨질 수 있다(UTF-16 리소스 UI 는 무관).
- 이름 입력 기능은 원작이 "전각 N글자, 반각 금지" 같은 일본어 전용 제약을 갖는 경우가
  있다 — 한국어 입력 UX 는 별도 검토 대상이다.
- 리소스 교체는 exe 를 다시 쓰므로 **다른 exe 패치([TEXT_RENDER.md](TEXT_RENDER.md) §8)와
  순서를 정해 두어야** 한다. `patch_exe.py` 가 UI → 엔진 패치 → 코드 케이브 순으로 돈다.
