# docconvert

离线、可搬移的通用文件格式转换工具。整套目录复制到任何装了 Python 3.8+ 的电脑就能跑，
转换过程不联网。

```
文档 / 表格 / 演示 / PDF / 图片 / 音视频  →  任意常见格式
```

---

## 30 秒上手

```bash
# 看这台机器现在能转什么
./dc.cmd doctor            # Windows
./docconvert doctor        # Linux / macOS

# 单文件：给了完整输出路径就行
./dc.cmd convert 合同.docx 合同.pdf

# 只给目标格式，输出到同目录
./dc.cmd convert 简历.pdf --to md

# 输出到指定目录
./dc.cmd convert 简历.pdf --to md -o out/

# 批量 / 整个目录树
./dc.cmd tree ./待转 --to pdf -o ./已转
./dc.cmd batch --to pdf a.docx b.xlsx c.pptx
```

先跑 `doctor` 能省很多事：它直接告诉你缺哪个引擎、怎么装，而不是让你在转换失败时猜。

---

## 图形界面

不想记命令就用浏览器：

```bash
./dc.cmd serve            # Windows，自动打开 http://127.0.0.1:8765
./docconvert serve        # Linux / macOS
```

拖文件进去 → 选目标格式 → 点转换 → 下载。

```
┌─────────────────────────────────────────────┐
│  docconvert                    7/7 引擎就绪 │
├─────────────────────────────────────────────┤
│      ┌───────────────────────────────┐      │
│      │   把文件拖到这里，或点击选择   │      │
│      └───────────────────────────────┘      │
│                                             │
│  引擎 [自动选择▾]  □扫描件走OCR  [开始转换] │
│                                             │
│  劳动合同.docx  → [pdf▾]   完成 · 549KB  ⬇  │
│  resume.pdf     → [docx▾]  完成 · 47KB   ⬇  │
│    ⚠ 内容全部落在 362 个文本框里，需人工重排 │
└─────────────────────────────────────────────┘
```

几个刻意的取舍：

- **只监听 `127.0.0.1`** —— 文件不出本机，也不对局域网开放
- **零额外依赖** —— 只用 Python 标准库 `http.server`，不引 Flask/FastAPI
- **队列串行** —— LibreOffice 每个进程吃几百 MB，并发反而容易崩
- **安全边界** —— 上传体积上限 512MB、文件名只取 basename（防目录穿越）、
  下载凭一次性 token（不能凭路径读任意文件）、临时目录随进程退出删除

```bash
docconvert serve --port 9000      # 换端口
docconvert serve --no-browser     # 不自动开浏览器
```

---

## 部署

### 在线（有网，一次性）

```powershell
# Windows
.\install\install.ps1

# Linux / macOS
./install/install.sh
```

会装 Python 依赖 + LibreOffice / Pandoc / Tesseract / FFmpeg，最后自动跑 doctor 和自检。

只想装 Python 依赖（不装系统软件）：

```powershell
.\install\install.ps1 -SkipEngines
```

### 离线（内网机器 / 无网环境）

这是本工具的主场景。**部署期需要物料，运行期完全离线。**

在有网的机器上下好安装包，放进 `install/vendor/`，然后把整个目录拷到目标机器：

```
install/vendor/
├── LibreOffice_24.x.x_Win_x86-64.msi
├── pandoc-3.x-windows-x86_64.msi      （或 pandoc-*.zip）
├── tesseract-ocr-w64-setup-5.x.exe
└── ffmpeg-release-essentials.zip

# Linux 离线：先把 wheel 下全
pip download -d install/vendor/wheels -r requirements.txt
```

脚本会自动优先使用 `vendor/` 里的本地包，找不到才去 winget / apt。全程不需要联网下载。

### 搬到另一台电脑

直接复制整个 `docconvert/` 目录即可。工具本身没有安装步骤，不写注册表、不依赖用户目录。

唯一的机器级依赖是 Python 3.8+ 和那几个引擎 —— 这是物理现实，不是设计缺陷：
**离线做格式转换，必须在本机有转换引擎。**

---

## 能力矩阵

格子 = 实际会调用的引擎。`×` = 不支持，而不是"还没实现"。

