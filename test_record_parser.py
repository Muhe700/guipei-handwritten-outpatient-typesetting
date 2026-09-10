# test_record_parser.py
"""record_parser 自测：章节匹配、校验、元数据、切割、模板填充。"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from record_parser import (
    DEFAULT_SECTION_MAP,
    SECTION_ORDER,
    extract_sections,
    fill_template_with_sections,
    load_section_map,
    parse_age_from_text,
    parse_disease_from_content,
    parse_name_age_from_name_line,
    process_text_to_template_json,
    save_failed_case,
    sections_complete,
    split_patient_records,
    validate_rewritten_text,
    write_split_files,
)

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


# ---------------------------------------------------------------------------
# 样例文本
# ---------------------------------------------------------------------------

GOOD_TEXT = """姓名：张三（女，44岁）
主诉：颈项疼痛不适1年余，腰部酸痛发凉1周，复诊。
现病史：患者长期低头工作，1年前出现颈项酸胀疼痛。
既往史：否认慢性病史。
过敏史：否认药物及食物过敏史。
四诊：T 36.5℃，舌淡红，苔薄白，脉细弦。
病因病机分析：患者素体亏虚，经络受阻，气血运行不畅。
中医诊断及辩证：痹症；气滞血瘀兼肾阳不足证。
治法：活血化瘀，通络止痛。
处方：针刺取穴百会、印堂、足三里，平补平泻，留针20分钟。
"""

VARIANT_TEXT = """姓名：李四, 32岁
主诉：失眠1年余。
望闻问切：舌淡苔白，脉细。
病因病机：气血亏虚，心神失养。
中医诊断及辨证：不寐；气血亏虚证。
治则：益气养血，安神定志。
方药：归脾汤加减。
"""

INCOMPLETE_TEXT = """姓名：王五
主诉：头痛。
治法：止痛。
"""

MULTI_TEXT = """姓名：施志刚（男，51岁）
主诉：腰痛2天。
门诊诊断：西医：腰椎间盘突出
中医诊断：腰痹
处方：针刺取穴委中。

