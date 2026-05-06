---
version: alpha
name: Apple Workbench
description: A high-clarity editorial production workspace inspired by Apple.com. The interface combines a dark utility sidebar, a cinematic hero panel, pale glass cards, generous white space, and a single Apple-blue interaction accent. It is optimized for dense Chinese writing workflows rather than commerce.

colors:
  canvas: "#f5f5f7"
  canvas-soft: "#fbfbfd"
  panel: "rgba(255, 255, 255, 0.92)"
  panel-strong: "rgba(255, 255, 255, 0.98)"
  panel-dark: "#111114"
  text: "#1d1d1f"
  text-muted: "#6e6e73"
  border: "rgba(29, 29, 31, 0.08)"
  border-strong: "rgba(29, 29, 31, 0.14)"
  brand: "#0071e3"
  brand-strong: "#0066cc"
  brand-soft: "rgba(0, 113, 227, 0.10)"
  success-soft: "rgba(52, 199, 89, 0.12)"
  on-dark: "#f5f5f7"

typography:
  hero-display:
    fontFamily: "SF Pro Display, Microsoft YaHei UI, PingFang SC, Noto Sans SC, sans-serif"
    fontSize: 64px
    fontWeight: 600
    lineHeight: 1.04
    letterSpacing: -0.04em
  section-display:
    fontFamily: "SF Pro Display, Microsoft YaHei UI, PingFang SC, Noto Sans SC, sans-serif"
    fontSize: 34px
    fontWeight: 700
    lineHeight: 1.08
    letterSpacing: -0.03em
  body:
    fontFamily: "SF Pro Text, Microsoft YaHei UI, PingFang SC, Noto Sans SC, sans-serif"
    fontSize: 17px
    fontWeight: 400
    lineHeight: 1.68
    letterSpacing: 0
  body-muted:
    fontFamily: "SF Pro Text, Microsoft YaHei UI, PingFang SC, Noto Sans SC, sans-serif"
    fontSize: 16px
    fontWeight: 400
    lineHeight: 1.68
    letterSpacing: 0
  caption:
    fontFamily: "SF Pro Text, Microsoft YaHei UI, PingFang SC, Noto Sans SC, sans-serif"
    fontSize: 13px
    fontWeight: 600
    lineHeight: 1.5
    letterSpacing: 0.08em
  button:
    fontFamily: "SF Pro Text, Microsoft YaHei UI, PingFang SC, Noto Sans SC, sans-serif"
    fontSize: 16px
    fontWeight: 600
    lineHeight: 1
    letterSpacing: 0

rounded:
  sm: 16px
  md: 22px
  lg: 28px
  xl: 32px
  pill: 9999px

spacing:
  xs: 8px
  sm: 12px
  md: 16px
  lg: 24px
  xl: 32px
  xxl: 48px
  section: 64px

components:
  sidebar-shell:
    backgroundColor: "{colors.panel-dark}"
    textColor: "{colors.on-dark}"
    rounded: "{rounded.none}"
    note: "Dark utility rail for model, prompt, Obsidian and podcast controls."
  hero-panel:
    backgroundColor: "{colors.panel-dark}"
    textColor: "{colors.on-dark}"
    rounded: "{rounded.xl}"
    note: "Large statement panel with minimal copy and three concise feature cards."
  stepper-card:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    note: "Flat Apple-style workflow card with subtle active state."
  section-intro:
    backgroundColor: transparent
    textColor: "{colors.text}"
    rounded: "{rounded.none}"
    note: "Eyebrow + title + subtitle stack for each main workflow area."
  context-strip:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.text-muted}"
    rounded: "{rounded.md}"
    note: "Compact contextual metadata row for tasks and filters."
  status-chip:
    backgroundColor: "{colors.brand-soft}"
    textColor: "{colors.brand-strong}"
    rounded: "{rounded.pill}"
    note: "Lightweight pill for active state and metadata."
  primary-button:
    backgroundColor: "{colors.brand}"
    textColor: "#ffffff"
    rounded: "{rounded.pill}"
    note: "Only primary action in each local action group should use blue."
  secondary-button:
    backgroundColor: "{colors.panel-strong}"
    textColor: "{colors.text}"
    rounded: "{rounded.pill}"
    note: "Neutral button style for non-destructive actions."
  task-queue-panel:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.text}"
    rounded: "{rounded.lg}"
    note: "Primary queue shell; keep stats, search, switching and cleanup on one calm card with compact expander rails."
  task-template-panel:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.text}"
    rounded: "{rounded.lg}"
    note: "Collapsed utility zone that should remain visually secondary."
  article-preview-panel:
    backgroundColor: "{colors.panel-strong}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    note: "Readable preview shell for extracted source content and drafts."
  highlighted-article-panel:
    backgroundColor: "{colors.panel-strong}"
    textColor: "{colors.text}"
    rounded: "{rounded.lg}"
    note: "Primary output panel for richly formatted highlighted reading mode."
  evidence-map-panel:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.text}"
    rounded: "{rounded.lg}"
    note: "Supportive verification surface; never compete with final article panel."
  obsidian-panel:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.text}"
    rounded: "{rounded.lg}"
    note: "Auxiliary knowledge context panel; keep collapsed or visually recessed."
  delivery-workbench:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.text}"
    rounded: "{rounded.xl}"
    note: "Final-step workbench with a dominant primary-output rail on the left and support surfaces on the right."
  feishu-publish-card:
    backgroundColor: "{colors.panel-strong}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    note: "Status-forward card for cloud document publishing and result feedback."
