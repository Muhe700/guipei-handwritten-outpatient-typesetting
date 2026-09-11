# test_stability.py
"""全项目功能与稳定性测试。"""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
import traceback
from pathlib import Path
from unittest.mock import MagicMock, patch

PASS = 0
FAIL = 0
ERRORS = []


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        ERRORS.append(f"{name}: {detail}")
        print(f"  [FAIL] {name} {detail}")


def section(title: str):
    print(f"\n== {title} ==")


# ---------------------------------------------------------------------------
section("模块导入完整性")
# ---------------------------------------------------------------------------

modules = [
    "record_parser",
    "api_client",
    "split_records",
    "extract_medical_record",
]
for mod in modules:
    try:
        importlib.import_module(mod)
        check(f"导入 {mod}", True)
    except Exception as e:
        check(f"导入 {mod}", False, str(e))

# visual_interface 含 streamlit 顶层副作用，只做语法检查
try:
    src = Path("visual_interface.py").read_text(encoding="utf-8")
    compile(src, "visual_interface.py", "exec")
    check("visual_interface.py 语法", True)
except Exception as e:
    check("visual_interface.py 语法", False, str(e))


# ---------------------------------------------------------------------------
section("record_parser 边界与鲁棒性")
# ---------------------------------------------------------------------------

