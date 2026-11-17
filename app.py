"""Verdict browser terminal for payment advice."""

import datetime as dt
import html
import math
import os
import sys

import pandas as pd
import streamlit as st

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "code"))
from assistant import Assistant
from terminal_flow import (BANNER, TerminalSession, fmt_money, profile_summary_text,
                           request_date_for_user, resolve_user_id, user_picker_options,
                           user_switch_arg)

APP_VERSION = "verdict-terminal-v5"
DATASET = os.path.join(ROOT, "dataset")
profiles = pd.read_csv(os.path.join(DATASET, "financial_profiles.csv"), dtype={"user_id": str})

st.set_page_config(page_title="Verdict", page_icon=":material/gavel:", layout="wide",
                   initial_sidebar_state="collapsed")

STYLES = """
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&display=swap');

:root{
  --bg:#061019;--bg-2:#0A1C2A;
  --panel:#0C2131;--panel-2:#10293C;--panel-3:#0A1D2B;
  --border:#1E4E67;--border-2:#2A5F79;--border-3:#3A7E99;
  --ink:#DCEFFB;--dim:#9CBFD2;--faint:#7FA6BD;
  --cyan:#5CE1E6;--ice:#8CC8FF;--mint:#7CF5C0;--amber:#FFD479;--coral:#FF7A90;
  --good:#7CF5C0;--caution:#FFD479;--bad:#FF7A90;
  --radius:10px;--radius-sm:7px;
  --mono:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
  --ease:cubic-bezier(.16,1,.3,1);
}

html,body,[data-testid="stAppViewContainer"]{background-color:var(--bg);color:var(--ink);font-family:var(--mono)}
[data-testid="stAppViewContainer"]{background-image:radial-gradient(1100px 520px at 50% -12%,#0E2A3E 0%,rgba(6,16,25,0) 72%);background-attachment:fixed}
[data-testid="stHeader"],[data-testid="stToolbar"],[data-testid="stDecoration"],[data-testid="stStatusWidget"],#MainMenu,footer{display:none!important}
[data-testid="stSidebar"],[data-testid="stSidebarCollapsedControl"]{display:none!important}

.block-container,[data-testid="stMainBlockContainer"]{max-width:1060px;padding:1.5rem 1.4rem 7rem}

::selection{background:#1D5A72;color:#EAF9FF}
textarea,input,[data-baseweb="select"]{caret-color:var(--cyan)}
*::-webkit-scrollbar{width:10px;height:10px}
*::-webkit-scrollbar-track{background:transparent}
*::-webkit-scrollbar-thumb{background:#1C4A61;border:2px solid transparent;border-radius:6px;background-clip:padding-box}
*::-webkit-scrollbar-thumb:hover{background:#2E7391;background-clip:padding-box}
:focus-visible{outline:2px solid var(--cyan);outline-offset:2px;border-radius:4px}

/* ---- masthead ---- */
.masthead{position:relative;display:flex;align-items:baseline;justify-content:space-between;gap:1rem;
  flex-wrap:wrap;padding-bottom:.75rem;border-bottom:1px solid var(--border)}
.masthead em{font-style:normal;color:var(--faint);font-size:.8rem;letter-spacing:.02em}
.wordmark{font-size:1.06rem;font-weight:700;letter-spacing:.3em;text-transform:uppercase;color:var(--ink)}
.wordmark b{color:var(--cyan);font-weight:700}
.masthead .rule{position:absolute;left:0;right:0;bottom:-1px;height:1px;
  background:radial-gradient(140px 1px at var(--mx,50%) 50%,var(--cyan),rgba(92,225,230,0) 72%);
  opacity:.8;transition:opacity .25s var(--ease)}
.masthead:hover .rule{opacity:1}

/* ---- readout strip ---- */
.status{display:flex;flex-wrap:wrap;gap:.4rem;padding:.75rem 0 0}
.chip{display:inline-flex;align-items:baseline;gap:.4rem;border:1px solid var(--border);background:var(--panel);
  border-radius:5px;padding:.3rem .6rem;font-size:.82rem;color:var(--ice);font-variant-numeric:tabular-nums}
.chip .label{color:var(--faint);font-size:.68rem;letter-spacing:.13em;text-transform:uppercase}
.profile{color:var(--dim);font-size:.82rem;line-height:1.5;padding-top:.15rem}
.profile b{color:var(--ink);font-weight:600}

/* ---- transcript blocks ---- */
.block{background:var(--panel);border:1px solid var(--border);border-radius:var(--radius-sm);
  padding:.85rem 1rem;margin:.65rem 0;color:var(--ink);line-height:1.6;white-space:pre-wrap;overflow-wrap:anywhere}
.welcome{position:relative;overflow:hidden;background:linear-gradient(180deg,var(--panel-2),var(--panel));
  border:1px solid var(--border-2);border-radius:var(--radius);padding:1.05rem 1.2rem;margin:1rem 0 .6rem;
  color:var(--ink);line-height:1.6}
.welcome p{margin:0;max-width:74ch;font-size:1rem}
.block:after,.welcome:after,.report:after{content:'';position:absolute;inset:0;pointer-events:none;opacity:0;
  transition:opacity .3s var(--ease);
  background:radial-gradient(360px 180px at var(--mx,50%) var(--my,0%),rgba(92,225,230,.08),rgba(92,225,230,0) 70%)}
.block,.welcome{position:relative;overflow:hidden}
.is-hovered:after{opacity:1}
/* ---- full-screen first-run launch experience ---- */
body:has(.st-key-launchpad) [data-testid="stBottom"]{display:none}
body:has(.st-key-launchpad) .block-container,
body:has(.st-key-launchpad) [data-testid="stMainBlockContainer"]{
  width:100%!important;max-width:none!important;margin:0!important;padding:0!important}
body:has(.st-key-launchpad) .masthead,
body:has(.st-key-launchpad) .st-key-user_switcher,
body:has(.st-key-launchpad) .profile,
body:has(.st-key-launchpad) .status{display:none!important}
body:has(.st-key-launchpad) [data-testid="stMain"]{padding:0!important}
.st-key-launchpad{position:relative;isolation:isolate;width:100%;min-height:100vh;min-height:100svh;margin:0;
  padding:clamp(2rem,7vh,5rem) clamp(1.25rem,5vw,5rem);border:0;border-radius:0;overflow:hidden;
  display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;
  background:radial-gradient(ellipse at 50% 45%,rgba(13,48,68,.5),transparent 58%),#061019}
.st-key-launchpad:before{content:"";position:absolute;inset:0;z-index:-1;pointer-events:none;opacity:.2;
  background-image:linear-gradient(rgba(92,225,230,.11) 1px,transparent 1px),linear-gradient(90deg,rgba(92,225,230,.11) 1px,transparent 1px);
  background-size:56px 56px;mask-image:radial-gradient(ellipse at center,black 0%,transparent 82%)}
.launch-canvas{position:absolute;inset:0;width:100%;height:100%;z-index:0;pointer-events:none}
.st-key-launchpad>div:not(.launch-canvas){position:relative;z-index:1}
.launch-content{position:relative;z-index:1;width:min(100%,920px);margin:0 auto}
.launch-brand{display:inline-flex;align-items:center;gap:.7rem;margin-bottom:1.7rem;color:var(--cyan);font-size:.72rem;
  font-weight:600;letter-spacing:.3em;text-transform:uppercase}
.launch-brand:before,.launch-brand:after{content:"";width:28px;height:1px;background:var(--border-3)}
.launch-title{max-width:900px;margin:0 auto;color:var(--ink);font-size:clamp(2.45rem,7vw,5.4rem);font-weight:600;
  line-height:1.04;letter-spacing:-.055em;text-wrap:balance}
.launch-copy{max-width:62ch;margin:1.2rem auto 2.3rem;color:var(--dim);font-size:clamp(.92rem,1.3vw,1.08rem);line-height:1.7}
.launch-section-label{margin:0 0 .7rem;color:var(--faint);font-size:.67rem;letter-spacing:.15em;text-transform:uppercase}
.launch-steps{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1.4rem;width:min(100%,760px);margin:2.6rem auto 0;padding-top:1.2rem;
  border-top:1px solid color-mix(in srgb,var(--border) 65%,transparent);text-align:left}
.launch-step{color:var(--dim);font-size:.75rem;line-height:1.55}
.launch-step b{display:block;margin-bottom:.25rem;color:var(--cyan);font-size:.69rem;font-weight:500;letter-spacing:.08em;text-transform:uppercase}
.st-key-launch_example_0 button,.st-key-launch_example_1 button,.st-key-launch_example_2 button{
  width:100%;min-height:3.15rem;border-radius:var(--radius-sm);text-align:left;justify-content:flex-start;
  background:rgba(12,33,49,.72);font-size:.77rem;color:var(--ink);backdrop-filter:blur(5px)}
.st-key-launch_example_0 button:hover,.st-key-launch_example_1 button:hover,.st-key-launch_example_2 button:hover{
  transform:translateY(-2px);border-color:var(--cyan);box-shadow:0 8px 28px -18px rgba(92,225,230,.75)}
.st-key-launch_start div.stButton>button{margin-top:.35rem;border-color:var(--cyan);background:var(--cyan);color:#061019;
  font-weight:700;border-radius:var(--radius-sm);padding:.62rem 1.25rem;min-height:2.8rem}
.st-key-launch_start div.stButton>button:hover{background:#8bf4f2;color:#061019;transform:translateY(-1px)}
@keyframes launchEnter{from{opacity:0;transform:translateY(13px);filter:blur(4px)}to{opacity:1;transform:translateY(0);filter:blur(0)}}
.st-key-launchpad{animation:launchEnter .48s var(--ease) both}
.chips-label{color:var(--faint);font-size:.7rem;letter-spacing:.16em;text-transform:uppercase;margin:.15rem 0 .1rem}
.commands{display:flex;flex-wrap:wrap;gap:.15rem .85rem;font-size:.78rem;color:var(--faint);margin:.55rem 0 .2rem}
.commands .cmd{color:var(--ice)}
.user-input{background:transparent;border:1px dashed var(--border);color:var(--dim)}
.user-input .prefix{color:var(--cyan)}
.error{color:var(--coral);border-color:color-mix(in srgb,var(--coral) 45%,var(--border))}
.prompt-question{color:var(--ink);font-weight:600;font-size:1rem}

/* ---- current-situation readout ---- */
.situation-card{position:relative;overflow:hidden;background:linear-gradient(180deg,var(--panel-2),var(--panel));
  border:1px solid var(--border-2);border-radius:var(--radius);padding:1.15rem 1.25rem 1rem;margin:.9rem 0;
  color:var(--ink);line-height:1.5;animation:verdictEnter .26s var(--ease) both}
.situation-head{display:flex;align-items:flex-start;justify-content:space-between;gap:1rem;padding-bottom:.9rem;border-bottom:1px solid var(--border)}
.situation-kicker{color:var(--faint);font-size:.66rem;font-weight:600;letter-spacing:.16em;text-transform:uppercase}
.situation-title{margin:.18rem 0 0;color:var(--ink);font-size:1.08rem;font-weight:600}
.situation-date{color:var(--faint);font-size:.75rem;white-space:nowrap}
.situation-current{display:flex;align-items:flex-end;justify-content:space-between;gap:1rem;padding:1rem 0}
.situation-current-label,.situation-metric-label{display:block;color:var(--faint);font-size:.66rem;letter-spacing:.12em;text-transform:uppercase}
.situation-current-value{display:block;margin-top:.25rem;color:var(--ink);font-size:clamp(1.35rem,3vw,1.8rem);font-weight:600;line-height:1.2;font-variant-numeric:tabular-nums}
.situation-low{text-align:right}
.situation-low strong{display:block;margin-top:.22rem;color:var(--mint);font-size:1rem;font-variant-numeric:tabular-nums}
.situation-low.is-low strong{color:var(--coral)}
.situation-low small{color:var(--dim);font-size:.72rem}
.situation-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border-top:1px solid var(--border);padding-top:.8rem}
.situation-metric{min-width:0;padding:.3rem .8rem .35rem;border-right:1px solid var(--border)}
.situation-metric:first-child{padding-left:0}.situation-metric:last-child{padding-right:0;border-right:0}
.situation-metric-value{display:block;margin-top:.28rem;color:var(--ink);font-size:.9rem;font-weight:600;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.situation-metric small{display:block;margin-top:.18rem;color:var(--faint);font-size:.68rem}
.situation-foot{display:flex;justify-content:space-between;gap:.6rem;flex-wrap:wrap;margin-top:.75rem;padding-top:.65rem;
  border-top:1px dashed var(--border);color:var(--faint);font-size:.72rem}

/* ---- report card ---- */
.report{position:relative;overflow:hidden;background:linear-gradient(180deg,var(--panel-2),var(--panel));
  border:1px solid var(--border-2);border-radius:var(--radius);padding:1.1rem 1.2rem 1.15rem;margin:.9rem 0;
  color:var(--ink);line-height:1.62;overflow-wrap:anywhere}
.report:before{content:'';position:absolute;left:0;right:0;top:0;height:1px;
  background:linear-gradient(90deg,var(--status,#7CF5C0),rgba(0,0,0,0) 70%)}
.report-head{display:flex;align-items:flex-start;gap:.7rem;padding-bottom:.6rem}
.status-dot{flex:none;width:9px;height:9px;margin-top:.5rem;border-radius:50%;background:var(--status,#7CF5C0);
  box-shadow:0 0 0 3px color-mix(in srgb,var(--status,#7CF5C0) 20%,transparent)}
.verdict{margin:0;font-size:1.32rem;font-weight:700;line-height:1.25;letter-spacing:.01em;
  color:var(--status,#7CF5C0);text-wrap:balance}
.headline-stat{flex:none;margin-left:auto;text-align:right}
.headline-stat .stat-label{display:block;color:var(--faint);font-size:.66rem;letter-spacing:.14em;text-transform:uppercase}
.headline-stat .stat-value{font-size:1.02rem;font-weight:600;color:var(--ink);font-variant-numeric:tabular-nums}
.headline-stat.is-neg .stat-value{color:var(--coral)}

.section-title{display:flex;align-items:center;gap:.6rem;margin:1.15rem 0 .5rem;color:var(--faint);
  font-size:.7rem;font-weight:600;letter-spacing:.18em;text-transform:uppercase}
.section-title:after{content:'';flex:1;height:1px;background:linear-gradient(90deg,var(--border),rgba(30,78,103,0))}
.report p{margin:0 0 .6rem;max-width:74ch}
.report .lead{font-size:1rem;color:var(--ink)}
.report .support{font-size:.93rem;color:var(--dim)}
.provenance{border-top:1px dashed var(--border);margin-top:.55rem;padding-top:.5rem;color:var(--faint);font-size:.79rem}
.loan-lines p{margin:0 0 .32rem}

/* ---- chart ---- */
.chart{margin:.15rem 0 0}
.chart svg{display:block;width:100%;height:134px}
.chart .grid{stroke:var(--border);stroke-width:1;opacity:.5;vector-effect:non-scaling-stroke}
.chart .floor{stroke:color-mix(in srgb,var(--amber) 60%,transparent);stroke-width:1;stroke-dasharray:4 4;vector-effect:non-scaling-stroke}
.chart .line{fill:none;stroke:var(--cyan);stroke-width:1.75;stroke-linecap:round;stroke-dasharray:var(--len,2400);vector-effect:non-scaling-stroke}
.chart .area{fill:url(#verdictArea);transform-box:fill-box}
.chart .lowdot{fill:var(--bg);stroke:var(--status,#7CF5C0);stroke-width:1.75}
.chart .hit{fill:transparent;cursor:crosshair}
.chart .probe-line{stroke:var(--ice);stroke-width:1;opacity:0;transition:opacity .14s linear;vector-effect:non-scaling-stroke}
.chart .probe-dot{fill:var(--ice);stroke:var(--bg);stroke-width:2;opacity:0;transition:opacity .14s linear}
.chart.is-probing .probe-line,.chart.is-probing .probe-dot{opacity:1}
.chart-meta{display:flex;align-items:baseline;justify-content:space-between;gap:1rem;flex-wrap:wrap;
  margin-top:.3rem;font-size:.79rem;color:var(--faint);font-variant-numeric:tabular-nums}
.chart-meta .legend{display:inline-flex;align-items:center;gap:.4rem}
.chart-meta .sw{display:inline-block;width:14px;height:0;border-top:1px dashed var(--amber);opacity:.75}
.chart-readout{color:var(--dim)}
.chart-readout:before{content:'▸ ';color:var(--cyan)}

/* ---- figures ---- */
.figures{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.55rem;margin:.85rem 0 0}
.figure{border:1px solid var(--border);background:var(--panel-3);border-radius:var(--radius-sm);padding:.5rem .65rem}
.figure dt{color:var(--faint);font-size:.66rem;letter-spacing:.12em;text-transform:uppercase;margin-bottom:.24rem}
.figure dd{margin:0;font-size:.97rem;font-weight:600;color:var(--ink);font-variant-numeric:tabular-nums}
.figure small{display:block;color:var(--faint);font-size:.72rem;font-weight:400;margin-top:.12rem}
.figure.is-good dd{color:var(--mint)}
.figure.is-bad dd{color:var(--coral)}
.figure.is-warn dd{color:var(--amber)}

/* ---- options table ---- */
.options-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;max-width:100%;border:1px solid var(--border);
  border-radius:var(--radius-sm);background:var(--panel-3)}
.options-table{border-collapse:collapse;width:100%;min-width:max-content;font-family:inherit;font-size:.84rem;
  font-variant-numeric:tabular-nums}
.options-table th{background:#0C2331;text-align:left;color:var(--faint);border-bottom:1px solid var(--border-2);
  padding:.5rem .7rem;font-size:.66rem;font-weight:600;letter-spacing:.12em;text-transform:uppercase;white-space:nowrap}
.options-table td{padding:.5rem .7rem;border-bottom:1px solid #16374a;color:var(--ink);white-space:nowrap;vertical-align:top}
.options-table tbody tr:last-child td{border-bottom:0}
.options-table th.num,.options-table td.num{text-align:right}
.options-table td.dim,.options-table th.dim{color:var(--faint)}
.options-table tr.is-picked{background:color-mix(in srgb,var(--status,#7CF5C0) 10%,transparent)}
.options-table tr.is-picked td:first-child{color:var(--ink);font-weight:600}
.options-table .pick{display:inline-block;margin-left:.45rem;padding:.05rem .38rem;border:1px solid color-mix(in srgb,var(--status,#7CF5C0) 55%,transparent);
  border-radius:3px;color:var(--status,#7CF5C0);font-size:.62rem;letter-spacing:.1em;text-transform:uppercase}
.options-table .safe{color:var(--mint)}
.options-table .unsafe{color:var(--coral)}
.options-notes{margin:.5rem 0 0}
.unsafe-note{position:relative;padding-left:1.05rem;margin:.32rem 0 0;color:var(--coral);font-size:.84rem}
.unsafe-note:before{content:'!';position:absolute;left:0;font-weight:700}
.unsafe-note b{color:var(--coral);font-weight:600}

/* ---- lists ---- */
.report ul.notes,.report ol.steps{list-style:none;margin:.1rem 0 0;padding:0;max-width:74ch}
.report ul.notes li{position:relative;padding-left:1rem;margin:.3rem 0;color:var(--dim);font-size:.92rem}
.report ul.notes li:before{content:'—';position:absolute;left:0;color:var(--border-3)}
.report ol.steps{counter-reset:step}
.report ol.steps li{counter-increment:step;position:relative;padding-left:1.6rem;margin:.32rem 0;color:var(--ink);font-size:.93rem}
.report ol.steps li:before{content:counter(step)'.';position:absolute;left:0;top:0;color:var(--cyan);font-size:.8rem}

/* ---- controls ---- */
div.stButton>button{background:var(--panel-2);color:var(--ice);border:1px solid var(--border-2);border-radius:999px;
  font-family:inherit;font-size:.8rem;padding:.34rem .78rem;
  transition:border-color .16s var(--ease),background .16s var(--ease),color .16s var(--ease),transform .16s var(--ease)}
div.stButton>button:hover{border-color:var(--cyan);background:#12303F;color:var(--cyan)}
div.stButton>button:active{transform:translateY(1px)}
/* Streamlit nowraps button labels; long prompt chips wrap instead of clipping */
div.stButton>button p{white-space:normal;overflow-wrap:anywhere;line-height:1.35}
div.stButton>button:disabled{opacity:.5;cursor:not-allowed;border-style:dashed}
div.stButton>button:disabled:hover{border-color:var(--border-2);background:var(--panel-2);color:var(--dim)}
div[data-testid="stDownloadButton"] button{background:transparent;color:var(--dim);border:1px solid var(--border);
  border-radius:999px;font-family:inherit;font-size:.78rem;padding:.32rem .68rem;
  transition:border-color .16s var(--ease),color .16s var(--ease)}
div[data-testid="stDownloadButton"] button:hover{border-color:var(--cyan);color:var(--cyan)}

[class*="st-key-mcq_"]{max-width:560px;margin:.1rem 0 .7rem}
[class*="st-key-mcq_"] button{justify-content:flex-start;text-align:left;width:100%;border-radius:var(--radius-sm);
  background:var(--panel);border:1px solid var(--border);color:var(--ink);padding:.6rem .9rem;font-size:.87rem}
[class*="st-key-mcq_"] button:hover{border-color:var(--cyan);background:#10304a}
[class*="st-key-mcq_"] button p{display:flex;align-items:center;gap:.6rem;margin:0}
[class*="st-key-mcq_"] button[kind="primary"]{border-color:color-mix(in srgb,var(--cyan) 50%,var(--border));background:#0F2C3D}
[class*="st-key-mcq_"] button[kind="primary"] p:after{content:'↵ default';margin-left:auto;padding-left:.6rem;
  color:var(--cyan);opacity:.75;font-size:.72rem;letter-spacing:.08em;white-space:nowrap}

/* Streamlit 1.66 renders these as react-aria controls, not BaseWeb. */
[data-testid="stSelectbox"] label p,[data-testid="stDateInput"] label p{color:var(--faint);font-size:.68rem;
  letter-spacing:.14em;text-transform:uppercase}
[data-testid="stSelectbox"] [role="group"],[data-testid="stDateInputField"]{background:var(--panel);
  border:1px solid var(--border-2);border-radius:var(--radius-sm);min-height:2.4rem;align-items:center;
  font-family:var(--mono);font-size:.86rem;color:var(--ink);
  transition:border-color .16s var(--ease),box-shadow .16s var(--ease)}
[data-testid="stSelectbox"] [role="group"]:hover,[data-testid="stDateInputField"]:hover{border-color:var(--border-3)}
[data-testid="stSelectbox"] [role="group"]:focus-within,[data-testid="stDateInputField"]:focus-within{border-color:var(--cyan);
  box-shadow:0 0 0 1px color-mix(in srgb,var(--cyan) 30%,transparent)}
[data-testid="stSelectbox"] input[role="combobox"]{font-family:var(--mono);font-size:.86rem;color:var(--ink)}
[data-testid="stSelectbox"] button,[data-testid="stDateInputCalendarButton"]{background:transparent;border:0;color:var(--faint)}
[data-testid="stSelectbox"] button:hover,[data-testid="stDateInputCalendarButton"]:hover{color:var(--cyan)}
[role="listbox"]{background:var(--panel-2)!important;border:1px solid var(--border-2);border-radius:var(--radius-sm);
  font-family:var(--mono)}
[role="option"]{font-family:var(--mono);font-size:.84rem;color:var(--ink)}
[role="option"][data-selected="true"],[role="option"][data-focused="true"]{background:#12303F;color:var(--cyan)}

/* ---- console ---- */
[data-testid="stBottom"]>div{background:linear-gradient(180deg,rgba(6,16,25,0),var(--bg) 32%)}
[data-testid="stBottomBlockContainer"]{max-width:1060px;padding-bottom:.9rem}
div[data-testid="stChatInput"]{position:relative;border:1px solid var(--border-2);border-radius:var(--radius);
  background:linear-gradient(180deg,#0C2231,#0A1C29);padding-left:6.5rem;
  transition:border-color .18s var(--ease),box-shadow .18s var(--ease)}
div[data-testid="stChatInput"]:focus-within{border-color:var(--cyan);
  box-shadow:0 0 0 1px color-mix(in srgb,var(--cyan) 30%,transparent),0 10px 30px -16px rgba(92,225,230,.65)}
div[data-testid="stChatInput"]:before{content:'verdict ❯';position:absolute;left:.95rem;top:.92rem;color:var(--cyan);
  font-family:var(--mono);font-size:.85rem;pointer-events:none}
div[data-testid="stChatInput"] textarea{font-family:var(--mono);font-size:.9rem;color:var(--ink);background:transparent}
div[data-testid="stChatInput"] textarea::placeholder{color:var(--faint)}
div[data-testid="stChatInput"] button{background:transparent!important;border:0!important;color:var(--cyan)!important;box-shadow:none!important}

/* ---- script host (hidden inline JS iframes) ---- */
[data-testid="stIFrame"]{height:1px;opacity:0;pointer-events:none;border:0}
[data-testid="stElementContainer"]:has(> [data-testid="stIFrame"]){position:fixed!important;left:0!important;top:0!important;
  width:1px!important;height:1px!important;min-height:0!important;margin:0!important;padding:0!important;
  overflow:hidden!important;opacity:0!important;pointer-events:none!important;z-index:-1!important}

/* ---- motion ---- */
@keyframes verdictEnter{from{opacity:0;transform:translateY(7px)}to{opacity:1;transform:translateY(0)}}
@keyframes verdictDraw{from{stroke-dashoffset:var(--len,2400)}to{stroke-dashoffset:0}}
@keyframes verdictArea{from{opacity:0;transform:translateY(7px)}to{opacity:1;transform:translateY(0)}}
@keyframes verdictDot{from{opacity:0;transform:scale(.4)}to{opacity:1;transform:scale(1)}}
[data-fresh]{animation:verdictEnter .26s var(--ease) both}
[data-fresh] .chart .line{animation:verdictDraw .78s var(--ease) both}
[data-fresh] .chart .area{animation:verdictArea .7s var(--ease) both}
[data-fresh] .chart .lowdot{transform-box:fill-box;transform-origin:center;animation:verdictDot .5s var(--ease) .5s both}

@media (max-width:860px){
  .block-container,[data-testid="stMainBlockContainer"]{padding:1rem .85rem 7rem}
  body:has(.st-key-launchpad) .block-container,
  body:has(.st-key-launchpad) [data-testid="stMainBlockContainer"]{padding:0!important}
  .st-key-launchpad{margin:0;padding:2rem 1rem}
  .launch-content{width:100%}
  .launch-brand{margin-bottom:1.25rem}
  .launch-title{font-size:clamp(2.35rem,11vw,3.5rem)}
  .launch-copy{margin:1rem auto 1.6rem}
  .launch-steps{grid-template-columns:1fr;gap:.65rem;width:min(100%,360px);margin:1.7rem auto 0}
  .launch-step b{margin:0 0 .15rem}
  .situation-current{align-items:flex-start;flex-direction:column}
  .situation-low{text-align:left}
  .situation-grid{grid-template-columns:repeat(2,minmax(0,1fr));row-gap:.5rem}
  .situation-metric{padding:.35rem .6rem;border-right:0}
  .situation-metric:nth-child(odd){padding-left:0;border-right:1px solid var(--border)}
  .situation-metric:nth-child(even){padding-right:0}
  .report-head{flex-direction:column;gap:.35rem}
  .headline-stat{margin-left:0;text-align:left}
  .verdict{font-size:1.14rem}
  .figures{grid-template-columns:repeat(2,minmax(0,1fr))}
  .masthead{gap:.35rem}
  div[data-testid="stChatInput"]{padding-left:1rem}
  div[data-testid="stChatInput"]:before{display:none}
  .chart svg{height:118px}
}
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{animation-duration:.001ms!important;animation-delay:0ms!important;
    animation-iteration-count:1!important;transition-duration:.001ms!important}
}
</style>
"""
st.markdown(STYLES, unsafe_allow_html=True)

