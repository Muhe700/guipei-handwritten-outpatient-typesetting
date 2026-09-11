# extract_medical_record.py
"""CLI：从门诊病历文本抽取五段并写 JSON。

默认输出逻辑章节；传入 --template 时按模板填充奎享 JSON。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from record_parser import (
    SECTION_ORDER,
    extract_sections,
    process_text_to_template_json,
    validate_rewritten_text,
)


def main():
    parser = argparse.ArgumentParser(description="Extract sections from a clinic medical record.")
    parser.add_argument("--input", required=True, help="Path to raw medical record text file")
    parser.add_argument("--template", default=None, help="Path to template JSON (optional, fills 奎享格式)")
    parser.add_argument("--output", required=True, help="Path to write the resulting JSON")
    parser.add_argument("--strict", action="store_true", help="五段不全时以非 0 退出")
    args = parser.parse_args()

    raw_text = Path(args.input).read_text(encoding="utf-8")
    validation = validate_rewritten_text(raw_text, require_patient_header=False)
    if args.strict and not validation.ok:
        print("校验失败: " + "；".join(validation.errors))
        raise SystemExit(2)

    output_path = Path(args.output)
    if args.template:
        template_path = Path(args.template)
        if not template_path.exists():
            print(f"模板不存在: {template_path}")
            raise SystemExit(1)
        process_text_to_template_json(raw_text, template_path, output_path)
    else:
        extracted = extract_sections(raw_text)
        output_json = {sec: extracted[sec] for sec in SECTION_ORDER}
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(output_json, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(f"Extraction completed. Output written to {output_path}")
    if validation.warnings:
        print("警告: " + "；".join(validation.warnings))


if __name__ == "__main__":
    main()
