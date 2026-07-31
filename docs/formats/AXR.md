# AXRe 아카이브 포맷 (Studio Miris 엔진)

동일 엔진 게임의 리소스 아카이브 `AXRe` 포맷을 실행파일에서 리버싱한 명세.
모든 아카이브(scenario/cg/se/voice/bgm의 `.axr` 및 패치 증분 `.ax2`, `.ax3`, …)가 동일 포맷.

구현: [`miris/axr.py`](../../miris/axr.py) — `load`/`getfile`/`pack` (왕복 byte-perfect 검증됨).
CLI: [`ujyu axr`](../../ujyu axr).

### 분할 아카이브 오버라이드 (패치 증분)
`scenario.axr` 외에 `scenario.ax2`, `ax3`, … 가 있으면 **같은 이름 파일을 덮어쓴다(번호 큰 쪽 우선)**. 증분 개수는 배포판마다 다르다(칸나기는 `ax4`까지). 개정판이라 오프셋이 달라 원문 텍스트로 매칭해 주입한다. `common.csv`처럼 **아카이브마다 내용이 다른** 파일 주의 → [COMMON_CSV.md](COMMON_CSV.md).

## 헤더 (16바이트)
```
"AXRe"(4) + enc_size(4) + blk2(4) + checksum(4)
```
- `magic = LE32("AXRe") = 0x65525841`
- `K2 = ks(ks(magic ^ blk2))`
- `index_size = LE32(enc_size) ^ K2`

## 스트림 암호 1스텝 `ks(S)`
```
S1 = S ^ ((S & 0xFFF) << 17)
K  = ~(((S1 >> 15) | (S1 << 18)) ^ S1)
```
바이트열을 K로 XOR한 뒤, 다음 상태 `S = K + LE32(평문dword)` (평문 피드백, CFB형).

## 인덱스
`index_size` 바이트, CFB형 스트림 암호(key=`index_size`)로 복호화.
- 엔트리 = `off(4, 절대오프셋) + size(4) + name('\0') + 4바이트 정렬 패딩`

## 파일 데이터
각 파일 = `raw XOR keystream_table[i % 1024]`
- `keystream_table` = CFB 암호를 0버퍼(1024B)에 seed=`LE32(enc_size)`로 돌려 생성.

## 체크섬 (h12:15)
```
K3 = ks(K2)                      # A0 → K1 → K2 → K3 연속 ks 3회
H  = h4 ^ XOR_{k=1..7} ((h[4+k] >> k) | (h[4+k] << (8-k)))
h12:15 = K3 ^ (H & 0xff)
```

## ⚠️ 리팩 필수 규칙 (+8 quirk)
헤더의 `index_size` 필드 = **실제 인덱스 바이트 + 8** (항상 +8).
파일 데이터는 `16 + 실제인덱스바이트`에서 시작(마지막 8B는 첫 파일과 겹침, 게임은 무시).
이 +8을 빼먹으면 콘텐츠 키스트림 시드(raw47)와 인덱스 키가 원본과 달라져 게임이 일부 파일 조회에 실패한다(`○○の読み出しに失敗`).

## 시나리오(.scn) = VNEG 바이트코드
상세 포맷·opcode는 [VNEG.md](VNEG.md). 아래는 아카이브 관점의 요약.
- 헤더 `"VNEG"` + 정의 수 + 심볼/리소스 테이블. 한 정의의 `slots`가
  2 이상이면 여러 연속 런타임 심볼 인덱스를 차지한다. 배경·효과음·다음
  시나리오는 이름 심볼로 참조한다.
- 대사는 flow 안의 인라인 Shift-JIS 런이다. `「…」` 같은 표시문자 사이에도
  모드·화자·클릭 대기 opcode가 섞이며, 일반적인 널종료 문자열로 취급하면 안 된다.
- 절대 주소 테이블은 없지만 flow 시작 기준의 **점프/라벨 오프셋 테이블**이 있다.
  길이 불변 치환은 그대로 쓸 수 있고, 자유길이 치환은 원본 JP 엔트리를 기준으로
  반드시 점프테이블을 재매핑한다.
- 전각 SJIS·CP949 모두 2바이트/자라 글자수 보존 치환이 쉽다. 자유길이도
  구조적 추출·주입과 점프테이블 재매핑을 거치면 가능하다.

번역 대상 판별(1바이트 vs 2바이트)은 [VNEG.md](VNEG.md) §4-2 참조.

## 사용법
```
ujyu axr unpack <archive.axr> <out_dir>
ujyu axr repack <in_dir> <archive.axr> [--blk2-from <orig.axr>]
ujyu scn extract <원본아카이브...> -o strings.json   # 시나리오 텍스트 추출 (VNEG 구조적)
```
(또는 라이브러리: `from miris import axr`.)

---
리버싱 근거(칸나기 exe 기준): 아카이브 open `0x428045` · 키스트림 테이블 init `0x428550` · 스트림 암호 `0x4284A0`. 다른 게임/버전에서 포맷이 어긋날 때 디버거로 재검증할 진입점.
