# visual_interface.py
"""Streamlit UI for batch processing and multi-patient record splitting.
All user-facing text is in Chinese.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import time
from pathlib import Path

import requests
import streamlit as st

from api_client import ApiError, call_chat_completion
from record_parser import (
    extract_sections as rp_extract_sections,
    get_available_templates as rp_get_available_templates,
    process_text_to_template_json,
    save_failed_case,
    split_patient_records,
    validate_rewritten_text,
    write_split_files,
)

# ---- 页面配置（须在其他 st 调用前） ----
st.set_page_config(
    page_title="门诊病历处理工具",
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


# ---- 主题样式：临床冷静风 ----
CUSTOM_CSS = """
<style>
:root {
  --ink: #1a2e28;
  --paper: #f4f6f4;
  --accent: #2d6a5a;
  --accent-soft: #e8f2ee;
  --muted: #6b7c76;
  --card: #ffffff;
  --line: #dde5e1;
  --warn: #b86e2b;
  --err: #a33b3b;
}
.stApp {
  background: linear-gradient(180deg, #eef3f0 0%, var(--paper) 220px);
  font-family: "PingFang SC", "Microsoft YaHei", "Segoe UI", system-ui, sans-serif;
  color: var(--ink);
}
h1 {
  font-size: 1.65rem !important;
  font-weight: 650 !important;
  letter-spacing: 0.02em;
  color: var(--ink) !important;
  padding-bottom: 0.2rem !important;
  border-bottom: 2px solid var(--accent);
  display: inline-block;
  margin-bottom: 0.6rem !important;
}
h2, h3 { color: var(--ink) !important; font-weight: 600 !important; }
h2 { font-size: 1.15rem !important; }
h3 { font-size: 1.02rem !important; }
section[data-testid="stSidebar"] { background: #1e3330; border-right: none; }
section[data-testid="stSidebar"] .stMarkdown,
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] .stCaption { color: #d5e4df !important; }
section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3 {
  color: #f2faf7 !important;
  border-bottom-color: #3d7a6a !important;
}
section[data-testid="stSidebar"] [data-testid="stFileUploader"] {
  background: rgba(255,255,255,0.04);
  border: 1px dashed #4a7a6e;
  border-radius: 10px;
  padding: 0.4rem;
}
.block-container { padding-top: 1.4rem !important; max-width: 1100px; }
.stButton > button {
  border-radius: 8px !important;
  font-weight: 560 !important;
  border: 1px solid transparent !important;
  transition: background 0.15s ease, box-shadow 0.15s ease;
}
.stButton > button[kind="primary"],
.stButton > button[data-testid="baseButton-primary"] {
  background: var(--accent) !important;
  color: #fff !important;
  border-color: var(--accent) !important;
}
.stButton > button:hover { box-shadow: 0 2px 10px rgba(45, 106, 90, 0.22); }
.stButton > button:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
[data-testid="stFileUploader"] {
  border-radius: 10px;
  border: 1px dashed var(--accent);
  background: var(--accent-soft);
  padding: 0.5rem;
}
[data-baseweb="input"], [data-baseweb="textarea"] { border-radius: 8px !important; }
details {
  border: 1px solid var(--line) !important;
  border-radius: 10px !important;
  background: var(--card) !important;
}
summary { font-weight: 560 !important; }
[data-testid="stAlert"] { border-radius: 8px !important; border-left-width: 4px !important; }
[data-testid="stDataFrame"] {
  border: 1px solid var(--line);
  border-radius: 10px;
  overflow: hidden;
}
[data-baseweb="tab-list"] { gap: 4px; border-bottom: 1px solid var(--line) !important; }
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
.pipeline {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  flex-wrap: wrap;
  margin: 0.4rem 0 1rem 0;
  font-size: 0.86rem;
  color: var(--muted);
}
.pipeline .step {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 0.28rem 0.75rem;
  font-weight: 500;
  color: var(--ink);
}
.pipeline .step.on {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}
.pipeline .arrow { color: var(--accent); opacity: 0.7; font-size: 0.8rem; }
.side-badge {
  display: inline-block;
  background: rgba(255,255,255,0.08);
  border: 1px solid #3d6b60;
  color: #cfe8df;
  border-radius: 6px;
  padding: 0.2rem 0.55rem;
  font-size: 0.78rem;
  margin: 0.15rem 0.15rem 0.15rem 0;
}
.side-badge.ok { border-color: #3d8f78; color: #b8e6d6; }
.side-badge.warn { border-color: #b86e2b; color: #f0c9a0; }
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
        process_text_to_template_json(raw_text, template_path, out_path)
        log_info(f"成功提取 JSON (模板格式): {out_path}")
        return out_path
    except Exception as e:
        error_msg = f"处理文件 {raw_path.name} 失败: {str(e)}"
        log_error(error_msg)
        raise Exception(error_msg) from e


def process_ai_file(ai_raw_file_obj, config, ai_output_dir, template_path, retry_feedback: str = None):
    """处理单个 AI 文件：改写 → 校验 → 切割 → 抽 JSON。"""
    file_name = "unknown"
    raw_text = ""
    rewritten = None
    try:
        if isinstance(ai_raw_file_obj, dict):
            file_name = ai_raw_file_obj["name"]
            raw_text = ai_raw_file_obj["content"]
        else:
            file_name = ai_raw_file_obj.name
            raw_text = ai_raw_file_obj.getvalue().decode("utf-8")

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

        rewritten = call_chat_completion(
            config["api_url"],
            api_key=config.get("api_key", ""),
            model=config.get("model", "gpt-3.5-turbo"),
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=config.get("temperature", 0.7),
            max_tokens=config.get("max_tokens", 4000),
            timeout=120,
            max_retries=3,
        )

        log_info(f"AI 改写完成: {file_name}")

        if not rewritten or not rewritten.strip():
            raise Exception("AI 返回内容为空")

        validation = validate_rewritten_text(rewritten, require_patient_header=True)
        if not validation.ok:
            raise Exception(f"改写结果校验失败: {'；'.join(validation.errors)}")
        if validation.warnings:
            log_info(f"校验警告 {file_name}: {'；'.join(validation.warnings)}")

        rewrite_dir = ai_output_dir / "AI_Rewrite"
        json_dir = ai_output_dir / "JSON_Transcript"
        rewrite_dir.mkdir(parents=True, exist_ok=True)
        json_dir.mkdir(parents=True, exist_ok=True)

        temp_full_path = ai_output_dir / f"{Path(file_name).stem}_temp_full.txt"
        temp_full_path.write_text(rewritten, encoding="utf-8")

        file_prefix = Path(file_name).stem
        raw_split_text = temp_full_path.read_text(encoding="utf-8")
        records = split_patient_records(raw_split_text, delimiter=None)
        if len(records) == 1 and records[0].name == "未知":
            from record_parser import parse_name_age_from_name_line
            hint_name, _ = parse_name_age_from_name_line(file_prefix)
            if hint_name and hint_name != "未知":
                records[0].name = hint_name
        written = write_split_files(records, rewrite_dir, file_prefix=file_prefix)
        log_info(f"文件 {file_name} 切割为 {len(written)} 个子文件 (保存至 {rewrite_dir})")

        json_count = 0
        json_errors = []
        for txt_file in written:
            try:
                process_file(txt_file, template_path, json_dir)
                json_count += 1
            except Exception as e:
                log_error(f"提取 JSON 失败 {txt_file.name}: {e}")
                json_errors.append(f"{txt_file.name}: {e}")

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
                    old_profile = {
                        "api_url": data.get("api_url", default_profile["api_url"]),
                        "api_key": data.get("api_key", default_profile["api_key"]),
                        "model": data.get("model", default_profile["model"]),
                        "temperature": data.get("temperature", default_profile["temperature"]),
                        "max_tokens": data.get("max_tokens", default_profile["max_tokens"]),
                    }
                    return {"current_profile": "默认配置", "profiles": {"默认配置": old_profile}}
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
# 侧边栏：模板 / API / 提示词
# =====================================================================
with st.sidebar:
    st.markdown("## 配置中心")
    st.caption("模板、接口与提示词都在这里管理")

    available_templates = get_available_templates()
    if not available_templates:
        st.error("template/ 下未找到 JSON 模板")
        template_path = None
    else:
        selected_template = st.selectbox(
            "JSON 模板",
            options=available_templates,
            index=available_templates.index(st.session_state.selected_template)
            if st.session_state.selected_template in available_templates
            else 0,
            help="从 template/ 目录选择输出模板",
        )
        st.session_state.selected_template = selected_template
        template_path = Path("template") / selected_template
        st.markdown(
            f'<span class="side-badge ok">模板就绪 · {selected_template}</span>',
            unsafe_allow_html=True,
        )

    st.divider()

    st.markdown("### API 接口")
    col_p1, col_p2 = st.columns([2, 1])
    with col_p1:
        selected_profile = st.selectbox(
            "配置方案",
            options=list(profiles.keys()),
            index=list(profiles.keys()).index(current_profile_name)
            if current_profile_name in profiles
            else 0,
        )
    with col_p2:
        new_profile_name = st.text_input("新建", placeholder="名称", label_visibility="collapsed")

    c_a, c_b = st.columns(2)
    with c_a:
        if st.button("➕ 新建配置", use_container_width=True):
            if new_profile_name and new_profile_name not in profiles:
                profiles[new_profile_name] = profiles[selected_profile].copy()
                full_config["profiles"] = profiles
                full_config["current_profile"] = new_profile_name
                save_config(full_config)
                st.success(f"已创建 {new_profile_name}")
                st.rerun()
            elif new_profile_name in profiles:
                st.error("名称已存在")
            else:
                st.error("请输入名称")
    with c_b:
        if st.button("🗑️ 删除", use_container_width=True):
            if len(profiles) <= 1:
                st.error("至少保留一个")
            else:
                del profiles[selected_profile]
                full_config["profiles"] = profiles
                full_config["current_profile"] = list(profiles.keys())[0]
                save_config(full_config)
                st.success(f"已删除 {selected_profile}")
                st.rerun()

    current_config = profiles[selected_profile]
    new_api_url = st.text_input("API URL", value=current_config.get("api_url", ""),
                                help="OpenAI 兼容 /v1/chat/completions")
    new_api_key = st.text_input("API Key", value=current_config.get("api_key", ""), type="password")
    new_model = st.text_input("模型", value=current_config.get("model", "gpt-3.5-turbo"))
    new_temperature = st.slider(
        "Temperature", 0.0, 2.0, float(current_config.get("temperature", 0.7)), 0.1
    )
    new_max_tokens = st.number_input(
        "Max Tokens", 100, 32000, int(current_config.get("max_tokens", 4000)), 100
    )

    if st.button("💾 保存 API 配置", use_container_width=True, type="primary"):
        profiles[selected_profile] = {
            "api_url": new_api_url,
            "api_key": new_api_key,
            "model": new_model,
            "temperature": new_temperature,
            "max_tokens": new_max_tokens,
        }
        full_config["profiles"] = profiles
        full_config["current_profile"] = selected_profile
        save_config(full_config)
        st.success(f"已保存「{selected_profile}」")

    if st.button("🔌 测试连接", use_container_width=True):
        if not new_api_url:
            st.error("请先填写 API URL")
        else:
            with st.spinner("测试中..."):
                try:
                    test_headers = {"Content-Type": "application/json"}
                    if new_api_key:
                        test_headers["Authorization"] = f"Bearer {new_api_key}"
                    test_payload = {
                        "model": new_model,
                        "messages": [{"role": "user", "content": "Hi"}],
                        "max_tokens": 5,
                    }
                    resp = requests.post(
                        new_api_url, headers=test_headers, json=test_payload, timeout=10
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

    st.markdown("### 提示词")
    current_prompt_config = load_prompt_config()
    st.caption(f"当前 v{current_prompt_config.get('version', 1)}")

    with st.expander("编辑提示词", expanded=False):
        new_system_prompt = st.text_area(
            "系统提示词",
            value=current_prompt_config.get("system_prompt", ""),
            height=70,
            label_visibility="collapsed",
        )
        new_user_prompt_template = st.text_area(
            "用户提示词模板（必须含 {raw_text}）",
            value=current_prompt_config.get("user_prompt_template", ""),
            height=220,
            label_visibility="collapsed",
        )
        placeholder_valid = "{raw_text}" in new_user_prompt_template
        if not placeholder_valid:
            st.error("缺少 `{raw_text}` 占位符")

        c1, c2 = st.columns(2)
        with c1:
            if st.button("保存", disabled=not placeholder_valid, use_container_width=True):
                new_config = {
                    "version": current_prompt_config.get("version", 1),
                    "system_prompt": new_system_prompt,
                    "user_prompt_template": new_user_prompt_template,
                }
                if save_prompt_config(new_config):
                    st.success("已保存")
                    st.rerun()
                else:
                    st.error("保存失败")
        with c2:
            if st.button("重置默认", use_container_width=True):
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
                "历史版本", options=list(range(len(versions))), format_func=lambda i: labels[i]
            )
            if st.button("↩️ 恢复选中版本", use_container_width=True):
                try:
                    restored = load_prompt_version(versions[selected_ver])
                    if save_prompt_config(restored):
                        st.success(f"已恢复 {versions[selected_ver].name}")
                        st.rerun()
                except Exception as e:
                    st.error(f"恢复失败: {e}")

    config = profiles[selected_profile]
    st.divider()
    st.caption("日志按日轮转 · parse_error.log 单独记录解析问题")


# =====================================================================
# 主区
# =====================================================================
st.title("门诊病历处理工具")
st.markdown("将非结构化门诊病历批量转为标准 JSON。支持多患者切割、AI 改写与模板填充。")


def render_pipeline(active: int = 1):
    steps = ["① 切割", "② AI 改写", "③ 校验", "④ JSON"]
    parts = []
    for i, s in enumerate(steps, 1):
        cls = "step on" if i == active else "step"
        parts.append(f'<span class="{cls}">{s}</span>')
        if i < len(steps):
            parts.append('<span class="arrow">→</span>')
    st.markdown(f'<div class="pipeline">{"".join(parts)}</div>', unsafe_allow_html=True)


tab_split, tab_ai, tab_basic, tab_log = st.tabs(
    ["批量切割", "AI 改写并提取", "基础提取", "运行日志"]
)

# ---------------------------------------------------------------------
with tab_split:
    st.markdown("### 批量病历切割")
    st.caption("按「姓名：」或自定义标记，把多患者大文件拆成单人文件。")
    render_pipeline(active=1)

    multi_txt = st.file_uploader("上传包含多个患者的 txt 文件", type=["txt"], key="split_uploader")
    c_out, c_del = st.columns([2, 1])
    with c_out:
        split_output_input = st.text_input("保存目录", value="split_output", key="split_out")
    with c_del:
        delimiter_input = st.text_input(
            "自定义切割标记", value="", placeholder="留空则按姓名", key="split_delim"
        )

    split_output_dir = Path(split_output_input)
    split_output_dir.mkdir(parents=True, exist_ok=True)

    if st.button("执行切割", type="primary", key="btn_split"):
        if not multi_txt:
            st.error("请先上传 txt 文件。")
        else:
            try:
                temp_input = split_output_dir / multi_txt.name
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
    st.markdown("### AI 改写并提取")
    st.caption("格式混乱的病历 → AI 改写为标准五段 → 校验 → 切割 → 填模板 JSON。")
    render_pipeline(active=2)

    ai_raw_files = st.file_uploader(
        "上传原始病历 txt（支持多选）",
        type=["txt"],
        accept_multiple_files=True,
        key="ai_uploader",
    )
    ai_output_input = st.text_input("输出根目录", value="output", key="ai_out")
    ai_output_dir = Path(ai_output_input)
    ai_output_dir.mkdir(parents=True, exist_ok=True)

    if template_path is None:
        st.warning("请先在左侧选择有效模板。")

    if st.button("执行 AI 改写并提取", type="primary", key="btn_ai"):
        if not ai_raw_files:
            st.error("请先上传原始病历 txt 文件。")
        elif not config.get("api_url"):
            st.error("请先在侧边栏配置 API URL。")
        elif template_path is None:
            st.error("请先在侧边栏选择一个有效的 JSON 模板。")
        else:
            total_files = len(ai_raw_files)
            success_count = 0
            progress_bar = st.progress(0)
            status_text = st.empty()

            for i, ai_raw_file in enumerate(ai_raw_files):
                status_text.text(f"正在处理第 {i+1}/{total_files} 个文件: {ai_raw_file.name} ...")
                success, msg, _ = process_ai_file(ai_raw_file, config, ai_output_dir, template_path)

                if not success:
                    retry_feedback = msg
                    status_text.text(f"首次失败，自动重试: {ai_raw_file.name} ...")
                    time.sleep(2)
                    success, msg, _ = process_ai_file(
                        ai_raw_file, config, ai_output_dir, template_path, retry_feedback=retry_feedback
                    )

                if success:
                    st.success(msg)
                    success_count += 1
                else:
                    st.error(msg)
                    st.session_state.failed_files.append(
                        {
                            "name": ai_raw_file.name,
                            "content": ai_raw_file.getvalue().decode("utf-8"),
                            "last_error": msg,
                        }
                    )

                progress_bar.progress((i + 1) / total_files)
                if i < total_files - 1:
                    time.sleep(2)

            status_text.text(f"所有文件处理完成！成功 {success_count}/{total_files} 个。")
            log_info(f"批量处理完成: 成功 {success_count}/{total_files}")

    if st.session_state.failed_files:
        st.markdown("---")
        st.warning(f"有 {len(st.session_state.failed_files)} 个文件处理失败，可一键重试。")
        with st.expander("失败文件列表"):
            for f in st.session_state.failed_files:
                err = f.get("last_error", "")
                st.text(f"- {f['name']}" + (f"  |  {err}" if err else ""))

        if st.button("🔄 一键重试所有失败文件", key="btn_retry"):
            retry_files = st.session_state.failed_files.copy()
            st.session_state.failed_files = []
            total_retry = len(retry_files)
            retry_success = 0
            retry_progress = st.progress(0)
            retry_status = st.empty()

            for i, f_obj in enumerate(retry_files):
                retry_status.text(f"正在重试: {f_obj['name']} ...")
                success, msg, _ = process_ai_file(
                    f_obj, config, ai_output_dir, template_path, retry_feedback=f_obj.get("last_error")
                )
                if success:
                    st.success(f"重试成功: {msg}")
                    retry_success += 1
                else:
                    st.error(f"重试失败: {msg}")
                    f_obj["last_error"] = msg
                    st.session_state.failed_files.append(f_obj)

                retry_progress.progress((i + 1) / total_retry)
                if i < total_retry - 1:
                    time.sleep(2)

            retry_status.text(f"重试完成！成功 {retry_success}/{total_retry} 个。")

# ---------------------------------------------------------------------
with tab_basic:
    st.markdown("### 基础 JSON 提取（无需 AI）")
    st.caption(
        "适用于已标准格式的文本，必须包含「主诉 / 病因病机分析 / 中医诊断及辩证 / 治法 / 处方」等章节标题。"
    )
    render_pipeline(active=4)

    basic_files = st.file_uploader(
        "上传标准格式 txt（支持多选）",
        type=["txt"],
        accept_multiple_files=True,
        key="basic_uploader",
    )
    basic_output_input = st.text_input("JSON 保存目录", value="output", key="basic_output")
    basic_output_dir = Path(basic_output_input)
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
                    content = b_file.getvalue().decode("utf-8")
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

                    temp_path = basic_output_dir / b_file.name
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
                            content=b_file.getvalue().decode("utf-8", errors="replace"),
                            errors=[str(e)],
                        )
                    except Exception:
                        pass

            if success_count > 0:
                st.success(f"已成功提取 {success_count} 个文件的 JSON！")

# ---------------------------------------------------------------------
with tab_log:
    st.markdown("### 系统运行日志")
    st.caption(f"主日志 `{LOG_FILE}` · 解析错误 `{PARSE_ERROR_LOG}`（按日轮转）")

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
