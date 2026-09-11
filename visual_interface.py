# visual_interface.py
"""Streamlit UI for batch processing and multi-patient record splitting.
All user-facing text is in Chinese.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import time
from pathlib import Path

import requests
import streamlit as st

from api_client import ApiError, call_chat_completion
from record_parser import (
    decode_upload_bytes,
    extract_sections as rp_extract_sections,
    get_available_templates as rp_get_available_templates,
    install_custom_template,
    normalize_ai_output,
    process_text_to_template_json,
    safe_project_path,
    save_failed_case,
    split_patient_records,
    validate_rewritten_text,
    write_split_files,
)

# ---- 页面配置（须在其他 st 调用前） ----
st.set_page_config(
    page_title="规培手写门诊病历自动排版",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---- 日志配置：按日轮转 + 解析错误独立文件 ----
LOG_FILE = "system.log"
PARSE_ERROR_LOG = "parse_error.log"


def _setup_logging():
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    sys_handler = logging.handlers.TimedRotatingFileHandler(
        LOG_FILE, when="midnight", backupCount=14, encoding="utf-8"
    )
    sys_handler.setFormatter(fmt)
    root.addHandler(sys_handler)

    parse_handler = logging.handlers.TimedRotatingFileHandler(
        PARSE_ERROR_LOG, when="midnight", backupCount=30, encoding="utf-8"
    )
    parse_handler.setLevel(logging.WARNING)
    parse_handler.setFormatter(fmt)
    parse_handler.addFilter(
        lambda record: (
            "解析" in record.getMessage()
            or "校验" in record.getMessage()
            or "失败样本" in record.getMessage()
            or "切割" in record.getMessage()
        )
    )
    root.addHandler(parse_handler)


_setup_logging()


def log_info(msg):
    logging.info(msg)


def log_error(msg):
    logging.error(msg)


# ---- 主题样式：统一浅色临床风 ----
CUSTOM_CSS = """
<style>
:root {
  --ink: #1a2e28;
  --muted: #6b7c76;
  --paper: #f4f6f4;
  --accent: #2d6a5a;
  --accent-soft: #e8f2ee;
  --line: #dde5e1;
  --card: #ffffff;
}
.stApp {
  background: var(--paper);
  color: var(--ink);
  font-family: "PingFang SC", "Microsoft YaHei", "Segoe UI", system-ui, sans-serif;
}
/* 主区滚动交给 Streamlit 原生容器；只放开被锁死的 overflow */
[data-testid="stAppViewContainer"] {
  overflow-y: auto !important;
}
[data-testid="stMain"],
[data-testid="stMainBlockContainer"],
section.main {
  overflow: visible !important;
}
.block-container {
  /* 预留 Streamlit 顶栏高度，避免标题被裁切 */
  padding-top: clamp(2.75rem, 6vh, 3.5rem) !important;
  padding-bottom: 2.5rem !important;
  max-width: 100% !important;
  width: 100% !important;
  /* 水平留白随视口缩放，避免固定 rem 在小屏挤/大屏空 */
  padding-left: clamp(1rem, 4vw, 2.75rem) !important;
  padding-right: clamp(1rem, 4vw, 2.75rem) !important;
  margin-left: 0 !important;
  margin-right: 0 !important;
}
.page-title {
  font-size: clamp(1.15rem, 2.5vw, 1.4rem) !important;
  font-weight: 700;
  color: var(--ink);
  margin: 0;
  line-height: 1.3;
}
.page-sub {
  margin: 0.2rem 0 0 0;
  font-size: clamp(0.8rem, 1.6vw, 0.88rem);
  color: var(--muted);
}
/* 侧栏收起：不要在左侧留空白，主区占满可用宽度 */
section[data-testid="stSidebar"][aria-expanded="false"] {
  min-width: 0 !important;
  width: 0 !important;
  max-width: 0 !important;
  margin: 0 !important;
  padding: 0 !important;
  border: none !important;
  overflow: hidden !important;
}
section[data-testid="stSidebar"][aria-expanded="false"] > div {
  width: 0 !important;
  min-width: 0 !important;
  overflow: hidden !important;
}
section[data-testid="stMain"] {
  width: 100% !important;
  max-width: 100% !important;
}
/* 顶栏与页面同色，避免压住标题像被裁切 */
[data-testid="stHeader"] {
  background: var(--paper) !important;
  border-bottom: none !important;
}

/* ---- 侧栏折叠/展开控件：从默认浮动 » 改成与主区对齐的圆角钮 ---- */
[data-testid="stSidebarCollapsedControl"] {
  top: 0.55rem !important;
  left: 0.85rem !important;
  z-index: 999 !important;
}
[data-testid="stSidebarCollapsedControl"] button,
[data-testid="stSidebarCollapsedControl"] button[kind="header"] {
  background: var(--card) !important;
  color: var(--accent) !important;
  border: 1px solid var(--line) !important;
  border-radius: 10px !important;
  width: 2.35rem !important;
  height: 2.35rem !important;
  min-width: 2.35rem !important;
  min-height: 2.35rem !important;
  padding: 0 !important;
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
  box-shadow: 0 1px 3px rgba(26, 46, 40, 0.06) !important;
  transition: background 0.15s ease, border-color 0.15s ease !important;
}
[data-testid="stSidebarCollapsedControl"] button:hover,
[data-testid="stSidebarCollapsedControl"] button:focus-visible {
  background: var(--accent-soft) !important;
  border-color: #8fbcad !important;
  color: var(--accent) !important;
  outline: none !important;
}
[data-testid="stSidebarCollapsedControl"] svg {
  width: 1.1rem !important;
  height: 1.1rem !important;
  stroke: currentColor !important;
}

/* 侧栏展开时，左上角收起钮也统一成同样的小圆角样式 */
[data-testid="stHeader"] button[kind="headerNoPadding"],
[data-testid="stBaseButton-headerNoPadding"] {
  background: var(--card) !important;
  color: var(--accent) !important;
  border: 1px solid var(--line) !important;
  border-radius: 10px !important;
  width: 2.2rem !important;
  height: 2.2rem !important;
  min-width: 2.2rem !important;
  padding: 0 !important;
}
[data-testid="stHeader"] button[kind="headerNoPadding"]:hover {
  background: var(--accent-soft) !important;
  border-color: #8fbcad !important;
}
h1, h2, h3 { color: var(--ink) !important; font-weight: 650 !important; }
h1 { font-size: 1.45rem !important; margin-bottom: 0.35rem !important; }
h2 { font-size: 1.1rem !important; }
h3 { font-size: 1rem !important; }

