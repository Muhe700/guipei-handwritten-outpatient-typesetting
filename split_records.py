# split_records.py
"""自动切割患者病历文本文件。

委托 record_parser 实现，保留旧接口以兼容 visual_interface / CLI。
"""

import argparse
import logging
from pathlib import Path
from typing import Optional

from record_parser import split_patient_records, write_split_files

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )


def split_records(
    input_path: Path,
    output_dir: Path,
    delimiter: Optional[str] = None,
    file_prefix: Optional[str] = None,
) -> int:
    """读取 input_path，按姓名行或自定义标记切割，返回生成文件数。"""
    text = input_path.read_text(encoding="utf-8")
    records = split_patient_records(text, delimiter=delimiter)
    written = write_split_files(records, output_dir, file_prefix=file_prefix)
    return len(written)


def main():
    parser = argparse.ArgumentParser(description="切割包含多个患者的病历 txt 为单独文件")
    parser.add_argument("--input", required=True, help="原始 txt 文件路径")
    parser.add_argument("--output", required=True, help="切割后文件保存目录")
    parser.add_argument("--delimiter", default=None, help="自定义分割标记（完整匹配）")
    args = parser.parse_args()
    count = split_records(Path(args.input), Path(args.output), args.delimiter)
    print(f"已生成 {count} 个患者文件，保存至 {args.output}")


if __name__ == "__main__":
    main()
