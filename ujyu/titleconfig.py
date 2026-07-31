# -*- coding: utf-8 -*-
"""타이틀 설정(config.py) 로더 — 한글 패치 도구 공용.

우선순위: $MIRIS_CONFIG_DIR → 현재 작업 디렉토리. 타이틀 리포 **루트에서 실행**하면
그 루트의 config.py 를 집는다. 새 타이틀은 engine/samples/config.py 템플릿을 리포
루트로 복사해 채운다 (`ujyu init` — docs/BOOTSTRAP.md 1단계).

사용:  from ujyu.titleconfig import config as C
"""
import os, sys

sys.path.insert(0, os.environ.get("MIRIS_CONFIG_DIR", os.getcwd()))
try:
    import config
except ImportError:
    raise SystemExit(
        "config.py 를 찾을 수 없습니다. 타이틀 리포 루트에서 실행하거나 "
        "MIRIS_CONFIG_DIR=<config.py 있는 폴더> 를 지정하세요. "
        "(새 타이틀: `ujyu init` 으로 만든다 — engine/docs/BOOTSTRAP.md 1단계)")
