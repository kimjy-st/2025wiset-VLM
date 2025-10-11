# mos_app.py — GitHub JSONL 지원 + (선택) Drive mapping.csv로 비디오 스트리밍
import os
import io
import re
import json
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="MOS 라벨링 툴", layout="wide")

# =========================
# ▷ 선택 1: GitHub의 JSONL 하나만 직접 로드
#   - Streamlit Secrets에 넣어두는 걸 권장
#   - 페이지 URL 또는 raw URL 모두 OK (자동 변환)
GITHUB_JSONL_URL_RAW = st.secrets.get("GITHUB_JSONL_URL", "")  # 예: https://github.com/kimjy-st/.../file.jsonl

# ▷ 선택 2: (선택) Google Drive의 mapping.csv (video/jsonl 매핑)
#   - 없어도 앱은 동작 (이 경우 영상은 미리보기 없이 파일명만 표시)
MAPPING_FILE_ID_RAW = st.secrets.get("MAPPING_FILE_ID", "")
# =========================


# ---------- 유틸 ----------
def extract_drive_id(s: str) -> str:
    if not s:
        return ""
    s = s.strip().strip('"').strip("'")
    m = re.search(r"/d/([a-zA-Z0-9_-]+)", s) or re.search(r"[?&]id=([a-zA-Z0-9_-]+)", s)
    return (m.group(1) if m else s).rstrip("/").strip()

def github_to_raw(url: str) -> str:
    """github.com/.../blob/... → raw.githubusercontent.com/... 로 변환"""
    if not url:
        return ""
    url = url.strip()
    if "raw.githubusercontent.com" in url:
        return url
    # github.com/<user>/<repo>/blob/<branch>/<path>
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.*)", url)
    if m:
        user, repo, branch, path = m.groups()
        return f"https://raw.githubusercontent.com/{user}/{repo}/{branch}/{path}"
    # github.com/<user>/<repo>/main/... 형태도 처리
    m2 = re.match(r"https?://github\.com/([^/]+)/([^/]+)/(?:tree|raw)/([^/]+)/(.*)", url)
    if m2:
        user, repo, branch, path = m2.groups()
        return f"https://raw.githubusercontent.com/{user}/{repo}/{branch}/{path}"
    return url  # 이미 raw거나 기타 케이스

@st.cache_data(show_spinner=True)
def fetch_drive_file_binary(file_id: str) -> bytes:
    session = requests.Session()
    url = f"https://drive.google.com/uc?export=download&id={file_id}"
    resp = session.get(url, allow_redirects=True, timeout=60)
    if "ServiceLogin" in resp.text or "signin/v2/identifier" in resp.url:
        raise RuntimeError("Drive가 로그인 페이지를 반환했습니다. mapping.csv를 '링크가 있는 모든 사용자(보기)'로 공개하세요.")
    resp.raise_for_status()
    return resp.content

@st.cache_data(show_spinner=True)
def load_mapping_csv_from_drive(file_id: str) -> pd.DataFrame:
    content = fetch_drive_file_binary(file_id)
    return pd.read_csv(io.BytesIO(content))

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
            # 잘못된 라인은 건너뜀
            pass
    return out

def pick_first_key(d: dict, candidates, default=""):
    for k in candidates:
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