# Cursor layer: one delegated listener on the parent document. Survives Streamlit
# reruns, throttled to a frame, and inert when the OS asks for reduced motion.
CURSOR_LAYER = """
<script>
(function(){
  var W = window.parent, D = W.document;
  var rm = !!(W.matchMedia && W.matchMedia('(prefers-reduced-motion: reduce)').matches);

  function money(el, n){
    var locale = el.dataset.locale || undefined;
    try { return new Intl.NumberFormat(locale,{maximumFractionDigits:0}).format(Math.round(n)); }
    catch (err) { return String(Math.round(n)); }
  }

  function count(){
    Array.prototype.forEach.call(
      D.querySelectorAll('.report[data-fresh] .stat-value[data-count-to]'), function(el){
        if (el.dataset.done) return;
        el.dataset.done = '1';
        var to = parseFloat(el.dataset.countTo), prefix = el.dataset.prefix || '';
        var fin = el.dataset.final || el.textContent;
        if (rm || !isFinite(to)) { el.textContent = fin; return; }
        var dur = 620, t0 = null;
        function step(ts){
          if (t0 === null) t0 = ts;
          var k = Math.min(1, (ts - t0) / dur), e = 1 - Math.pow(2, -10 * k);
          el.textContent = prefix + money(el, to * e);
          if (k < 1) { W.requestAnimationFrame(step); } else { el.textContent = fin; }
        }
        if (W.requestAnimationFrame) { W.requestAnimationFrame(step); } else { el.textContent = fin; }
      });
  }

  function probe(chart, clientX){
    var svg = chart.querySelector('svg'), hits = chart.querySelectorAll('[data-hit]');
    var n = parseInt(chart.dataset.count || '0', 10);
    if (!svg || !n || !hits.length) return;
    var box = svg.getBoundingClientRect();
    var i = Math.round(((clientX - box.left) / box.width) * (n - 1));
    i = Math.max(0, Math.min(n - 1, i));
    var hit = hits[i];
    if (!hit) return;
    var lx = hit.dataset.x, ly = hit.dataset.y;
    var line = chart.querySelector('.probe-line'), dot = chart.querySelector('.probe-dot');
    if (line) { line.setAttribute('x1', lx); line.setAttribute('x2', lx); }
    if (dot) { dot.setAttribute('cx', lx); dot.setAttribute('cy', ly); }
    chart.classList.add('is-probing');
    var out = chart.querySelector('.chart-readout');
    if (out) out.textContent = hit.dataset.day + ' · ' + hit.dataset.bal;
  }

  function release(node){
    if (!node || !node.classList) return;
    var chart = node.classList.contains('chart') ? node : node.querySelector('.chart');
    if (chart) {
      chart.classList.remove('is-probing');
      var out = chart.querySelector('.chart-readout');
      if (out) out.textContent = out.dataset.default || '';
    }
    var card = node.closest ? node.closest('.report,.welcome') : null;
    if (card) card.classList.remove('is-hovered');
  }

  function elementOf(ev){
    var node = ev.target;
    if (node && node.closest) return node;
    var path = ev.composedPath ? ev.composedPath() : null;
    if (path) { for (var i = 0; i < path.length; i++) { if (path[i] && path[i].closest) return path[i]; } }
    return null;
  }

  // Inline, throttled by timestamp: no dependency on the parent window's frame
  // scheduling, which a hidden or offscreen tab can suspend.
  if (W.__verdictFx && W.__verdictFx.retire) W.__verdictFx.retire();
  var ctl = new AbortController();
  var lastSeen = 0;
  D.addEventListener('pointermove', function(ev){
    var now = ev.timeStamp || Date.now();
    if (now - lastSeen < 12) return;
    lastSeen = now;
    var t = elementOf(ev);
    if (!t) return;
    var card = t.closest('.report,.welcome');
    if (card) {
      var box = card.getBoundingClientRect();
      card.style.setProperty('--mx', (ev.clientX - box.left) + 'px');
      card.style.setProperty('--my', (ev.clientY - box.top) + 'px');
      card.classList.add('is-hovered');
    }
    var masthead = t.closest('.masthead');
    if (masthead) {
      masthead.style.setProperty('--mx', (ev.clientX - masthead.getBoundingClientRect().left) + 'px');
    }
    var chart = t.closest('.chart');
    if (chart) probe(chart, ev.clientX);
  }, { passive: true, capture: true, signal: ctl.signal });
  D.addEventListener('pointerout', function(ev){
    var t = elementOf(ev), to = ev.relatedTarget;
    if (!t) return;
    var card = t.closest('.report,.welcome');
    if (card && (!to || !card.contains(to))) card.classList.remove('is-hovered');
    var chart = t.closest('.chart');
    if (chart && (!to || !chart.contains(to))) release(chart);
  }, { passive: true, capture: true, signal: ctl.signal });

  W.__verdictFx = { count: count, retire: function(){ ctl.abort(); } };
  count();
})();
</script>
"""

