"""쇼핑리스트 Streamlit 앱."""

import html
import json
import os
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------
DATA_FILE = Path(__file__).parent / "shopping_list.json"
PAGE_TITLE = "쇼핑리스트"
PAGE_ICON = "🛒"
APP_TITLE = "🛒 쇼핑리스트"
EMPTY_MESSAGE = "쇼핑리스트가 비어 있습니다. 아이템을 추가해보세요!"
EMPTY_NAME_WARNING = "아이템 이름을 입력해주세요."
ALL_DONE_MESSAGE = "🎉 장보기 완료!"

QTY_MIN = 1
QTY_MAX = 999
QTY_HELP = f"{QTY_MIN}~{QTY_MAX} 사이의 정수"

STATE_ITEMS = "items"
STATE_EDITING_ID = "editing_id"
STATE_CONFIRM_CLEAR = "confirm_clear"
STATE_MESSAGES = "messages"

ADD_NAME_KEY = "add_name"
ADD_QTY_KEY = "add_qty"

MARKDOWN_SPECIAL = re.compile(r"([\\`*_~\[\]()#+\-.!|<>{}:$])")

# 색은 반투명/상속 값만 사용해 라이트·다크 테마 모두에서 읽히도록 한다.
CUSTOM_CSS = """
<style>
/* 목록·추가 폼의 행은 좁은 화면에서도 세로로 쌓이지 않게 한 줄 유지 */
.st-key-item_list [data-testid="stHorizontalBlock"],
.st-key-add_row [data-testid="stHorizontalBlock"] {
    flex-wrap: nowrap !important;
    gap: 0.5rem;
}
.st-key-item_list [data-testid="stColumn"],
.st-key-add_row [data-testid="stColumn"] {
    min-width: 0 !important;
}
/* 아이템 행 사이 간격 */
.st-key-item_list [data-testid="stVerticalBlock"] { gap: 0.35rem; }
/* Streamlit 마크다운 기본 음수 여백(-1rem) 제거 → 라벨 높이가 정상 계산되어 세로 정렬·여백이 맞음 */
.st-key-item_list [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }

.item-label {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    min-width: 0;
    line-height: 1.6;
}
.item-label .item-name {
    overflow-wrap: anywhere;
    min-width: 0;
}
.item-label.checked .item-name {
    text-decoration: line-through;
    opacity: 0.5;
}
.qty-badge {
    flex-shrink: 0;
    font-size: 0.75rem;
    font-weight: 600;
    padding: 0.05rem 0.5rem;
    border-radius: 999px;
    background: rgba(128, 128, 128, 0.18);
}
.item-label.checked .qty-badge { opacity: 0.5; }

.empty-message {
    text-align: center;
    opacity: 0.55;
    padding: 1.5rem 0;
}
.progress-text { margin-bottom: 0.25rem; }
</style>
"""


# ---------------------------------------------------------------------------
# 데이터
# ---------------------------------------------------------------------------
def now_iso() -> str:
    """현재 시각을 ISO 8601(초 단위) 문자열로 반환한다."""
    return datetime.now().isoformat(timespec="seconds")


def create_item(name: str, quantity: int = 1) -> dict:
    """PRD 데이터 모델에 맞는 새 아이템 딕셔너리를 만든다."""
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "quantity": quantity,
        "checked": False,
        "created_at": now_iso(),
    }


def clamp_qty(value) -> int | None:
    """수량을 QTY_MIN~QTY_MAX 정수로 변환한다. 변환할 수 없으면 None."""
    if isinstance(value, bool):
        return None
    try:
        number = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None
    return min(max(number, QTY_MIN), QTY_MAX)


def sanitize_item(raw, seen_ids: set[str]) -> tuple[dict | None, bool]:
    """저장 파일의 항목 하나를 검증한다. (보정된 항목 또는 None, 보정 여부)를 반환한다."""
    if not isinstance(raw, dict):
        return None, True
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        return None, True

    item_id = raw.get("id")
    if not isinstance(item_id, str) or not item_id or item_id in seen_ids:
        item_id = str(uuid.uuid4())
    quantity = clamp_qty(raw.get("quantity"))
    checked = raw.get("checked")
    created_at = raw.get("created_at")

    item = {
        "id": item_id,
        "name": name.strip(),
        "quantity": quantity if quantity is not None else QTY_MIN,
        "checked": checked if isinstance(checked, bool) else False,
        "created_at": created_at if isinstance(created_at, str) else now_iso(),
    }
    seen_ids.add(item_id)
    changed = any(raw.get(k) != v for k, v in item.items()) or len(raw) != len(item)
    return item, changed


