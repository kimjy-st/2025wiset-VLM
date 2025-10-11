# mos_app.py — GitHub API 기반 .jsonl + 공개 비디오 URL/매핑 지원
import os
import re
import json
import pandas as pd
import requests
import streamlit as st
from urllib.parse import quote

st.set_page_config(page_title="MOS 라벨링 툴 (GitHub API)", layout="wide")

# ===== 필수: GitHub 폴더(내부의 .jsonl 모두 자동 탐색) =====
# 예) https://github.com/kimjy-st/2025wiset-VLM/blob/main/mos_results/
GITHUB_JSONL_URL = st.secrets.get("GITHUB_JSONL_URL", "")

# ===== 선택 1: 모든 비디오가 같은 공개 폴더에 있다면(HTTP) =====
# 예) https://cdn.example.com/videos
VIDEO_BASE_URL = st.secrets.get("VIDEO_BASE_URL", "")

# ===== 선택 2: 비디오 매핑 CSV(URL) =====
# CSV 컬럼: name,url  또는  name,file_id (Google Drive fileId)
# 예) https://raw.githubusercontent.com/user/repo/branch/video_mapping.csv
VIDEO_MAPPING_CSV_URL = st.secrets.get("VIDEO_MAPPING_CSV_URL", "")

def parse_github_url(url: str):
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/(?:blob|tree)/([^/]+)/(.*)", url)
    if not m:
        st.error("GITHUB_JSONL_URL 형식이 잘못되었습니다. 예: https://github.com/<user>/<repo>/tree/main/mos_results/")
        st.stop()
    return m.groups()  # user, repo, branch, path

def list_github_jsonl_files(url: str):
    user, repo, branch, path = parse_github_url(url)
    api_url = f"https://api.github.com/repos/{user}/{repo}/contents/{path}?ref={branch}"
    r = requests.get(api_url, headers={"Accept": "application/vnd.github.v3+json"}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"GitHub API 요청 실패: {r.status_code}, {r.text}")
    data = r.json()
    files = []
    for f in data:
        if f.get("type") == "file" and str(f.get("name","")).endswith(".jsonl"):
            files.append((f["name"], f["download_url"]))
    return files

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
def load_video_mapping_csv(url: str) -> pd.DataFrame:
    df = pd.read_csv(url)
    # 표준화
    df.columns = [c.strip().lower() for c in df.columns]
    return df

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

def basename_only(path_like: str) -> str:
    # 앞 경로 제거 → 파일명만
    return re.sub(r".*[\\/]", "", str(path_like)).strip()

def build_public_video_url(video_path: str, mapping_df: pd.DataFrame | None) -> tuple[str|None, str]:
    """
    반환: (재생URL 또는 None, 표시할 파일명)
    우선순위:
      A) video_path가 http(s)면 그대로
      B) VIDEO_MAPPING_CSV_URL에서 name 매칭 → url 또는 file_id
      C) VIDEO_BASE_URL + 파일명
      D) None
    """
    name = basename_only(video_path)

    # A) 직접 URL
    if str(video_path).startswith(("http://", "https://")):
        return str(video_path), name

    # B) 매핑 CSV
    if mapping_df is not None and not mapping_df.empty:
        row = mapping_df[mapping_df["name"].astype(str).str.strip() == name]
        if not row.empty:
            row = row.iloc[0]
            if "url" in mapping_df.columns and pd.notna(row.get("url")):
                return str(row["url"]), name
            if "file_id" in mapping_df.columns and pd.notna(row.get("file_id")):
                # Google Drive 미리보기 iframe 용도로 표기
                return f"gdrive:{row['file_id']}", name

    # C) BASE_URL + 파일명
    if VIDEO_BASE_URL:
        url = VIDEO_BASE_URL.rstrip("/") + "/" + quote(name)
        return url, name

    # D) 재생 불가
    return None, name

# ===== 사이드바 =====
st.sidebar.header("설정")

if not GITHUB_JSONL_URL:
    st.sidebar.error("GITHUB_JSONL_URL 이 비어 있습니다.")
    st.stop()

# JSONL 목록
try:
    jsonl_files = list_github_jsonl_files(GITHUB_JSONL_URL)
except Exception as e:
    st.sidebar.error(f"GitHub 폴더 스캔 실패: {e}")
    st.stop()

if not jsonl_files:
    st.sidebar.warning("폴더 내 .jsonl 파일을 찾지 못했습니다.")
    st.stop()

file_names = [f[0] for f in jsonl_files]
selected_file = st.sidebar.selectbox("JSONL 파일 선택", file_names)
jsonl_url = dict(jsonl_files)[selected_file]
username = st.sidebar.text_input("User name", value="", placeholder="예: jykim")

# 비디오 매핑 CSV(옵션)
video_map_df = None
if VIDEO_MAPPING_CSV_URL:
    try:
        video_map_df = load_video_mapping_csv(VIDEO_MAPPING_CSV_URL)
    except Exception as e:
        st.sidebar.warning(f"VIDEO_MAPPING_CSV_URL 로드 실패: {e}")