LAUNCH_LAYER = """
<script>
(function(){
  var W=window.parent,D=W.document,node=D.querySelector('.st-key-launchpad');
  if(W.__verdictLaunch&&W.__verdictLaunch.retire)W.__verdictLaunch.retire();
  if(!node)return;
  var ctl=new W.AbortController(), old=node.querySelector('.launch-canvas');
  if(old)old.remove();
  var canvas=D.createElement('canvas'),ctx=canvas.getContext('2d');
  canvas.className='launch-canvas';canvas.setAttribute('aria-hidden','true');node.prepend(canvas);
  var rm=!!(W.matchMedia&&W.matchMedia('(prefers-reduced-motion: reduce)').matches);
  var w=0,h=0,dpr=1,raf=0,mouse={x:-1000,y:-1000},points=[];
  function resize(){
    var r=node.getBoundingClientRect();w=r.width;h=r.height;dpr=Math.min(W.devicePixelRatio||1,2);
    canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);
    ctx.setTransform(dpr,0,0,dpr,0,0);
    var count=Math.max(26,Math.min(72,Math.round(w/22)));
    points=Array.from({length:count},function(){return {x:Math.random()*w,y:Math.random()*h,
      vx:(Math.random()-.5)*.18,vy:(Math.random()-.5)*.18,r:.7+Math.random()*1.1};});
    draw();
  }
  function draw(){
    if(!ctx||!w||!h)return;
    ctx.clearRect(0,0,w,h);
    if(mouse.x>-500){
      var glow=ctx.createRadialGradient(mouse.x,mouse.y,0,mouse.x,mouse.y,260);
      glow.addColorStop(0,'rgba(64,183,205,.14)');glow.addColorStop(1,'rgba(64,183,205,0)');
      ctx.fillStyle=glow;ctx.fillRect(0,0,w,h);
    }
    for(var i=0;i<points.length;i++){
      var p=points[i];
      if(!rm){
        p.x+=p.vx;p.y+=p.vy;
        if(mouse.x>-500){var dx=p.x-mouse.x,dy=p.y-mouse.y,dist=Math.hypot(dx,dy);
          if(dist<150&&dist>1){var push=(150-dist)/150*.045;p.vx+=dx/dist*push;p.vy+=dy/dist*push;}}
        p.vx*=.996;p.vy*=.996;
        if(Math.abs(p.vx)<.035)p.vx+=(Math.random()-.5)*.003;
        if(Math.abs(p.vy)<.035)p.vy+=(Math.random()-.5)*.003;
        if(p.x<0||p.x>w)p.vx*=-1;if(p.y<0||p.y>h)p.vy*=-1;
      }
      for(var j=i+1;j<points.length;j++){
        var q=points[j],dd=Math.hypot(p.x-q.x,p.y-q.y);
        if(dd<128){ctx.strokeStyle='rgba(92,225,230,'+((1-dd/128)*.16)+')';ctx.lineWidth=.7;
          ctx.beginPath();ctx.moveTo(p.x,p.y);ctx.lineTo(q.x,q.y);ctx.stroke();}
      }
      var md=mouse.x>-500?Math.hypot(p.x-mouse.x,p.y-mouse.y):999;
      ctx.fillStyle=md<170?'rgba(140,200,255,'+(.38+(1-md/170)*.48)+')':'rgba(92,225,230,.42)';
      ctx.beginPath();ctx.arc(p.x,p.y,p.r+(md<140?.6:0),0,Math.PI*2);ctx.fill();
    }
    if(!rm)raf=W.requestAnimationFrame(draw);
  }
  D.addEventListener('pointermove',function(e){var r=node.getBoundingClientRect();mouse.x=e.clientX-r.left;mouse.y=e.clientY-r.top;},{passive:true,capture:true,signal:ctl.signal});
  node.addEventListener('pointerleave',function(){mouse.x=mouse.y=-1000;},{passive:true,signal:ctl.signal});
  W.addEventListener('resize',resize,{passive:true,signal:ctl.signal});
  W.__verdictLaunch={retire:function(){ctl.abort();if(raf)W.cancelAnimationFrame(raf);if(canvas.parentNode)canvas.remove();}};
  resize();
})();
</script>
"""


