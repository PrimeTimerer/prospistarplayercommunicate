# StarPlayer Community Simulator · StarModeFeed

[![검증 및 Windows 빌드](https://github.com/PrimeTimerer/prospistarplayercommunicate/actions/workflows/verify.yml/badge.svg)](https://github.com/PrimeTimerer/prospistarplayercommunicate/actions/workflows/verify.yml)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

프로야구 스피리츠 스타플레이어의 세이브를 **읽기 전용**으로 분석하고, 기록을 바탕으로
가상의 기사·커뮤니티·SNS·대화형 서사를 만드는 비공식 Windows 앱입니다.

**2.7.10-preview · MIT 오픈소스 공개본.** 기존 2.7.9 엔진을 기반으로 개인 자료를
제거하고 합성 예제와 독립적인 개발 환경을 추가했습니다. 지속적인 업데이트나
모든 게임 버전의 호환성을 보장하지 않는 프리뷰입니다. 포크·수정·PR을 환영합니다.

## 할 수 있는 것

- 세이브 무결성 확인, 선수별 세계선 분리, 시즌·통산 기록 및 변화 확인
- 내장 엔진으로 가상의 기사·게시판·SNS와 반응 생성
- 원하는 경우 로컬 LLM 또는 Gemini API로 기사·커뮤니티·서사 작성
- 대화와 이미지 힌트로 이야기를 이어가는 서사 작업실, 기억·인물·행동 관리
- 기사·커뮤니티·서사를 연결하는 일간·월간·연간 종합 스토리
- 날짜별 보관, 기존 결과 유지, 공통 작업 콘솔과 선택형 알림

실제 SNS에 게시하거나 실제 댓글을 수집하는 프로그램이 아닙니다. 출력은 창작물입니다.
세이브 원본이나 게임 프로세스를 수정하지 않으며 Cheat Engine·다른 툴이 필요하지 않습니다.

## 소스에서 실행하기

Windows 10/11 64비트, Python **3.14** 기준으로 검증합니다. Microsoft Edge/WebView2
런타임을 사용하며, 데스크톱 셸을 사용할 수 없으면 Edge/브라우저 경로로 대체됩니다.

```powershell
git clone https://github.com/PrimeTimerer/prospistarplayercommunicate.git
cd prospistarplayercommunicate
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app_native.py
```

PowerShell의 활성화 정책에 막히면 정책을 바꿀 필요 없이
`.\.venv\Scripts\python.exe -m pip install -r requirements.txt`와
`.\.venv\Scripts\python.exe app_native.py`를 사용하세요.

1. 설정에서 **본인의** `StarPlayer.dat`을 선택합니다.
2. 선수와 날짜를 확인한 뒤 **최신 세이브 확인**을 누릅니다.
3. 커뮤니티·기사·서사 모드에서 필요한 작업을 선택합니다.
4. AI 없이 시작할 수 있습니다. 로컬 모델은 명시적으로 켜고, Gemini는 별도 동의와
   본인의 API 키를 설정해야 합니다. 저장소에 API 키를 넣지 마세요.

이 저장소에는 세이브, 기존 사용자의 설정·기억·대화, API 키, 모델 가중치가 없습니다.
샘플 메모와 테스트 자료는 가상 예제입니다.

## Windows 실행파일 만들기

```powershell
python -m pip install -r requirements-dev.txt
python -m compileall -q .
python run_tests.py
powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
```

결과: `dist-v2/StarModeFeed.exe`. 기존 앱을 정상 종료하고 사용자 데이터와 이전 실행파일을
백업한 뒤 교체하세요. **설정·세이브·원장을 삭제해서 업데이트하지 마세요.**
GitHub Actions도 테스트와 Windows 패키지 검증을 수행합니다. 이번 공개는 소스 코드
공개이며, 실행파일은 위 명령으로 직접 빌드합니다. 정식 서명된 설치 프로그램은 아닙니다.
실행파일을 재배포할 때는 Python과 번들된 의존성의 라이선스·고지문도 함께 제공하세요.

## AI와 개인정보

- 내장 엔진에는 API 키가 필요 없습니다. 로컬 LLM은 자동 실행하지 않습니다.
- Gemini는 선택·동의된 텍스트를 외부 서비스로 전송합니다. API 요금과 제한은
  본인의 Google 프로젝트 설정에 따라 달라집니다. 유료 설정을 자동으로 켜지 않습니다.
- 일반 생성은 세이브 원본을 보내지 않습니다. 이미지 전송에는 별도 확인이 필요합니다.
- 생성된 소설이나 이미지 힌트가 검증된 경기 기록을 덮어쓰지 않습니다.
- 모델 실패나 검증 실패 시 기존 결과를 유지합니다. 모든 생성이 항상 성공하거나
  특정 시간 안에 끝난다고 보장하지 않습니다.
- 사용자 상태는 기본적으로 `%LOCALAPPDATA%\StarModeFeed` 아래에 저장됩니다.
  다른 설정 경로를 지정했다면 그 경로도 함께 백업해야 합니다.

## 함께 개발하기

[기여 가이드](CONTRIBUTING.md) · [구조와 검증 경계](docs/ARCHITECTURE.md) ·
[공개본 개인정보 정책](docs/PRIVACY.md) · [보안 제보](SECURITY.md) ·
[변경 사항](CHANGELOG.md) · [검증 범위](docs/VERIFICATION.md) ·
[외부 구성요소](THIRD_PARTY_NOTICES.md)

문제 제보에는 버전, 모드, 재현 순서와 **개인정보를 지운** 오류 메시지만 적어 주세요.
세이브·API 키·원본 로그·개인 대화·스크린샷 원본은 공개 이슈에 첨부하지 마세요.

## 라이선스

[MIT License](LICENSE). KONAMI, NPB 및 각 구단·플랫폼과 무관한 비공식 프로젝트입니다.
게임·구단·플랫폼의 명칭 및 외부 자료의 권리는 해당 권리자에게 있습니다.
이 라이선스가 게임 자체나 외부 게시물의 권리까지 허용하는 것은 아닙니다.
