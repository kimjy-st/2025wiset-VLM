# mos_app.py — GitHub API 인증 + 폴백(수동 URL/업로드) + mapping.csv
import os
import re
import io
import json
import time
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="MOS 라벨링 툴 (GitHub + Fallback)", layout="wide")

# ============== 설정 ==============
# 예) https://github.com/kimjy-st/2025wiset-VLM/blob/main/mos_results/
GITHUB_JSONL_URL = st.secrets.get("GITHUB_JSONL_URL", "")
# 예) https://github.com/kimjy-st/2025wiset-VLM/blob/main/mapping.csv
VIDEO_MAPPING_CSV_URL = st.secrets.get("VIDEO_MAPPING_CSV_URL", "")
# 선택: GitHub Personal Access Token (권장). classic token(리드 권한)으로 충분
GITHUB_TOKEN = st.secrets.get("GITHUB_TOKEN", "")  # 없으면 비인증 호출(금방 403)

# ============== 유틸 ==============
def parse_github_url(url: str):
    """
    github.com/<user>/<repo>/(blob|tree)/<branch>/<path> → (user, repo, branch, path)
    """
    m = re.match(r"^https?://github\.com/([^/]+)/([^/]+)/(?:blob|tree)/([^/]+)/(.*)$", url)
    if not m:
        raise ValueError("GITHUB_JSONL_URL 형식이 잘못되었습니다. 예: https://github.com/<user>/<repo>/tree/main/mos_results/")
    return m.groups()

def github_to_raw(url: str) -> str:
    if "raw.githubusercontent.com" in url:
        return url
    m = re.match(r"^https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.*)$", url)
    if m:
        u, r, b, p = m.groups()
        return f"https://raw.githubusercontent.com/{u}/{r}/{b}/{p}"
    m = re.match(r"^https?://github\.com/([^/]+)/([^/]+)/(?:tree|raw)/([^/]+)/(.*)$", url)
    if m:
        u, r, b, p = m.groups()
        return f"https://raw.githubusercontent.com/{u}/{r}/{b}/{p}"
    return url

def req_headers():
    h = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h

@st.cache_data(show_spinner=True)
def list_github_jsonl_files(folder_url: str):
    """
    GitHub API로 폴더 내 .jsonl 목록을 가져온다.
    - 토큰 있으면 인증 헤더 사용(레이트리밋 완화)
    - 403 등 실패하면 예외를 올림 (호출부에서 폴백)
    """
    user, repo, branch, path = parse_github_url(folder_url)
    api = f"https://api.github.com/repos/{user}/{repo}/contents/{path}?ref={branch}"
    # 간단 재시도
    for i in range(3):
        r = requests.get(api, headers=req_headers(), timeout=30)
        if r.status_code == 200:
            data = r.json()
            files = []
            for f in data:
                if f.get("type") == "file" and str(f.get("name","")).endswith(".jsonl"):
                    files.append((f["name"], f["download_url"]))
            return files
        elif r.status_code in (403, 429):
            # 레이트 리밋일 가능성 → 대기 후 재시도
            time.sleep(2*(i+1))
        else:
            break
    raise RuntimeError(f"GitHub API 요청 실패: {r.status_code}, {r.text[:200]}")

@st.cache_data(show_spinner=True)
def load_jsonl_from_url(url: str):
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    out = []
    for line in r.text.splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            out.append(json.loads(s))
        except Exception:
            pass
    return out

@st.cache_data(show_spinner=True)
def load_video_mapping_csv(url_or_page: str) -> pd.DataFrame:
    raw = github_to_raw(url_or_page)
    df = pd.read_csv(raw)
    df.columns = [c.strip().lower() for c in df.columns]
    if "name" not in df.columns:
        raise RuntimeError("mapping.csv에 'name' 컬럼이 필요합니다.")
    return df

def pick_first_key(d: dict, keys, default=""):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default

def normalize_text(v):
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    try:
        return json.dumps(v, ensure_ascii=False)
    except Exception:
        return str(v)

def basename_only(p: str) -> str:
    return re.sub(r".*[\\/]", "", str(p)).strip()

def drive_preview_url(fid: str) -> str:
    return f"https://drive.google.com/file/d/{fid}/preview"