def esc(value):
    """Escape any dynamic/user-provided value before it enters HTML."""
    return html.escape(str(value), quote=True)


def fresh_attr(fresh):
    """Markup that lets one entry animation fire for newly appended blocks only."""
    return ' data-fresh="1"' if fresh else ""


def money_prefix(currency):
    """'₹' for INR, 'USD' for dollar accounts: whatever fmt_money puts before the digits."""
    prefix = ""
    for char in fmt_money(0, currency):
        if char.isdigit() or char in ".,-+ ":
            break
        prefix += char
    return prefix.strip()


def series_dates(start_iso, end_iso, count):
    """One ISO date per point of an evenly spaced 90-day series."""
    if count <= 1:
        return [str(start_iso)]
    try:
        start = dt.date.fromisoformat(str(start_iso))
        end = dt.date.fromisoformat(str(end_iso))
    except ValueError:
        return [str(start_iso)] * count
    span = (end - start).days
    return [(start + dt.timedelta(days=round(span * i / (count - 1)))).isoformat() for i in range(count)]


def short_date(iso):
    try:
        return dt.date.fromisoformat(str(iso)).strftime("%d %b")
    except ValueError:
        return ""


def lead_and_support(text):
    """Split prose into a lead sentence, its support, and a trailing provenance note."""
    body = " ".join(str(text or "").split())
    provenance = ""
    marker = "Based on:"
    if marker in body and body.index(marker) > len(body) * 0.4:
        body, provenance = body.split(marker, 1)
        provenance = marker + " " + provenance.strip()
    parts = [p.strip() for p in body.replace("\n", " ").split(". ") if p.strip()]
    if not parts:
        return "", "", provenance
    lead = parts[0] + ("." if not parts[0].endswith(".") else "")
    support = ". ".join(parts[1:]).strip()
    if support and not support.endswith("."):
        support += "."
    return lead, support, provenance


