# 规培手写门诊病历自动排版

> 给中医规培同学用的门诊病历「自动抄写」流水线，解决手抄病历的难题  
> 乱文本 → 切割 / AI 改写 / 格式转换 / 校验 → **奎享雕刻**可直接导入的 JSON

---
请加入QQ群：760335197
<img width="1284" height="2283" alt="76aa222e0008e49f1af797d05d4acd66" src="https://github.com/user-attachments/assets/d3fb1b55-7de3-4f4d-bf34-4c06420da3ee" />
好用的话请赞助我，以覆盖服务器成本
<img width="1242" height="1692" alt="4fda66a4b8806c319bdadee25f227950" src="https://github.com/user-attachments/assets/9f9db94d-33cd-485b-9575-1da3555aaf88" />



## 解决什么问题

规培期间门诊跟师，不同科室的门诊病历完成度不同，但是规培办要求我们主诉、现病史、四诊、辨证、治法、处方一整套都要抄。  
本工具帮你把下面这类**格式混乱、信息不全**的大段文本，自动整理成写字机能直接吃的 JSON：

- 门诊his系统病历拍照后的 **OCR 导出文本**（错字、缺标点、章节混杂都常见）
- **AI 生成 / 整理过的病历草稿**


处理完成后，把 JSON 一键导入 **奎享雕刻**（写字机排版软件），即可由机器完成门诊病历抄写。

```text
[OCR / AI / 手打乱文本]
        │
        ▼
   ① 多患者切割          按「姓名：」或自定义标记拆分
        │
        ▼
   ② AI 改写             补全主诉/现病史/四诊/病机/诊断/治法/处方
        │
        ▼
   ③ 结构化校验          缺段自动带错误重试；失败样本可回放
        │
        ▼
   ④ 填入手写模板 JSON   奎享雕刻可直接识别导入
```

## 功能一览

| 功能 | 说明 |
| --- | --- |
| **批量病历切割** | 一份 txt 里多位患者，按 `姓名：` 或自定义分隔符拆成单人文件，文件名自动带序号-姓名-年龄-诊断 |
| **AI 改写并提取** | 调用任意 OpenAI 兼容接口（DeepSeek / 通义 / 本地模型等），把乱病历改写成标准五段，再填模板出 JSON |
| **基础 JSON 提取** | 文本已是标准格式时跳过 AI，直接抽取 |
| **结构化校验** | 五段是否齐全、是否缺「姓名：」行；失败自动重试一次，并把样本落到 `output/failed_cases/` |
| **提示词可改可回滚** | 侧边栏在线编辑改写提示词，自动版本快照 |
| **多模板** | `template/` 下可放多套奎享模板，元素映射用 `*.map.json` 配置 |

标准五段：**四诊（主诉/现病史/既往史/过敏史/望闻问切）· 病因病机分析 · 中医诊断及辨证 · 治法 · 处方**

## 快速开始

### 环境

- Windows（推荐）
- Python 3.8+

### 安装与启动

```bash
# 1. 进入项目目录
cd 规培手写门诊病历自动排版   # 或你本机的项目文件夹名

# 2. （推荐）创建虚拟环境
python -m venv .venv
.venv\Scripts\activate

# 3. 启动（会检查依赖、处理端口占用）
python start.py

# 非交互 / 脚本化：
python start.py --yes              # 自动装缺依赖并启动
python start.py --restart          # 端口占用时重启「本应用」
python start.py --stop             # 只停止本应用
python start.py --port 8502        # 换端口
```

浏览器会打开 `http://127.0.0.1:8501`。  
也可以直接：`streamlit run visual_interface.py`

### 配置 AI 接口

1. 复制 `api_config.example.json` 为 `api_config.json`（或在界面侧边栏「API 接口」里填）。
2. 填入 OpenAI 兼容地址与 Key，例如 DeepSeek：

```json
{
  "current_profile": "deepseek",
  "profiles": {
    "deepseek": {
      "api_url": "https://api.deepseek.com/v1/chat/completions",
      "api_key": "sk-xxxxxxxx",
      "model": "deepseek-chat",
      "temperature": 0.3,
      "max_tokens": 4000
    }
  }
}
```

3. 侧边栏点「测试连接」，绿了再批量跑。

### 推荐工作流（规培场景）

1. 门诊结束：把病历本拍照，用 OCR 得到 txt（或直接丢给 AI 整理成草稿）。
2. 打开本工具 → **批量切割** Tab，确认每位患者已拆开。
3. **AI 改写并提取** Tab，多选 txt，一键处理。
4. 到 `output/JSON_Transcript/` 取 JSON，导入 **奎享雕刻**。
5. 写字机完成抄写；个别失败的文件可在界面里「一键重试」。