# ===== JSONL 로드 =====
try:
    records = load_jsonl_from_url(jsonl_url)
except Exception as e:
    st.error(f"JSONL 로드 실패: {e}")
    st.stop()

# ===== 상태 =====
if "idx" not in st.session_state:
    st.session_state["idx"] = 0
if "scores" not in st.session_state:
    st.session_state["scores"] = pd.DataFrame(columns=["id", "video", "score", "rater"])

st.title(f"MOS 라벨링 툴 ({selected_file})")

if not isinstance(records, list) or len(records) == 0:
    st.warning("JSONL 파일이 비어 있습니다.")
    st.stop()

# ===== 내비게이션 =====
st.session_state["idx"] = max(0, min(st.session_state["idx"], len(records) - 1))
left, mid, right = st.columns([1, 2, 1])
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

# ===== 현재 항목 =====
curr = records[st.session_state["idx"]]
rec_id     = pick_first_key(curr, ["id", "idx"])
video_path = pick_first_key(curr, ["video", "video_path", "path"])
prompt     = pick_first_key(curr, ["prompt", "instruction", "question"])
answer_raw = pick_first_key(curr, ["answer", "anwser", "caption", "response", "text"])
answer     = normalize_text(answer_raw)

video_url, video_name = build_public_video_url(video_path, video_map_df)

col1, col2 = st.columns([3, 2])
with col1:
    st.subheader("Video")
    if video_url is None:
        st.error(f"영상 URL을 만들 수 없습니다. 파일명: {video_name}")
        with st.expander("해결 가이드"):
            st.markdown(
                "- `VIDEO_BASE_URL` 시크릿을 설정해서 `[BASE]/파일명`으로 접속 가능하게 하세요.\n"
                "- 또는 `VIDEO_MAPPING_CSV_URL`에 `name,url`(또는 `name,file_id`)를 넣으세요.\n"
                "- `video` 값이 http(s)라면 그대로 재생됩니다."
            )
    else:
        if video_url.startswith("gdrive:"):
            # Google Drive 프리뷰 iframe
            fid = video_url.split(":", 1)[1]
            st.components.v1.iframe(f"https://drive.google.com/file/d/{fid}/preview", height=400)
            st.caption("Google Drive 미리보기(재생바 이동 가능)")
        else:
            # 직접 URL 재생
            st.video(video_url)
    st.caption(f"파일명: {video_name}")
    st.markdown(
        """
        <div style="margin-top:8px;padding:12px;border:1px solid #e6e6e6;border-radius:8px;background:#fbfbfd;">
        <b>평가 안내</b><br>
        영상을 보고 <b>프롬프트에 대한 답변을 명확히 하였는지</b> 점수로 매겨주세요.<br>
        5점에 가까울수록 잘 표현한 것이고, 1점에 가까울수록 잘 표현하지 못한 것입니다.
        </div>
        """,
        unsafe_allow_html=True,
    )

with col2:
    st.subheader("Prompt")
    st.text(prompt or "(없음)")
    st.subheader("Answer")
    st.text_area("", value=answer or "(없음)", height=160, label_visibility="collapsed", disabled=True)

    default_score = 3
    if not st.session_state["scores"].empty:
        row = st.session_state["scores"]
        row = row[(row["id"] == rec_id) & (row["rater"] == username)]
        if not row.empty:
            try:
                default_score = int(row.iloc[0]["score"])
            except Exception:
                pass

    score_key = f"score::{selected_file}::{username}::{rec_id}"
    if score_key not in st.session_state:
        st.session_state[score_key] = default_score

    def save_score(rec_id, video_name, score_key, username):
        try:
            score_val = int(st.session_state[score_key])
        except Exception:
            return
        df = st.session_state["scores"]
        mask = (df["id"] == rec_id) & (df["rater"] == username)
        if mask.any():
            df.loc[mask, ["video", "score"]] = [video_name, score_val]
        else:
            st.session_state["scores"] = pd.concat(
                [df, pd.DataFrame([{"id": rec_id, "video": video_name, "score": score_val, "rater": username}])],
                ignore_index=True,
            )
        st.toast(f"저장됨: id={rec_id}, score={score_val}")

    st.slider(
        "Score (1~5)", 1, 5, step=1,
        key=score_key,
        on_change=save_score,
        args=(rec_id, video_name, score_key, username),
    )

# ===== 진행 현황 =====
st.divider()
st.subheader("진행 현황")
st.dataframe(st.session_state["scores"], use_container_width=True)
csv_bytes = st.session_state["scores"].to_csv(index=False).encode("utf-8")
dl_name = f"{os.path.basename(selected_file)}_{username or 'anon'}.csv"
st.download_button("CSV 다운로드", csv_bytes, file_name=dl_name)