def balance_chart(numbers, currency):
    """Inline SVG of the projected balance, with a floor line and a cursor probe."""
    values = [float(v) for v in (numbers.get("sparkline_values") or []) if v is not None]
    if len(values) < 2:
        return ""
    count = len(values)
    floor = float(numbers.get("minimum") or 0)
    dates = series_dates(numbers.get("start_date"), numbers.get("end_date"), count)
    low_index = min(range(count), key=lambda i: values[i])
    low_value = values[low_index]
    lo = min(min(values), floor)
    hi = max(max(values), floor)
    pad = (hi - lo) * 0.12 or max(abs(hi) * 0.08, 1.0)
    ylo, yhi = lo - pad, hi + pad
    width, height, inset = 760.0, 134.0, 8.0
    span = yhi - ylo or 1.0
    y = lambda v: round(height - inset - (v - ylo) / span * (height - 2 * inset), 2)
    x = lambda i: round((i + 0.5) * (width / count), 2)
    points = [(x(i), y(v)) for i, v in enumerate(values)]
    line = "M " + " L ".join(f"{px} {py}" for px, py in points)
    area = f"{line} L {points[-1][0]} {height - inset} L {points[0][0]} {height - inset} Z"
    length = sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))
    floor_y = min(max(y(floor), inset), height - inset)
    slots = "".join(f'<rect class="hit" x="{(i * width / count):.2f}" y="0" width="{(width / count):.2f}" '
                    f'height="{height}" data-hit data-i="{i}" data-x="{x(i)}" data-y="{y(v)}" '
                    f'data-day="{esc(short_date(dates[i]))}" data-bal="{esc(fmt_money(v, currency))}"/>'
                    for i, v in enumerate(values))
    label = (f"90-day projected balance from {dates[0]} to {dates[-1]}; "
             f"lowest {fmt_money(low_value, currency)} on {dates[low_index]}; "
             f"lowest balance you keep {fmt_money(floor, currency)}.")
    return (
        f'<figure class="chart" data-count="{count}">'
        f'<svg viewBox="0 0 {width:.0f} {height:.0f}" preserveAspectRatio="none" role="img" '
        f'aria-label="{esc(label)}">'
        f'<defs><linearGradient id="verdictArea" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="#5CE1E6" stop-opacity="0.22"/>'
        f'<stop offset="100%" stop-color="#5CE1E6" stop-opacity="0"/></linearGradient></defs>'
        f'<line class="grid" x1="0" y1="{y(yhi)}" x2="{width:.0f}" y2="{y(yhi)}"/>'
        f'<line class="grid" x1="0" y1="{y((yhi + ylo) / 2)}" x2="{width:.0f}" y2="{y((yhi + ylo) / 2)}"/>'
        f'<path class="area" d="{area}"/>'
        f'<line class="floor" x1="0" y1="{floor_y}" x2="{width:.0f}" y2="{floor_y}"/>'
        f'<path class="line" style="--len:{length:.0f}" d="{line}"/>'
        f'<g class="probe" aria-hidden="true"><line class="probe-line" x1="0" y1="{inset}" x2="0" y2="{height - inset}"/>'
        f'<circle class="probe-dot" cx="0" cy="0" r="3.5"/></g>'
        f'<circle class="lowdot" cx="{points[low_index][0]}" cy="{points[low_index][1]}" r="3.5"/>'
        f'{slots}</svg>'
        f'<figcaption class="chart-meta">'
        f'<span class="legend"><span class="sw"></span>floor {esc(fmt_money(floor, currency))} · low '
        f'{esc(short_date(dates[low_index]))}</span>'
        f'<span class="chart-readout" data-default="low point · {esc(short_date(dates[low_index]))} · '
        f'{esc(fmt_money(low_value, currency))}">low point · {esc(short_date(dates[low_index]))} · '
        f'{esc(fmt_money(low_value, currency))}</span>'
        f'</figcaption></figure>')


