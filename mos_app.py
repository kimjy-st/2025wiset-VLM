# mos_app.py — Streamlit Cloud 배포용 (Google Drive mapping.csv 사용, ID 정규화/리다이렉트 대응)
import os
import io
import re
import json
import pandas as pd
import requests
import streamlit as st

# =========================
# 설정: mapping.csv 의 Google Drive "file id" 또는 전체 URL을 넣어도 됩니다.
# 예) "1AbCdEf..." 또는 "https://drive.google.com/file/d/1AbCdEf.../view?usp=sharing"
MAPPING_FILE_ID_RAW = st.secrets.get("MAPPING_FILE_ID", "")
# =========================

st.set_page_config(page_title="MOS 라벨링 툴 (Drive)", layout="wide")

def extract_drive_id(s: str) -> str:
    """Drive file id 또는 전체 URL이 들어와도 fileId만 안정적으로 뽑아낸다."""
    if not s:
        return ""
    s = s.strip().strip('"').strip("'")
    # /d/<id>/ 패턴
    m = re.search(r"/d/([a-zA-Z0-9_-]+)", s)
    if m:
        return m.group(1)
    # ?id=<id> 패턴
    m = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", s)
    if m:
        return m.group(1)
    # 그냥 id가 들어왔을 때: 끝의 슬래시 제거
    return s.rstrip("/").strip()

MAPPING_FILE_ID = extract_drive_id(MAPPING_FILE_ID_RAW)

@st.cache_data(show_spinner=True)
def fetch_drive_file_binary(file_id: str) -> bytes:
    """
    공개 파일 기준: Google Drive 'uc?export=download&id='로 다운로드
    - allow_redirects=True 로 리다이렉트 허용
    - 로그인 페이지가 뜨면 친절한 에러 표시
    """
    if not file_id:
        raise RuntimeError("MAPPING_FILE_ID 가 비었습니다.")
    session = requests.Session()
    url = f"https://drive.google.com/uc?export=download&id={file_id}"
    resp = session.get(url, allow_redirects=True, timeout=60)
    # 드라이브가 로그인 페이지를 반환할 때의 흔한 신호
    if "ServiceLogin" in resp.text or "signin/v2/identifier" in resp.url:
        raise RuntimeError("Google이 로그인 페이지를 반환했습니다. 파일이 '링크가 있는 모든 사용자(보기)'로 공개되어 있는지 확인하세요.")
    resp.raise_for_status()
    return resp.content

@st.cache_data(show_spinner=True)
def load_mapping_csv(file_id: str) -> pd.DataFrame:
    content = fetch_drive_file_binary(file_id)
    # pandas가 제대로 읽지 못하면 에러를 던지게 함
    try:
        return pd.read_csv(io.BytesIO(content))
    except Exception as e:
        # 디버깅을 위해 앞부분 샘플 노출
        head = content[:200].decode("utf-8", errors="ignore")
        raise RuntimeError(f"CSV 파싱 실패: {e}\n응답 샘플: {head}")

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