/* 页头 */
.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
  margin: 0 0 1rem 0;
  padding-bottom: 0.85rem;
  border-bottom: 1px solid var(--line);
}
.page-meta {
  display: flex;
  gap: 0.4rem;
  flex-wrap: wrap;
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  border-radius: 999px;
  padding: 0.22rem 0.65rem;
  font-size: 0.78rem;
  font-weight: 550;
  border: 1px solid var(--line);
  background: var(--card);
  color: var(--ink);
  white-space: nowrap;
}
.chip.ok { background: var(--accent-soft); border-color: #b9d7cc; color: var(--accent); }
.chip.warn { background: #f8efe4; border-color: #e6c9a8; color: #b86e2b; }
.chip.err { background: #f8ecec; border-color: #e3bcbc; color: #a33b3b; }
.chip .dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: currentColor;
}

/* 分区标题 */
.sec-label {
  font-size: 0.75rem;
  font-weight: 650;
  letter-spacing: 0.04em;
  color: var(--accent);
  margin: 0 0 0.15rem 0;
}
.sec-title {
  font-size: 1.05rem;
  font-weight: 700;
  color: var(--ink);
  margin: 0 0 0.25rem 0;
}
.sec-desc {
  margin: 0 0 0.8rem 0;
  font-size: 0.88rem;
  color: var(--muted);
}

/* 流水线 */
.pipeline {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-wrap: wrap;
  margin: 0 0 1rem 0;
  font-size: 0.84rem;
  color: var(--muted);
}
.pipeline .step {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 0.25rem 0.7rem;
  font-weight: 500;
  color: var(--ink);
}
.pipeline .step.on {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}
.pipeline .step.done {
  background: color-mix(in srgb, var(--accent) 18%, var(--card));
  border-color: color-mix(in srgb, var(--accent) 45%, var(--line));
  color: var(--accent);
}
.pipeline .arrow { color: var(--accent); opacity: 0.7; }

/* 控件 */
.stButton > button {
  border-radius: 8px !important;
  font-weight: 560 !important;
}
.stButton > button[kind="secondary"],
.stButton > button[data-testid="baseButton-secondary"] {
  background: var(--card) !important;
  color: var(--ink) !important;
  border: 1px solid var(--line) !important;
}
.stButton > button[kind="primary"],
.stButton > button[data-testid="baseButton-primary"] {
  background: var(--accent) !important;
  border-color: var(--accent) !important;
  color: #fff !important;
}
.stButton > button:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
[data-baseweb="input"],
[data-baseweb="textarea"],
[data-baseweb="select"] {
  border-radius: 8px !important;
}
[data-testid="stFileUploader"] {
  border-radius: 10px;
  border: 1px dashed var(--accent);
  background: var(--accent-soft);
  padding: 0.5rem;
}
[data-testid="stExpander"] {
  border: 1px solid var(--line) !important;
  border-radius: 10px !important;
  background: var(--card) !important;
}
[data-testid="stAlert"] {
  border-radius: 8px !important;
  border-left-width: 4px !important;
}
[data-testid="stDataFrame"] {
  border: 1px solid var(--line);
  border-radius: 10px;
  overflow: hidden;
}
[data-baseweb="tab-list"] {
  gap: 4px;
  border-bottom: 1px solid var(--line) !important;
}
[data-baseweb="tab"] {
  border-radius: 8px 8px 0 0 !important;
  font-weight: 550 !important;
  padding-left: 1rem !important;
  padding-right: 1rem !important;
}
[data-baseweb="tab"][aria-selected="true"] { color: var(--accent) !important; }
.stProgress > div > div > div > div { background-color: var(--accent) !important; }
.stCodeBlock, pre {
  border-radius: 10px !important;
  background: #1a2421 !important;
  color: #d8ebe4 !important;
  font-size: 0.78rem !important;
}
hr { border-color: var(--line) !important; opacity: 0.7; }

/* 窄屏：收紧边距与侧栏，避免控件被挤出可视区 */
@media (max-width: 820px) {
  .block-container {
    padding-left: 0.9rem !important;
    padding-right: 0.9rem !important;
    padding-top: 2.6rem !important;
  }
  section[data-testid="stSidebar"][aria-expanded="true"] {
    min-width: 240px !important;
  }
  .page-head {
    gap: 0.6rem;
  }
}

/* 侧栏：与主区同一浅色临床风（仅展开时占位，避免折叠后左侧空白） */
section[data-testid="stSidebar"][aria-expanded="true"] {
  background: #eef2ef !important;
  border-right: 1px solid var(--line) !important;
  min-width: 280px !important;
}
section[data-testid="stSidebar"] > div {
  background: #eef2ef !important;
}
section[data-testid="stSidebar"] [data-testid="stSidebarContent"] {
  background: #eef2ef !important;
  padding-top: 0.75rem !important;
}
section[data-testid="stSidebar"] .stMarkdown,
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] .stCaption,
section[data-testid="stSidebar"] p {
  color: var(--ink) !important;
}
section[data-testid="stSidebar"] .stCaption {
  color: var(--muted) !important;
}
section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3 {
  color: var(--ink) !important;
  font-weight: 650 !important;
  border-bottom: 1px solid #c5d6cf !important;
  padding-bottom: 0.35rem !important;
  margin-top: 1.15rem !important;
  margin-bottom: 0.7rem !important;
  letter-spacing: 0.01em;
}
section[data-testid="stSidebar"] h2:first-of-type {
  margin-top: 0.2rem !important;
  border-bottom-color: #8fbcad !important;
  color: var(--accent) !important;
}
section[data-testid="stSidebar"] hr {
  border-color: #c5d6cf !important;
  opacity: 1 !important;
  margin: 1.1rem 0 !important;
}
section[data-testid="stSidebar"] [data-testid="stTextInputRootElement"],
section[data-testid="stSidebar"] [data-testid="stNumberInputRootElement"],
section[data-testid="stSidebar"] [data-baseweb="input"],
section[data-testid="stSidebar"] [data-baseweb="textarea"],
section[data-testid="stSidebar"] [data-baseweb="select"] {
  background: var(--card) !important;
  border: 1px solid #c5d6cf !important;
  border-radius: 8px !important;
}
section[data-testid="stSidebar"] input,
section[data-testid="stSidebar"] textarea {
  color: var(--ink) !important;
  -webkit-text-fill-color: var(--ink) !important;
  background: transparent !important;
}
section[data-testid="stSidebar"] input::placeholder,
section[data-testid="stSidebar"] textarea::placeholder {
  color: #9aaba4 !important;
  -webkit-text-fill-color: #9aaba4 !important;
}
section[data-testid="stSidebar"] [data-baseweb="select"] {
  cursor: pointer !important;
  color: var(--ink) !important;
}
section[data-testid="stSidebar"] [data-baseweb="select"] span {
  color: var(--ink) !important;
}
section[data-testid="stSidebar"] [data-testid="stFileUploader"] {
  background: var(--accent-soft) !important;
  border: 1px dashed #8fbcad !important;
  border-radius: 10px !important;
  padding: 0.4rem !important;
}
section[data-testid="stSidebar"] [data-testid="stExpander"] {
  background: var(--card) !important;
  border: 1px solid #c5d6cf !important;
  border-radius: 10px !important;
}
section[data-testid="stSidebar"] [data-testid="stExpander"] summary {
  color: var(--ink) !important;
  font-weight: 600 !important;
}
section[data-testid="stSidebar"] .stButton > button {
  border-radius: 8px !important;
  font-weight: 560 !important;
  font-size: 0.86rem !important;
  min-height: 2.2rem !important;
}
section[data-testid="stSidebar"] .stButton > button[kind="secondary"],
section[data-testid="stSidebar"] .stButton > button[data-testid="baseButton-secondary"] {
  background: var(--card) !important;
  color: var(--ink) !important;
  border: 1px solid #c5d6cf !important;
}
section[data-testid="stSidebar"] .stButton > button[kind="primary"],
section[data-testid="stSidebar"] .stButton > button[data-testid="baseButton-primary"] {
  background: var(--accent) !important;
  border-color: var(--accent) !important;
  color: #fff !important;
}
section[data-testid="stSidebar"] .stButton > button:hover {
  box-shadow: 0 1px 6px rgba(45, 106, 90, 0.12);
}
section[data-testid="stSidebar"] [data-testid="stSlider"] label,
section[data-testid="stSidebar"] [data-baseweb="form-control-label"] {
  color: var(--ink) !important;
}
section[data-testid="stSidebar"] [data-testid="stAlert"] {
  border-radius: 8px !important;
  background: var(--card) !important;
}
section[data-testid="stSidebar"] [data-baseweb="tab-list"] {
  border-bottom-color: var(--line) !important;
}
section[data-testid="stSidebar"] [data-baseweb="tab"][aria-selected="true"] {
  color: var(--accent) !important;
}
section[data-testid="stSidebar"] .stSlider [data-baseweb="slider"] {
  background: transparent !important;
}

.footer-note {
  margin-top: 1.5rem;
  padding-top: 0.7rem;
  border-top: 1px solid var(--line);
  font-size: 0.78rem;
  color: var(--muted);
  display: flex;
  justify-content: space-between;
  gap: 0.75rem;
  flex-wrap: wrap;
}

@media (prefers-reduced-motion: reduce) {
  .stButton > button { transition: none !important; }
}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ----------------------------------------------------------------------
# Core helpers
# ----------------------------------------------------------------------
def extract_sections(text: str, keys: dict = None) -> dict:
    return rp_extract_sections(text, keys)


def get_available_templates():
    return rp_get_available_templates("template")


def process_file(raw_path: Path, template_path: Path, output_dir: Path):
    try:
        raw_text = raw_path.read_text(encoding="utf-8")
        out_path = output_dir / (raw_path.stem + ".json")
        if out_path.exists():
            n = 2
            while True:
                cand = output_dir / f"{raw_path.stem}_{n}.json"
                if not cand.exists():
                    out_path = cand
                    break
                n += 1
        process_text_to_template_json(raw_text, template_path, out_path)
        log_info(f"成功提取 JSON (模板格式): {out_path}")
        return out_path
    except Exception as e:
        error_msg = f"处理文件 {raw_path.name} 失败: {str(e)}"
        log_error(error_msg)
        raise Exception(error_msg) from e


def process_ai_file(
    ai_raw_file_obj,
    config,
    ai_output_dir,
    template_path,
    retry_feedback: str = None,
    on_stage=None,
):
    """处理单个 AI 文件：改写 → 校验 → 切割 → 抽 JSON。

    on_stage: 可选回调 on_stage("rewrite"|"validate"|"split"|"json"|"done")
    """
    def _stage(name: str):
        if on_stage:
            try:
                on_stage(name)
            except Exception:
                pass

    file_name = "unknown"
    raw_text = ""
    rewritten = None
    try:
        if isinstance(ai_raw_file_obj, dict):
            file_name = ai_raw_file_obj["name"]
            raw_text = ai_raw_file_obj["content"]
        else:
            file_name = ai_raw_file_obj.name
            raw_text = decode_upload_bytes(ai_raw_file_obj.getvalue(), file_name)

        log_info(f"开始处理文件: {file_name}")

        prompt_config = load_prompt_config()
        system_prompt = prompt_config.get("system_prompt", "你是一名经验丰富的中医门诊医师。")
        user_prompt_template = prompt_config.get("user_prompt_template", "")

        user_prompt = user_prompt_template.replace("{raw_text}", raw_text)
        if retry_feedback:
            user_prompt = (
                user_prompt
                + "\n\n【上次输出校验失败，请务必修正以下问题后再输出】\n"
                + retry_feedback
                + "\n请严格按照模板完整输出五段内容，并以「姓名：」开头。"
            )

        _stage("rewrite")
        rewritten = call_chat_completion(
            config["api_url"],
            api_key=config.get("api_key", ""),
            model=config.get("model", "gpt-3.5-turbo"),
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=config.get("temperature", 0.7),
            max_tokens=config.get("max_tokens", 4000),
            timeout=int(config.get("timeout", 120)),
            max_retries=int(config.get("max_retries", 3)),
            enable_thinking=bool(config.get("enable_thinking", False)),
        )

        log_info(f"AI 改写完成: {file_name}")

        rewritten = normalize_ai_output(rewritten or "")
        if not rewritten or not rewritten.strip():
            raise Exception("AI 返回内容为空")

        _stage("validate")
        validation = validate_rewritten_text(rewritten, require_patient_header=True)
        if not validation.ok:
            preview = rewritten[:400].replace("\n", " / ")
            raise Exception(
                f"改写结果校验失败: {'；'.join(validation.errors)}"
                f"。已有内容前400字: {preview}"
            )
        if validation.warnings:
            log_info(f"校验警告 {file_name}: {'；'.join(validation.warnings)}")

        rewrite_dir = ai_output_dir / "AI_Rewrite"
        json_dir = ai_output_dir / "JSON_Transcript"
        rewrite_dir.mkdir(parents=True, exist_ok=True)
        json_dir.mkdir(parents=True, exist_ok=True)

        file_prefix = Path(_safe_upload_name(file_name)).stem
        temp_full_path = ai_output_dir / f"{file_prefix}_temp_full.txt"
        json_count = 0
        json_errors = []
        written = []
        try:
            temp_full_path.write_text(rewritten, encoding="utf-8")
            raw_split_text = temp_full_path.read_text(encoding="utf-8")
            _stage("split")
            records = split_patient_records(raw_split_text, delimiter=None)
            if len(records) == 1 and records[0].name == "未知":
                from record_parser import parse_name_age_from_name_line
                hint_name, _ = parse_name_age_from_name_line(file_prefix)
                if hint_name and hint_name != "未知":
                    records[0].name = hint_name

            # 逐患者再校验，避免「第一人齐全、后面缺段」被整体校验放行
            incomplete = []
            for rec in records:
                rec_val = validate_rewritten_text(rec.content, require_patient_header=False)
                if not rec_val.ok:
                    incomplete.append(f"{rec.name}: {'；'.join(rec_val.errors)}")
            if incomplete:
                raise Exception("切割后存在缺段患者: " + " | ".join(incomplete))

            written = write_split_files(records, rewrite_dir, file_prefix=file_prefix)
            log_info(f"文件 {file_name} 切割为 {len(written)} 个子文件 (保存至 {rewrite_dir})")

            _stage("json")
            for txt_file in written:
                try:
                    process_file(txt_file, template_path, json_dir)
                    json_count += 1
                except Exception as e:
                    log_error(f"提取 JSON 失败 {txt_file.name}: {e}")
                    json_errors.append(f"{txt_file.name}: {e}")
        finally:
            if temp_full_path.exists():
                try:
                    os.remove(temp_full_path)
                except Exception as e:
                    log_error(f"无法删除临时文件 {temp_full_path}: {e}")

        if json_count == 0:
            save_failed_case(
                ai_output_dir,
                stage="json_extract",
                source_name=file_name,
                content=rewritten,
                errors=json_errors or ["未能生成任何 JSON"],
                warnings=validation.warnings,
            )
            raise Exception(
                f"JSON 提取全部失败: {'；'.join(json_errors) if json_errors else '无输出文件'}"
            )

        msg = (
            f"文件 {file_name} 处理完成：切割 {len(written)} 人，JSON {json_count} 份。\n"
            f"- 改写文件: `{rewrite_dir}`\n"
            f"- JSON 文件: `{json_dir}`"
        )
        if validation.warnings:
            msg += "\n- 警告: " + "；".join(validation.warnings)
        _stage("done")
        return True, msg, rewritten

    except Exception as e:
        err_msg = f"文件 {file_name} 处理失败: {e}"
        log_error(err_msg)
        try:
            content_to_save = rewritten if rewritten else raw_text
            if content_to_save:
                save_failed_case(
                    ai_output_dir,
                    stage="ai_rewrite",
                    source_name=file_name,
                    content=content_to_save,
                    errors=[str(e)],
                )
        except Exception as save_err:
            log_error(f"保存失败样本时出错: {save_err}")
        return False, err_msg, None


def _safe_upload_name(name: str) -> str:
    base = Path(name or "upload.txt").name
    base = re.sub(r'[\\/:*?"<>|]', "_", base).strip() or "upload.txt"
    return base


MAX_FAILED_FILES = 30


def _append_failed_file(name: str, content: str, last_error: str) -> None:
    """失败队列有上限，避免长时间会话把 PHI 无限堆在内存里。"""
    items = st.session_state.failed_files
    items.append({"name": name, "content": content, "last_error": last_error})
    if len(items) > MAX_FAILED_FILES:
        st.session_state.failed_files = items[-MAX_FAILED_FILES:]
        log_error(f"失败队列已满，仅保留最近 {MAX_FAILED_FILES} 条")


# ---- 提示词配置管理（含版本化） ----
PROMPT_CONFIG_FILE = "prompt_config.json"
PROMPT_HISTORY_DIR = Path("prompt_history")


def get_default_prompt_config():
    return {
        "version": 1,
        "system_prompt": "你是一名经验丰富的中医门诊医师。",
        "user_prompt_template": """请将以下这份门诊病历(可能包含一个或多个患者)改写成标准的门诊病历。
要求：
1. 如果包含多位患者，请务必保持每位患者的记录独立。
2. 每位患者的记录必须以 "姓名：XXX" 开头(如果原始记录中有姓名)。
3. 包含完整的：主诉、现病史、既往史、过敏史、望闻问切(四诊)、病因病机分析、中医诊断及辩证、治法、处方。
4. 保持原意，但使用专业的中医术语。
5. 【重要】"四诊"部分(包含主诉、现病史、既往史、过敏史、望闻问切)必须写成**一个完整的段落**，中间**绝对不要换行**。所有内容连在一起写。
6. 直接输出改写后的病历内容，不要包含任何解释性语言。

原始病历：
{raw_text}""",
    }


def load_prompt_config():
    if os.path.exists(PROMPT_CONFIG_FILE):
        try:
            with open(PROMPT_CONFIG_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    log_info("提示词配置文件为空，使用默认配置")
                    return get_default_prompt_config()
                data = json.loads(content)
                if "system_prompt" not in data or "user_prompt_template" not in data:
                    log_error("提示词配置文件缺少必需字段，使用默认配置")
                    return get_default_prompt_config()
                if "{raw_text}" not in data["user_prompt_template"]:
                    log_error("提示词模板缺少 {raw_text} 占位符，使用默认配置")
                    return get_default_prompt_config()
                data.setdefault("version", 1)
                return data
        except json.JSONDecodeError as e:
            log_error(f"提示词配置文件 JSON 格式错误: {e}，将使用默认配置")
            try:
                import shutil
                shutil.copy(PROMPT_CONFIG_FILE, PROMPT_CONFIG_FILE + ".bak")
                log_info(f"已备份损坏的配置文件到 {PROMPT_CONFIG_FILE}.bak")
            except Exception:
                pass
            return get_default_prompt_config()
        except Exception as e:
            log_error(f"加载提示词配置文件失败: {e}")
            return get_default_prompt_config()
    return get_default_prompt_config()


def _snapshot_prompt_config(prompt_config: dict):
    try:
        PROMPT_HISTORY_DIR.mkdir(exist_ok=True)
        ver = int(prompt_config.get("version", 1))
        snap_path = PROMPT_HISTORY_DIR / f"prompt_v{ver}.json"
        while snap_path.exists():
            ver += 1
            snap_path = PROMPT_HISTORY_DIR / f"prompt_v{ver}.json"
        payload = dict(prompt_config)
        payload["version"] = ver
        payload["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        snap_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return ver
    except Exception as e:
        log_error(f"保存提示词快照失败: {e}")
        return None


def list_prompt_versions():
    if not PROMPT_HISTORY_DIR.exists():
        return []
    return sorted(PROMPT_HISTORY_DIR.glob("prompt_v*.json"), key=lambda p: p.stat().st_mtime)


def load_prompt_version(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "system_prompt" not in data or "user_prompt_template" not in data:
        raise ValueError("历史版本缺少必需字段")
    if "{raw_text}" not in data["user_prompt_template"]:
        raise ValueError("历史版本缺少 {raw_text} 占位符")
    return data


def save_prompt_config(prompt_config):
    try:
        old_ver = 1
        if os.path.exists(PROMPT_CONFIG_FILE):
            try:
                old_data = json.loads(Path(PROMPT_CONFIG_FILE).read_text(encoding="utf-8"))
                old_ver = int(old_data.get("version", 1))
            except Exception:
                pass
        prompt_config = dict(prompt_config)
        prompt_config["version"] = old_ver + 1
        with open(PROMPT_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(prompt_config, f, ensure_ascii=False, indent=2)
        snap_ver = _snapshot_prompt_config(prompt_config)
        log_info(f"提示词配置已保存 (v{prompt_config['version']}, 快照 v{snap_ver})")
        return True
    except Exception as e:
        log_error(f"保存提示词配置文件失败: {e}")
        return False


# ---- API 配置 ----
CONFIG_FILE = "api_config.json"


def _env_api_key() -> str:
    """优先使用环境变量密钥，避免把真实 Key 固化在仓库/配置里。"""
    for name in ("CLINIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
        val = os.environ.get(name, "").strip()
        if val:
            return val
    return ""


def _normalize_api_url(url: str) -> str:
    """纠正常见 DeepSeek/OpenAI 地址缺路径问题。"""
    u = (url or "").strip().rstrip("/")
    if not u:
        return u
    lower = u.lower()
    if lower in ("https://api.deepseek.com", "http://api.deepseek.com"):
        return u + "/v1/chat/completions"
    if lower in ("https://api.openai.com", "http://api.openai.com"):
        return u + "/v1/chat/completions"
    if lower.endswith("api.deepseek.com/chat/completions") and "/v1/" not in lower:
        return u.replace("/chat/completions", "/v1/chat/completions")
    if lower.endswith("api.openai.com/chat/completions") and "/v1/" not in lower:
        return u.replace("/chat/completions", "/v1/chat/completions")
    return u


def _coerce_profile(profile: dict, defaults: dict) -> dict:
    """把磁盘配置强转为 UI 控件需要的类型，避免手改 JSON 后滑条/数字框崩溃。"""
    out = dict(defaults)
    out.update(profile or {})
    try:
        out["temperature"] = float(out.get("temperature", 0.7))
    except (TypeError, ValueError):
        out["temperature"] = 0.7
    try:
        out["max_tokens"] = int(out.get("max_tokens", 4000))
    except (TypeError, ValueError):
        out["max_tokens"] = 4000
    try:
        out["timeout"] = max(5, int(out.get("timeout", 120)))
    except (TypeError, ValueError):
        out["timeout"] = 120
    try:
        out["max_retries"] = max(0, int(out.get("max_retries", 3)))
    except (TypeError, ValueError):
        out["max_retries"] = 3
    # 病历改写默认关闭思考，避免推理占满 max_tokens 导致正文为空
    out["enable_thinking"] = bool(out.get("enable_thinking", False))
    out["api_url"] = str(out.get("api_url") or "")
    out["api_key"] = str(out.get("api_key") or "")
    out["model"] = str(out.get("model") or "gpt-3.5-turbo")
    return out


def load_config():
    default_profile = {
        "api_url": "https://api.openai.com/v1/chat/completions",
        "api_key": "",
        "model": "gpt-3.5-turbo",
        "temperature": 0.7,
        "max_tokens": 4000,
    }
    default_config = {"current_profile": "默认配置", "profiles": {"默认配置": default_profile}}

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return default_config
                data = json.loads(content)
                if "profiles" not in data:
                    old_profile = _coerce_profile(
                        {
                            "api_url": data.get("api_url", default_profile["api_url"]),
                            "api_key": data.get("api_key", default_profile["api_key"]),
                            "model": data.get("model", default_profile["model"]),
                            "temperature": data.get("temperature", default_profile["temperature"]),
                            "max_tokens": data.get("max_tokens", default_profile["max_tokens"]),
                        },
                        default_profile,
                    )
                    return {"current_profile": "默认配置", "profiles": {"默认配置": old_profile}}
                profiles = data.get("profiles") or {}
                if not isinstance(profiles, dict):
                    profiles = {}
                coerced = {
                    name: _coerce_profile(p if isinstance(p, dict) else {}, default_profile)
                    for name, p in profiles.items()
                }
                data["profiles"] = coerced or {"默认配置": dict(default_profile)}
                if not data.get("current_profile"):
                    data["current_profile"] = next(iter(data["profiles"]))
                return data
        except json.JSONDecodeError as e:
            log_error(f"配置文件 JSON 格式错误: {e}，将使用默认配置")
            try:
                import shutil
                shutil.copy(CONFIG_FILE, CONFIG_FILE + ".bak")
            except Exception:
                pass
            return default_config
        except Exception as e:
            log_error(f"加载配置文件失败: {e}")
            return default_config
    return default_config


def save_config(config):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        log_info("配置已保存")
    except Exception as e:
        log_error(f"保存配置文件失败: {e}")


def resolve_runtime_config(profile: dict) -> dict:
    """合并环境变量 Key，并规范化 API URL（不改写磁盘配置）。"""
    cfg = dict(profile or {})
    env_key = _env_api_key()
    if env_key:
        cfg["api_key"] = env_key
    cfg["api_url"] = _normalize_api_url(cfg.get("api_url", ""))
    return cfg


# =====================================================================
# 初始化
# =====================================================================
if "selected_template" not in st.session_state:
    available_templates = get_available_templates()
    if available_templates:
        if "门诊小病例模板.json" in available_templates:
            st.session_state.selected_template = "门诊小病例模板.json"
        else:
            st.session_state.selected_template = available_templates[0]
    else:
        st.session_state.selected_template = None

if "failed_files" not in st.session_state:
    st.session_state.failed_files = []

if "ai_pipeline" not in st.session_state:
    st.session_state.ai_pipeline = {"active": 2, "done": 0}

full_config = load_config()
current_profile_name = full_config.get("current_profile", "默认配置")
profiles = full_config.get("profiles", {})
if current_profile_name not in profiles:
    current_profile_name = list(profiles.keys())[0] if profiles else "默认配置"
    if not profiles:
        profiles = {
            "默认配置": {
                "api_url": "https://api.openai.com/v1/chat/completions",
                "api_key": "",
                "model": "gpt-3.5-turbo",
                "temperature": 0.7,
                "max_tokens": 4000,
            }
        }


# =====================================================================
# 侧边栏：模板（常显） / API、提示词（收进展开）
# =====================================================================
with st.sidebar:
    st.markdown("## 配置中心")

    available_templates = get_available_templates()

    # 刚导入的模板在控件创建前选中
    pending_tpl = st.session_state.pop("_pending_template", None)
    if pending_tpl and pending_tpl in available_templates:
        st.session_state["cfg_template"] = pending_tpl
        st.session_state.selected_template = pending_tpl

    if not available_templates:
        st.warning("template/ 下暂无模板，可先导入自定义模板")
        template_path = None
    else:
        selected_template = st.selectbox(
            "模板",
            options=available_templates,
            index=available_templates.index(st.session_state.selected_template)
            if st.session_state.selected_template in available_templates
            else 0,
            key="cfg_template",
        )
        st.session_state.selected_template = selected_template
        template_path = Path("template") / selected_template

    with st.expander("导入自定义模板", expanded=False):
        st.caption("从任意文件夹选择奎享导出的 .json；可选同名 .map.json 映射")
        tpl_file = st.file_uploader("模板 JSON", type=["json"], key="tpl_upload")
        map_file = st.file_uploader("映射 map.json（可选）", type=["json"], key="map_upload")
        if st.button("导入并选用", use_container_width=True, key="btn_import_tpl"):
            if not tpl_file:
                st.error("请先选择模板 JSON 文件")
            else:
                try:
                    dest = install_custom_template(
                        tpl_file.getvalue(),
                        filename=tpl_file.name,
                        map_bytes=map_file.getvalue() if map_file else None,
                        map_filename=map_file.name if map_file else None,
                    )
                    st.session_state["_pending_template"] = dest.name
                    st.session_state.selected_template = dest.name
                    st.success(f"已导入 {dest.name}")
                    st.rerun()
                except Exception as e:
                    st.error(f"导入失败: {e}")
                    log_error(f"导入自定义模板失败: {e}")

    # ---- API：外层只留摘要，点开编辑 ----
    pending_profile = st.session_state.pop("_pending_profile", None)
    if pending_profile and pending_profile in profiles:
        st.session_state["cfg_profile"] = pending_profile
        current_profile_name = pending_profile
        full_config["current_profile"] = pending_profile

    profile_names = list(profiles.keys())
    if current_profile_name not in profile_names:
        current_profile_name = profile_names[0] if profile_names else "默认配置"

    selected_profile = st.selectbox(
        "API 方案",
        options=profile_names,
        index=profile_names.index(current_profile_name) if current_profile_name in profile_names else 0,
        key="cfg_profile",
    )
    current_config = profiles[selected_profile]
    runtime_config = resolve_runtime_config(current_config)
    st.caption(f"{selected_profile} · {runtime_config.get('model') or '未配置模型'}")

    with st.expander("编辑 API", expanded=False):
        if st.session_state.get("_cfg_loaded_profile") != selected_profile:
            st.session_state["cfg_api_url"] = current_config.get("api_url", "")
            st.session_state["cfg_api_key"] = current_config.get("api_key", "")
            st.session_state["cfg_model"] = current_config.get("model", "gpt-3.5-turbo")
            st.session_state["cfg_temperature"] = float(current_config.get("temperature", 0.7))
            st.session_state["cfg_max_tokens"] = int(current_config.get("max_tokens", 4000))
            st.session_state["cfg_enable_thinking"] = bool(current_config.get("enable_thinking", False))
            st.session_state["_cfg_loaded_profile"] = selected_profile

        new_api_url = st.text_input("API URL", key="cfg_api_url")
        new_api_key = st.text_input("API Key", type="password", key="cfg_api_key")
        new_model = st.text_input("模型", key="cfg_model")
        col_t, col_m = st.columns(2)
        with col_t:
            new_temperature = st.slider(
                "Temperature", min_value=0.0, max_value=2.0, step=0.1, key="cfg_temperature"
            )
        with col_m:
            new_max_tokens = st.number_input(
                "Max Tokens", min_value=100, max_value=32000, step=100, key="cfg_max_tokens"
            )
            if int(new_max_tokens) < 4000:
                st.caption("偏低：推理模型易截断并导致正文为空，建议 ≥ 8000")
        new_enable_thinking = st.toggle(
            "启用思考模式",
            key="cfg_enable_thinking",
            help="病历改写建议关闭：思考会占用 max_tokens，容易导致正文为空",
        )

        c_save, c_test = st.columns(2)
        with c_save:
            if st.button("保存", use_container_width=True, type="primary", key="btn_save_api"):
                profiles[selected_profile] = {
                    "api_url": new_api_url,
                    "api_key": new_api_key,
                    "model": new_model,
                    "temperature": new_temperature,
                    "max_tokens": new_max_tokens,
                    "timeout": int(current_config.get("timeout", 120)),
                    "max_retries": int(current_config.get("max_retries", 3)),
                    "enable_thinking": bool(new_enable_thinking),
                }
                full_config["profiles"] = profiles
                full_config["current_profile"] = selected_profile
                save_config(full_config)
                st.success(f"已保存「{selected_profile}」")
        with c_test:
            if st.button("测试连接", use_container_width=True, key="btn_test_api"):
                if not new_api_url:
                    st.error("请先填写 API URL")
                else:
                    with st.spinner("测试中..."):
                        try:
                            test_url = _normalize_api_url(new_api_url)
                            test_key = new_api_key or _env_api_key()
                            test_headers = {"Content-Type": "application/json"}
                            if test_key:
                                test_headers["Authorization"] = f"Bearer {test_key}"
                            test_payload = {
                                "model": new_model,
                                "messages": [{"role": "user", "content": "Hi"}],
                                "max_tokens": 5,
                            }
                            resp = requests.post(
                                test_url, headers=test_headers, json=test_payload, timeout=10
                            )
                            resp.raise_for_status()
                            try:
                                resp_json = resp.json()
                                if "choices" in resp_json or "output" in resp_json or "result" in resp_json:
                                    st.success("连接成功")
                                else:
                                    st.warning("连接成功但响应结构异常")
                            except json.JSONDecodeError:
                                st.error("响应不是 JSON，请检查 URL 是否含 /v1/chat/completions")
                        except Exception as e:
                            st.error(f"连接失败: {e}")
                            log_error(f"API 连接测试失败 ({selected_profile}): {e}")

        st.divider()
        new_profile_name = st.text_input(
            "新建方案名", placeholder="例如 deepseek", key="cfg_new_profile_name"
        )
        c_new, c_del = st.columns(2)
        with c_new:
            if st.button("新建方案", use_container_width=True, key="btn_new_profile"):
                name = (new_profile_name or "").strip()
                if not name:
                    st.error("请输入方案名")
                elif name in profiles:
                    st.error("名称已存在")
                else:
                    profiles[name] = profiles[selected_profile].copy()
                    full_config["profiles"] = profiles
                    full_config["current_profile"] = name
                    save_config(full_config)
                    st.session_state["_pending_profile"] = name
                    st.success(f"已创建 {name}")
                    st.rerun()
        with c_del:
            if st.button("删除当前", use_container_width=True, key="btn_del_profile"):
                if len(profiles) <= 1:
                    st.error("至少保留一个")
                else:
                    del profiles[selected_profile]
                    remain = list(profiles.keys())[0]
                    full_config["profiles"] = profiles
                    full_config["current_profile"] = remain
                    save_config(full_config)
                    st.session_state["_pending_profile"] = remain
                    st.success(f"已删除 {selected_profile}")
                    st.rerun()

    # ---- 提示词：同样收进展开 ----
    current_prompt_config = load_prompt_config()
    ver = current_prompt_config.get("version", 1)
    st.caption(f"提示词 v{ver}")

    if "prompt_system_draft" not in st.session_state:
        st.session_state.prompt_system_draft = current_prompt_config.get("system_prompt", "")
    if "prompt_user_draft" not in st.session_state:
        st.session_state.prompt_user_draft = current_prompt_config.get("user_prompt_template", "")
    if st.session_state.get("_prompt_version_loaded") != ver:
        st.session_state.prompt_system_draft = current_prompt_config.get("system_prompt", "")
        st.session_state.prompt_user_draft = current_prompt_config.get("user_prompt_template", "")
        st.session_state._prompt_version_loaded = ver

    with st.expander("编辑提示词", expanded=False):
        new_system_prompt = st.text_area("系统提示词", key="prompt_system_draft", height=140)
        new_user_prompt_template = st.text_area(
            "用户提示词模板（须含 {raw_text}）",
            key="prompt_user_draft",
            height=220,
        )
        placeholder_valid = "{raw_text}" in new_user_prompt_template
        if not placeholder_valid:
            st.error("缺少 `{raw_text}`")

        c1, c2 = st.columns(2)
        with c1:
            if st.button(
                "保存",
                disabled=not placeholder_valid,
                use_container_width=True,
                key="btn_prompt_save",
            ):
                new_config = {
                    "version": ver,
                    "system_prompt": new_system_prompt,
                    "user_prompt_template": new_user_prompt_template,
                }
                if save_prompt_config(new_config):
                    st.success("已保存")
                    st.rerun()
                else:
                    st.error("保存失败")
        with c2:
            if st.button("重置默认", use_container_width=True, key="btn_prompt_reset"):
                if save_prompt_config(get_default_prompt_config()):
                    st.success("已重置")
                    st.rerun()

        versions = list_prompt_versions()
        if versions:
            labels = [
                f"{p.stem} ({time.strftime('%m-%d %H:%M', time.localtime(p.stat().st_mtime))})"
                for p in versions
            ]
            selected_ver = st.selectbox(
                "历史版本",
                options=list(range(len(versions))),
                format_func=lambda i: labels[i],
                key="prompt_version_pick",
            )
            if st.button("恢复选中版本", use_container_width=True, key="btn_prompt_restore"):
                try:
                    restored = load_prompt_version(versions[selected_ver])
                    if save_prompt_config(restored):
                        st.success(f"已恢复 {versions[selected_ver].name}")
                        st.rerun()
                except Exception as e:
                    st.error(f"恢复失败: {e}")

    config = profiles[selected_profile]
    runtime_config = resolve_runtime_config(config)


# =====================================================================
# 主区
# =====================================================================
# 状态就绪度（页头 chips）
template_ready = template_path is not None
api_configured = bool(runtime_config.get("api_url") and runtime_config.get("model"))
failed_n = len(st.session_state.failed_files)

st.markdown(
    f"""
    <div class="page-head">
      <div>
        <div class="page-title">规培手写门诊病历自动排版</div>
        <p class="page-sub">乱文本 → AI 改写 → 校验切割 → 奎享雕刻可导入 JSON</p>
      </div>
      <div class="page-meta">
        <span class="chip {'ok' if template_ready else 'err'}">
          <span class="dot"></span>模板{'就绪' if template_ready else '缺失'}
        </span>
        <span class="chip {'ok' if api_configured else 'warn'}">
          <span class="dot"></span>API{'已配置' if api_configured else '待配置'}
        </span>
        {f'<span class="chip warn"><span class="dot"></span>失败 {failed_n}</span>' if failed_n else ''}
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)


PIPELINE_STEPS = ["① 切割", "② AI 改写", "③ 校验", "④ JSON"]


def _pipeline_html(active: int, done: int = 0) -> str:
    parts = []
    for i, s in enumerate(PIPELINE_STEPS, 1):
        if i == active:
            cls = "step on"
        elif i <= done:
            cls = "step done"
        else:
            cls = "step"
        parts.append(f'<span class="{cls}">{s}</span>')
        if i < len(PIPELINE_STEPS):
            parts.append('<span class="arrow">→</span>')
    return f'<div class="pipeline">{"".join(parts)}</div>'


def render_pipeline(active: int = 1, done: int = 0):
    st.markdown(_pipeline_html(active, done), unsafe_allow_html=True)


def render_section_head(num: str, title: str, desc: str = ""):
    st.markdown(
        f'<div class="sec-label">{num}</div>'
        f'<div class="sec-title">{title}</div>'
        + (f'<p class="sec-desc">{desc}</p>' if desc else ""),
        unsafe_allow_html=True,
    )


tab_split, tab_ai, tab_basic, tab_log = st.tabs(
    ["批量切割", "AI 改写并提取", "基础提取", "运行日志"]
)

# ---------------------------------------------------------------------
with tab_split:
    render_section_head("01", "批量病历切割", "按「姓名：」或自定义标记，把多患者大文件拆成单人文件。")
    render_pipeline(active=1)

    multi_txt = st.file_uploader("上传包含多个患者的 txt 文件", type=["txt"], key="split_uploader")
    c_out, c_del = st.columns([2, 1])
    with c_out:
        split_output_input = st.text_input("保存目录", value="split_output", key="split_out")
    with c_del:
        delimiter_input = st.text_input(
            "自定义切割标记", value="", placeholder="留空则按姓名", key="split_delim"
        )

    split_output_dir = safe_project_path(split_output_input, default_subdir="split_output")
    split_output_dir.mkdir(parents=True, exist_ok=True)

    if st.button("执行切割", type="primary", key="btn_split"):
        if not multi_txt:
            st.error("请先上传 txt 文件。")
        else:
            try:
                temp_input = split_output_dir / _safe_upload_name(multi_txt.name)
                temp_input.write_bytes(multi_txt.getvalue())
                delimiter = delimiter_input if delimiter_input.strip() != "" else None

                raw_text = temp_input.read_text(encoding="utf-8")
                records = split_patient_records(raw_text, delimiter=delimiter)
                written = write_split_files(records, split_output_dir)

                msg = f"已生成 {len(written)} 个患者文件，保存至 `{split_output_dir}`。"
                st.success(msg)
                log_info(f"批量切割成功: {msg}")

                if written:
                    st.markdown("**切割结果预览**")
                    preview_rows = []
                    for p in written:
                        first_line = ""
                        try:
                            for line in p.read_text(encoding="utf-8").splitlines():
                                if line.strip():
                                    first_line = line.strip()[:60]
                                    break
                        except Exception:
                            pass
                        preview_rows.append({"文件名": p.name, "首行摘要": first_line})
                    st.dataframe(preview_rows, use_container_width=True, hide_index=True)
            except Exception as e:
                err_msg = f"批量切割失败: {e}"
                st.error(err_msg)
                log_error(err_msg)

# ---------------------------------------------------------------------
with tab_ai:
    render_section_head(
        "02",
        "AI 改写并提取",
        "格式混乱的病历 → AI 改写为标准五段 → 校验 → 切割 → 填模板 JSON。",
    )
    ai_pipe = st.session_state.get("ai_pipeline") or {"active": 2, "done": 0}
    pipeline_slot = st.empty()
    pipeline_slot.markdown(_pipeline_html(ai_pipe["active"], ai_pipe["done"]), unsafe_allow_html=True)

    # 阶段 → (当前步, 已完成步)；切割后进入 JSON，成功后停在④
    _AI_STAGE_MAP = {
        "rewrite": (2, 1),
        "validate": (3, 2),
        "split": (3, 2),
        "json": (4, 3),
        "done": (4, 4),
    }

    def _set_ai_pipeline(active: int, done: int):
        st.session_state["ai_pipeline"] = {"active": active, "done": done}
        pipeline_slot.markdown(_pipeline_html(active, done), unsafe_allow_html=True)

    def _ai_on_stage(stage: str):
        a, d = _AI_STAGE_MAP.get(stage, (2, 0))
        _set_ai_pipeline(a, d)

    ai_raw_files = st.file_uploader(
        "上传原始病历 txt（支持多选）",
        type=["txt"],
        accept_multiple_files=True,
        key="ai_uploader",
    )
    ai_output_input = st.text_input("输出根目录", value="output", key="ai_out")
    ai_output_dir = safe_project_path(ai_output_input, default_subdir="output")
    ai_output_dir.mkdir(parents=True, exist_ok=True)

    if template_path is None:
        st.warning("请先在左侧选择有效模板。")

    if st.button("执行 AI 改写并提取", type="primary", key="btn_ai"):
        if not ai_raw_files:
            st.error("请先上传原始病历 txt 文件。")
        elif not runtime_config.get("api_url"):
            st.error("请先在侧边栏配置 API URL。")
        elif not runtime_config.get("api_key"):
            st.error("未检测到 API Key：请在侧边栏填写，或设置环境变量 CLINIC_API_KEY / DEEPSEEK_API_KEY。")
        elif template_path is None:
            st.error("请先在侧边栏选择一个有效的 JSON 模板。")
        else:
            total_files = len(ai_raw_files)
            success_count = 0
            _set_ai_pipeline(2, 0)
            progress_bar = st.progress(0)
            status_text = st.empty()

            for i, ai_raw_file in enumerate(ai_raw_files):
                status_text.text(f"正在处理第 {i+1}/{total_files} 个文件: {ai_raw_file.name} ...")
                success, msg, _ = process_ai_file(
                    ai_raw_file,
                    runtime_config,
                    ai_output_dir,
                    template_path,
                    on_stage=_ai_on_stage,
                )

                if not success:
                    retry_feedback = msg
                    status_text.text(f"首次失败，自动重试: {ai_raw_file.name} ...")
                    _set_ai_pipeline(2, 0)
                    time.sleep(2)
                    success, msg, _ = process_ai_file(
                        ai_raw_file,
                        runtime_config,
                        ai_output_dir,
                        template_path,
                        retry_feedback=retry_feedback,
                        on_stage=_ai_on_stage,
                    )

                if success:
                    st.success(msg)
                    success_count += 1
                else:
                    st.error(msg)
                    _append_failed_file(
                        ai_raw_file.name,
                        decode_upload_bytes(ai_raw_file.getvalue(), ai_raw_file.name),
                        msg,
                    )

                progress_bar.progress((i + 1) / total_files)
                if i < total_files - 1:
                    time.sleep(2)

            status_text.text(f"所有文件处理完成！成功 {success_count}/{total_files} 个。")
            if success_count == total_files and total_files > 0:
                _set_ai_pipeline(4, 4)
            elif success_count > 0:
                _set_ai_pipeline(4, 3)
            log_info(f"批量处理完成: 成功 {success_count}/{total_files}")

    if st.session_state.failed_files:
        st.markdown("---")
        st.warning(f"有 {len(st.session_state.failed_files)} 个文件处理失败，可一键重试。")
        c_retry, c_clear = st.columns([3, 1])
        with c_retry:
            if st.button("🔄 一键重试所有失败文件", key="btn_retry"):
                retry_files = st.session_state.failed_files.copy()
                st.session_state.failed_files = []
                total_retry = len(retry_files)
                retry_success = 0
                retry_progress = st.progress(0)
                retry_status = st.empty()

                for i, f_obj in enumerate(retry_files):
                    retry_status.text(f"正在重试: {f_obj['name']} ...")
                    _set_ai_pipeline(2, 0)
                    success, msg, _ = process_ai_file(
                        f_obj,
                        runtime_config,
                        ai_output_dir,
                        template_path,
                        retry_feedback=f_obj.get("last_error"),
                        on_stage=_ai_on_stage,
                    )
                    if success:
                        st.success(f"重试成功: {msg}")
                        retry_success += 1
                    else:
                        st.error(f"重试失败: {msg}")
                        _append_failed_file(f_obj["name"], f_obj.get("content", ""), msg)

                    retry_progress.progress((i + 1) / total_retry)
                    if i < total_retry - 1:
                        time.sleep(2)

                retry_status.text(f"重试完成！成功 {retry_success}/{total_retry} 个。")
        with c_clear:
            if st.button("清空失败队列", key="btn_clear_failed"):
                st.session_state.failed_files = []
                st.success("已清空")
                st.rerun()

        with st.expander("失败文件列表"):
            for f in st.session_state.failed_files:
                err = f.get("last_error", "")
                st.text(f"- {f['name']}" + (f"  |  {err}" if err else ""))

# ---------------------------------------------------------------------
with tab_basic:
    render_section_head(
        "03",
        "基础 JSON 提取（无需 AI）",
        "适用于已标准格式的文本，必须包含「主诉 / 病因病机分析 / 中医诊断及辩证 / 治法 / 处方」等章节标题。",
    )
    render_pipeline(active=4)

    basic_files = st.file_uploader(
        "上传标准格式 txt（支持多选）",
        type=["txt"],
        accept_multiple_files=True,
        key="basic_uploader",
    )
    basic_output_input = st.text_input("JSON 保存目录", value="output", key="basic_output")
    basic_output_dir = safe_project_path(basic_output_input, default_subdir="output")
    basic_output_dir.mkdir(parents=True, exist_ok=True)

    if st.button("执行基础提取", type="primary", key="btn_basic"):
        if not basic_files:
            st.error("请先上传 txt 文件。")
        elif template_path is None:
            st.error("请先在侧边栏选择一个有效的 JSON 模板。")
        else:
            success_count = 0
            for b_file in basic_files:
                try:
                    content = decode_upload_bytes(b_file.getvalue(), b_file.name)
                    validation = validate_rewritten_text(content, require_patient_header=False)
                    if not validation.ok:
                        save_failed_case(
                            basic_output_dir,
                            stage="basic_extract",
                            source_name=b_file.name,
                            content=content,
                            errors=validation.errors,
                            warnings=validation.warnings,
                        )
                        st.error(f"基础提取失败 {b_file.name}: {'；'.join(validation.errors)}")
                        log_error(f"基础提取校验失败 {b_file.name}: {validation.errors}")
                        continue

                    temp_path = basic_output_dir / _safe_upload_name(b_file.name)
                    temp_path.write_bytes(b_file.getvalue())
                    process_file(temp_path, template_path, basic_output_dir)
                    success_count += 1
                    log_info(f"基础提取成功: {b_file.name}")
                except Exception as e:
                    err_msg = f"基础提取失败 {b_file.name}: {e}"
                    st.error(err_msg)
                    log_error(err_msg)
                    try:
                        save_failed_case(
                            basic_output_dir,
                            stage="basic_extract",
                            source_name=b_file.name,
                            content=decode_upload_bytes(b_file.getvalue(), b_file.name),
                            errors=[str(e)],
                        )
                    except Exception:
                        pass

            if success_count > 0:
                st.success(f"已成功提取 {success_count} 个文件的 JSON！")

# ---------------------------------------------------------------------
with tab_log:
    render_section_head(
        "04",
        "系统运行日志",
        f"主日志 `{LOG_FILE}` · 解析错误 `{PARSE_ERROR_LOG}`（按日轮转）",
    )

    log_tab_a, log_tab_b = st.tabs(["系统日志", "解析错误"])
    with log_tab_a:
        if st.button("刷新系统日志", key="btn_log_sys"):
            st.rerun()
        if os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE, "r", encoding="utf-8") as f:
                    last_lines = f.readlines()[-200:]
                    st.code("".join(last_lines), language="text")
            except Exception as e:
                st.error(f"读取日志失败: {e}")
        else:
            st.info("暂无日志文件。")

    with log_tab_b:
        if st.button("刷新解析错误", key="btn_log_parse"):
            st.rerun()
        if os.path.exists(PARSE_ERROR_LOG):
            try:
                with open(PARSE_ERROR_LOG, "r", encoding="utf-8") as f:
                    last_lines = f.readlines()[-200:]
                    st.code("".join(last_lines), language="text")
            except Exception as e:
                st.error(f"读取日志失败: {e}")
        else:
            st.info("暂无解析错误日志。")

# ---- 页脚 ----
st.markdown(
    """
    <div class="footer-note">
      <span>仅供规培学习与文书辅助 · 输出需本人核对后再用于正式病历</span>
      <span>奎享雕刻 JSON · UTF-8 · 模板可在左侧切换</span>
    </div>
    """,
    unsafe_allow_html=True,
)