---

# Apple 化内容生产工作台规范

## 1. 设计目标

这套界面不是营销站，也不是传统后台。它的目标是把高频写作、审稿、定稿和分发过程，包装成一种接近 Apple 官网的高端工作台体验：

- 第一眼是克制、清晰、昂贵感，而不是“控制台堆满工具”
- 信息密度依然很高，但通过留白、圆角和层级消化复杂度
- 深色只用于侧栏和 Hero 这类“容器型区域”
- 真正需要长时间阅读和操作的地方，必须回到高对比浅底

## 2. 页面结构原则

主界面采用四层骨架：

1. 深色侧栏  
   负责模型、Prompt、Obsidian、播客等配置，不承担主阅读任务。

2. Hero 宣言区  
   负责定义产品气质，只保留一句大标题、一段简短导语和三张功能概览卡。

3. 流程卡区  
   用扁平 Apple 式卡片表达 Step 1-5 的线性关系，不做复杂状态仪表盘。

4. 当前工作区  
   任务队列、当前 Step 面板、Step 6 交付区按大卡片组织，确保长文本和复杂操作有清晰边界。

## 3. 视觉层级

### 3.1 深浅使用规则

- 近黑深灰只用于：
  - 侧栏
  - Hero
  - 少量反白说明块
- 白色和极浅灰是主要工作底色：
  - 任务队列
  - 表单
  - 高亮阅读版
  - 交付工作台
  - 归档与模板折叠区

### 3.2 强调色规则

- Apple 蓝是唯一主交互色。
- 主按钮、活动态 chip、焦点边框可以使用蓝色。
- 不要同时引入第二种竞争型品牌色。
- 成功、完成、归档等状态可以使用非常轻的辅助底色，但不能喧宾夺主。

### 3.3 阴影规则

- 阴影只用于建立层次，不用于制造“悬浮玩具感”。
- 侧栏本身不靠阴影取胜，主要靠深色容器分区。
- 白卡只允许使用软阴影，避免厚重投影。

## 4. 中文工作台的排版约束

### 4.1 标题

- Hero 标题要大、紧、短，像产品宣言。
- Section 标题要明显大于正文，但不能像后台系统那样层级混乱。
- 中文标题优先依赖字重与字号，不依赖花哨装饰。

### 4.2 正文与说明

- 正文说明统一保持浅灰次级文本，而不是偏绿色或偏蓝灰。
- 辅助说明不能过长；一段说明最好服务一个动作。
- 中文长文本区域必须保持高对比和足够行距，避免“白字浮在浅底上”。

### 4.3 输入与占位文本

- 所有输入框、筛选框、placeholder 在浅底上都必须维持可读性。
- placeholder 可以弱，但不能弱到看不清。

## 5. 组件规范

### 5.1 Sidebar Shell

- 视觉角色：工具配置层
- 背景：近黑深灰
- 文本：高亮白字 + 浅灰辅助文案
- 目标：压缩视觉体积，但保留功能密度

### 5.2 Hero Panel

- 视觉角色：整页主声明
- 背景：深色大面板
- 内容：一个 kicker、一个大标题、一段简短导语、三张摘要卡
- 禁止：像后台一样堆大量说明和控制

### 5.3 Stepper Card

- 视觉角色：主流程导航
- 每张卡只能包含：
  - 步骤编号
  - 短标题
  - 一行描述
- 当前步骤通过轻微蓝色边框和更亮白底来区分

### 5.4 Task Queue Panel

- 视觉角色：当前工作上下文的调度中心
- 顶部统计应轻量、整齐，不要做厚重仪表板
- 搜索、筛选、切换、清理应该按照一条主操作逻辑排列

### 5.5 Highlighted Article Panel

- 视觉角色：Step 6 中最重要的可读输出
- 应该比辅助区更大、更中心、更干净
- 高亮样式要精确，不能为追求“效果”牺牲内容完整性

### 5.6 Delivery Workbench

- 视觉角色：最后一步的操作总台
- 复制、导出、飞书发布、配图、播客都在这里发生
- 主操作必须先于辅助操作出现
- 操作分组应该有明显节奏，不能像普通工具栏平铺

## 6. 信息优先级规则

在任何页面内，优先级排序固定如下：

1. 当前步骤的核心输入或输出
2. 当前任务上下文
3. 决策所需的辅助证据
4. 附加能力，如配图、播客、知识增强
5. 历史归档、模板和管理性动作

这条规则的目标是：苹果化不能牺牲实用性。

## 7. 响应式与窄宽度策略

- 以桌面工作台优先。
- 窄宽度下允许：
  - Step 卡换行
  - 任务队列搜索与筛选改为上下堆叠
  - 高亮阅读版与交付区改为单列
- 侧栏保留，但在较窄宽度下应减少视觉压迫。

## 8. 可用性底线

以下问题绝对不能出现：

- 浅底文字发白，导致看不清
- 图标字体被全局中文字体覆盖
- 复选框、筛选器、placeholder 对比度不足
- 高亮阅读版为了视觉效果牺牲正文完整性
- 任务队列和交付区因为苹果化而失去可操作性

## 9. 落地准则

后续所有前端修改都应优先问这三个问题：

1. 这是不是让页面更像 Apple 的高端产品界面？
2. 这是不是同时保住了中文长文本工具的可读性？
3. 这是不是让主流程更清楚，而不是更花哨？

只有同时满足这三条，才算符合本项目的 Apple 化设计方向。
