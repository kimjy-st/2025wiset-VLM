# mos_app.py — GitHub API 기반 .jsonl 자동 로드 버전
import os
import io
import re
import json
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="MOS 라벨링 툴 (GitHub API)", layout="wide")

# =========================
# 예: https://github.com/kimjy-st/2025wiset-VLM/blob/main/mos_results/
GITHUB_JSONL_URL = st.secrets.get("GITHUB_JSONL_URL", "")
# =========================


# ---------- 유틸 ----------
def parse_github_url(url: str):
    """
    github.com/<user>/<repo>/blob/<branch>/<path>
    또는 github.com/<user>/<repo>/tree/<branch>/<path> → (user, repo, branch, path)
    """
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/(?:blob|tree)/([^/]+)/(.*)", url)
    if not m:
        st.error("GITHUB_JSONL_URL 형식이 잘못되었습니다. 예: https://github.com/<user>/<repo>/tree/main/mos_results/")
        st.stop()
    return m.groups()  # user, repo, branch, path


def list_github_jsonl_files(url: str):
    """GitHub API를 이용해 폴더 내 .jsonl 파일 리스트 반환"""
    user, repo, branch, path = parse_github_url(url)
    api_url = f"https://api.github.com/repos/{user}/{repo}/contents/{path}?ref={branch}"
    headers = {"Accept": "application/vnd.github.v3+json"}
    r = requests.get(api_url, headers=headers)
    if r.status_code != 200:
        raise RuntimeError(f"GitHub API 요청 실패: {r.status_code}, {r.text}")
    data = r.json()
    files = []
    for f in data:
        if f["type"] == "file" and f["name"].endswith(".jsonl"):
            files.append((f["name"], f["download_url"]))
    return files


@st.cache_data(show_spinner=True)
def load_jsonl_from_url(url: str):
    """JSONL을 URL에서 로드"""
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return [json.loads(line) for line in r.text.splitlines() if line.strip()]


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


# ---------- 사이드바 ----------
st.sidebar.header("설정")

if not GITHUB_JSONL_URL:
    st.sidebar.error("GITHUB_JSONL_URL 이 비어 있습니다.")
    st.stop()

try:
    jsonl_files = list_github_jsonl_files(GITHUB_JSONL_URL)
except Exception as e:
    st.sidebar.error(f"GitHub 폴더 스캔 실패: {e}")
    st.stop()

if not jsonl_files:
    st.sidebar.warning("폴더 내에서 .jsonl 파일을 찾지 못했습니다.")
    st.stop()

file_names = [f[0] for f in jsonl_files]
selected_file = st.sidebar.selectbox("JSONL 파일 선택", file_names)
jsonl_url = dict(jsonl_files)[selected_file]

username = st.sidebar.text_input("User name", value="", placeholder="예: jykim")

# ---------- 데이터 로드 ----------
try:
    records = load_jsonl_from_url(jsonl_url)
except Exception as e:
    st.error(f"JSONL 로드 실패: {e}")
    st.stop()

# ---------- 상태 ----------
if "idx" not in st.session_state:
    st.session_state["idx"] = 0
if "scores" not in st.session_state:
    st.session_state["scores"] = pd.DataFrame(columns=["id", "video", "score", "rater"])

st.title(f"MOS 라벨링 툴 ({selected_file})")

if not isinstance(records, list) or len(records) == 0:
    st.warning("JSONL 파일이 비어 있습니다.")
    st.stop()

# ---------- 내비게이션 ----------
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

# ---------- 현재 항목 ----------
curr = records[st.session_state["idx"]]
rec_id = pick_first_key(curr, ["id", "idx"])
video_path = pick_first_key(curr, ["video", "video_path", "path"])
prompt = pick_first_key(curr, ["prompt", "instruction", "question"])
answer_raw = pick_first_key(curr, ["answer", "anwser", "caption", "response", "text"])
answer = normalize_text(answer_raw)

col1, col2 = st.columns([3, 2])
with col1:
    st.subheader("Video")
    st.text(video_path or "(없음)")
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
        "Score (1~5)",
        1,
        5,
        step=1,
        key=score_key,
        on_change=save_score,
        args=(rec_id, video_path, score_key, username),
    )

# ---------- 진행 현황 ----------
st.divider()
st.subheader("진행 현황")
st.dataframe(st.session_state["scores"], use_container_width=True)
csv_bytes = st.session_state["scores"].to_csv(index=False).encode("utf-8")
dl_name = f"{os.path.basename(selected_file)}_{username or 'anon'}.csv"
st.download_button("CSV 다운로드", csv_bytes, file_name=dl_name)
