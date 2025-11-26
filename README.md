# 2025wiset-VLM

# 📊 MOS 평가 웹페이지 (Streamlit 기반)
본 웹페이지는 2025여대학원생 공학연구팀제 지원사업 'VLM을 활용한 영상 내 보안 이벤트 상황 설명 기술 개발' 프로젝트를 위해 만들어졌습니다. 

`mos_app.py`는 **JSONL 형식으로 저장된 비디오·텍스트 응답에 대해 MOS(Mean Opinion Score) 점수를 부여**하는 Streamlit 웹 애플리케이션입니다.  
GitHub API와 연동하여 JSONL 파일 목록을 자동으로 불러오고, 문제가 생기면 **URL 직접 입력 / 로컬 업로드**로 폴백할 수 있도록 구현되어 있습니다.  
또한 `mapping.csv`를 통해 **비디오 파일명 → 실제 재생 URL / Google Drive file_id**를 매핑하여 브라우저 안에서 바로 영상을 확인하면서 평가할 수 있습니다.

---

## ✨ 주요 기능

- **GitHub 폴더 자동 스캔**
  - `GITHUB_JSONL_URL`을 입력하면 GitHub API로 폴더 내 `.jsonl` 파일 목록을 가져와 선택할 수 있습니다.
  - GitHub Personal Access Token(`GITHUB_TOKEN`)이 있을 경우 레이트리밋 완화.

- **JSONL 입력 폴백 지원**
  - GitHub 연동 실패 또는 미사용 시:
    - 🔹 JSONL RAW URL 직접 입력  
    - 🔹 JSONL 파일 로컬 업로드  
  - 세 가지 모드 중에서 사이드바에서 선택 가능:
    - `GitHub 폴더 목록`
    - `URL 직접 입력`
    - `로컬 업로드`

- **mapping.csv 기반 비디오 매핑**
  - JSONL 안의 `video`/`video_path`/`path`에서 파일명을 추출.
  - `mapping.csv`의 `name` 컬럼과 매칭하여:
    - `url`이 있으면 그대로 사용
    - `file_id`가 있으면 Google Drive 미리보기/다운로드 링크로 변환
  - `xxx__cv2.mp4` 변환본이 있을 경우 이를 우선 사용.

- **Google Drive 미리보기 지원**
  - `file_id`가 주어진 경우:
    - 페이지 내 `<iframe>`으로 미리보기
    - 새 창 열기 / 다운로드 링크 제공

- **MOS 점수 입력 & 자동 저장**
  - 각 항목마다 1~5점 슬라이더로 점수 입력.
  - 사용자 이름(`User name`)을 기준으로 id + rater 조합으로 점수 관리.
  - 동일 항목을 다시 볼 경우 이전 점수 기본값으로 로드.

- **진행 현황 및 결과 저장**
  - 현재까지 라벨링한 항목을 `pandas.DataFrame`으로 화면에 표시.
  - `mos_scores_{username}.csv` 형식으로 CSV 다운로드 버튼 제공.

---

## ⚙️ 설치 및 실행

> 아래는 일반적인 Python + Streamlit 프로젝트 기준 예시입니다.  
> (Python 버전, 가상환경 구성 등은 본인 환경에 맞게 조정해주세요.)

### 1. 필수 패키지 설치

```bash
pip install streamlit pandas requests
```
코드 상에서 사용하는 주요 라이브러리:
```
	•	streamlit
	•	pandas
	•	requests
```

2. 프로젝트 구조 예시 (설명용)
```
your-project/
├─ mos_app.py
└─ .streamlit/
   └─ secrets.toml      # (선택) GitHub / mapping 설정
```
실제 디렉터리 구조는 사용자 환경에 따라 달라질 수 있습니다.

3. 실행
```
streamlit run mos_app.py
```


⸻

📄 JSONL 입력 형식

각 줄은 하나의 JSON 객체입니다.
코드는 아래 여러 키 중 존재하는 첫 번째 키를 사용하는 형태로 작성되어 있습니다.
	•	ID 관련 (첫 번째로 찾은 키 사용)
	•	id
	•	idx
	•	비디오 경로 관련
	•	video
	•	video_path
	•	path
	•	프롬프트/질문 관련
	•	prompt
	•	instruction
	•	question
	•	모델 응답/캡션 관련
	•	answer
	•	anwser (오타까지 케어)
	•	caption
	•	response
	•	text