| 源 → 目标 | 文档 | 表格 | 演示 | PDF | 标记 | 文本 | 图片 | 音视频 |
|---|---|---|---|---|---|---|---|---|
| **文档** docx/odt/rtf | — | × | × | LibreOffice | LibreOffice / Pandoc | Pandoc / 原生 | LibreOffice | × |
| **表格** xlsx/ods | × | — | × | LibreOffice | LibreOffice | 原生(Python) | × | × |
| **演示** pptx/odp | × | × | — | LibreOffice | LibreOffice | 原生(Python) | LibreOffice | × |
| **PDF** | LibreOffice ⚠️ | × | LibreOffice ⚠️ | — | PyMuPDF | PyMuPDF | PyMuPDF | × |
| **标记** md/html/epub | Pandoc / LibreOffice | × | × | LibreOffice | — | Pandoc | PyMuPDF(epub) | × |
| **文本** txt/csv | LibreOffice | LibreOffice | × | LibreOffice | × | — | × | × |
| **图片** png/jpg/webp | × | × | × | Pillow / PyMuPDF | × | Tesseract | Pillow | × |
| **音视频** mp4/mp3 | × | × | × | × | × | × | FFmpeg | FFmpeg |

### 保真度分级（这个比矩阵重要）

| 等级 | 含义 | 典型链路 |
|---|---|---|
| **高** | 版式、字体、分页基本一致，可直接交付 | docx↔pdf、xlsx↔pdf、pptx↔pdf、图片互转 |
| **中高** | 结构完整（标题/列表/表格），样式丢失 | docx→md、md→docx、html→docx |
| **中** | 只有文字，排版靠人工 | pdf→txt、docx→txt、xlsx→csv |
| **低 ⚠️** | 版式看着像，但内容不可编辑 | **pdf→docx**（每页变成文本框/图片） |
| **需 OCR** | 扫描件才走这条路，必有错字 | 扫描 pdf→txt |

**关于 PDF → 可编辑 Word**：这是所有人最想要、也是离线方案最做不到的一件事。
PDF 存的是"画在哪、用什么字体画"，不是"这段是标题、那段是正文"。所以任何离线工具
（LibreOffice / Adobe / WPS 同理）还原出来的都是**视觉像但结构散**的文档。
真要可编辑，只有两条路：OCR 重建，或人工重排。本工具不假装能做到，只在转换后明确警告。

---

## 命令参考

```
docconvert convert IN [OUT]     单文件；IN 是目录时自动转批量
    -o, --out DIR               输出目录
    --to FMT                    目标格式（未给 OUT 时必填）
    --ext docx,xlsx             目录模式下只转这些类型
    --no-recursive              不递归子目录
    --jobs N                    并发数（默认 1；LibreOffice 吃内存，别开太大）
    --engine NAME               强制指定引擎
    --ocr                       优先走 OCR（扫描件）
    --dpi N                     PDF↔图片渲染精度（默认 200）
    --sheet NAME|N              xlsx 导 CSV 时指定工作表
    --quality N                 图片质量 1-100
    --lang chi_sim+eng          OCR 语言
    --reference-doc FILE        pandoc 生成 docx 的样式模板
    --force                     允许覆盖源文件

docconvert tree DIR --to FMT -o OUT   目录树批量
docconvert batch --to FMT FILES...    指定文件批量
docconvert serve [--port 8765]        启动本地 Web 界面（拖拽转换）
docconvert doctor                     环境体检 + 能力矩阵
docconvert explain pdf docx           看某条转换走哪个引擎、为什么
docconvert formats                    列出支持的扩展名
docconvert matrix                     能力矩阵
```

退出码：`0` 全部成功 / `1` 有失败 / `2` 参数或环境错误。脚本里可以直接判断。

---

## 架构

```
docconvert.py          统一 CLI（参数解析 + 路径推导 + 批量调度）
dc/
├── router.py          路由：按 (源,目标) 给引擎打分，失败自动降级
├── doctor.py          体检与能力矩阵
├── engines/
│   ├── libreoffice.py 办公文档主力（docx/xlsx/pptx/pdf 互转）
│   ├── pandoc.py      标记语言（md/html/docx/rst/epub）
│   ├── pymupdf_engine.py  PDF 出口（文本/图/HTML）、逐页渲染
│   ├── pillow_engine.py   位图互转、图片合成 PDF
│   ├── officedoc.py   Office 原生提取（无外部依赖的兜底）
│   ├── ocr.py         Tesseract 扫描件识别
│   └── ffmpeg_engine.py   音视频
└── utils.py           可执行查找、子进程超时、结果对象
```

