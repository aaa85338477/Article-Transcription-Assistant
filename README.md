# Article Transcription Assistant

一个面向游戏行业内容生产的 Streamlit 本地工作台。

它把素材抓取、知识增强、初稿生成、审稿修改、去 AI 味定稿、高亮阅读版、播客脚本、配图辅助、Word 导出、飞书发布和任务队列管理整合进了一套界面里，适合单人高频生产和多任务并行处理。

## 这是什么

这个项目的目标不是做一个“单次生成器”，而是做一个能连续处理多篇任务的本地内容生产台。

它尤其适合这些场景：

- 把海外游戏行业文章转成中文长文分析
- 把 YouTube 视频字幕整理成结构化稿件
- 把多篇网页、视频、上传文件合并成一篇专题稿
- 在同一套流程里同时产出正文、高亮阅读版、播客稿、音频和飞书文档
- 对同一篇文章做版本回溯、重试、去 AI 重写和最终发布

## 当前核心能力

### 1. 多源素材输入

支持把多种来源合并成一个 source packet：

- 文章 URL
- YouTube URL
- 本地上传文件
- 图片素材

文件侧目前支持：

- `doc`
- `docx`
- `xls`
- `xlsx`
- `txt`
- `md`
- `csv`
- `log`
- `json`
- 常见图片格式

抓取链路带有网页正文提取、YouTube 字幕提取和 fallback 抓取策略。

### 2. 两种工作模式

- 手动精调：逐步确认每一阶段结果
- 全自动驾驶：一路跑到定稿和交付区

### 3. 多阶段写作链路

当前主链路包括：

1. 素材提取与合并
2. 路由与写作角色选择
3. 初稿生成
4. 严格审稿与修改建议
5. 去 AI 味定稿
6. 交付与分发

### 4. 去 AI 味定稿

当前支持 4 种去 AI 变体：

- `普通版`
- `社区文章去AI版`
- `自然唠嗑版`
- `Humanizer-zh 版`

去 AI 阶段会输出三块结果：

- 备选标题组
- 纯净最终正文
- 高亮阅读版

### 5. 高亮阅读版

高亮阅读版现在是独立交付物，不是简单复制正文。

当前已经支持：

- 独立生成与展示
- 富文本复制
- 飞书兼容复制增强
- 高亮保真修复
- 仅重建高亮阅读版
- 与正文结构的一致性保护

### 6. 任务队列

项目已经从单任务工具升级成了本地任务队列。

当前支持：

- 多任务切换
- 复制当前任务为新任务
- 删除单个任务
- 按标题 / 站点搜索任务
- 批量清理已完成 / 失败任务
- 已完成任务自动归档
- 历史归档恢复
- 任务模板保存与复用

### 7. 版本时间线

每篇文章会记录版本节点，便于：

- 查看历史版本
- 恢复旧版正文
- 恢复旧版高亮阅读版
- 对照不同阶段输出

### 8. 本地知识增强

如果启用 Obsidian，本项目可以：

- 从本地知识库里检索相关笔记
- 生成研究摘要
- 构建影响映射
- 生成证据覆盖图

这部分主要用于辅助分析和追踪正文与知识库之间的关系，不替代原始素材事实源。

### 9. 下游产物与交付

当前可选的下游能力包括：

- 分镜脚本
- 单人播客解说稿
- 播客音频合成
- 智能配图助手
- Word 导出
- 飞书群推送
- 飞书云文档发布

## 典型工作流

### Step 1. 输入素材

把网页、视频、上传文件和图片统一收进来，系统会合并成一份可供写作的 source packet。

### Step 2. 选择模式

根据你的使用方式选择：

- 手动一步步走
- 自动跑完整链路

### Step 3. 初稿生成

编辑角色会生成：

- 标题组
- 正文草稿

### Step 4. 审稿与修改

审稿阶段会检查：

- 事实一致性
- 逻辑和论证
- 背景交代是否充分
- 标题结构和段落结构
- 词表风险和表达问题

