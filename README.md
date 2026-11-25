# ClipFile · 剪贴板文件外传工具

在云桌面只能复制文字、无法通过剪贴板直接外传文件的场景下，利用“剪贴板可传纯文本”这一唯一通道，把任意格式文件分片编码成 Base64 文本，逐片复制到本地后自动拼接、校验并还原。

项目包含一对配套工具：

| 端 | 入口 | 职责 |
|---|---|---|
| 云桌面内 | `send_tool.py` / `send_tool.exe` | 读取文件、流式分片、Base64 编码、逐片写入剪贴板 |
| 本地桌面 | `recv_tool.py` / `recv_tool.exe` | 监听/读取剪贴板、解析分片、落盘缓存、断点续传、合并校验并保存 |

全流程不发起任何网络连接，仅使用剪贴板纯文本通道。

## 功能特性

- **分片传输**：默认 512 KB 一片（界面可选 128 KB / 256 KB / 512 KB / 1 MB），支持 500 MB 文件（可通过环境变量调整上限）。
- **双端校验**：每片 CRC32 校验 + 整文件 SHA-256 端到端校验；校验失败保留临时分片，可补发重试。
- **断点续传**：接收端进度持久化在保存目录，关闭工具后重新打开可继续接收；发送端也会记住上次发送位置。
- **自动模式**：发送端可按固定间隔连续发送；接收端每秒轮询剪贴板，自动去重、自动合并。
- **容错**：重复片幂等忽略；非协议剪贴板文本直接忽略；缺片时可一键查看缺失片号范围。
- **低内存**：发送端按片 `seek + read`，接收端按片解码落盘，不会一次性加载整个文件。
- **绿色运行**：仅依赖 Python 标准库（tkinter），可用 PyInstaller 打包为单文件 exe。
- **ASCII 安全**：协议头全 ASCII，文件名使用 UTF-8 百分号编码，中文/空格/特殊字符不会破坏协议。

## 环境要求

- Python 3.9+（Windows 10/11 或 Windows Server 2016+；桌面端也可在 Linux/macOS 运行）
- Python 自带 tkinter（Windows 官方安装包默认包含）
- 打包 exe 时需要 PyInstaller

## 快速开始

> 详细的分步教程、断点续传方法、注意事项与故障排查见 [操作指南](docs/操作指南.md)。

### 源码运行

```bash
# 云桌面内
python send_tool.py

# 本地桌面
python recv_tool.py
```

两个入口也可以直接作为模块运行：

```bash
python -m clipfile.gui_sender
python -m clipfile.gui_receiver
```

### 打包为单文件 exe

> exe 只能在 Windows 上构建；Linux/macOS 只能生成对应平台的可执行文件。

在 Windows 上双击 `build.bat`，或在项目根目录执行：

```bat
build.bat
```