def report_html(block, currency, fresh=False):
    data = block.get("data", {})
    status = str(data.get("status", "caution"))
    color = {"good": "#7CF5C0", "caution": "#FFD479", "bad": "#FF7A90"}.get(status, "#FFD479")
    numbers = data.get("numbers", {}) or {}
    values = [float(v) for v in (numbers.get("sparkline_values") or []) if v is not None]
    floor = float(numbers.get("minimum") or 0)
    low_value = min(values) if values else float(numbers.get("start_balance") or 0)
    options_now = data.get("options", []) or []
    # The chart series is the forecast BEFORE this payment, so it is not the
    # verdict's outcome.  The headline states what the recommended plan leaves
    # you, or, when nothing is recommended, the best any option still reaches.
    option_lows = [float(o.get("lowest_value") or 0) for o in options_now if o.get("lowest_value") is not None]
    chosen_low = next((float(o.get("lowest_value") or 0) for o in options_now if o.get("recommended")), None)
    plan_low = chosen_low if chosen_low is not None else (max(option_lows) if option_lows else low_value)
    margin = plan_low - floor
    verdict = str(data.get("verdict", "Report"))
    lead, support, provenance = lead_and_support(data.get("why", ""))
    dates = series_dates(numbers.get("start_date"), numbers.get("end_date"), len(values))
    low_day = short_date(dates[min(range(len(values)), key=lambda i: values[i])]) if values else ""

    pieces = [f'<div class="report status-{esc(status)}" style="--status:{color}"{fresh_attr(fresh)}>',
              '<header class="report-head">',
              '<span class="status-dot" aria-hidden="true"></span>',
              f'<h3 class="verdict">{esc(verdict)}</h3>',
              f'<div class="headline-stat{" is-neg" if margin < 0 else ""}">'
              f'<span class="stat-label">{"shortfall" if margin < 0 else "safety margin"}</span>'
              f'<span class="stat-value" data-count-to="{margin:.2f}" data-final="{esc(fmt_money(margin, currency))}" '
              f'data-prefix="{esc(money_prefix(currency))}" data-locale="{esc(locale_for(currency))}">'
              f'{esc(fmt_money(margin, currency))}</span></div>',
              '</header>']
    pieces += ['<div class="section"><div class="section-title">WHY</div>']
    if lead:
        pieces.append(f'<p class="lead">{esc(lead)}</p>')
    if support:
        pieces.append(f'<p class="support">{esc(support)}</p>')
    if provenance:
        pieces.append(f'<p class="provenance">{esc(provenance)}</p>')
    pieces.append("</div>")

    chart = balance_chart(numbers, currency)
    pieces += ['<div class="section"><div class="section-title">NUMBERS</div>', chart,
               '<dl class="figures">']
    figures = [("Available now", numbers.get("start_balance"), short_date(numbers.get("start_date")),
                "is-neutral"),
               ("Floor", floor, "keep this free", "is-neutral"),
               ("Lowest in 90 days", low_value,
                f"before payment · {low_day}" if low_day else "before payment",
                "is-warn" if low_value < floor else "is-good"),
               ("At 90 days", numbers.get("end_balance"), short_date(numbers.get("end_date")), "is-neutral")]
    for title, value, note, cls in figures:
        pieces.append(f'<div class="figure {cls}"><dt>{esc(title)}</dt><dd>{esc(fmt_money(value, currency))}'
                      f'<small>{esc(note or "")}</small></dd></div>')
    pieces += ["</dl></div>"]

    options = options_now
    pieces += ['<div class="section"><div class="section-title">OPTIONS</div>',
               '<div class="options-scroll"><table class="options-table">',
               '<thead><tr><th scope="col">Option</th><th scope="col">When</th>'
               '<th scope="col" class="num">Cost</th><th scope="col" class="num">Lowest</th>'
               '<th scope="col">Deadline</th><th scope="col">Safe</th></tr></thead><tbody>']
    for option in options:
        mark = '<span class="safe">✓</span>' if option.get("safe") else '<span class="unsafe">✗</span>'
        deadline = str(option.get("meets_deadline", "n/a"))
        picked = bool(option.get("recommended"))
        pick = '<span class="pick">chosen</span>' if picked else ""
        pieces.append(f'<tr class="{"is-picked" if picked else ""}">'
                      f'<td>{esc(option.get("name", ""))}{pick}</td>'
                      f'<td class="dim">{esc(option.get("when", ""))}</td>'
                      f'<td class="num">{esc(option.get("cost", ""))}</td>'
                      f'<td class="num">{esc(option.get("lowest", ""))}</td>'
                      f'<td class="{"dim" if deadline == "n/a" else ""}">{esc(deadline)}</td>'
                      f'<td>{mark}</td></tr>')
    pieces.append("</tbody></table></div>")
    notes = [option for option in options if option.get("note")]
    if notes:
        pieces.append('<div class="options-notes">')
        pieces.extend(f'<div class="unsafe-note"><b>{esc(option.get("name", ""))}</b> — '
                      f'{esc(str(option["note"]).removeprefix("Unsafe: ").strip())}</div>' for option in notes)
        pieces.append("</div>")
    pieces.append("</div>")

    for title, key, tag in (("ALTERNATIVES", "alternatives", "ul"), ("NEXT STEPS", "next_steps", "ol")):
        lines = [str(line).lstrip("• ").strip() for line in (data.get(key) or []) if str(line).strip()]
        if not lines:
            continue
        opening = '<ul class="notes">' if tag == "ul" else '<ol class="steps">'
        closing = "</ul>" if tag == "ul" else "</ol>"
        pieces.append(f'<div class="section"><div class="section-title">{title}</div>{opening}')
        pieces.extend(f"<li>{esc(line)}</li>" for line in lines)
        pieces.append(f"{closing}</div>")

    if data.get("loan"):
        pieces.append('<div class="section"><div class="section-title">LOAN OPTION (ILLUSTRATIVE)</div>'
                      '<div class="loan-lines">')
        pieces.extend(f'<p class="support">{esc(line)}</p>' for line in str(data["loan"]).splitlines())
        pieces.append("</div></div>")
    return "".join(pieces) + "</div>"