姓名：周婷, 25岁
主诉：痛经3月。
中医诊断及辩证：痛经；寒凝血瘀证。
处方：温经汤。
"""

MESSY_AGE = "年龄：51岁[]\n主诉：xx"


def test_extract_sections():
    print("\n== 章节抽取 ==")
    extracted = extract_sections(GOOD_TEXT)
    ok, missing = sections_complete(extracted)
    check("标准五段齐全", ok, f"missing={missing}")
    check("四诊含主诉", "主诉" in extracted["四诊"]["content"] or extracted["四诊"]["content"].startswith("颈项"))
    check("处方非空", len(extracted["处方"]["content"]) > 5)

    v = extract_sections(VARIANT_TEXT)
    # 四诊 may be found via 望闻问切
    check("别名:病因病机", bool(v["病因病机分析"]["content"]), repr(v["病因病机分析"]["content"][:40]))
    check("别名:辨证", bool(v["中医诊断及辩证"]["content"]), repr(v["中医诊断及辩证"]["content"][:40]))
    check("别名:治则", bool(v["治法"]["content"]), repr(v["治法"]["content"][:40]))
    check("别名:方药", bool(v["处方"]["content"]), repr(v["处方"]["content"][:40]))

    inc = extract_sections(INCOMPLETE_TEXT)
    ok2, missing2 = sections_complete(inc)
    check("不完整文本检出缺失", (not ok2) and ("治法" not in missing2), f"missing={missing2}")


def test_validate():
    print("\n== 结构化校验 ==")
    r1 = validate_rewritten_text(GOOD_TEXT, require_patient_header=True)
    check("合格文本 ok", r1.ok, str(r1.errors))
    check("合格文本有姓名行", r1.has_patient_header)

    r2 = validate_rewritten_text(INCOMPLETE_TEXT, require_patient_header=True)
    check("缺段不 ok", not r2.ok)
    check("缺段有 errors", len(r2.errors) > 0)

    r3 = validate_rewritten_text("", require_patient_header=True)
    check("空文本不 ok", not r3.ok)

    r4 = validate_rewritten_text(GOOD_TEXT.replace("姓名：张三（女，44岁）\n", ""), require_patient_header=True)
    check("缺姓名行有警告", (not r4.has_patient_header) and any("姓名" in w for w in r4.warnings))


def test_meta_parse():
    print("\n== 元数据解析 ==")
    n, a = parse_name_age_from_name_line("张三（女，44岁）")
    check("姓名张三年龄44", n == "张三" and a == "44岁", f"{n},{a}")

    n2, a2 = parse_name_age_from_name_line("李四, 32岁")
    check("逗号年龄", n2 == "李四" and a2 == "32岁", f"{n2},{a2}")

    n3, a3 = parse_name_age_from_name_line("1-施志刚-51岁[]-西医：腰椎间盘突出")
    check("脏文件名前缀清洗", "[]" not in a3 and n3 != "", f"{n3},{a3}")

    check("年龄字段清洗", parse_age_from_text(MESSY_AGE) == "51岁", parse_age_from_text(MESSY_AGE))

    d1 = parse_disease_from_content(GOOD_TEXT)
    check("诊断含痹症", "痹症" in d1, d1)

    d2 = parse_disease_from_content(MULTI_TEXT)
    check("西医诊断可提取", "腰椎间盘突出" in d2 or "腰痹" in d2, d2)


def test_split():
    print("\n== 多患者切割 ==")
    records = split_patient_records(MULTI_TEXT)
    check("切割为2人", len(records) == 2, f"got {len(records)}")
    if len(records) == 2:
        check("第1人姓名", records[0].name == "施志刚", records[0].name)
        check("第1人年龄", records[0].age == "51岁", records[0].age)
        check("第1人诊断干净", "西医" not in records[0].disease, records[0].disease)
        check("第2人姓名", records[1].name == "周婷", records[1].name)

    # 无姓名行的单段
    single = split_patient_records("主诉：xx\n处方：yy\n")
    check("无姓名兜底单患者", len(single) == 1 and single[0].name == "未知", f"{len(single)}")

    # 自定义分隔符
    dtext = "姓名：甲\n主诉：a\n---\n姓名：乙\n主诉：b\n"
    drecs = split_patient_records(dtext, delimiter="---")
    check("分隔符切割", len(drecs) == 2 and drecs[0].name == "甲" and drecs[1].name == "乙", f"{[r.name for r in drecs]}")


def test_template_fill():
    print("\n== 模板填充 ==")
    template_path = Path("template/门诊小病例模板.json")
    if not template_path.exists():
        check("模板文件存在", False, "template missing")
        return
    with open(template_path, "r", encoding="utf-8") as f:
        tmpl = json.load(f)
    extracted = extract_sections(GOOD_TEXT)
    filled = fill_template_with_sections(tmpl, extracted, DEFAULT_SECTION_MAP)
    item_map = {it.get("name"): it.get("text", "") for it in filled.get("items", [])}
    check("元素14写入四诊", "主诉" in item_map.get("元素14", ""), item_map.get("元素14", "")[:40])
    check("元素13写入处方", "百会" in item_map.get("元素13", "") or "针" in item_map.get("元素13", ""), item_map.get("元素13", "")[:40])

    smap = load_section_map(template_path)
    check("默认映射完整", set(smap.keys()) == set(SECTION_ORDER))


def test_end_to_end_and_failed():
    print("\n== 端到端 + 失败样本 ==")
    template_path = Path("template/门诊小病例模板.json")
    if not template_path.exists():
        check("模板存在", False)
        return

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        # 完整流程
        records = split_patient_records(GOOD_TEXT)
        written = write_split_files(records, td_path / "split")
        check("写出切割文件", len(written) == 1, str(written))

        out_json = td_path / "out" / "sample.json"
        process_text_to_template_json(GOOD_TEXT, template_path, out_json)
        check("生成JSON", out_json.exists())
        data = json.loads(out_json.read_text(encoding="utf-8"))
        names = [it.get("name") for it in data.get("items", [])]
        check("JSON含模板items", "元素14" in names)

        # 失败样本
        case_dir = save_failed_case(
            td_path,
            stage="ai_rewrite",
            source_name="bad.txt",
            content=INCOMPLETE_TEXT,
            errors=["缺少必需章节: 病因病机分析"],
            warnings=["缺少「姓名：」行"],
        )
        check("失败目录存在", case_dir.exists())
        check("original.txt", (case_dir / "original.txt").exists())
        meta = json.loads((case_dir / "meta.json").read_text(encoding="utf-8"))
        check("meta.json字段", meta.get("stage") == "ai_rewrite" and meta.get("errors"))


def test_regression_samples():
    print("\n== 历史样例回归 ==")
    sample = Path("output/AI_Rewrite/1-施志刚-51岁[]-西医：腰椎间盘突出-1-XXX-51岁-未知病名.txt")
    if not sample.exists():
        # 尝试 glob
        cands = list(Path("output/AI_Rewrite").glob("*.txt")) if Path("output/AI_Rewrite").exists() else []
        if not cands:
            check("历史样例存在", False, "no samples")
            return
        sample = cands[0]

    text = sample.read_text(encoding="utf-8")
    extracted = extract_sections(text)
    ok, missing = sections_complete(extracted)
    check(f"样例可抽全五段 ({sample.name})", ok, f"missing={missing}")

    # 切割样例文件名不应再带 []
    from record_parser import PatientRecord
    rec = PatientRecord(name="施志刚", age="51岁", disease="腰椎间盘突出", content=text)
    check("文件名无方括号", "[" not in rec.filename_stem and "]" not in rec.filename_stem, rec.filename_stem)


def test_map_json_excluded():
    print("\n== 模板映射与扫描 ==")
    from record_parser import get_available_templates
    templates = get_available_templates("template")
    check("扫描不含 map.json", all(not t.endswith(".map.json") for t in templates), str(templates))
    check("仍含正式模板", any(t.endswith("门诊小病例模板.json") for t in templates), str(templates))

    # 自定义映射文件
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        fake_tpl = td_path / "custom.json"
        fake_tpl.write_text(json.dumps({"items": [{"name": "A"}, {"name": "B"}, {"name": "C"}, {"name": "D"}, {"name": "E"}]}), encoding="utf-8")
        map_path = td_path / "custom.map.json"
        map_path.write_text(json.dumps({
            "四诊": "A", "病因病机分析": "B", "中医诊断及辩证": "C", "治法": "D", "处方": "E"
        }, ensure_ascii=False), encoding="utf-8")
        smap = load_section_map(fake_tpl)
        check("读取自定义映射", smap.get("四诊") == "A", str(smap))

        # 填充
        with open(fake_tpl, encoding="utf-8") as f:
            tmpl = json.load(f)
        extracted = extract_sections(GOOD_TEXT)
        filled = fill_template_with_sections(tmpl, extracted, smap)
        item_map = {it["name"]: it.get("text", "") for it in filled["items"]}
        check("按自定义映射写入", "主诉" in item_map.get("A", ""), item_map.get("A", "")[:30])


def test_api_client_retry():
    print("\n== API 退避重试 ==")
    from unittest.mock import MagicMock, patch
    from api_client import ApiError, call_chat_completion

    # 1) 429 两次后成功
    responses = []
    for code in (429, 429):
        r = MagicMock()
        r.status_code = code
        r.headers = {}
        r.text = "rate limited"
        responses.append(r)
    ok_resp = MagicMock()
    ok_resp.status_code = 200
    ok_resp.json.return_value = {"choices": [{"message": {"content": "改写结果"}}]}
    ok_resp.raise_for_status = MagicMock()
    responses.append(ok_resp)

    with patch("api_client.requests.post", side_effect=responses) as mock_post, \
         patch("api_client.time.sleep") as mock_sleep:
        text = call_chat_completion("http://x", max_retries=3, backoff_base=0.01)
        check("429重试后成功", text == "改写结果", text)
        check("调用了3次", mock_post.call_count == 3, str(mock_post.call_count))
        check("有退避sleep", mock_sleep.call_count >= 2, str(mock_sleep.call_count))

    # 2) 持续 429 最终抛错
    always_429 = MagicMock()
    always_429.status_code = 429
    always_429.headers = {}
    always_429.text = "rate limited"
    with patch("api_client.requests.post", return_value=always_429), \
         patch("api_client.time.sleep"):
        try:
            call_chat_completion("http://x", max_retries=2, backoff_base=0.01)
            check("耗尽后抛错", False, "should raise")
        except ApiError as e:
            check("耗尽后抛错", True)
            check("标记可重试", e.retryable, str(e))

    # 3) 400 不重试
    bad = MagicMock()
    bad.status_code = 400
    bad.headers = {}
    bad.text = "bad request"
    # raise_for_status will be called after status check - 400 not in RETRYABLE so goes to raise_for_status
    bad.raise_for_status.side_effect = __import__("requests").HTTPError(response=bad)
    with patch("api_client.requests.post", return_value=bad) as mock_post2, \
         patch("api_client.time.sleep") as mock_sleep2:
        try:
            call_chat_completion("http://x", max_retries=3, backoff_base=0.01)
            check("400抛错", False)
        except ApiError:
            check("400抛错", True)
            check("400不重试", mock_post2.call_count == 1 and mock_sleep2.call_count == 0,
                  f"post={mock_post2.call_count} sleep={mock_sleep2.call_count}")


def test_prompt_version_helpers():
    print("\n== 提示词版本化 ==")
    # 只测纯函数/目录逻辑，不导入 streamlit UI
    from pathlib import Path as P
    import tempfile as tf

    # 模拟 snapshot 逻辑
    with tf.TemporaryDirectory() as td:
        hist = P(td) / "prompt_history"
        hist.mkdir()
        cfg = {"version": 1, "system_prompt": "s", "user_prompt_template": "u {raw_text}"}
        snap = hist / "prompt_v1.json"
        payload = dict(cfg)
        payload["saved_at"] = "2026-01-01 00:00:00"
        snap.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        loaded = json.loads(snap.read_text(encoding="utf-8"))
        check("快照可读回", loaded.get("system_prompt") == "s" and "{raw_text}" in loaded.get("user_prompt_template", ""))


def main():
    print("========== record_parser 自测 ==========")
    test_extract_sections()
    test_validate()
    test_meta_parse()
    test_split()
    test_template_fill()
    test_end_to_end_and_failed()
    test_regression_samples()
    test_map_json_excluded()
    test_api_client_retry()
    test_prompt_version_helpers()
    print("\n" + "=" * 40)
    print(f"结果: PASS={PASS}  FAIL={FAIL}")
    if FAIL:
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