예시 (sample.jsonl):
```
{"id": 1, "video": "videos/sample1.mp4", "prompt": "What is happening in the scene?", "answer": "A person is entering the store."}
{"id": 2, "video_path": "videos/sample2.mp4", "question": "Describe the abnormal event.", "response": "A car is driving in the wrong direction."}
```
응답 내용은 문자열이 아니더라도 JSON으로 직렬화하여 표현 가능하도록 normalize_text()에서 처리합니다.

⸻

🗂️ mapping.csv 형식

mapping.csv는 비디오 파일명 → 실제 재생 경로를 매핑하기 위한 파일입니다.

**필수 컬럼**
	•	name
	•	JSONL에서 추출한 파일명과 정확히 일치해야 합니다.
	•	예: sample1__cv2.mp4, sample2.mp4

**선택 컬럼**
	•	url
	•	직접 재생 가능한 비디오 URL (예: S3, 웹 서버 등)
	•	file_id
	•	Google Drive 파일 ID
	•	코드에서 gdrive:<file_id> 형식으로 사용되며,
	•	미리보기: https://drive.google.com/file/d/<file_id>/preview
	•	새 창 열기: https://drive.google.com/file/d/<file_id>/view?usp=sharing
	•	다운로드: https://drive.google.com/uc?export=download&id=<file_id>
	•	type
	•	선택 값, "video"일 때만 필터링하여 사용
	•	비디오 외 리소스를 같은 CSV에 둘 때 구분용으로 사용 가능

**매칭 우선순위**
	1.	JSONL의 비디오 경로에서 파일명만 추출 (ex. .../foo/bar/sample1.mp4 → sample1.mp4)
	2.	stem__cv2.mp4를 우선 탐색
	•	예: sample1.mp4 → sample1__cv2.mp4가 있으면 이것부터 사용
	3.	없다면 원래 파일명(sample1.mp4)으로 검색
	4.	매칭된 행에서:
	•	url이 있으면 → 그 URL 사용
	•	file_id가 있으면 → Google Drive 프리뷰/다운로드 사용
	5.	아무것도 매칭되지 않으면 에러 메시지와 함께 가이드를 출력

예시 mapping.csv
```
name,type,url,file_id
sample1__cv2.mp4,video,https://example.com/videos/sample1_cv2.mp4,
sample2.mp4,video,,1AbCdEfGhIjKlMnOpQrStUvWxYz
```

⸻

### 🖱️ 사용 방법
	1.	앱 실행
	•	터미널에서 streamlit run mos_app.py 실행
	2.	사이드바에서 JSONL 소스 선택
	•	GitHub 폴더 목록
	•	GITHUB_JSONL_URL이 올바르게 설정되어 있다면 .jsonl 리스트가 자동으로 뜸
	•	URL 직접 입력
	•	https://raw.githubusercontent.com/.../file.jsonl 형식의 RAW URL 입력
	•	로컬 업로드
	•	PC에 있는 .jsonl 파일 업로드
	3.	User name 입력
	•	jykim 등 평가자 이름을 넣으면, rater 컬럼에 반영되고
같은 사용자 기준으로 점수 상태가 유지됩니다.
	4.	비디오/프롬프트/답변 확인
	•	좌측: 비디오 영역
	•	GitHub + mapping.csv 설정이 올바르면 비디오가 바로 재생됨
	•	Google Drive인 경우 iframe 미리보기 + 새 창 열기 + 다운로드 버튼 제공
	•	우측: Prompt / Answer 표시
	5.	MOS 점수 입력
	•	슬라이더(Score (1~5))로 점수 선택
	•	변경 시 자동 저장 + 상단 오른쪽에 토스트(저장됨: id=..., score=...)
	6.	항목 이동
	•	상단 ◀ 이전 / 다음 ▶ 버튼으로 인덱스 변경
	•	중앙에 항목 X / N 상태 표시
	7.	결과 저장
	•	하단 진행 현황 섹션에서 현재까지의 라벨링 결과를 테이블로 확인
	•	CSV 다운로드 버튼으로 mos_scores_{username}.csv 저장

⸻

### 🧩 기타 동작 메모
	•	st.cache_data를 사용하여:
	•	GitHub 목록 조회
	•	JSONL URL 로드
	•	mapping.csv 로드
등의 결과를 캐시하여 성능과 레이트리밋 이슈를 완화합니다.
	•	JSONL 파싱 중 JSON 형식이 아닌 라인은 try/except로 무시합니다.
	•	username이 비어 있을 때는 내부적으로 "anon"으로 처리합니다.

⸻