def situation_html(block, currency):
    """Structured, scannable view of the exact /situation snapshot data."""
    data = block.get("data") or {}
    currency = str(data.get("currency") or currency)
    current = data.get("current_balance", 0)
    minimum = data.get("minimum_balance", 0)
    low = data.get("lowest_projected_balance", 0)
    low_date = str(data.get("lowest_projected_date", "n/a"))
    as_of = str(data.get("as_of_date", ""))
    try:
        as_of_label = dt.date.fromisoformat(as_of).strftime("%d %b %Y")
    except ValueError:
        as_of_label = as_of
    try:
        low_date_label = dt.date.fromisoformat(low_date).strftime("%d %b %Y")
    except ValueError:
        low_date_label = low_date
    income_info = data.get("monthly_income_counted")
    income_note = income_info if isinstance(income_info, str) else "counted per month"
    low_class = "is-low" if float(low or 0) < float(minimum or 0) else ""
    metrics = [
        ("Minimum to keep", fmt_money(minimum, currency), ""),
        ("Income counted / month", fmt_money(data.get("monthly_income_total", 0), currency), income_note),
        ("Outflows · next 30 days", fmt_money(data.get("outflows_next_30_days", 0), currency), ""),
        ("Outflows · next 90 days", fmt_money(data.get("outflows_next_90_days", 0), currency), ""),
    ]
    metric_html = "".join(
        f'<div class="situation-metric"><span class="situation-metric-label">{esc(label)}</span>'
        f'<strong class="situation-metric-value">{esc(value)}</strong>'
        f'{f"<small>{esc(note)}</small>" if note else ""}</div>'
        for label, value, note in metrics
    )
    considered = int(data.get("pending_considered_count", 0) or 0)
    ignored = int(data.get("pending_ignored_count", 0) or 0)
    if considered or ignored:
        pending_label = f"Pending items · {considered} included · {ignored} not counted"
    else:
        pending_label = "No pending items"
    return (
        f'<section class="situation-card" aria-label="Current financial situation">'
        f'<header class="situation-head"><div><div class="situation-kicker">ACCOUNT SNAPSHOT</div>'
        f'<h2 class="situation-title">Your current situation</h2></div>'
        f'<time class="situation-date" datetime="{esc(as_of)}">As of {esc(as_of_label)}</time></header>'
        f'<div class="situation-current"><div><span class="situation-current-label">Current balance</span>'
        f'<strong class="situation-current-value">{esc(fmt_money(current, currency))}</strong></div>'
        f'<div class="situation-low {low_class}"><span class="situation-current-label">Lowest projected balance</span>'
        f'<strong>{esc(fmt_money(low, currency))}</strong><small>{esc(low_date_label)} · next 90 days</small></div></div>'
        f'<div class="situation-grid">{metric_html}</div>'
        f'<footer class="situation-foot"><span>{esc(pending_label)}</span>'
        f'<span>Forecast horizon · 90 days</span></footer></section>'
    )


def locale_for(currency):
    """Intl locale so the tooltip/animated numerals group like the server-side format."""
    return "en-IN" if str(currency).upper() == "INR" else "en-US"


def inject_script(script):
    """Host an inline script.  st.iframe replaces st.components.v1.html in Streamlit
    1.66 and rejects a height of 0, so it gets 1px (hidden by CSS); the old call is
    kept for the streamlit>=1.40 floor in requirements.txt."""
    iframe = getattr(st, "iframe", None)
    if callable(iframe):
        return iframe(script, height=1)
    return st.components.v1.html(script, height=0)


user_ids = profiles.user_id.astype(str).tolist()
picker = user_picker_options(profiles)
picker_ids = [uid for uid, _label in picker]
picker_labels = dict(picker)
profile_by_user = {str(row.user_id): row for _, row in profiles.iterrows()}

user_id = st.session_state.get("terminal_user", "user_07" if "user_07" in user_ids else user_ids[0])
# Account switcher.  A picker change is committed to session state before the
# run starts, so it is adopted first; the picker is written back (pre-widget,
# which overrides any pending picker event) only when the active user was
# changed another way, e.g. by a /user command line.
picker_now = st.session_state.get("user_switcher")
picker_seen = st.session_state.get("user_switcher_seen")
switched = picker_now is not None and picker_now != picker_seen and picker_now != user_id
if switched:
    user_id = picker_now
    st.session_state.terminal_user = user_id
    st.session_state.terminal_date = None
elif picker_now != user_id and user_id in picker_ids:
    st.session_state.user_switcher = user_id
st.session_state.user_switcher_seen = user_id
date_key = st.session_state.get("terminal_date")

st.markdown('<div class="masthead"><span class="wordmark">Verdict<b>_</b></span>'
            '<em>payment decision terminal</em><span class="rule"></span></div>', unsafe_allow_html=True)

account_col, profile_col = st.columns([1.28, 1.5], gap="medium")
with account_col:
    st.selectbox("Account", options=picker_ids, format_func=picker_labels.get,
                 index=picker_ids.index(user_id) if user_id in picker_ids else 0, key="user_switcher")
profile_row = profile_by_user.get(user_id)
if profile_row is not None:
    with profile_col:
        st.markdown(f'<div class="profile">{esc(profile_summary_text(profile_row))}</div>', unsafe_allow_html=True)

session_key = f"terminal_session_{APP_VERSION}"
session = st.session_state.get(session_key)
if (not isinstance(session, TerminalSession) or not hasattr(getattr(session, "assistant", None), "situation")
        or session.user_id != user_id or (date_key and session.as_of_date != date_key)):
    if date_key is None:
        # The defaulted as-of date is the user's own request date; last bank
        # event + 1 only when the user has no request row at all.
        date_key = request_date_for_user(user_id, DATASET)
        if date_key is None:
            assistant = Assistant(user_id, dt.date.today().isoformat(), dataset_dir=DATASET, no_llm=True)
            dates = assistant.engine.events[assistant.engine.events.user_id.astype(str).eq(user_id)].event_date.astype(str)
            if len(dates):
                last = sorted(dates.astype(str))[-1][:10]
                date_key = (dt.date.fromisoformat(last) + dt.timedelta(days=1)).isoformat()
            else:
                date_key = dt.date.today().isoformat()
        st.session_state.terminal_date = date_key
    # A picker switch defaults the as-of date: pass None so the session keeps
    # as_of_defaulted and the switch message carries the usual note.
    session = TerminalSession(user_id, None if switched else date_key, dataset_dir=DATASET,
                              assistant=Assistant(user_id, date_key, dataset_dir=DATASET, no_llm=True))
    st.session_state[session_key] = session
    st.session_state.terminal_started = False
    currency = str(session.assistant.engine.get_user_home_currency(user_id))
    st.session_state.terminal_output = [{"kind": "welcome", "plain_text": BANNER}]
    if switched:
        # Same message as the /user command: data range plus defaulted as-of.
        st.session_state.terminal_output.append({"kind": "text", "plain_text": session.switch_message()})
    elif session.as_of_note:
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

COMMANDS = ["/help", "/users", "/user ID", "/date YYYY-MM-DD", "/situation", "/why", "/sources", "/reset", "/export"]
output = st.session_state.get("terminal_output", [])
latest_prompt_index = max((i for i, item in enumerate(output) if item.get("kind") == "prompt"), default=-1)
latest_report_index = max((i for i, item in enumerate(output) if item.get("kind") == "report"), default=-1)
newest_index = len(output) - 1
launch_state = (not st.session_state.get("terminal_started", False)
                and not any(item.get("kind") in {"user_input", "prompt", "report", "error"} for item in output))