# === 이 함수만 교체 ===
def build_video_url(video_path: str, mapping_df: pd.DataFrame | None) -> tuple[str | None, str]:
    """
    JSONL의 video 경로에서 파일명 추출 후,
    1) <stem>__cv2.mp4 가 mapping.csv에 있으면 그걸 우선 사용
    2) 없으면 원본 파일명으로 검색
    반환: ( "gdrive:<file_id>" 또는 직접 URL 또는 None, 매칭된_파일명 )
    """
    base = basename_only(video_path or "")
    if not base:
        return None, ""

    # 후보: cv2 변환본을 1순위
    stem, ext = os.path.splitext(base)
    candidates = []
    # 확장자가 있으면 stem__cv2.mp4, 없으면 base__cv2.mp4도 고려
    candidates.append(f"{stem}__cv2.mp4" if ext else f"{base}__cv2.mp4")
    candidates.append(base)

    if mapping_df is None or mapping_df.empty:
        return None, base

    df = mapping_df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    # type 컬럼이 있으면 video만 필터
    if "type" in df.columns:
        df = df[df["type"].astype(str).str.lower() == "video"]

    # name 컬럼 필수
    if "name" not in df.columns:
        return None, base

    # 공백 제거 후 일치 검색
    df["name"] = df["name"].astype(str).str.strip()

    for cand in candidates:
        row = df[df["name"] == cand]
        if not row.empty:
            r0 = row.iloc[0]
            # url 우선, 없으면 file_id → gdrive 프리뷰
            if "url" in df.columns and pd.notna(r0.get("url")) and str(r0["url"]).strip():
                return str(r0["url"]).strip(), cand
            if "file_id" in df.columns and pd.notna(r0.get("file_id")) and str(r0["file_id"]).strip():
                return f"gdrive:{str(r0['file_id']).strip()}", cand

    # 둘 다 못 찾은 경우
    return None, base

# ============== 사이드바 ==============
st.sidebar.header("설정")

# mapping.csv 로드
video_map_df = None
if VIDEO_MAPPING_CSV_URL:
    try:
        video_map_df = load_video_mapping_csv(VIDEO_MAPPING_CSV_URL)
    except Exception as e:
        st.sidebar.error(f"mapping.csv 로드 실패: {e}")

# JSONL 소스 선택 섹션
st.sidebar.subheader("JSONL 소스 선택")

jsonl_files = []
api_error = None
if GITHUB_JSONL_URL:
    try:
        jsonl_files = list_github_jsonl_files(GITHUB_JSONL_URL)
    except Exception as e:
        api_error = str(e)

# 1) API 성공 시: 선택 박스
selected_mode = None
selected_jsonl_url = None
uploaded_jsonl = None

if jsonl_files:
    selected_mode = st.sidebar.radio("불러오기 방식", ["GitHub 폴더 목록", "URL 직접 입력", "로컬 업로드"], index=0)
else:
    # API 실패 → 폴백 only
    if api_error:
        st.sidebar.warning(f"GitHub API 폴더 스캔 실패: {api_error}")
        if not GITHUB_TOKEN:
            st.sidebar.info("✅ 해결: Streamlit Secrets에 GITHUB_TOKEN을 넣으면 레이트 리밋이 크게 완화됩니다.")
    selected_mode = st.sidebar.radio("불러오기 방식", ["URL 직접 입력", "로컬 업로드"], index=0)

if selected_mode == "GitHub 폴더 목록":
    file_names = [f[0] for f in jsonl_files]
    selected_name = st.sidebar.selectbox("JSONL 파일 선택", file_names)
    selected_jsonl_url = dict(jsonl_files)[selected_name]
elif selected_mode == "URL 직접 입력":
    selected_jsonl_url = st.sidebar.text_input("JSONL 파일 RAW URL", value="", placeholder="https://raw.githubusercontent.com/<user>/<repo>/<branch>/mos_results/file.jsonl")
elif selected_mode == "로컬 업로드":
    uploaded_jsonl = st.sidebar.file_uploader("JSONL 업로드", type=["jsonl"])

username = st.sidebar.text_input("User name", value="", placeholder="예: jykim")

# ============== 데이터 로드 ==============
records = []
if selected_mode in ("GitHub 폴더 목록", "URL 직접 입력"):
    if not selected_jsonl_url:
        st.info("왼쪽에서 JSONL을 선택/입력하세요.")
        st.stop()
    try:
        records = load_jsonl_from_url(selected_jsonl_url)
    except Exception as e:
        st.error(f"JSONL 로드 실패: {e}")
        st.stop()
elif selected_mode == "로컬 업로드":
    if not uploaded_jsonl:
        st.info("왼쪽에서 JSONL 파일을 업로드하세요.")
        st.stop()
    try:
        content = uploaded_jsonl.read().decode("utf-8", errors="ignore")
        for line in content.splitlines():
            s = line.strip()
            if not s:
                continue
            try:
                records.append(json.loads(s))
            except Exception:
                pass
    except Exception as e:
        st.error(f"JSONL 파싱 실패: {e}")
        st.stop()

st.title("MOS 라벨링 툴")

if not isinstance(records, list) or len(records) == 0:
    st.warning("선택/업로드한 JSONL이 비어 있습니다.")
    st.stop()

# ============== 상태 ==============
if "idx" not in st.session_state:
    st.session_state["idx"] = 0
if "scores" not in st.session_state:
    st.session_state["scores"] = pd.DataFrame(columns=["id", "video", "score", "rater"])