def parse_jsonl_bytes(b: bytes):
    out = []
    for line in b.decode("utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            out.append(json.loads(s))
        except Exception:
            pass
    return out

def drive_preview_url(file_id: str) -> str:
    """Drive 미리보기(스트리밍) URL"""
    return f"https://drive.google.com/file/d/{file_id}/preview"

# ===== 사이드바 =====
st.sidebar.header("설정")

if not MAPPING_FILE_ID:
    st.sidebar.error("MAPPING_FILE_ID 가 비어 있습니다. Streamlit Secrets에 MAPPING_FILE_ID를 추가하세요.")
    st.stop()

# 디버그: 정규화된 ID와 테스트 URL 안내(필요시 확인)
with st.sidebar.expander("디버그: mapping.csv 링크 확인"):
    st.code(MAPPING_FILE_ID, language="text")
    st.markdown(f"[테스트 다운로드 링크](https://drive.google.com/uc?export=download&id={MAPPING_FILE_ID})")

try:
    mapping_df = load_mapping_csv(MAPPING_FILE_ID)
except Exception as e:
    st.sidebar.error(f"mapping.csv 로드 실패: {e}")
    st.stop()

# 기대 컬럼 확인
required_cols = {"name", "type", "file_id"}
if not required_cols.issubset(set(mapping_df.columns)):
    st.sidebar.error("mapping.csv 는 name,type,file_id 컬럼을 포함해야 합니다.")
    st.stop()

# 인덱스 구성
video_index = {str(row["name"]).strip(): str(row["file_id"]).strip()
               for _, row in mapping_df[mapping_df["type"].astype(str)=="video"].iterrows()}
jsonl_index = {str(row["name"]).strip(): str(row["file_id"]).strip()
               for _, row in mapping_df[mapping_df["type"].astype(str)=="jsonl"].iterrows()}

# mos_results 내 JSONL 목록만 추려서 셀렉트박스
# 모든 JSONL 파일을 포함 (mos_results 폴더 여부와 관계없이)
jsonl_display = sorted([name for name in jsonl_index.keys() if str(name).lower().endswith(".jsonl")])
selected_file = st.sidebar.selectbox("JSONL 파일 선택", jsonl_display)
username = st.sidebar.text_input("User name", value="", placeholder="예: jykim")

# ===== 세션 상태 =====
if "records" not in st.session_state:
    st.session_state["records"] = []
if "idx" not in st.session_state:
    st.session_state["idx"] = 0
if "active_file" not in st.session_state:
    st.session_state["active_file"] = None
if "scores" not in st.session_state:
    st.session_state["scores"] = pd.DataFrame(columns=["id", "video", "score", "rater"])

# 파일 변경 시 JSONL 로드
if selected_file and st.session_state["active_file"] != selected_file:
    fid = jsonl_index.get(selected_file)
    if not fid:
        st.error(f"선택한 JSONL을 찾지 못했습니다: {selected_file}")
        st.stop()
    try:
        data_bytes = fetch_drive_file_binary(fid)
        st.session_state["records"] = parse_jsonl_bytes(data_bytes)
        st.session_state["idx"] = 0
        st.session_state["active_file"] = selected_file
    except Exception as e:
        st.error(f"JSONL 로드 실패: {e}")
        st.stop()

st.title("MOS 라벨링 툴 (Google Drive)")

if not selected_file:
    st.info("왼쪽에서 JSONL 파일을 선택하세요.")
    st.stop()

records = st.session_state["records"]
if not isinstance(records, list) or len(records) == 0:
    st.warning("선택한 파일에서 항목을 불러오지 못했습니다.")
    st.stop()

# ===== 내비게이션 =====
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

# ===== 현재 항목 =====
curr = records[st.session_state["idx"]]
rec_id     = pick_first_key(curr, ["id", "idx"])
video_path = pick_first_key(curr, ["video", "video_path", "path"])
prompt     = pick_first_key(curr, ["prompt", "instruction", "question"])
# answer 오타/변형 대응
answer_raw = pick_first_key(curr, ["answer", "anwser", "caption", "response", "text"])
answer     = normalize_text(answer_raw)

# 비디오 파일명(basename)으로 매핑
video_basename = os.path.basename(str(video_path)).strip()
file_id = video_index.get(video_basename)

col_video, col_text = st.columns([3, 2], gap="large")
with col_video:
    st.subheader("Video (Google Drive)")
    if file_id:
        st.components.v1.iframe(drive_preview_url(file_id), height=400)
        st.caption("Google Drive 미리보기 스트리밍(재생바 이동 가능)")
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
        st.error(f"드라이브에서 '{video_basename}' 파일 ID를 찾을 수 없습니다.")
        with st.expander("해결 방법"):
            st.write("mapping.csv에 해당 파일명이 있는지 확인하세요. (name 열에 정확한 이름)")

with col_text:
    st.subheader("Prompt")
    st.text(prompt or "(없음)")
    st.subheader("Answer")
    st.text_area("", value=answer or "(없음)", height=160, label_visibility="collapsed", disabled=True)
    st.divider()

    # 이미 저장된 점수 불러오기
    default_score = 3
    if not st.session_state["scores"].empty:
        row = st.session_state["scores"]
        row = row[(row["id"]==rec_id) & (row["rater"]==username)]
        if not row.empty:
            try:
                default_score = int(row.iloc[0]["score"])
            except Exception:
                pass

    score_key = f"score::{selected_file}::{username}::{rec_id}"
    if score_key not in st.session_state:
        st.session_state[score_key] = default_score

    def _save_in_memory(rec_id, video_name, score_key, username):
        try:
            score_val = int(st.session_state[score_key])
        except Exception:
            return
        # upsert in session dataframe
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

# ===== 진행 현황 & 다운로드 =====
st.divider()
st.subheader("진행 현황")
st.dataframe(st.session_state["scores"], use_container_width=True)

csv_bytes = st.session_state["scores"].to_csv(index=False).encode("utf-8")
dl_name = f"{os.path.splitext(os.path.basename(selected_file))[0]}_{username or 'anonymous'}.csv"
st.download_button("CSV 다운로드", data=csv_bytes, file_name=dl_name, mime="text/csv")