### Step 5. 去 AI 味定稿

在这一阶段，系统会把文章整理成：

- 纯净定稿
- 高亮阅读版

并保留对应版本。

### Step 6. 交付与分发

最终工作台里可以完成：

- 查看和复制最终正文
- 查看和复制高亮阅读版
- 返回去 AI 步骤重试
- 仅重建高亮阅读版
- 生成播客稿与音频
- 提取配图关键词
- 导出 Word
- 发布到飞书云文档
- 推送到飞书群

## 飞书发布能力

项目当前已经内置飞书云文档发布链路。

### 已支持

- 创建飞书云文档
- 将文章正文写入飞书文档
- 在任务状态里记录飞书文档 URL
- 从交付区直接打开飞书文档

### 需要的配置

请在 `.streamlit/secrets.toml` 或等价的 Streamlit secrets 配置中提供：

```toml
FEISHU_APP_ID = "your_app_id"
FEISHU_APP_SECRET = "your_app_secret"
FEISHU_FOLDER_TOKEN = "optional_folder_token"
```

说明：

- `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET` 用于获取 tenant access token
- `FEISHU_FOLDER_TOKEN` 可选，用于指定新文档的目标文件夹

## 项目结构

```text
.
|-- app.py
|-- podcast_audio.py
|-- podcast_script.py
|-- prompts.json
|-- requirements.txt
|-- launch_article_tool.cmd
|-- launch_article_tool.ps1
|-- launch_article_assistant_new.cmd
|-- launch_article_assistant_new_silent.vbs
|-- tests/
|-- .streamlit/
|-- draft_state.json
|-- task_queue_state.json
|-- ai_diagnostics.log
`-- runtime_audio/
```

## 安装

```bash
pip install -r requirements.txt
```

## 运行

### 方式 1：直接运行 Streamlit

```bash
streamlit run app.py
```

### 方式 2：Windows 启动脚本

```powershell
.\launch_article_tool.ps1
```

或：

```cmd
launch_article_tool.cmd
```

## 依赖

当前主要依赖：

- `streamlit`
- `trafilatura`
- `youtube-transcript-api`
- `python-docx`
- `requests`
- `openai`
- `httpx`
- `beautifulsoup4`
- `pandas`
- `openpyxl`
- `xlrd`
- `dashscope`
- `pydub`
- `audioop-lts`
- `imageio-ffmpeg`

## Prompt 与配置

Prompt 配置文件是：

- [`prompts.json`](./prompts.json)

主要内容包括：

- `editors`
- `reviewer`
- `global_instruction`

你可以：

- 直接编辑 `prompts.json`
- 在应用内通过 Prompt 管理界面修改
- 用 `PROMPTS_FILE` 环境变量切换自定义 prompt 文件

## 本地持久化文件

应用运行时会维护这些状态文件：

- `draft_state.json`
- `task_queue_state.json`
- `ai_diagnostics.log`
- `runtime_audio/`

这些通常属于运行产物，不建议直接手工改写，也通常不应该进 Git。

## 测试

常用回归测试：

```bash
python -m unittest tests.test_prompt_structure tests.test_copy_formatting tests.test_article_versions tests.test_task_queue tests.test_article_extraction tests.test_feishu_docs
```

快速语法检查：

```bash
python -m py_compile app.py
```

## 适合谁

这套工具目前最适合：

- 高频写作和编发的单人作者
- 游戏行业分析 / 快讯 / 专题内容团队
- 需要“正文 + 高亮版 + 播客稿 + 飞书交付”一体化链路的人

如果你想把它当成一个稳定的本地生产台来用，建议优先维护好：

- Prompt 角色体系
- 飞书配置
- Obsidian 知识库路径
- 任务队列的归档与清理习惯

## 当前定位

这是一个 Streamlit 本地工作台，不是一个通用 CLI 抓取器，也不是一个纯 SDK 项目。

它的核心价值在于：把游戏内容生产链路里的高频步骤，放进一个能持续使用、能回滚、能多任务管理的本地面板里。
