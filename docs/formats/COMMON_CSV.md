# common.csv — 엔진 설정 파일

각 시나리오 아카이브(`scenario.axr` + 패치 증분 `ax2`, `ax3`, … — 개수는 배포판마다 가변) 안에 들어 있는 텍스트 설정 파일.
게임이 창 제목·세이브 경로·엔진 변수 초기값 등을 여기서 읽는다.

## 포맷
- 한 줄 = `<type>,<name>[,<value>...]`, 줄 구분은 **CRLF**.
- `<type>` 은 VNEG 심볼 타입과 같은 계열: `object` `bool` `int` `string` `file` `flag` …
  (전체 타입은 [VNEG.md](VNEG.md) 심볼 op 표 참조).
- 값은 SJIS(CP932) 또는 (한글 패치 후) CP949 바이트가 섞일 수 있으므로 **바이트 단위**로 다룬다.

예:
```
string,title,神無ノ鳥
int,version,140
file,save,...
```

## ⚠️ 아카이브마다 다르다 — 제자리 편집
`common.csv` 는 **모든 시나리오 아카이브에 각각** 들어 있고 **내용이 서로 다르다**. 번호가 큰 아카이브가
우선(override)한다. 예: 칸나기는 최우선 `ax4` 에만 있는 고유 정의(`int,version` 등)가 있다.

> 한 아카이브의 `common.csv` 를 다른 아카이브에 통째로 주입하면, 그 아카이브 고유 정의가
> **사라져** 변수 테이블이 밀리고 미초기화 슬롯을 참조해 크래시한다. 반드시 **각 아카이브의
> 것을 제자리에서** 필요한 필드만 편집한다.

## 다루는 코드
- 파서: [`miris/common_csv.py`](../../miris/common_csv.py) — `fields` / `get_field` / `set_field` / `has_field`.
  값 교체는 **해당 줄만** 치환해 다른 정의·순서·길이를 보존한다.
- 아카이브 입출력은 [`miris/axr.py`](../../miris/axr.py), 타이틀별 설정값은 상위 도구
  ([`ujyu title`](../../ujyu title))가 주입한다.