脚本会自动探测 `py` / `python`、检查 tkinter、安装 PyInstaller（如缺失），并在 `dist\` 下生成：

```text
dist\send_tool.exe
dist\recv_tool.exe
```

如果打包失败，窗口会给出具体原因（Python 未安装、缺少 tkinter、PyInstaller 安装失败、被杀毒软件拦截等）。请从包含 `clipfile\` 目录的项目根目录运行脚本。也可以手动执行：

```bat
py -3 -m pip install pyinstaller
py -3 -m PyInstaller --noconfirm --clean --onefile --windowed --name send_tool send_tool.py
py -3 -m PyInstaller --noconfirm --clean --onefile --windowed --name recv_tool recv_tool.py
```

手动执行时产物位于 `dist\`；如果使用的是 `python` 命令而不是 `py -3`，请自行替换。

## 使用步骤

### 云桌面（发送端）

1. 运行 `send_tool.exe`，点击“选择文件”。
2. 按需选择分片大小；工具会自动流式计算 SHA-256 并显示总片数。
3. 点击“发送当前片到剪贴板”。
4. 切到本地接收端接收完成后，回到发送端点击“下一片”继续；也可以勾选“自动连续发送”，按设定间隔自动切换分片。
5. 发送端会显示当前片号、总片数、本片大小，并记录进度。

### 本地桌面（接收端）

1. 运行 `recv_tool.exe`，选择保存目录（默认 `下载` 或用户主目录）。
2. 点击“开始监听”（自动模式），或逐片点击“接收当前片”（手动模式）。
3. 工具自动解析片号、CRC32 并落盘；重复片自动忽略，非协议文本直接忽略。
4. 全部分片收齐后自动合并、校验 SHA-256 并保存，弹出完成提示。
5. 如果关闭过工具，重新打开并选择同一保存目录即可从已接收分片继续。

> 接收端的保存目录下会创建 `.clipfile_temp/` 存放分片缓存，传输成功后自动删除；校验失败时会保留，便于补发重试。

## 分片协议（v1）

首片携带文件元信息：

```text
===CLIPFILE===|v1|name=report.zip|size=104857600|sha256=<64位十六进制>|part=1/205|crc=9f8e7d6c|
<Base64 数据>
```

后续片只携带片号与 CRC：

```text
===CLIPFILE===|v1|part=2/205|crc=1a2b3c4d|
<Base64 数据>
```

约定：

- `name`：文件名 UTF-8 百分号编码，避免中文和特殊字符破坏 ASCII 协议。
- `size`：原始文件字节数。
- `sha256`：原始完整文件的 SHA-256，用于最终校验。
- `part`：`当前片/总片数`，从 1 开始。
- `crc`：本片原始二进制数据的 CRC32，8 位小写十六进制。
- 只有以 `===CLIPFILE===|` 开头的剪贴板文本才会被处理，其余内容一律忽略。

## 断点续传与容错

- **接收端**：`<保存目录>/.clipfile_temp/session.json` 记录文件名、大小、SHA-256、总片数和已收片列表；`parts/000001.bin` 等文件保存解码后的分片。重新打开工具选择同一目录即可继续。
- **发送端**：`~/.clipfile/sessions/` 记录文件路径、大小、修改时间、分片大小与发送位置；文件未变化时自动恢复到上次位置。
- **重复片**：相同片号且 CRC 一致时直接忽略，不会重复追加。
- **损坏片**：CRC 不一致会被拒绝并提示；补发后覆盖旧片。
- **缺片**：点击“合并保存”时会提示缺失片号范围，例如 `1-3, 5, 7-9`。
- **校验失败**：不会覆盖本地已有文件，临时分片保留，补发后可再次合并。
- **同名文件**：自动追加 ` (1)`、` (2)` 等后缀，不覆盖已有文件。

## 配置项

| 配置 | 默认值 | 说明 |
|---|---|---|
| 分片大小 | 512 KB | 发送端界面可选 128 KB / 256 KB / 512 KB / 1 MB |
| 单文件上限 | 500 MB | 环境变量 `CLIPFILE_MAX_FILE_SIZE`（字节）可覆盖 |
| 自动发送间隔 | 3 秒 | 发送端界面可调，1–60 秒 |
| 剪贴板轮询间隔 | 1 秒 | 接收端自动监听固定间隔 |
| 发送端进度目录 | `~/.clipfile` | 环境变量 `CLIPFILE_HOME` 可覆盖 |

示例：

```bat
set CLIPFILE_MAX_FILE_SIZE=1073741824
set CLIPFILE_HOME=D:\clipfile-data
send_tool.exe
```

## 测试

测试覆盖协议解析、CRC/哈希校验、重复片、缺片、断点续传、空文件、中文文件名、路径穿越防护、超大文件限制等场景：

```bash
python -m unittest discover -s tests -v
```

当前共 23 个测试用例，全部通过。

### 验收标准对照

| 编号 | 验收项 | 实现与验证 |
|---|---|---|
| AC-01 | 小文件传输 | `test_small_text_file`：10 KB 以上文本逐字节一致 |
| AC-02 | 大文件传输 | `test_binary_roundtrip`：3 MB 随机二进制 SHA-256 一致（分片逻辑与 100 MB 相同，仅耗时不同） |
| AC-03 | 中断恢复 | `test_resume_after_restart`：半途重建接收会话后继续并完整还原 |
| AC-04 | 重复片容错 | `test_duplicate_parts_ignored`：同一片重复粘贴 5 次不报错、不重复追加 |
| AC-05 | 缺失片检测 | `test_missing_part_detection`：跳过第 5 片时准确报告缺片，补发后完成 |
| AC-06 | 剪贴板干扰 | `test_non_protocol_text_ignored`：无关文本被忽略且不崩溃 |

## 目录结构

```text
.
├── clipfile/
│   ├── protocol.py        # 分片协议：构造、解析、CRC32
│   ├── sender_core.py     # 发送端核心：流式分片、进度持久化
│   ├── receiver_core.py   # 接收端核心：落盘、续传、合并、SHA-256
│   ├── clipboard.py       # tkinter 剪贴板适配
│   ├── gui_sender.py      # 发送端界面
│   └── gui_receiver.py    # 接收端界面
├── tests/
│   ├── test_protocol.py
│   └── test_transfer.py
├── docs/
│   └── 操作指南.md         # 使用教程、注意事项与故障排查
├── send_tool.py           # 发送端入口
├── recv_tool.py           # 接收端入口
├── build.bat              # Windows 单文件 exe 打包脚本
└── requirements.txt       # 运行时零第三方依赖，仅打包需要 PyInstaller
```

## 安全与限制

- 工具不发起任何网络请求，不访问云桌面以外的路径（发送端只读目标文件）。
- 协议头与 Base64 数据均为 ASCII，只使用纯文本剪贴板格式（CF_UNICODETEXT）。
- 接收端对文件名做路径穿越与非法字符清理，只保存到用户选择的目录。
- 若云桌面策略完全禁止复制纯文本，本方案无法工作。
- 剪贴板单次文本长度受云桌面策略限制（常见 1–4 MB），如遇截断请把分片大小调小（如 128 KB）。
- 100 MB 以上文件建议使用“自动连续发送 + 自动监听”，可显著减少人工操作。