def drive_preview_url(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/preview"


# ---------- 사이드바: 데이터 소스 로드 ----------
st.sidebar.header("설정")

# 1) JSONL 소스: GitHub URL 필수
if not GITHUB_JSONL_URL_RAW:
    st.sidebar.error("GITHUB_JSONL_URL 이 비었습니다. Streamlit Secrets에 GitHub JSONL 링크를 추가하세요.")
    st.stop()

jsonl_url = github_to_raw(GITHUB_JSONL_URL_RAW)

with st.sidebar.expander("디버그: JSONL 링크 확인"):
    st.code(jsonl_url, language="text")

# JSONL 로드
try:
    records = load_jsonl_from_url(jsonl_url)
except Exception as e:
    st.sidebar.error(f"JSONL 로드 실패: {e}")
    st.stop()

# 2) (선택) Drive mapping.csv 로드 → 비디오 스트리밍용
video_index = {}
if MAPPING_FILE_ID_RAW:
    file_id = extract_drive_id(MAPPING_FILE_ID_RAW)
    with st.sidebar.expander("디버그: mapping.csv (Drive)"):
        st.code(file_id, language="text")
        st.markdown(f"[테스트 다운로드](https://drive.google.com/uc?export=download&id={file_id})")
    try:
        mapping_df = load_mapping_csv_from_drive(file_id)
        required_cols = {"name", "type", "file_id"}
        if required_cols.issubset(set(mapping_df.columns)):
            video_index = {
                str(row["name"]).strip(): str(row["file_id"]).strip()
                for _, row in mapping_df[mapping_df["type"].astype(str)=="video"].iterrows()
            }
        else:
            st.sidebar.warning("mapping.csv에 name,type,file_id 컬럼이 없습니다. 비디오 스트리밍을 생략합니다.")
    except Exception as e:
        st.sidebar.warning(f"mapping.csv 로드 실패(비디오 스트리밍 건너뜀): {e}")

# 사용자 이름
username = st.sidebar.text_input("User name", value="", placeholder="예: jykim")

# ---------- 세션 상태 ----------
if "idx" not in st.session_state:
    st.session_state["idx"] = 0
if "scores" not in st.session_state:
    st.session_state["scores"] = pd.DataFrame(columns=["id", "video", "score", "rater"])

st.title("MOS 라벨링 툴 (GitHub JSONL + 선택적 Drive Video)")

if not isinstance(records, list) or len(records) == 0:
    st.warning("JSONL에 항목이 없습니다.")
    st.stop()

# ---------- 내비게이션 ----------
st.session_state["idx"] = max(0, min(st.session_state["idx"], len(records) - 1))

left_nav, mid_nav, right_nav = st.columns([1,2,1])
with left_nav:
    if st.button("◀ 이전", use_container_width=True):
        st.session_state["idx"] = max(0, st.session_state["idx"] - 1)
        st.rerun()
with right_nav:
    if st.button("다음 ▶", use_container_width=True):
        st.session_state["idx"] = min(len(records) - 1, st.session_state["idx"] + 1)
        st.rerun()
with mid_nav:
    st.markdown(
        f"<div style='text-align:center;'>항목 {st.session_state['idx'] + 1} / {len(records)}</div>",
        unsafe_allow_html=True
    )

# ---------- 현재 항목 ----------
curr = records[st.session_state["idx"]]
rec_id     = pick_first_key(curr, ["id", "idx"])
video_path = pick_first_key(curr, ["video", "video_path", "path"])
prompt     = pick_first_key(curr, ["prompt", "instruction", "question"])
answer_raw = pick_first_key(curr, ["answer", "anwser", "caption", "response", "text"])
answer     = normalize_text(answer_raw)

video_basename = os.path.basename(str(video_path)).strip()
file_id_for_video = video_index.get(video_basename) if video_index else None

col_video, col_text = st.columns([3, 2], gap="large")

with col_video:
    st.subheader("Video")
    if file_id_for_video:
        st.components.v1.iframe(drive_preview_url(file_id_for_video), height=400)
        st.caption("Google Drive 미리보기 스트리밍(재생바 이동 가능)")
    else:
        st.info(f"영상 파일명: {video_basename or '(없음)'}")
        st.caption("※ mapping.csv를 제공하지 않으면 스트리밍 미리보기 대신 파일명만 표시됩니다.")

    st.markdown(
        """
        <div style="margin-top:8px;padding:12px;border:1px solid #e6e6e6;border-radius:8px;background:#fbfbfd;">
          <b>평가 안내</b><br>
          영상을 보고 <b>프롬프트에 대한 답변을 명확히 하였는지</b> 점수로 매겨주세요.<br>
          5점에 가까울수록 잘 표현한 것이고, 1점에 가까울수록 잘 표현하지 못한 것입니다.
        </div>
        """,
        unsafe_allow_html=True
    )

with col_text:
    st.subheader("Prompt")
    st.text(prompt or "(없음)")
    st.subheader("Answer")
    st.text_area("", value=answer or "(없음)", height=160, label_visibility="collapsed", disabled=True)
    st.divider()

    # 기존 점수 불러오기
    default_score = 3
    if not st.session_state["scores"].empty:
        row = st.session_state["scores"]
        row = row[(row["id"]==rec_id) & (row["rater"]==username)]
        if not row.empty:
            try:
                default_score = int(row.iloc[0]["score"])
            except Exception:
                pass

    score_key = f"score::github::{username}::{rec_id}"
    if score_key not in st.session_state:
        st.session_state[score_key] = default_score

    def _save_in_memory(rec_id, video_name, score_key, username):
        try:
            score_val = int(st.session_state[score_key])
        except Exception:
            return
        df = st.session_state["scores"]
        mask = (df["id"]==rec_id) & (df["rater"]==username)
        if mask.any():
            df.loc[mask, ["video","score"]] = [video_name, score_val]
        else:
            st.session_state["scores"] = pd.concat(
                [df, pd.DataFrame([{"id":rec_id, "video":video_name, "score":score_val, "rater":username}])],
                ignore_index=True
            )
        st.toast(f"저장됨: id={rec_id}, score={score_val}")

    st.slider(
        "Score (1~5)",
        min_value=1, max_value=5, step=1,
        key=score_key,
        help="5점에 가까울수록 프롬프트를 잘 반영한 설명입니다.",
        on_change=_save_in_memory,
        args=(rec_id, video_basename, score_key, username)
    )

# ---------- 진행 현황 & 다운로드 ----------
st.divider()
st.subheader("진행 현황")
st.dataframe(st.session_state["scores"], use_container_width=True)

csv_bytes = st.session_state["scores"].to_csv(index=False).encode("utf-8")
base = os.path.splitext(os.path.basename(github_to_raw(GITHUB_JSONL_URL_RAW)))[0] or "mos_scores"
dl_name = f"{base}_{username or 'anonymous'}.csv"
st.download_button("CSV 다운로드", data=csv_bytes, file_name=dl_name, mime="text/csv")