def backup_path() -> Path:
    """손상된 원본을 보관할 파일 경로를 만든다."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return DATA_FILE.with_name(f"{DATA_FILE.stem}.corrupt-{stamp}{DATA_FILE.suffix}")


def load_items() -> list[dict]:
    """저장 파일에서 아이템 목록을 불러온다.

    읽을 수 없거나 구조가 잘못된 경우 원본을 백업 파일로 보관하고 경고를 남긴다.
    """
    if not DATA_FILE.exists():
        return []
    try:
        raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        raw = None

    if not isinstance(raw, list):
        backup = backup_path()
        try:
            DATA_FILE.replace(backup)
            where = f" 원본은 {backup.name}에 보관했습니다."
        except OSError:
            where = ""
        flash("warning", f"저장 파일을 읽을 수 없어 빈 목록으로 시작합니다.{where}")
        return []

    seen: set[str] = set()
    cleaned, problems = [], 0
    for entry in raw:
        item, changed = sanitize_item(entry, seen)
        problems += changed
        if item is not None:
            cleaned.append(item)

    if problems:
        backup = backup_path()
        shutil.copy2(DATA_FILE, backup)
        save_items(cleaned)
        flash(
            "warning",
            f"저장 파일의 항목 {problems}개가 잘못되어 보정하거나 제외했습니다. "
            f"원본은 {backup.name}에 보관했습니다.",
        )
    return cleaned


def save_items(items: list[dict]) -> None:
    """아이템 목록을 임시 파일에 쓴 뒤 교체해 원자적으로 저장한다."""
    tmp = DATA_FILE.with_name(DATA_FILE.name + ".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, DATA_FILE)


def commit(items: list[dict]) -> None:
    """변경된 목록을 저장하고 세션에 반영한다. 목록이 바뀌면 전체 삭제 확인도 닫는다."""
    save_items(items)
    st.session_state[STATE_ITEMS] = items
    st.session_state[STATE_CONFIRM_CLEAR] = False


def current_items() -> list[dict]:
    """이번 렌더링에서 사용하는 아이템 목록을 반환한다."""
    return st.session_state[STATE_ITEMS]


def find_item(items: list[dict], item_id: str) -> dict | None:
    """id로 아이템을 찾는다."""
    return next((it for it in items if it["id"] == item_id), None)


def init_state() -> None:
    """세션 상태를 준비하고, 다른 탭의 변경을 반영하도록 매 실행마다 파일을 다시 읽는다."""
    st.session_state.setdefault(STATE_MESSAGES, [])
    st.session_state.setdefault(STATE_EDITING_ID, None)
    st.session_state.setdefault(STATE_CONFIRM_CLEAR, False)
    items = load_items()
    st.session_state[STATE_ITEMS] = items
    if find_item(items, st.session_state[STATE_EDITING_ID] or "") is None:
        st.session_state[STATE_EDITING_ID] = None
    if not items:
        st.session_state[STATE_CONFIRM_CLEAR] = False


# ---------------------------------------------------------------------------
# 메시지 (콜백에서 만든 알림을 다음 렌더링에 표시)
# ---------------------------------------------------------------------------
def flash(level: str, text: str) -> None:
    """다음 화면 렌더링 때 보여줄 메시지를 쌓는다. level: warning/info/toast."""
    st.session_state.setdefault(STATE_MESSAGES, []).append((level, text))


def render_messages() -> None:
    """쌓인 메시지를 표시하고 비운다."""
    for level, text in st.session_state[STATE_MESSAGES]:
        if level == "toast":
            st.toast(text)
        else:
            getattr(st, level)(text)
    st.session_state[STATE_MESSAGES] = []


def escape_md(text: str) -> str:
    """사용자 입력을 마크다운으로 안전하게 표시하도록 특수문자를 이스케이프한다."""
    return MARKDOWN_SPECIAL.sub(r"\\\1", text)


# ---------------------------------------------------------------------------
# 콜백: 항상 파일의 최신 목록을 읽어 그 위에 변경을 적용한 뒤 저장한다.
# (다른 탭의 변경을 덮어쓰지 않기 위함. 콜백 후 Streamlit이 자동으로 rerun)
# ---------------------------------------------------------------------------
def on_add() -> None:
    """F1: 입력값을 검증해 새 아이템을 목록 끝에 추가한다."""
    name = st.session_state[ADD_NAME_KEY].strip()
    quantity = clamp_qty(st.session_state[ADD_QTY_KEY]) or QTY_MIN
    if not name:
        flash("warning", EMPTY_NAME_WARNING)
        return
    items = load_items()
    if any(it["name"].lower() == name.lower() for it in items):
        flash("info", f"'{escape_md(name)}'은(는) 이미 목록에 있어요. 그래도 추가했습니다.")
    items.append(create_item(name, quantity))
    commit(items)
    flash("toast", f"✅ {escape_md(name)} 추가됨")


def on_start_edit(item_id: str) -> None:
    """F2: 해당 아이템을 편집 모드로 전환하고 입력칸에 현재 값을 채운다."""
    item = find_item(load_items(), item_id)
    if item is None:
        return
    st.session_state[STATE_EDITING_ID] = item_id
    st.session_state[f"name_{item_id}"] = item["name"]
    st.session_state[f"qty_{item_id}"] = item["quantity"]


def on_save_edit(item_id: str) -> None:
    """F2: 편집한 이름·수량을 저장한다. 빈 이름은 거부한다."""
    name = st.session_state[f"name_{item_id}"].strip()
    if not name:
        flash("warning", EMPTY_NAME_WARNING)
        return
    items = load_items()
    item = find_item(items, item_id)
    st.session_state[STATE_EDITING_ID] = None
    if item is None:
        flash("warning", "다른 곳에서 삭제된 항목이라 저장하지 못했습니다.")
        return
    item["name"] = name
    item["quantity"] = clamp_qty(st.session_state[f"qty_{item_id}"]) or QTY_MIN
    commit(items)
    flash("toast", f"✏️ {escape_md(name)} 수정됨")


def on_cancel_edit() -> None:
    """F2: 편집을 취소한다."""
    st.session_state[STATE_EDITING_ID] = None


def on_delete(item_id: str) -> None:
    """F3: 아이템 하나를 삭제한다."""
    items = load_items()
    item = find_item(items, item_id)
    if st.session_state[STATE_EDITING_ID] == item_id:
        st.session_state[STATE_EDITING_ID] = None
    commit([it for it in items if it["id"] != item_id])
    if item is not None:
        flash("toast", f"🗑️ {escape_md(item['name'])} 삭제됨")


def on_delete_checked() -> None:
    """F3: 체크된 아이템을 모두 삭제한다."""
    items = load_items()
    remaining = [it for it in items if not it["checked"]]
    commit(remaining)
    flash("toast", f"🗑️ 체크된 항목 {len(items) - len(remaining)}개 삭제됨")


def on_request_clear(confirm: bool) -> None:
    """F3: 전체 삭제 확인 단계를 켜거나 끈다."""
    st.session_state[STATE_CONFIRM_CLEAR] = confirm


def on_clear_all() -> None:
    """F3: 확인 후 모든 아이템을 삭제한다."""
    st.session_state[STATE_EDITING_ID] = None
    commit([])
    flash("toast", "🗑️ 전체 삭제됨")


def on_toggle(item_id: str) -> None:
    """F4: 체크박스 값으로 아이템의 구매 완료 여부를 갱신한다."""
    items = load_items()
    item = find_item(items, item_id)
    if item is not None:
        item["checked"] = bool(st.session_state[f"check_{item_id}"])
        commit(items)


# ---------------------------------------------------------------------------
# 화면 영역
# ---------------------------------------------------------------------------
def render_header() -> None:
    """타이틀과 구매 진행 상황을 표시한다."""
    st.title(APP_TITLE)
    total = len(current_items())
    done = sum(1 for it in current_items() if it["checked"])
    st.markdown(
        f'<div class="progress-text">구매 완료 <b>{done}</b> / 전체 <b>{total}</b></div>',
        unsafe_allow_html=True,
    )
    st.progress(done / total if total else 0.0)
    if total and done == total:
        st.success(ALL_DONE_MESSAGE)


def render_add_form() -> None:
    """F1: 아이템 추가 폼을 한 줄(이름 | 수량 | 추가)로 표시한다."""
    with st.form("add_form", clear_on_submit=True), st.container(key="add_row"):
        col_name, col_qty, col_btn = st.columns([5, 1.5, 1.5], vertical_alignment="bottom")
        col_name.text_input("아이템 이름", key=ADD_NAME_KEY, placeholder="예: 우유")
        col_qty.number_input(
            "수량", min_value=QTY_MIN, max_value=QTY_MAX, value=QTY_MIN, step=1,
            key=ADD_QTY_KEY, help=QTY_HELP,
        )
        col_btn.form_submit_button(
            "추가", on_click=on_add, type="primary", width="stretch", help="목록에 추가"
        )


def item_label_html(item: dict) -> str:
    """아이템 이름과 수량 배지를 HTML로 만든다 (사용자 입력은 이스케이프)."""
    css = "item-label checked" if item["checked"] else "item-label"
    return (
        f'<div class="{css}"><span class="item-name">{html.escape(item["name"])}</span>'
        f'<span class="qty-badge">x{item["quantity"]}</span></div>'
    )


def render_item_row(item: dict) -> None:
    """아이템 한 줄을 보기 모드 또는 편집 모드로 표시한다."""
    item_id = item["id"]
    label = escape_md(item["name"])
    if st.session_state[STATE_EDITING_ID] == item_id:
        col_name, col_qty, col_save, col_cancel = st.columns(
            [4, 1.6, 1, 1], vertical_alignment="bottom"
        )
        col_name.text_input("이름", key=f"name_{item_id}")
        col_qty.number_input(
            "수량", min_value=QTY_MIN, max_value=QTY_MAX, step=1,
            key=f"qty_{item_id}", help=QTY_HELP,
        )
        col_save.button(
            "💾", key=f"save_{item_id}", help=f"'{label}' 저장", type="primary",
            on_click=on_save_edit, args=(item_id,), width="stretch",
        )
        col_cancel.button(
            "✖️", key=f"cancel_{item_id}", help="편집 취소",
            on_click=on_cancel_edit, width="stretch",
        )
        return

    col_check, col_label, col_edit, col_del = st.columns(
        [0.5, 7, 0.6, 0.6], vertical_alignment="center"
    )
    # 다른 탭에서 바뀐 체크 상태를 반영한다. 값이 다를 때만 덮어써야
    # 빠른 연속 클릭 중에 서버가 보낸 값이 사용자의 새 클릭을 덮지 않는다.
    check_key = f"check_{item_id}"
    if st.session_state.get(check_key) != item["checked"]:
        st.session_state[check_key] = item["checked"]
    col_check.checkbox(
        f"'{label}' 구매 완료",
        key=check_key,
        on_change=on_toggle,
        args=(item_id,),
        label_visibility="collapsed",
    )
    col_label.markdown(item_label_html(item), unsafe_allow_html=True)
    col_edit.button(
        "✏️", key=f"edit_{item_id}", help=f"'{label}' 수정", type="tertiary",
        on_click=on_start_edit, args=(item_id,),
    )
    col_del.button(
        "🗑️", key=f"del_{item_id}", help=f"'{label}' 삭제", type="tertiary",
        on_click=on_delete, args=(item_id,),
    )


def render_item_list() -> None:
    """F2~F4: 아이템 목록(체크/수정/삭제)을 테두리 상자 안에 표시한다."""
    with st.container(border=True, key="item_list"):
        if not current_items():
            st.markdown(
                f'<div class="empty-message">{EMPTY_MESSAGE}</div>', unsafe_allow_html=True
            )
            return
        for item in current_items():
            render_item_row(item)


def render_bulk_actions() -> None:
    """F3: 체크된 항목 삭제, 전체 삭제(확인 단계 포함) 버튼을 표시한다."""
    has_items = bool(current_items())
    has_checked = any(it["checked"] for it in current_items())
    col_checked, col_clear = st.columns(2)
    col_checked.button(
        "체크된 항목 삭제", on_click=on_delete_checked, disabled=not has_checked,
        width="stretch", help="구매 완료로 체크한 항목을 모두 삭제",
    )
    if not st.session_state[STATE_CONFIRM_CLEAR]:
        col_clear.button(
            "전체 삭제", on_click=on_request_clear, args=(True,), disabled=not has_items,
            width="stretch", help="목록의 모든 항목을 삭제",
        )
        return
    with col_clear:
        st.warning("정말 전체 삭제할까요?")
        col_yes, col_no = st.columns(2)
        col_yes.button("확인", on_click=on_clear_all, type="primary", width="stretch")
        col_no.button(
            "취소", key="cancel_clear", on_click=on_request_clear, args=(False,),
            width="stretch",
        )


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------
def main() -> None:
    """페이지 설정 후 각 화면 영역을 순서대로 그린다."""
    st.set_page_config(page_title=PAGE_TITLE, page_icon=PAGE_ICON, layout="centered")
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
    init_state()
    render_header()
    render_messages()
    render_add_form()
    render_item_list()
    render_bulk_actions()


main()