### 奎享雕刻模板

- 默认模板：`template/门诊小病例模板.json`（另有针灸科、不同手写字体版本）。
- 模板里的 `元素9/11/12/13/14` 分别对应病机、诊断、治法、处方、四诊等文本块。
- 若你换了奎享里的元素名，复制一份 `模板名.map.json` 改映射即可，无需改代码。

```json
{
  "四诊": "元素14",
  "病因病机分析": "元素9",
  "中医诊断及辩证": "元素11",
  "治法": "元素12",
  "处方": "元素13"
}
```

## 界面说明

| 区域 | 作用 |
| --- | --- |
| **侧边栏·配置中心** | 选择模板、管理 API 方案、编辑/回滚提示词 |
| **批量切割** | 多患者大文件拆分 + 结果预览 |
| **AI 改写并提取** | 主流程：改写 → 校验 → 切割 → JSON |
| **基础提取** | 已标准格式的文本，免 AI 直出 |
| **运行日志** | 系统日志与解析错误分栏查看 |

## 项目结构

```text
├── start.py                  # 启动器（依赖检查 / 端口管理）
├── visual_interface.py       # Streamlit 主界面
├── record_parser.py          # 切割、抽取、校验、模板填充
├── api_client.py             # OpenAI 兼容调用 + 429/超时退避
├── split_records.py          # 切割 CLI
├── extract_medical_record.py # 抽取 CLI
├── prompt_config.json        # 改写提示词（可版本回滚）
├── api_config.example.json   # API 配置示例
├── template/                 # 奎享模板 + *.map.json 映射
├── output/                   # AI_Rewrite / JSON_Transcript / failed_cases
└── test_*.py                 # 功能与稳定性测试
```

## 注意事项

- 上传 txt 请使用 **UTF-8** 编码（GBK 会尝试自动回退）。
- AI 改写会消耗 Token；批量前建议先用 1 个文件试跑。
- **患者隐私**：走第三方 API 时请确认是否需要脱敏；可接本地模型。
- `api_config.json` 已在 `.gitignore` 中，不会被提交到仓库。
- **上线前**：请将真实 API Key 从 `api_config.json` 挪到环境变量（优先级更高）：
  ```powershell
  $env:CLINIC_API_KEY = "sk-..."
  # 或 DEEPSEEK_API_KEY / OPENAI_API_KEY
  ```
- Streamlit 默认只监听 `127.0.0.1`。若需局域网访问，请自行评估 PHI 暴露风险后再改 `.streamlit/config.toml`。
- 输出目录输入会被限制在项目目录内，防止误写到系统路径。

## 上线检查清单

1. `python test_record_parser.py` 与 `python test_stability.py` 全部通过  
2. 环境变量提供 API Key；`api_config.json` 中不含真实密钥，或密钥不会随仓库分发  
3. `.streamlit/config.toml` 的 `address` 仍为 `127.0.0.1`（或已加鉴权反代）  
4. 确认 `output/`、`split_output/`、日志中无遗留真实患者信息再打包分发  
5. 在侧边栏「测试连接」绿后再批量跑

## 测试

```bash
python test_record_parser.py   # 解析/校验/切割/映射/路径安全
python test_stability.py       # 边界、API 重试、CLI、历史样例回归
python test_ui_app.py          # Streamlit AppTest：界面可渲染、按钮校验
```

CLI 示例：

```bash
python extract_medical_record.py --input a.txt --output a.json
python extract_medical_record.py --input a.txt --template "template/门诊小病例模板.json" --output a.json --strict
python split_records.py --input multi.txt --output split_output
```

## 常见问题

**Q: 启动提示 `ModuleNotFoundError`？**  
A: 跑 `python start.py`，它会提示并安装缺失依赖。

**Q: 8501 端口被占用？**  
A: `start.py` 会检测并提供重启/停止选项。

**Q: AI 改写失败或 JSON 提不出来？**  
A: 先「测试连接」；再看侧栏提示词是否含 `{raw_text}`；失败样本在 `output/failed_cases/`，可改提示词后重试。

**Q: 奎享导入后空白或错位？**  
A: 确认选中的模板与奎享工程一致；元素名对不上就改对应的 `*.map.json`。

**Q: 文件名出现「未知年龄 / 未知病名」？**  
A: 原文缺年龄或诊断时会占位；可在 OCR/AI 草稿里补上 `年龄：`、`诊断：` 后再跑。

## 免责声明

本工具仅供规培学习与文书辅助，**不能替代带教审签**。输出内容需本人核对后再用于正式病历。请遵守医院关于病历书写与患者隐私的规定。

---

如果这个工具帮你省下了抄病历的时间，欢迎 Star；有问题或模板需求可以提 Issue。
