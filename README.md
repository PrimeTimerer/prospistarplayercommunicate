# StarPlayer Community Simulator · StarModeFeed

[![검증 및 Windows 빌드](https://github.com/PrimeTimerer/prospistarplayercommunicate/actions/workflows/verify.yml/badge.svg)](https://github.com/PrimeTimerer/prospistarplayercommunicate/actions/workflows/verify.yml)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

프로야구 스피리츠 스타플레이어의 세이브를 **읽기 전용**으로 분석하고, 기록을 바탕으로
가상의 기사·커뮤니티·SNS·대화형 서사를 만드는 비공식 Windows 앱입니다.

**2.7.10-preview · MIT 오픈소스 공개본.** 기존 2.7.9 엔진을 기반으로 개인 자료를
제거하고 합성 예제와 독립적인 개발 환경을 추가했습니다. 지속적인 업데이트나
모든 게임 버전의 호환성을 보장하지 않는 프리뷰입니다. 포크·수정·PR을 환영합니다.

프로스피 스튜디오 제작자가 스튜디오 개발에 집중하기 위해 공개한 프로젝트입니다.
다른 개발자분들, 특히 **소셜 반응·커뮤니티 시뮬레이션을 중심으로 작업하는 분들**께
도움이 되었으면 합니다. 필요한 자료·구조·기능을 참고하거나 MIT 조건에 따라
가져다 개선해 주세요. 함께 개발하기 위한 안내는 아래에 정리했습니다.

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

## Gemini API 키 추가해서 사용하기

현재 앱에서 직접 지원하는 외부 생성 API는 **Google Gemini API**입니다. 다른 회사의
API 키를 Gemini 입력칸에 붙여 넣는 방식으로 연결되지는 않습니다. 소스 코드를 고치거나
별도의 Gemini SDK를 설치할 필요 없이 다음 순서로 설정할 수 있습니다.

1. [Google AI Studio 키 관리](https://aistudio.google.com/app/apikey)에 Google 계정으로
   로그인해 본인 프로젝트의 API 키를 발급받습니다. 프로젝트가 보이지 않거나 키가
   차단되어 있다면 [Google 공식 키 안내](https://ai.google.dev/gemini-api/docs/api-key)를
   확인하세요. 키는 비밀번호처럼 취급하고 게시글·이슈·소스에 올리지 마세요.
2. 앱을 실행하고 오른쪽 위 **설정 → AI 연결**로 들어갑니다.
3. **피드·서사·일반 대화 엔진**에서 **Gemini 우선 · 실패 시 실행 중인 로컬/내장**을
   선택하고, **Gemini 모델** 목록에서 본인 프로젝트가 사용할 수 있는 모델을 고릅니다.
   목록에 있다고 계정의 사용 가능 여부나 무료 제공이 보장되는 것은 아닙니다.
4. **Gemini API 키** 입력칸에 발급받은 키를 붙여 넣습니다. 전송 대상을 읽고 동의하는
   경우에만 **선택한 피드·서사·대화의 검증 요약과 내가 쓴 문장을 Google에 전송하는 데
   동의**를 켭니다. 동의하지 않으면 내장 엔진이나 로컬 LLM을 사용하세요.
5. **연결 시험**을 눌러 선택 모델 접근을 확인합니다. 이 시험은 선수·경기·서사나 세이브
   원본을 보내지 않고 모델 정보만 조회합니다. **새 키는 연결 시험만으로 저장되지 않습니다.**
6. 성공하면 반드시 창 아래 **설정 저장**을 누릅니다. 저장 후 앱이 연결을 다시 확인하고
   정상 연결이면 상단에 **Gemini API 켜짐**을 표시합니다. 저장된 키가 입력칸에 다시
   보이지 않는 것은 정상이며, 이 Windows 사용자 계정으로 암호화해 보관합니다.
7. 커뮤니티·기사·서사에서 원하는 생성 버튼을 사용합니다. 대화형 서사에서는 내용을
   입력하고 **Gemini로 이어 쓰기**를 누르세요. 해당 장면·기억·선택 이미지에 대한
   추가 전송 동의가 표시되면 전송할 내용을 확인한 뒤 진행합니다.

연결 시험 성공은 모델 접근 확인이지 긴 서사의 생성 성공 보장은 아닙니다.
실제 생성 진행과 오류는 **작업 콘솔**, 호출·토큰 참고치는 설정의 **Gemini API 앱 집계**에서
확인하세요. 한 번의 생성 작업도 묶음 작성·재시도 때문에 여러 API 호출을 사용할 수 있습니다.

### 요금·한도와 연결 문제

- 무료 제공 여부·요금은 모델과 프로젝트 설정에 따라 다릅니다. 사용 전에
  [공식 가격표](https://ai.google.dev/gemini-api/docs/pricing)와 본인 프로젝트의 사용량을
  확인하세요. 이 앱이 결제를 자동으로 설정하거나 한도를 올리지는 않습니다.
- `429`처럼 한도 오류가 나면 요청을 연속으로 누르지 말고 작업 콘솔과
  [Google의 실제 한도](https://ai.google.dev/gemini-api/docs/rate-limits)를 확인하세요.
  한도는 키가 아니라 프로젝트 단위이며 요청 수·토큰·일일 한도를 따로 봅니다.
  기사·커뮤니티 작성에서 반복될 때만 **Gemini 동시 작성 수**를 `1개 · 순차 처리` 또는
  `2개 · 요청 분산`으로 낮춰 보세요. 일일 한도 자체를 늘리는 설정은 아닙니다.
- `503`·시간초과는 연결 시험이 성공했어도 발생할 수 있습니다. 자동 재시도 이후에는
  잠시 기다리거나 본인이 사용할 수 있는 다른 모델을 선택하고 다시 연결 시험을 하세요.
  숫자·문맥 검증 실패는 연결 오류와 다르므로 콘솔의 이유를 확인하세요.
  [Google 오류 해결 안내](https://ai.google.dev/gemini-api/docs/troubleshooting)
- 저장 키를 바꾸거나 없애려면 같은 화면에서 새 키를 입력하고 **설정 저장**, 또는
  **저장 키 지우기**를 사용합니다. 환경 변수 `GOOGLE_API_KEY` / `GEMINI_API_KEY`가
  설정된 개발 환경에서는 환경 변수의 키가 저장 키보다 우선합니다(둘 다 있으면
  `GOOGLE_API_KEY` 우선). 앱의 키 삭제 버튼은 환경 변수를 지우지 않습니다.

### 외부 API 없이 로컬 LLM 사용하기

Gemini 키는 필요 없습니다. 호환되는 로컬 서버를 직접 실행하거나, **AI 연결**에서
**로컬 모델 실행 파일**을 지정하고 **로컬 LLM 시작**을 눌러 실행을 확인합니다.
서버 연결 후 로컬 우선 엔진 또는 각 모드의 로컬 작성 버튼을 사용합니다. 경로를
저장하는 것만으로 서버가 켜지지 않으며, 종료 버튼은 이 앱에서 시작한 서버만 대상으로 합니다.

현재 로컬 연결은 `narrate.py`의 `ENDPOINTS`에 있는 localhost OpenAI 호환 서버 경로와
모델 ID를 사용합니다. 아무 서버나 주소·키를 입력해 자동 연결하는 범용 API 설정창은
아닙니다. 다른 서버·모델에 맞추는 개발자는 해당 목록과
[구조·검증 경계](docs/ARCHITECTURE.md)를 확인하세요. 임의의 외부 API를 추가하려면 별도
연동 구현이 필요하며, 세이브 보호와 전송 동의·검증 경계도 함께 유지해야 합니다.

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
