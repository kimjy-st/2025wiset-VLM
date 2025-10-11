# mos_app.py
import os
import json
import glob
import pandas as pd
import streamlit as st

# ====== 경로 설정 ======
MOS_RESULTS_DIR = "/home/jykim1/EventDetection/HolmesVAU/custom_tests/cococaption/mos_results"
VIDEO_ROOT      = "/mnt/data/HIVAU-70k/videos/xd-violence/videos/test"

# ====== 기본 설정 ======
st.set_page_config(page_title="MOS 라벨링 툴", layout="wide")

# ====== 유틸 ======
def load_jsonl(path):
    """jsonl을 한 줄씩 읽어 dict 리스트로 반환"""
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                out.append(json.loads(s))
            except Exception:
                # 형식 불명확 줄은 스킵
                pass
    return out

def csv_path_for(file_basename: str, username: str) -> str:
    base = os.path.splitext(file_basename)[0]  # ".jsonl" 제거
    return os.path.join(MOS_RESULTS_DIR, f"{base}_{username}.csv")

def read_scores(path: str) -> pd.DataFrame:
    if path and os.path.exists(path):
        try:
            return pd.read_csv(path)
        except Exception:
            return pd.DataFrame(columns=["id", "video", "score"])
    return pd.DataFrame(columns=["id", "video", "score"])

from filelock import FileLock

def upsert_score(path: str, rec_id: int, video_name: str, score: int) -> pd.DataFrame:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock = FileLock(path + ".lock")
    with lock:
        df = read_scores(path)
        if (df["id"] == rec_id).any():
            df.loc[df["id"] == rec_id, ["video", "score"]] = [video_name, score]
        else:
            df = pd.concat(
                [df, pd.DataFrame([{"id": rec_id, "video": video_name, "score": score}])],
                ignore_index=True
            )
        df.to_csv(path, index=False)
    return df

# ====== 사이드바 ======
st.sidebar.header("설정")
jsonl_files = sorted(glob.glob(os.path.join(MOS_RESULTS_DIR, "*.jsonl")))
file_display = [os.path.basename(p) for p in jsonl_files]
selected_file = st.sidebar.selectbox("mos_results 내 파일 선택", file_display)
username = st.sidebar.text_input("User name", value="", placeholder="예: jykim")

# ====== 세션 상태 초기화 ======
if "records" not in st.session_state:
    st.session_state["records"] = []
if "idx" not in st.session_state:
    st.session_state["idx"] = 0
if "active_file" not in st.session_state:
    st.session_state["active_file"] = None

# 파일 변경 시 로드 (인덱스 0으로 리셋)
if selected_file and st.session_state["active_file"] != selected_file:
    sel_path = os.path.join(MOS_RESULTS_DIR, selected_file)
    st.session_state["records"] = load_jsonl(sel_path)
    st.session_state["idx"] = 0
    st.session_state["active_file"] = selected_file

st.title("MOS 점수 매기기")

if not selected_file:
    st.info("왼쪽에서 파일을 선택하세요.")
    st.stop()

records = st.session_state["records"]
if not isinstance(records, list) or len(records) == 0:
    st.warning("선택한 파일에서 항목을 불러오지 못했습니다.")
    st.stop()

# 현재 진행 CSV (유저명 없으면 저장 불가)
csv_path = csv_path_for(selected_file, username) if username.strip() else None
scores_df = read_scores(csv_path) if csv_path else pd.DataFrame(columns=["id","video","score"])

# ====== 상단 내비게이션 ======
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

# ====== 현재 항목 표시 ======
curr = records[st.session_state["idx"]]
rec_id = curr.get("id")
video_name = curr.get("video", "")
prompt = curr.get("prompt", "")
answer = curr.get("answer", "")
video_path = os.path.join(VIDEO_ROOT, video_name)

col_video, col_text = st.columns([3, 2], gap="large")

with col_video:
    st.subheader("Video")
    if os.path.exists(video_path):
        # HTML5 비디오 플레이어(스트리밍/시킹 지원)
        st.video(video_path, format="video/mp4", start_time=0)
        st.caption("재생바로 원하는 위치로 이동할 수 있습니다.")
        # 평가 안내
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
    else:
        st.error(f"비디오 파일을 찾을 수 없습니다: {video_path}")

with col_text:
    st.subheader("Prompt")
    st.code(prompt or "(없음)")
    st.subheader("Answer")
    st.write(answer or "(없음)")
    st.divider()

    # CSV에 저장된 기존 점수 로드 (없으면 기본 3)
    default_score = 3
    try:
        if not scores_df.empty and (scores_df["id"] == rec_id).any():
            default_score = int(scores_df.loc[scores_df["id"] == rec_id, "score"].values[0])
    except Exception:
        default_score = 3

    # 슬라이더 키를 파일/유저/ID에 종속시켜 화면 전환 시 정확히 동기화
    score_key = f"score::{selected_file}::{username}::{rec_id}"
    if score_key not in st.session_state:
        st.session_state[score_key] = default_score

    # 자동 저장 콜백
    def _auto_save_callback(rec_id, video_name, score_key, csv_path):
        if not csv_path:
            st.warning("사용자 이름을 입력해야 점수가 저장됩니다.", icon="⚠️")
            return
        try:
            score_val = int(st.session_state[score_key])
        except Exception:
            return
        upsert_score(csv_path, rec_id, video_name, score_val)
        st.toast(f"저장됨: id={rec_id}, score={score_val}")

    # 슬라이더: 변경 시 즉시 저장
    st.slider(
        "Score (1~5)",
        min_value=1, max_value=5, step=1,
        key=score_key,
        help="5점에 가까울수록 프롬프트를 잘 반영한 설명입니다.",
        on_change=_auto_save_callback,
        args=(rec_id, video_name, score_key, csv_path)
    )

# ====== 진행 현황 ======
st.divider()
st.subheader("진행 현황")
if username.strip():
    st.dataframe(read_scores(csv_path_for(selected_file, username)), use_container_width=True)
else:
    st.caption("CSV 진행 현황은 사용자 이름 입력 후 표시됩니다.")