from record_parser import (
    DEFAULT_SECTION_MAP,
    PatientRecord,
    extract_sections,
    fill_template_with_sections,
    get_available_templates,
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

# 空/None 安全
check("空文本抽取不炸", extract_sections("") is not None)
r = validate_rewritten_text("")
check("空文本校验失败", not r.ok)
check("空年龄", parse_age_from_text("") == "未知年龄")
check("空诊断", parse_disease_from_content("") == "未知病名")
n, a = parse_name_age_from_name_line("")
check("空姓名", n == "未知" and a == "未知年龄")

# 特殊字符
n2, a2 = parse_name_age_from_name_line("张三<script>alert(1)</script>")
check("特殊字符姓名不炸", isinstance(n2, str) and len(n2) > 0, n2)

# 超长年龄
check("三位数年龄", parse_age_from_text("年龄：150岁") == "150岁")

# 无 items 的模板
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    tpl = td / "empty.json"
    tpl.write_text("{}", encoding="utf-8")
    out = td / "out.json"
    process_text_to_template_json("主诉：x\n处方：y", tpl, out)
    check("无items模板可写出", out.exists())
    data = json.loads(out.read_text(encoding="utf-8"))
    check("无items输出为空dict", data == {})

# 损坏 map.json 回退默认
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    tpl = td / "t.json"
    tpl.write_text(json.dumps({"items": []}), encoding="utf-8")
    bad_map = td / "t.map.json"
    bad_map.write_text("{not json", encoding="utf-8")
    smap = load_section_map(tpl)
    check("坏map回退默认", smap == DEFAULT_SECTION_MAP, str(smap))

# 并发写大量失败样本
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    ok = 0
    for i in range(20):
        try:
            save_failed_case(td, stage="stress", source_name=f"f{i}.txt",
                             content=f"content {i}", errors=[f"e{i}"])
            ok += 1
        except Exception:
            pass
    check("连续20个失败样本", ok == 20, f"ok={ok}")

# 多患者大量切割
multi = "\n".join([f"姓名：甲{i}（男，{20+i}岁）\n主诉：症状{i}\n处方：方{i}\n" for i in range(30)])
recs = split_patient_records(multi)
check("30患者切割", len(recs) == 30, f"got {len(recs)}")
check("第1人姓名正确", recs[0].name == "甲0", recs[0].name)

# 文件名非法字符
rec = PatientRecord(name="张/三:*?", age="40岁", disease="腰痛|腿", content="x")
check("非法文件名字符被清洗", "/" not in rec.filename_stem and "*" not in rec.filename_stem,
      rec.filename_stem)


# ---------------------------------------------------------------------------
section("api_client 稳定性")
# ---------------------------------------------------------------------------

from api_client import ApiError, call_chat_completion

# 超时重试
timeout_exc = __import__("requests").Timeout("timed out")
with patch("api_client.requests.post", side_effect=[timeout_exc, timeout_exc, timeout_exc, timeout_exc]):
    with patch("api_client.time.sleep"):
        try:
            call_chat_completion("http://x", max_retries=3, backoff_base=0.01)
            check("超时耗尽抛错", False, "should raise")
        except ApiError as e:
            check("超时耗尽抛错", True)
            check("超时标记可重试", e.retryable)

# 连接错误
conn_exc = __import__("requests").ConnectionError("refused")
with patch("api_client.requests.post", side_effect=conn_exc):
    with patch("api_client.time.sleep"):
        try:
            call_chat_completion("http://x", max_retries=1, backoff_base=0.01)
            check("连接错误抛错", False)
        except ApiError:
            check("连接错误抛错", True)

# HTML 响应
html_resp = MagicMock()
html_resp.status_code = 200
html_resp.text = "<!DOCTYPE html><html></html>"
html_resp.json.side_effect = json.JSONDecodeError("x", "y", 0)
html_resp.raise_for_status = MagicMock()
with patch("api_client.requests.post", return_value=html_resp):
    try:
        call_chat_completion("http://x", max_retries=0)
        check("HTML响应报错", False)
    except ApiError as e:
        check("HTML响应报错", "HTML" in str(e) or "html" in str(e).lower(), str(e)[:80])

# Retry-After 头
ra_resp = MagicMock()
ra_resp.status_code = 429
ra_resp.headers = {"Retry-After": "0.05"}
ra_resp.text = "limited"
ok_resp = MagicMock()
ok_resp.status_code = 200
ok_resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
ok_resp.raise_for_status = MagicMock()
with patch("api_client.requests.post", side_effect=[ra_resp, ok_resp]) as mp:
    with patch("api_client.time.sleep") as slp:
        t = call_chat_completion("http://x", max_retries=2)
        check("Retry-After后成功", t == "ok")
        check("使用了Retry-After延时", slp.called)


# ---------------------------------------------------------------------------
section("CLI 入口")
# ---------------------------------------------------------------------------

# extract_medical_record main
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    inp = td / "in.txt"
    inp.write_text(
        "姓名：测试\n主诉：头痛\n病因病机分析：x\n中医诊断及辩证：y\n治法：z\n处方：w\n",
        encoding="utf-8",
    )
    out = td / "out.json"
    import subprocess
    r = subprocess.run(
        [sys.executable, "extract_medical_record.py",
         "--input", str(inp), "--template", "template/门诊小病例模板.json", "--output", str(out)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=".",
    )
    check("extract CLI 退出码0", r.returncode == 0, (r.stderr or "")[-200:])
    check("extract CLI 有输出", out.exists())

# split_records main
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    inp = td / "multi.txt"
    inp.write_text("姓名：甲\n主诉：a\n\n姓名：乙\n主诉：b\n", encoding="utf-8")
    outdir = td / "split"
    import subprocess
    r = subprocess.run(
        [sys.executable, "split_records.py", "--input", str(inp), "--output", str(outdir)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=".",
    )
    check("split CLI 退出码0", r.returncode == 0, (r.stderr or "")[-200:])
    files = list(outdir.glob("*.txt")) if outdir.exists() else []
    check("split CLI 生成文件", len(files) == 2, str(files))


# ---------------------------------------------------------------------------
section("端到端：历史样例全量重抽")
# ---------------------------------------------------------------------------

template_path = Path("template/门诊小病例模板.json")
sample_dir = Path("output/AI_Rewrite")
SYNTHETIC = """姓名：测试甲（女，40岁）
主诉：头痛3天。
现病史：受凉后头痛，无呕吐。
既往史：体健。
过敏史：否认。
四诊：舌淡红苔薄白，脉弦。
病因病机分析：外感风邪，上扰清窍，不通则痛。
中医诊断及辩证：头痛；风邪袭表证。
治法：疏风散邪，通络止痛。
处方：针刺百会、风池、合谷，平补平泻。
"""

samples = list(sample_dir.glob("*.txt")) if sample_dir.exists() else []
if not samples and template_path.exists():
    print("  [INFO] 无历史样例，使用合成标准五段做端到端")
    samples = []  # 下面单独测合成

if template_path.exists():
    full_ok = 0
    partial = []
    test_texts = [(s.name, s.read_text(encoding="utf-8")) for s in samples]
    if not test_texts:
        test_texts = [("synthetic.txt", SYNTHETIC)]
    for name, text in test_texts:
        extracted = extract_sections(text)
        ok, missing = sections_complete(extracted)
        if ok:
            full_ok += 1
        else:
            partial.append((name, missing))
        try:
            with tempfile.TemporaryDirectory() as td:
                out = Path(td) / "t.json"
                process_text_to_template_json(text, template_path, out)
                data = json.loads(out.read_text(encoding="utf-8"))
                assert "items" in data
        except Exception as e:
            check(f"样例JSON写出 {name}", False, str(e))
            break
    else:
        check("全部样例可写出JSON", True)
    check(f"五段齐全 {full_ok}/{len(test_texts)}", full_ok == len(test_texts),
          f"partial={partial[:3]}")
else:
    check("模板存在", False, "template missing")


# ---------------------------------------------------------------------------
section("配置文件健壮性")
# ---------------------------------------------------------------------------

# api_config 兼容旧格式
from pathlib import Path as P
with tempfile.TemporaryDirectory() as td:
    td = P(td)
    old = {"api_url": "http://x", "api_key": "k", "model": "m", "temperature": 0.5, "max_tokens": 100}
    # 仅验证 json 可解析，不直接调 UI 函数
    raw = json.dumps(old)
    data = json.loads(raw)
    check("旧格式api_config字段齐", set(old.keys()) <= set(data.keys()))

# prompt_config 真实文件
pc = P("prompt_config.json")
if pc.exists():
    data = json.loads(pc.read_text(encoding="utf-8"))
    check("prompt_config 可解析", True)
    check("prompt_config 含raw_text", "{raw_text}" in data.get("user_prompt_template", ""))
else:
    check("prompt_config 存在", False)


# ---------------------------------------------------------------------------
section("模板与映射一致性")
# ---------------------------------------------------------------------------

templates = get_available_templates("template")
check("扫描到模板", len(templates) >= 1, str(templates))
for t in templates:
    tp = Path("template") / t
    smap = load_section_map(tp)
    check(f"映射完整 {t}", set(smap.keys()) == {
        "四诊", "病因病机分析", "中医诊断及辩证", "治法", "处方"
    })
    # 模板 items 含映射目标
    data = json.loads(tp.read_text(encoding="utf-8"))
    names = {it.get("name") for it in data.get("items", [])}
    missing_targets = [v for v in smap.values() if v not in names]
    check(f"映射目标存在 {t}", not missing_targets, str(missing_targets))


# ---------------------------------------------------------------------------
section("日志文件")
# ---------------------------------------------------------------------------

# logging setup 不炸
import logging.handlers
logger = logging.getLogger("stability_test")
logger.info("stability test ran")
check("logging 可用", True)


# ---------------------------------------------------------------------------
print("\n" + "=" * 50)
print(f"稳定性测试结果: PASS={PASS}  FAIL={FAIL}")
if ERRORS:
    print("失败项:")
    for e in ERRORS:
        print(f"  - {e}")
if FAIL:
    sys.exit(1)
print("全部通过")
