# record_parser.py
"""门诊病历统一解析、校验与切割模块。

集中处理：
- 章节标题兼容匹配（中医诊断及辩证/辨证 等变体）
- AI 改写结果的结构化校验
- 姓名/年龄/诊断元数据解析与文件名清洗
- 多患者记录切割
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 章节定义
# ---------------------------------------------------------------------------

# 输出模板中固定的五段逻辑名
SECTION_ORDER: List[str] = [
    "四诊",
    "病因病机分析",
    "中医诊断及辩证",
    "治法",
    "处方",
]

# 各段可接受的标题（按优先级；匹配时允许行首空白与中英文冒号）
SECTION_ALIASES: Dict[str, List[str]] = {
    "四诊": ["主诉", "现病史", "既往史", "过敏史", "望闻问切", "四诊"],
    "病因病机分析": ["病因病机分析", "病因病机", "病机分析", "辨证分析"],
    "中医诊断及辩证": [
        "中医诊断及辩证",
        "中医诊断及辨证",
        "中医诊断与辩证",
        "中医诊断与辨证",
        "中医诊断",
        "辨证",
    ],
    "治法": ["治法", "治疗原则", "治则"],
    "处方": ["处方", "方药", "针灸处方"],
}

# 默认模板元素映射（门诊小病例模板.json）
DEFAULT_SECTION_MAP: Dict[str, str] = {
    "四诊": "元素14",
    "病因病机分析": "元素9",
    "中医诊断及辩证": "元素11",
    "治法": "元素12",
    "处方": "元素13",
}


# ---------------------------------------------------------------------------
# 校验结果
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    ok: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    has_patient_header: bool = False
    found_sections: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "has_patient_header": self.has_patient_header,
            "found_sections": {k: bool(v) for k, v in self.found_sections.items()},
        }


# ---------------------------------------------------------------------------
# 基础文本工具
# ---------------------------------------------------------------------------

def _normalize_heading_line(line: str) -> str:
    """去掉行首尾空白、全角空格，统一冒号，便于标题匹配。"""
    s = line.strip().replace("　", " ")
    s = s.replace("：", ":")
    return s


def _build_heading_pattern(keywords: Sequence[str]) -> re.Pattern:
    """构造行首标题正则，允许空白和中英文冒号。"""
    alts = "|".join(re.escape(k) for k in keywords)
    # 允许：可选空白 + 标题 + 可选冒号/顿号/空格
    return re.compile(
        r"^[ \t]*(?:" + alts + r")[ \t]*[:：、.．]?[ \t]*",
        re.MULTILINE,
    )


def _build_end_pattern(keywords: Sequence[str]) -> re.Pattern:
    alts = "|".join(re.escape(k) for k in keywords)
    return re.compile(
        r"^[ \t]*(?:" + alts + r")[ \t]*[:：、.．]",
        re.MULTILINE,
    )


# ---------------------------------------------------------------------------
# 章节抽取
# ---------------------------------------------------------------------------

def find_section(
    text: str,
    start_keywords: Sequence[str],
    end_keywords: Optional[Sequence[str]] = None,
) -> Tuple[str, int, int]:
    """返回 (content, start_idx, end_idx)。找不到时 content 为空、索引为 -1。"""
    start_pat = _build_heading_pattern(start_keywords)
    start_match = start_pat.search(text)
    if not start_match:
        return "", -1, -1

    start_idx = start_match.end()
    if end_keywords:
        end_pat = _build_end_pattern(end_keywords)
        end_match = end_pat.search(text, start_idx)
        end_idx = end_match.start() if end_match else len(text)
    else:
        end_idx = len(text)

    content = text[start_idx:end_idx].strip()
    # 去掉尾部多余的分隔符
    content = content.rstrip("。．.；; \t")
    return content, start_match.start(), end_idx


def extract_sections(text: str, keys: Optional[dict] = None) -> Dict[str, dict]:
    """按 SECTION_ORDER 抽取五段，兼容标题别名。

    返回 {section: {"content": str, "position": {"start": int, "end": int}}}
    """
    _ = keys  # 兼容旧签名
    result: Dict[str, dict] = {}
    for i, sec in enumerate(SECTION_ORDER):
        start_keys = SECTION_ALIASES[sec]
        end_keys: Optional[List[str]] = None
        # 终点：后续任一段的第一个别名集合（取所有后续别名，避免漏停）
        if i + 1 < len(SECTION_ORDER):
            end_keys = []
            for later in SECTION_ORDER[i + 1 :]:
                end_keys.extend(SECTION_ALIASES[later])
        content, start, end = find_section(text, start_keys, end_keys)
        result[sec] = {"content": content, "position": {"start": start, "end": end}}
    return result


def sections_complete(extracted: Dict[str, dict]) -> Tuple[bool, List[str]]:
    missing = [
        sec for sec in SECTION_ORDER if not extracted.get(sec, {}).get("content")
    ]
    return (len(missing) == 0), missing


# ---------------------------------------------------------------------------
# 结构化校验
# ---------------------------------------------------------------------------

_NAME_HEADER_RE = re.compile(r"^[ \t]*姓名\s*[:：]", re.MULTILINE)


def validate_rewritten_text(
    text: str,
    *,
    require_patient_header: bool = True,
    min_four_zhen_len: int = 10,
    min_rx_len: int = 10,
) -> ValidationResult:
    """校验 AI 改写后的标准病历文本。

    - require_patient_header: 单文件可设为 False；批量切割场景建议 True
    """
    result = ValidationResult(ok=True)
    if not text or not text.strip():
        result.ok = False
        result.errors.append("文本为空")
        return result

    result.has_patient_header = bool(_NAME_HEADER_RE.search(text))
    if require_patient_header and not result.has_patient_header:
        result.warnings.append("缺少「姓名：」行，多患者切割可能失败")

    extracted = extract_sections(text)
    result.found_sections = {
        sec: extracted[sec]["content"] for sec in SECTION_ORDER
    }
    ok, missing = sections_complete(extracted)
    if not ok:
        result.ok = False
        result.errors.append("缺少必需章节: " + "、".join(missing))

    four = extracted.get("四诊", {}).get("content", "")
    if four and len(four) < min_four_zhen_len:
        result.warnings.append(f"四诊内容过短 ({len(four)} 字符)")

    rx = extracted.get("处方", {}).get("content", "")
    if rx and len(rx) < min_rx_len:
        result.warnings.append(f"处方内容过短 ({len(rx)} 字符)")

    # 前后矛盾粗检：治法有内容但诊断为空
    if extracted.get("治法", {}).get("content") and not extracted.get(
        "中医诊断及辩证", {}
    ).get("content"):
        result.warnings.append("有治法但缺少中医诊断，可能前后矛盾")

    return result


# ---------------------------------------------------------------------------
# 元数据：姓名 / 年龄 / 诊断
# ---------------------------------------------------------------------------

def _sanitize_filename_part(s: str) -> str:
    s = re.sub(r"[\\/:*?\"<>|\[\]【】]", "", s or "")
    s = re.sub(r"\s+", "_", s)
    return s.strip("_")


def parse_age_from_text(text: str) -> str:
    """从任意文本提取年龄，优先「年龄：」字段，其次「NN岁」。"""
    if not text:
        return "未知年龄"
    m = re.search(r"年龄\s*[:：]\s*([0-9]{1,3})\s*岁?", text)
    if m:
        return f"{m.group(1)}岁"
    m = re.search(r"([0-9]{1,3})\s*岁", text)
    if m:
        return f"{m.group(1)}岁"
    return "未知年龄"


def parse_name_age_from_name_line(raw_name: str) -> Tuple[str, str]:
    """解析姓名行内容，如「张三（女，44岁）」「李四, 32岁」。"""
    raw = (raw_name or "").strip()
    age = "未知年龄"
    age_m = re.search(r"([0-9]{1,3})\s*岁", raw)
    if age_m:
        age = f"{age_m.group(1)}岁"

    # 去掉括号备注、逗号后的性别年龄等（全/半角括号与逗号）
    name = re.split(r"[（(［\[，,、]", raw)[0]
    name = name.strip()
    # 去掉可能残留的标签前缀（如「患者张三」「姓名：张三」），但保留真正含「患者」的名字
    m = re.match(r"^(?:患者|病人|姓名)\s*[:：]?\s*(.+)$", name)
    if m and m.group(1).strip():
        name = m.group(1).strip()
    name = name.strip()
    if not name:
        name = "未知"
    return name, age


def parse_disease_from_content(content: str) -> str:
    """从病历正文提取诊断，优先中医，其次西医/门诊诊断。"""
    if not content:
        return "未知病名"

    patterns = [
        # 辨/辩 兼容；「中医诊断及辩证」整体优先
        r"中医诊断(?:及[辨辩]证)?\s*[:：]\s*([^\n\r]+)",
        r"(?:中医|西医)?诊断\s*[:：]\s*([^\n\r]+)",
        r"门诊诊断\s*[:：]\s*([^\n\r]+)",
    ]
    for pat in patterns:
        m = re.search(pat, content)
        if m:
            disease = m.group(1).strip()
            # 去掉「西医：」「中医：」前缀
            disease = re.sub(r"^(西医|中医)\s*[:：]\s*", "", disease)
            # 取第一诊断
            disease = re.split(r"[，,；;。.\n]", disease)[0]
            disease = disease.strip(" []【】")
            if disease:
                return disease
    return "未知病名"


@dataclass
class PatientRecord:
    name: str
    age: str
    disease: str
    content: str

    @property
    def filename_stem(self) -> str:
        safe_name = _sanitize_filename_part(self.name) or "未知"
        safe_age = _sanitize_filename_part(self.age) or "未知年龄"
        safe_disease = _sanitize_filename_part(self.disease) or "未知病名"
        return f"{safe_name}-{safe_age}-{safe_disease}"


def build_patient_record(
    content: str,
    name_hint: Optional[str] = None,
    age_hint: Optional[str] = None,
) -> PatientRecord:
    name = name_hint or "未知"
    age = age_hint or "未知年龄"
    if age == "未知年龄":
        age = parse_age_from_text(content)
    disease = parse_disease_from_content(content)
    # 若姓名仍未知，尝试从正文「姓名：」再抽
    if name == "未知":
        m = re.search(r"姓名\s*[:：]\s*([^\n\r（(]+)", content)
        if m:
            name, age2 = parse_name_age_from_name_line(m.group(1))
            if age == "未知年龄":
                age = age2
    return PatientRecord(name=name, age=age, disease=disease, content=content.strip())


# ---------------------------------------------------------------------------
# 切割
# ---------------------------------------------------------------------------

def split_patient_records(
    text: str,
    delimiter: Optional[str] = None,
) -> List[PatientRecord]:
    """将多患者大文本切割为 PatientRecord 列表。"""
    records: List[PatientRecord] = []

    if delimiter:
        parts = text.split(delimiter)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            name_match = re.search(r"姓名\s*[:：]\s*([^\n\r]+)", part)
            if name_match:
                name, age = parse_name_age_from_name_line(name_match.group(1))
            else:
                name, age = "未知", "未知年龄"
            records.append(build_patient_record(part, name, age))
        return records

    # 按姓名行切割
    pattern = re.compile(r"(姓名\s*[:：]\s*[^\n\r]+)")
    parts = pattern.split(text)
    current = ""
    name = None
    age = "未知年龄"

    for i, part in enumerate(parts):
        if i % 2 == 1:  # 姓名行
            if current.strip() and name:
                records.append(build_patient_record(current, name, age))
            raw_name = re.split(r"[:：]", part, maxsplit=1)[-1].strip()
            name, age = parse_name_age_from_name_line(raw_name)
            logger.debug("解析姓名行: raw=%r -> name=%r age=%r", raw_name, name, age)
            current = ""
        else:
            current += part

    if current.strip() and name:
        records.append(build_patient_record(current, name, age))

    # 兜底：整段没有姓名行，但内容非空 → 单患者
    if not records and text.strip():
        records.append(build_patient_record(text, "未知", "未知年龄"))

    return records


def write_split_files(
    records: List[PatientRecord],
    output_dir: Path,
    file_prefix: Optional[str] = None,
    start_index: int = 1,
) -> List[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    idx = start_index
    for rec in records:
        if not rec.content.strip():
            logger.warning("跳过空记录: %s", rec.name)
            continue
        stem = rec.filename_stem
        filename = f"{file_prefix}-{idx}-{stem}.txt" if file_prefix else f"{idx}-{stem}.txt"
        out_path = output_dir / filename
        out_path.write_text(rec.content, encoding="utf-8")
        written.append(out_path)
        logger.info("生成文件: %s", filename)
        idx += 1
    return written


# ---------------------------------------------------------------------------
# 模板映射
# ---------------------------------------------------------------------------

def load_section_map(template_path: Optional[Path] = None) -> Dict[str, str]:
    """加载章节→模板元素映射。

    优先读 <template>.map.json；否则按 DEFAULT_SECTION_MAP。
    """
    if template_path:
        map_path = Path(template_path).with_suffix(".map.json")
        if map_path.exists():
            try:
                data = json.loads(map_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and all(k in data for k in SECTION_ORDER):
                    return {k: str(data[k]) for k in SECTION_ORDER}
                logger.warning("映射文件格式不正确: %s，使用默认映射", map_path)
            except Exception as e:
                logger.error("读取映射文件失败 %s: %s", map_path, e)
    return dict(DEFAULT_SECTION_MAP)


def get_available_templates(template_dir: Path | str = "template") -> List[str]:
    """扫描模板目录，返回正式模板文件名（排除 .map.json）。"""
    template_dir = Path(template_dir)
    if not template_dir.exists():
        return []
    templates = [
        t.name
        for t in template_dir.glob("*.json")
        if not t.name.endswith(".map.json")
    ]
    return sorted(templates)


def fill_template_with_sections(
    template_data: dict,
    extracted: Dict[str, dict],
    section_map: Optional[Dict[str, str]] = None,
) -> dict:
    """把抽取结果写入模板 items 的 text 字段。"""
    section_map = section_map or DEFAULT_SECTION_MAP
    if "items" not in template_data:
        return template_data

    for item in template_data["items"]:
        name = item.get("name")
        for section, item_name in section_map.items():
            if name == item_name:
                content = extracted.get(section, {}).get("content", "")
                if section == "四诊" and content:
                    if not content.startswith("主诉"):
                        content = "主诉：" + content
                    content = content.replace("\n", "").replace("\r", "")
                item["text"] = content
    return template_data


def process_text_to_template_json(
    raw_text: str,
    template_path: Path,
    output_path: Path,
) -> Path:
    """单文件：抽取五段 → 填模板 → 写 JSON。"""
    with open(template_path, "r", encoding="utf-8") as f:
        template_data = json.load(f)
    extracted = extract_sections(raw_text)
    section_map = load_section_map(template_path)
    filled = fill_template_with_sections(template_data, extracted, section_map)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(filled, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return output_path


# ---------------------------------------------------------------------------
# 失败样本
# ---------------------------------------------------------------------------

def save_failed_case(
    output_root: Path,
    *,
    stage: str,
    source_name: str,
    content: str,
    errors: Sequence[str],
    warnings: Sequence[str] = (),
    extra: Optional[dict] = None,
) -> Path:
    """把失败样本落盘，便于回放调试。

    目录结构: <output_root>/failed_cases/<stage>/<timestamp>_<name>/
      - original.txt
      - meta.json
    """
    import time

    safe_name = _sanitize_filename_part(Path(source_name).stem) or "unnamed"
    ts = time.strftime("%Y%m%d_%H%M%S")
    case_dir = output_root / "failed_cases" / stage / f"{ts}_{safe_name}"
    case_dir.mkdir(parents=True, exist_ok=True)

    (case_dir / "original.txt").write_text(content or "", encoding="utf-8")
    meta = {
        "stage": stage,
        "source_name": source_name,
        "errors": list(errors),
        "warnings": list(warnings),
        "extra": extra or {},
        "timestamp": ts,
    }
    (case_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.error("失败样本已保存: %s errors=%s", case_dir, list(errors))
    return case_dir