if launch_state:
    with st.container(key="launchpad"):
        st.markdown('<div class="launch-content">'
                    '<div class="launch-brand">Verdict</div>'
                    '<h1 class="launch-title">See how a purchase fits your next 90 days.</h1>'
                    '<p class="launch-copy">Tell Verdict what you are considering. It checks your balance, income, bills and planned payments over the next 90 days, then explains whether it fits.</p>'
                    '<div class="launch-section-label">Try an example</div></div>', unsafe_allow_html=True)
        examples = ["Can I afford a laptop that costs ₹12k?",
                    "Can I pay ₹50,000 in 6 installments?",
                    "/situation"]
        example_cols = st.columns(3, gap="small")
        clicked_example = None
        for index, (column, example) in enumerate(zip(example_cols, examples)):
            label = "Show my current situation" if example == "/situation" else example
            if column.button(label, key=f"launch_example_{index}", use_container_width=True):
                clicked_example = example
        st.markdown('<div class="launch-content"><div class="launch-steps">'
                    '<div class="launch-step"><b>Share the plan</b><span>Tell us the cost and when you need to pay.</span></div>'
                    '<div class="launch-step"><b>Answer a few questions</b><span>Choose a deadline and payment approach.</span></div>'
                    '<div class="launch-step"><b>See the cash-flow check</b><span>Review the 90-day forecast and the reasoning.</span></div>'
                    '</div></div>', unsafe_allow_html=True)
        if clicked_example:
            st.session_state.terminal_started = True
            st.session_state.pending_line = clicked_example
            st.session_state.pending_label = "Show my current situation" if clicked_example == "/situation" else None
            st.rerun()
        if st.button("Start a conversation", key="launch_start", icon=":material/arrow_forward:"):
            st.session_state.terminal_started = True
            st.rerun()
    for item in output:
        note = item.get("plain_text", "")
        if item.get("kind") == "text" and note and not note.startswith("Account currency:"):
            st.markdown(f'<div class="block">{esc(note)}</div>', unsafe_allow_html=True)

rendered_output = [] if launch_state else output
for block_index, block in enumerate(rendered_output):
    kind = block.get("kind", "text")
    fresh = block_index >= newest_index - 1
    if kind == "welcome":
        st.markdown(f'<div class="welcome"{fresh_attr(fresh)}><p>{esc(block["plain_text"])}</p></div>',
                    unsafe_allow_html=True)
        st.markdown('<div class="commands">' + "".join(f'<span class="cmd">{esc(c)}</span>' for c in COMMANDS) + "</div>",
                    unsafe_allow_html=True)
    elif kind == "report":
        st.markdown(report_html(block, currency, fresh=fresh), unsafe_allow_html=True)
        if block_index == latest_report_index:
            chip_lines = session.quick_replies()
            cols = st.columns(len(chip_lines) + 1)
            for col, (label, value) in zip(cols, chip_lines):
                if col.button(label, key=f"followup_{block_index}_{label}"):
                    st.session_state.pending_line = value
            report_text = block.get("plain_text", "")
            sources = (block.get("data", {}) or {}).get("loan_sources")
            if sources:
                checked, urls = sources
                report_text += ("\n\nSOURCES\nLoan reference pages checked " + checked + ":\n"
                                + "\n".join(f"- {url}" for url in urls))
            cols[-1].download_button("Download report", report_text, file_name="verdict-report.txt",
                                     mime="text/plain", key=f"download_report_{block_index}")
    elif kind == "export":
        st.download_button("Download report", block.get("plain_text", ""), file_name="verdict-report.txt",
                           mime="text/plain", key=f"export_report_{block_index}")
    elif kind == "prompt":
        data = block.get("data", {})
        # A prompt stays interactive only while it is still awaiting its
        # answer (no user input was submitted after it).  Answered prompts
        # render read-only with the chosen value marked, and every widget is
        # keyed on the prompt's unique id so a re-run can never resubmit an
        # older prompt's value.
        chosen = next((item.get("plain_text", "") for item in output[block_index + 1:]
                       if item.get("kind") == "user_input"), None)
        awaiting = chosen is None
        active = block_index == latest_prompt_index and awaiting
        pid = str(data.get("id") or f"p{block_index}")
        st.markdown(f'<div class="block"{fresh_attr(fresh)}><div class="prompt-question">{esc(data.get("question", block["plain_text"]))}</div></div>', unsafe_allow_html=True)
        options = data.get("options", [])
        if options:
            with st.container(key=f"mcq_{pid}"):
                for option_index, option in enumerate(options):
                    label = option.get("label", "")
                    default = option.get("default")
                    unchecked = ":material/radio_button_checked:" if default else ":material/radio_button_unchecked:"
                    widget_key = f"prompt_{pid}_{option_index}"
                    if active:
                        if st.button(label, icon=unchecked, key=widget_key, use_container_width=True,
                                     type="primary" if default else "secondary"):
                            st.session_state.pending_line = str(option.get("key", ""))
                            st.session_state.pending_label = option.get("label", "")
                    else:
                        picked = chosen is not None and chosen.strip() in {str(option.get("key", "")), option.get("label", "")}
                        st.button(label, icon=":material/radio_button_checked:" if picked else unchecked,
                                  key=widget_key + "_done", disabled=True, use_container_width=True)
        picker = data.get("picker")
        if picker:
            low = dt.date.fromisoformat(picker["min"]); high = max(low, dt.date.fromisoformat(picker["max"]))
            with st.container(key=f"mcq_date_{pid}"):
                picker_value = low
                if chosen:
                    try: picker_value = dt.date.fromisoformat(chosen.strip())
                    except ValueError: pass
                picked = st.date_input(picker.get("label", "Choose a date"), value=picker_value,
                                       min_value=low, max_value=high, key=f"deadline_date_{pid}",
                                       disabled=not active)
                if active and st.button("Confirm date", icon=":material/check:", key=f"confirm_date_{pid}"):
                    st.session_state.pending_line = picked.isoformat()
                    st.session_state.pending_label = picked.isoformat()
    elif kind == "user_input":
        st.markdown(f'<div class="block user-input"{fresh_attr(fresh)}>'
                    f'<span class="prefix">❯ </span>{esc(block["plain_text"])}</div>', unsafe_allow_html=True)
    elif kind == "text" and (block.get("data") or {}).get("view") == "situation":
        st.markdown(situation_html(block, currency), unsafe_allow_html=True)
    else:
        cls = "error" if kind == "error" else ""
        st.markdown(f'<div class="block {cls}"{fresh_attr(fresh)}>{esc(block["plain_text"])}</div>',
                    unsafe_allow_html=True)

line = st.chat_input("enter a request or command")
pending = st.session_state.get("pending_line")
pending_label = None
if pending is not None:
    line = pending
    pending_label = st.session_state.get("pending_label")
    st.session_state.pending_line = None
    st.session_state.pending_label = None
if line is not None:
    st.session_state.terminal_started = True
    blocks = session.handle(line)
    output = list(output) + [{"kind": "user_input", "plain_text": pending_label or line}] + blocks
    st.session_state.terminal_output = output
    switch_arg = user_switch_arg(line)
    if switch_arg:
        resolved, _closest = resolve_user_id(switch_arg, user_ids)
        if resolved:
            # Same switch semantics as the picker: the defaulted as-of date.
            st.session_state.terminal_user = resolved
            st.session_state.terminal_date = None
    if line.startswith("/date "):
        try: st.session_state.terminal_date = dt.date.fromisoformat(line.split(maxsplit=1)[1]).isoformat()
        except ValueError: pass
    st.rerun()

inject_script(CURSOR_LAYER)
inject_script(LAUNCH_LAYER)
inject_script("""<script>
(function(){
  var W = window.parent, D = W.document;
  var node = D.querySelector('[data-testid="stMain"]') || D.querySelector('section.main');
  var scroller = null, walk = node;
  while (walk && walk !== D.body) {
    var style = W.getComputedStyle(walk);
    if (/(auto|scroll)/.test(style.overflowY) && walk.scrollHeight > walk.clientHeight + 4) { scroller = walk; break; }
    walk = walk.parentElement;
  }
  var target = scroller || D.scrollingElement;
  var smooth = !(W.matchMedia && W.matchMedia('(prefers-reduced-motion: reduce)').matches);
  target.scrollTo({ top: target.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
})();
</script>""")
