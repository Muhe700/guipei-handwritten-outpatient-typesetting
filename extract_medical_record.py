# extract_medical_record.py
"""CLI：从门诊病历文本抽取五段并写 JSON。

逻辑统一由 record_parser 提供，本文件仅保留命令行入口。
"""

import argparse
import json
from pathlib import Path

from record_parser import (
    DEFAULT_SECTION_MAP,
    SECTION_ORDER,
    extract_sections,
)


def load_template(template_path: str) -> dict:
    """兼容旧接口：返回固定的五段逻辑名映射。"""
    _ = Path(template_path)  # 路径仅作兼容保留
    return dict(DEFAULT_SECTION_MAP)


def main():
    parser = argparse.ArgumentParser(description="Extract sections from a clinic medical record.")
    parser.add_argument("--input", required=True, help="Path to raw medical record text file")
    parser.add_argument("--template", required=True, help="Path to template JSON file")
    parser.add_argument("--output", required=True, help="Path to write the resulting JSON")
    args = parser.parse_args()

    raw_text = Path(args.input).read_text(encoding="utf-8")
    extracted = extract_sections(raw_text)

    output_json = {sec: extracted[sec] for sec in SECTION_ORDER}
    Path(args.output).write_text(
        json.dumps(output_json, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Extraction completed. Output written to {args.output}")


if __name__ == "__main__":
    main()