三个设计决定，都是为了"换台电脑还能用"：

**1. 引擎可缺失，工具不能废。**
每个引擎独立探测、独立失败。没装 LibreOffice 时，图片和 PDF 提取照样能用，
`doctor` 会明确告诉你还差什么。不会因为一个组件没装就整个工具不可用。

**2. 路由不是"谁能转"，是"谁转得好"。**
`docx→txt` 三家都能接：Pandoc 保留表格脚注最完整、原生读取次之、
LibreOffice 走一遍排版再导出纯属浪费。所以按任务特化打分，而不是按引擎优先级。

**3. 失败绝不产出空壳。**
转换失败就报失败，并把每个引擎的失败原因摊开。宁可让你看到错误，
也不要交付一个能打开但内容是空/乱的文件 —— 那种问题在批量场景里几乎不可能被发现。

---

## 已知边界

不是 bug，是物理现实：

1. **PDF 没有"反向解析"这回事。** 转 docx/pptx 必然是低保真，转 txt 会丢排版，多栏会串行。
2. **97-2003 老格式（.doc/.xls/.ppt）** 能读，但 LibreOffice 导出时某些特性会丢。
3. **扫描件必须走 OCR**，识别率取决于清晰度和 dpi，数字和编号一定要人工核对。
4. **没有真·"任意转任意"**。比如 docx→mp4、图片→xlsx 这种就没有意义，
   工具会直接说"没有引擎能处理"，而不是硬造一个烂产物。
5. **并发别贪。** LibreOffice 每个进程吃几百 MB，`--jobs 4` 起 4 个实例容易把内存打满。
   文档批量建议 1-2，图片批量可以开到 CPU 核数。

## 常见问题

**Q: 装完 LibreOffice 还是提示不可用？**
先跑 `docconvert doctor` 看它找到的路径。如果装在非标准位置，在 `config.yaml` 里写死：
```yaml
engines:
  libreoffice:
    path: "D:/Apps/LibreOffice/program/soffice.exe"
```

**Q: 转换卡住不动？**
LibreOffice 偶发僵尸进程。`config.yaml` 里 `timeout` 默认 300 秒，超时会自动杀。
检查任务管理器里有没有残留 `soffice.bin`，有就结束掉。

**Q: 中文 OCR 全是乱码？**
缺语言包。把 `chi_sim.traineddata` 放进 tesseract 安装目录的 `tessdata/`，
然后 `docconvert doctor` 应该显示 `chi_sim` 在语言列表里。

**Q: 能不能当服务常驻？**
可以。`docconvert.py` 是纯 CLI，退出码标准，适合被定时任务/队列调用。
但它不是守护进程，也没做并发锁 —— 交给调度器控制并发。

---

## 许可证

代码采用 [MIT](LICENSE)。

**本仓库不含任何转换引擎的二进制。** `bin/` 与 `install/vendor/` 已在
`.gitignore` 中排除（合计约 3.2GB），引擎由部署脚本或你手动安装。

各引擎遵循自己的许可证，其中两条需要留意：

- **PyMuPDF 是 AGPL-3.0**。本地使用不受影响；若你要拿 docconvert 对外提供
  网络服务（比如做成 SaaS），需自行评估 AGPL 的源码开放义务。
- **LibreOffice 是 MPL-2.0，Pandoc 是 GPL-2.0+**。docconvert 通过
  `subprocess` 调用它们，不链接其库，因此许可证不传染到本项目代码。
  但如果你把它们的安装包一起分发，需要遵守各自的再分发条款。

## 贡献

```bash
# 开发依赖
pip install -r requirements.txt

# 跑自检（不需要真实文件，约 60 秒）
python tests/selftest.py

# 跑 Web UI 集成测试（含安全边界验证）
python tests/test_webui.py
```

改引擎逻辑后，**务必用真实文件再验一遍** —— 自带的 `selftest` 只能证明
链路通，证明不了真实文件的复杂度能过。这个坑已经踩过两次了。
