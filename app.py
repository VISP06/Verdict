"""Glacier browser terminal for payment advice."""

import datetime as dt
import html
import os
import sys

import pandas as pd
import streamlit as st

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "code"))
from assistant import Assistant
from terminal_flow import BANNER, TerminalSession, fmt_money

APP_VERSION = "glacier-terminal-v4"
DATASET = os.path.join(ROOT, "dataset")
profiles = pd.read_csv(os.path.join(DATASET, "financial_profiles.csv"), dtype={"user_id": str})

st.set_page_config(page_title="Glacier Terminal", page_icon="❄", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&display=swap');
:root{--bg:#08141F;--panel:#0D2233;--border:#1F4660;--frost:#D8F3FF;--dim:#7FA6BD;--cyan:#5CE1E6;--ice:#8CC8FF;--mint:#7CF5C0;--amber:#FFD479;--coral:#FF7A90}
html,body,[data-testid="stAppViewContainer"]{background:linear-gradient(180deg,#08141F 0%,#0D2233 100%) fixed;color:var(--frost);font-family:'JetBrains Mono',ui-monospace,monospace}
[data-testid="stHeader"],[data-testid="stToolbar"],#MainMenu,footer,header{visibility:hidden;height:0}
.block-container{max-width:980px;padding:1rem 1.2rem 5rem}
.status{position:sticky;top:0;z-index:5;background:#08141Fee;border-bottom:1px solid #1F4660;padding:.65rem 0;margin-bottom:1.1rem;display:flex;gap:.5rem;flex-wrap:wrap}
.chip{border:1px solid #1F4660;border-radius:999px;padding:.22rem .58rem;font-size:.78rem;color:#5CE1E6}.chip .label{color:#7FA6BD}
.welcome,.block{background:#0D2233;border:1px solid #1F4660;border-radius:8px;padding:1rem 1.15rem;margin:.7rem 0;color:#D8F3FF;line-height:1.6;white-space:pre-wrap;overflow-wrap:anywhere}
.welcome{border-color:#24516a}.welcome p{margin:.1rem 0 .8rem}.commands{font-size:.76rem;color:#7FA6BD;margin-top:.5rem}
.user-input{color:#D8F3FF}.user-input .prefix{color:#5CE1E6}.prompt-question{color:#8CC8FF;font-weight:600}.option-chip{display:inline-block;color:#8CC8FF;border:1px solid #31536b;border-radius:4px;padding:.08rem .4rem;margin:.45rem .35rem 0 0;font-size:.78rem}.option-chip.default{border-color:#5CE1E6;color:#5CE1E6}
.report{border:1px solid #1F4660;border-left:4px solid var(--status);border-radius:8px;background:#0D2233;padding:1rem 1.2rem;margin:.7rem 0;color:#D8F3FF;line-height:1.55;overflow-wrap:anywhere}.verdict{font-size:1.35rem;font-weight:700;color:var(--status);margin-bottom:.9rem}.section-title{color:#5CE1E6;font-size:.78rem;letter-spacing:.16em;font-weight:700;margin:1rem 0 .35rem}.report p{margin:.25rem 0}.spark{color:#5CE1E6;letter-spacing:.12em;font-size:1.1rem}.range{color:#7FA6BD;font-size:.78rem}.options-table{border-collapse:collapse;width:100%;font-family:inherit;font-size:.78rem}.options-table th{text-align:left;color:#7FA6BD;border-bottom:1px solid #31536b;padding:.35rem}.options-table td{padding:.35rem;border-bottom:1px solid #1a394e;vertical-align:top}.options-table thead th:nth-child(5){white-space:nowrap;min-width:9ch}.options-table thead th:nth-child(6){white-space:nowrap;min-width:6ch}.safe{color:#7CF5C0}.unsafe{color:#FF7A90}.unsafe-note{color:#FF7A90;padding:.1rem 0 .35rem 1rem;font-size:.76rem}.error{color:#FF7A90}
div[data-testid="stChatInput"]{border:1px solid #1F4660;background:#0D2233;position:relative;padding-left:5.7rem}div[data-testid="stChatInput"]:before{content:'glacier ❯';position:absolute;left:1rem;top:.85rem;color:#5CE1E6;font-family:'JetBrains Mono',monospace;font-size:.85rem;pointer-events:none}div[data-testid="stChatInput"] textarea{font-family:'JetBrains Mono',monospace;color:#D8F3FF;caret-color:#5CE1E6;background:transparent}div[data-testid="stChatInput"] button{background:transparent!important;border:0!important;color:#5CE1E6!important;box-shadow:none!important}
div.stButton>button{background:#10283a;color:#8CC8FF;border:1px solid #31536b;border-radius:999px;font-family:inherit;font-size:.78rem;padding:.25rem .65rem}div.stButton>button:hover{border-color:#5CE1E6;color:#5CE1E6}
[class*="st-key-mcq_"]{max-width:540px;margin:.15rem 0 .6rem}[class*="st-key-mcq_"] button{justify-content:flex-start;text-align:left;border-radius:8px;background:#0D2233;border:1px solid #1F4660;color:#D8F3FF;padding:.55rem .9rem;font-size:.86rem}[class*="st-key-mcq_"] button:hover{border-color:#5CE1E6;background:#10304a;color:#D8F3FF}[class*="st-key-mcq_"] button:focus-visible{outline:2px solid #5CE1E6}[class*="st-key-mcq_"] button p{display:flex;gap:.55rem;align-items:center;margin:0}[class*="st-key-mcq_"] button[kind="primary"]{border-color:#2c6f7a}
div[data-testid="stDownloadButton"] button{background:transparent;color:#7FA6BD;border:1px solid #31536b;font-family:inherit;font-size:.75rem;padding:.2rem .55rem}
</style>""", unsafe_allow_html=True)


def esc(value):
    """Escape any dynamic/user-provided value before it enters HTML."""
    return html.escape(str(value), quote=True)


def report_html(block):
    data = block.get("data", {})
    status = data.get("status", "caution")
    color = {"good": "#7CF5C0", "caution": "#FFD479", "bad": "#FF7A90"}.get(status, "#FFD479")
    numbers = data.get("numbers", {})
    vals = numbers.get("sparkline_values", [])
    levels = "▁▂▃▄▅▆▇█"
    lo, hi = (min(vals), max(vals)) if vals else (0, 0)
    spark = "".join(levels[round((v-lo)/(hi-lo)*7)] if hi != lo else "▄" for v in vals)
    range_text = (f"{numbers.get('start_date', '')} · {fmt_money(numbers.get('start_balance', 0), currency)}"
                  f" → {numbers.get('end_date', '')} · {fmt_money(numbers.get('end_balance', 0), currency)}")
    pieces = [f'<div class="report status-{esc(status)}" style="--status:{color}">',
              f'<div class="verdict">{esc(data.get("verdict", "Report"))}</div>']
    for title, key in (("WHY", "why"),):
        pieces += [f'<div class="section-title">{title}</div><p>{esc(data.get(key, ""))}</p>']
    pieces += [f'<div class="section-title">NUMBERS</div><div class="spark">{esc(spark)}</div>',
               f'<p>Minimum balance: {esc(fmt_money(numbers.get("minimum", 0), currency))}</p>',
               f'<div class="range">{esc(range_text)}</div>', '<div class="section-title">OPTIONS</div>',
               '<table class="options-table"><thead><tr><th>OPTION</th><th>WHEN</th><th>COST</th><th>LOWEST</th><th>DEADLINE</th><th>SAFE</th></tr></thead><tbody>']
    for option in data.get("options", []):
        mark = '<span class="safe">✓</span>' if option.get("safe") else '<span class="unsafe">✗</span>'
        pieces += [f'<tr><td>{esc(option.get("name", ""))}</td><td>{esc(option.get("when", ""))}</td>'
                   f'<td>{esc(option.get("cost", ""))}</td><td>{esc(option.get("lowest", ""))}</td>'
                   f'<td>{esc(option.get("meets_deadline", "n/a"))}</td><td>{mark}</td></tr>']
        if option.get("note"):
            pieces.append(f'</tbody></table><div class="unsafe-note">{esc(option["note"])}</div><table class="options-table"><tbody>')
    pieces += ['</tbody></table>']
    for title, key in (("ALTERNATIVES", "alternatives"), ("NEXT STEPS", "next_steps")):
        pieces.append(f'<div class="section-title">{title}</div>')
        pieces.extend(f'<p>• {esc(line.lstrip("• "))}</p>' for line in data.get(key, []))
    if data.get("loan"):
        pieces.append('<div class="section-title">LOAN OPTION (illustrative)</div>')
        pieces.append(f'<p>{esc(data["loan"])}</p>')
    return "".join(pieces) + "</div>"


user_ids = profiles.user_id.astype(str).tolist()
user_id = st.session_state.get("terminal_user", "user_07" if "user_07" in user_ids else user_ids[0])
date_key = st.session_state.get("terminal_date")
session_key = f"terminal_session_{APP_VERSION}"
session = st.session_state.get(session_key)
if (not isinstance(session, TerminalSession) or not hasattr(getattr(session, "assistant", None), "situation")
        or session.user_id != user_id or (date_key and session.as_of_date != date_key)):
    if date_key is None:
        assistant = Assistant(user_id, dt.date.today().isoformat(), dataset_dir=DATASET, no_llm=True)
        dates = assistant.engine.events[assistant.engine.events.user_id.astype(str).eq(user_id)].event_date.astype(str)
        if len(dates):
            last = sorted(dates.astype(str))[-1][:10]
            date_key = (dt.date.fromisoformat(last) + dt.timedelta(days=1)).isoformat()
        else:
            date_key = dt.date.today().isoformat()
        st.session_state.terminal_date = date_key
    session = TerminalSession(user_id, date_key, dataset_dir=DATASET,
                              assistant=Assistant(user_id, date_key, dataset_dir=DATASET, no_llm=True))
    st.session_state[session_key] = session
    currency = str(session.assistant.engine.get_user_home_currency(user_id))
    st.session_state.terminal_output = [{"kind": "welcome", "plain_text": BANNER},
                                       {"kind": "text", "plain_text": f"Account currency: {currency}. Amounts without a currency are treated as {currency}."}]
    if session.as_of_note:
        st.session_state.terminal_output.append({"kind": "text", "plain_text": session.as_of_note})

try:
    situation = session.assistant.situation()
    currency = str(session.assistant.engine.get_user_home_currency(user_id))
    chips = [("user", user_id), ("currency", currency), ("balance", fmt_money(situation["current_balance"], currency)),
             ("minimum", fmt_money(situation["minimum_balance"], currency)), ("as-of", session.as_of_date)]
except Exception:
    currency = "INR"
    chips = [("user", user_id), ("currency", currency), ("as-of", session.as_of_date)]
st.markdown('<div class="status">' + "".join(f'<span class="chip"><span class="label">{esc(k)} </span>{esc(v)}</span>' for k, v in chips) + "</div>", unsafe_allow_html=True)

output = st.session_state.get("terminal_output", [])
latest_prompt_index = max((i for i, item in enumerate(output) if item.get("kind") == "prompt"), default=-1)
latest_report_index = max((i for i, item in enumerate(output) if item.get("kind") == "report"), default=-1)
for block_index, block in enumerate(output):
    kind = block.get("kind", "text")
    if kind == "welcome":
        st.markdown(f'<div class="welcome"><p>{esc(block["plain_text"])}</p></div>', unsafe_allow_html=True)
        examples = ["Can I afford a laptop that costs ₹12k?", "Can I pay ₹50,000 in 6 installments?", "/situation"]
        cols = st.columns(3)
        clicked = next((example for col, example in zip(cols, examples) if col.button(example, key="example_" + str(examples.index(example)))), None)
        if clicked:
            line = clicked
            blocks = session.handle(line)
            output = list(output) + [{"kind": "user_input", "plain_text": line}] + blocks
            st.session_state.terminal_output = output
            st.rerun()
        st.markdown('<div class="commands">/help　/users　/user ID　/date YYYY-MM-DD　/situation　/why　/reset　/export</div>', unsafe_allow_html=True)
    elif kind == "report":
        st.markdown(report_html(block), unsafe_allow_html=True)
        if block_index == latest_report_index:
            chip_lines = session.quick_replies()
            cols = st.columns(len(chip_lines) + 1)
            for col, (label, value) in zip(cols, chip_lines):
                if col.button(label, key=f"followup_{block_index}_{label}"):
                    st.session_state.pending_line = value
            report_text = block.get("plain_text", "")
            cols[-1].download_button("Download report", report_text, file_name="glacier-report.txt",
                                     mime="text/plain", key=f"download_report_{block_index}")
    elif kind == "export":
        st.download_button("Download report", block.get("plain_text", ""), file_name="glacier-report.txt",
                           mime="text/plain", key=f"export_report_{block_index}")
    elif kind == "prompt":
        data = block.get("data", {})
        active = block_index == latest_prompt_index
        st.markdown(f'<div class="block"><div class="prompt-question">{esc(data.get("question", block["plain_text"]))}</div></div>', unsafe_allow_html=True)
        options = data.get("options", [])
        if active and options:
            with st.container(key=f"mcq_{block_index}"):
                for option_index, option in enumerate(options):
                    label = option.get("label", "")
                    if option.get("default"):
                        label += "  :gray[default]"
                    icon = ":material/radio_button_checked:" if option.get("default") else ":material/radio_button_unchecked:"
                    if st.button(label, icon=icon, key=f"prompt_{block_index}_{option_index}", use_container_width=True):
                        st.session_state.pending_line = str(option.get("key", ""))
                        st.session_state.pending_label = option.get("label", "")
        picker = data.get("picker")
        if active and picker:
            low = dt.date.fromisoformat(picker["min"]); high = max(low, dt.date.fromisoformat(picker["max"]))
            with st.container(key=f"mcq_date_{block_index}"):
                picked = st.date_input(picker.get("label", "Choose a date"), value=low, min_value=low, max_value=high, key=f"deadline_date_{block_index}")
                if st.button("Confirm date", icon=":material/check:", key=f"confirm_date_{block_index}"):
                    st.session_state.pending_line = picked.isoformat()
                    st.session_state.pending_label = picked.isoformat()
    elif kind == "user_input":
        st.markdown(f'<div class="block user-input"><span class="prefix">❯ </span>{esc(block["plain_text"])}</div>', unsafe_allow_html=True)
    else:
        cls = "error" if kind == "error" else ""
        st.markdown(f'<div class="block {cls}">{esc(block["plain_text"])}</div>', unsafe_allow_html=True)

line = st.chat_input("enter a request or command")
pending = st.session_state.get("pending_line")
pending_label = None
if pending is not None:
    line = pending
    pending_label = st.session_state.get("pending_label")
    st.session_state.pending_line = None
    st.session_state.pending_label = None
if line is not None:
    blocks = session.handle(line)
    output = list(output) + [{"kind": "user_input", "plain_text": pending_label or line}] + blocks
    st.session_state.terminal_output = output
    if line.startswith("/user "):
        candidate = line.split(maxsplit=1)[1]
        if candidate in user_ids: st.session_state.terminal_user = candidate
    if line.startswith("/date "):
        try: st.session_state.terminal_date = dt.date.fromisoformat(line.split(maxsplit=1)[1]).isoformat()
        except ValueError: pass
    st.rerun()

st.components.v1.html("<script>window.parent.document.querySelector('section.main').scrollTo(0, document.body.scrollHeight);</script>", height=0)