# ============== 내비게이션 ==============
st.session_state["idx"] = max(0, min(st.session_state["idx"], len(records) - 1))
left, mid, right = st.columns([1,2,1])
with left:
    if st.button("◀ 이전", use_container_width=True):
        st.session_state["idx"] = max(0, st.session_state["idx"] - 1)
        st.rerun()
with right:
    if st.button("다음 ▶", use_container_width=True):
        st.session_state["idx"] = min(len(records) - 1, st.session_state["idx"] + 1)
        st.rerun()
with mid:
    st.markdown(
        f"<div style='text-align:center;'>항목 {st.session_state['idx'] + 1} / {len(records)}</div>",
        unsafe_allow_html=True,
    )

# ============== 현재 항목 ==============
curr     = records[st.session_state["idx"]]
rec_id   = pick_first_key(curr, ["id", "idx"])
vpath    = pick_first_key(curr, ["video", "video_path", "path"])
prompt   = pick_first_key(curr, ["prompt", "instruction", "question"])
ans_raw  = pick_first_key(curr, ["answer", "anwser", "caption", "response", "text"])
answer   = normalize_text(ans_raw)

video_url, video_name = build_video_url(vpath, video_map_df)

col1, col2 = st.columns([3,2])
with col1:
    st.subheader("Video")
    if video_url is None:
        st.error(f"영상 URL/ID를 찾을 수 없습니다. 파일명: {basename_only(vpath)}")
        with st.expander("해결 가이드"):
            st.markdown(
                "- mapping.csv에 해당 파일명이 있는지 확인 (정확한 파일명 일치)\n"
                "- mapping.csv 컬럼: `name,url` 또는 `name,file_id` (+ 선택: `type=video`)\n"
                "- JSONL의 `video`에 경로가 붙어도 자동으로 파일명만 매칭합니다."
            )
    else:
        if video_url.startswith("gdrive:"):
            fid = video_url.split(":",1)[1]
            st.components.v1.iframe(drive_preview_url(fid), height=400)
            st.caption("Google Drive 미리보기(재생바 이동 가능) — 프리뷰가 멈추면 3rd-party 쿠키/권한 확인")
            # 보조 링크
            view = f"https://drive.google.com/file/d/{fid}/view?usp=sharing"
            dl   = f"https://drive.google.com/uc?export=download&id={fid}"
            c1, c2 = st.columns(2)
            with c1: st.link_button("새 창에서 열기", view)
            with c2: st.link_button("다운로드", dl)
        else:
            st.video(video_url)
    st.caption(f"파일명: {video_name or '(미상)'}")
    st.markdown(
        """
        <div style="margin-top:8px;padding:12px;border:1px solid #e6e6e6;border-radius:8px;background:#fbfbfd;">
          <b>평가 안내</b><br>
          영상을 보고 <b>프롬프트에 대한 답변을 명확히 하였는지</b> 점수로 매겨주세요.<br>
          5점에 가까울수록 잘 표현한 것이고, 1점에 가까울수록 잘 표현하지 못한 것입니다.
        </div>
        """, unsafe_allow_html=True
    )

with col2:
    st.subheader("Prompt")
    st.text(prompt or "(없음)")
    st.subheader("Answer")
    st.text_area("", value=answer or "(없음)", height=160, label_visibility="collapsed", disabled=True)

    default_score = 3
    if not st.session_state["scores"].empty and (username or "").strip():
        row = st.session_state["scores"]
        row = row[(row["id"]==rec_id) & (row["rater"]==(username or "anon"))]
        if not row.empty:
            try: default_score = int(row.iloc[0]["score"])
            except: pass

    score_key = f"score::{(selected_jsonl_url or 'uploaded')}::{username or 'anon'}::{rec_id}"
    if score_key not in st.session_state:
        st.session_state[score_key] = default_score

    def save_score(rec_id, video_name, score_key, username):
        try:
            val = int(st.session_state[score_key])
        except Exception:
            return
        df = st.session_state["scores"]
        mask = (df["id"]==rec_id) & (df["rater"]==(username or "anon"))
        if mask.any():
            df.loc[mask, ["video","score"]] = [video_name, val]
        else:
            st.session_state["scores"] = pd.concat(
                [df, pd.DataFrame([{"id":rec_id, "video":video_name, "score":val, "rater":(username or "anon")}])],
                ignore_index=True
            )
        st.toast(f"저장됨: id={rec_id}, score={val}")

    st.slider("Score (1~5)", 1, 5, step=1,
              key=score_key,
              help="5점에 가까울수록 프롬프트를 잘 반영한 설명입니다.",
              on_change=save_score,
              args=(rec_id, video_name, score_key, username))

# ============== 진행 현황 ==============
st.divider()
st.subheader("진행 현황")
st.dataframe(st.session_state["scores"], use_container_width=True)
csv_bytes = st.session_state["scores"].to_csv(index=False).encode("utf-8")
dl_name = f"mos_scores_{(username or 'anon')}.csv"
st.download_button("CSV 다운로드", csv_bytes, file_name=dl_name, mime="text/csv")
