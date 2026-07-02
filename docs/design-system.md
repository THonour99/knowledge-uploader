# 知识库平台 · 前端设计系统规范 v1.0

> 主题：**知识绿（Emerald）+ 暖灰（Stone）**
> 目标：**温暖、专注、可读**的知识库气质 ＋ **统一、专业、可落地**的产品级一致性。
> 技术落地：React 18 + Ant Design 5 + 原生 CSS 变量（`--ku-*`）。设计 token 单一源 = `frontend/src/theme/tokens.ts`。

---

## 一、设计原则（北极星）

1. **一致压倒个性** —— 同一种信息用同一种表达。宁可"无聊地统一"，不要"花哨地各异"。
2. **克制优先** —— 少即是多。默认不加装饰动画、不加多余渐变；强调靠留白和层级，不靠特效。
3. **层级清晰** —— 每个页面一眼能看出"主操作、主信息、次要信息"三层。
4. **温暖可读** —— 知识库是给人读内容的。用暖灰中性色 + 绿主色，长时间看不累。
5. **状态明确，语义不打架** —— 主色只承担"品牌/主操作"，绝不与"成功/警告/危险"状态色混用。

---

## 二、配色系统

### 2.1 主色 Primary（Emerald 翠绿 —— 知识、成长、专注）

| 档 | 色值 | 用途 |
|---|---|---|
| 50 | `#ECFDF5` | 极浅底 |
| 100 | `#D1FAE5` | 选中浅底 / brand-soft |
| 200 | `#A7F3D0` | |
| 300 | `#6EE7B7` | |
| 400 | `#34D399` | 深色模式 hover |
| 500 | `#10B981` | 深色模式主色 |
| **600** | **`#059669`** | **主色 · 按钮 / 链接 / 选中（浅色默认）** |
| 700 | `#047857` | hover / 主色文字 |
| 800 | `#065F46` | active 按下 |
| 900 | `#064E3B` | 深色 brand-soft 底 |

### 2.2 点缀色 Accent（Teal 青 —— 协调、少量点睛）

`#0D9488`（默认） / soft `#CCFBF1`。用于图表次色、次要强调、信息可视化。**用量 < 10%**。

### 2.3 语义色 Semantic（与主色拉开区分）

| 语义 | 主值 | 浅底 | 深文字 | 场景 |
|---|---|---|---|---|
| 成功 success | `#16A34A` | `#DCFCE7` | `#15803D` | 已通过 / 已同步 |
| 警告 warning | `#D97706` | `#FEF3C7` | `#B45309` | 待审 / 需关注 |
| 危险 danger | `#DC2626` | `#FEE2E2` | `#B91C1C` | 拒绝 / 失败 / 删除 |
| 信息 info | `#2563EB` | `#DBEAFE` | `#1D4ED8` | 处理中 / 同步中 |

> **关键规则**：成功用的绿（`#16A34A`，偏黄）与主色 emerald（`#059669`，偏青）**刻意做了区分**，并置可分辨。主色**不用于**表达"成功"。

### 2.4 中性色 Neutral（Stone 暖灰 —— 温度来源）

`50 #FAFAF9` · `100 #F5F5F4` · `200 #E7E5E4` · `300 #D6D3D1` · `400 #A8A29E` · `500 #78716C` · `600 #57534E` · `700 #44403C` · `800 #292524` · `900 #1C1917`

### 2.5 浅色主题语义层

| CSS 变量 | 色值 | 含义 |
|---|---|---|
| `--bg-base` | `#FAFAF9` | 页面底（暖白） |
| `--bg-card` | `#FFFFFF` | 卡片 / 浮层 |
| `--bg-subtle` | `#F5F5F4` | 次级底 / 表头 |
| `--text-primary` | `#1C1917` | 主文字 |
| `--text-secondary` | `#57534E` | 次文字 |
| `--text-tertiary` | `#A8A29E` | 占位 / 禁用 |
| `--border` | `#E7E5E4` | 常规边框 |
| `--border-strong` | `#D6D3D1` | 强边框 / 输入框 |
| `--brand` | `#059669` | 主色 |
| `--brand-hover` | `#047857` | 主色 hover |
| `--brand-soft` | `#D1FAE5` | 主色浅底 |

### 2.6 深色主题语义层（暖深，非纯黑）

| CSS 变量 | 色值 |
|---|---|
| `--bg-base` | `#1C1917` |
| `--bg-card` | `#292524` |
| `--bg-subtle` | `#44403C` |
| `--text-primary` | `#FAFAF9` |
| `--text-secondary` | `#A8A29E` |
| `--text-tertiary` | `#78716C` |
| `--border` | `#44403C` |
| `--border-strong` | `#57534E` |
| `--brand` | `#10B981`（提亮保对比度） |
| `--brand-hover` | `#34D399` |
| `--brand-soft` | `#064E3B` |

---

## 三、字体排印

### 3.1 字族

```
--font-sans: 'Inter', 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif;
--font-mono: 'JetBrains Mono', 'Fira Code', Consolas, monospace;
```

- **Inter** 负责英文 / 数字（数字、文件大小、统计值立刻精致）
- 中文回退 PingFang SC / 雅黑
- 等宽用于：密钥、ID、文件哈希、代码
- 建议开启 `font-feature-settings: 'cv11','ss01';`（Inter 更规整的字形）

### 3.2 字号阶（base = 14px，适配中文后台）

| Token | px | 行高 | 用途 |
|---|---|---|---|
| xs | 12 | 16 | 辅助 / 标签 |
| sm | 13 | 20 | 次要文字 |
| **base** | **14** | **22** | **正文默认** |
| md | 16 | 24 | 强调正文 / 小标题 |
| lg | 18 | 28 | 卡片标题 |
| xl | 20 | 30 | 页面区块标题 |
| 2xl | 24 | 32 | 页面主标题 |
| 3xl | 30 | 38 | KPI 大数字 |
| 4xl | 36 | 44 | 认证页品牌标题 |

### 3.3 字重

400 常规正文 · 500 中（次强调）· 600 半粗（标题 / 按钮 / 表头）· 700 粗（页面标题 / KPI）· 800 特粗（品牌）。

### 3.4 排版规则

- 正文行高 ≥ 1.5；标题行高 1.25–1.35
- 段落最大宽度 ≤ 72 字符（长文档场景）
- 数字统一用 Inter + `font-variant-numeric: tabular-nums`（表格数字对齐）

---

## 四、尺度系统

### 4.1 间距（4px 基准）

`2 · 4 · 6 · 8 · 12 · 16 · 20 · 24 · 32 · 40 · 48`

- 组件内元素间隙：6–8
- 卡片内边距：16–20
- 区块之间：16–24
- 页面外边距：20–24

### 4.2 圆角

| Token | px | 用途 |
|---|---|---|
| sm | 6 | 标签 / 小元素 |
| **md** | **8** | **控件：按钮 / 输入 / 下拉** |
| **lg** | **12** | **卡片 / 面板 / 弹窗** |
| xl | 16 | 大容器 / 特殊卡片 |
| full | 999 | 徽章 / 头像 / 状态胶囊 |

### 4.3 阴影（暖色投影，克制分层）

| Token | 值 | 用途 |
|---|---|---|
| xs | `0 1px 2px rgba(28,25,23,.04)` | 输入 / 极轻 |
| sm | `0 1px 3px rgba(28,25,23,.06), 0 1px 2px rgba(28,25,23,.04)` | 卡片默认 |
| md | `0 4px 12px rgba(28,25,23,.08)` | hover 抬升 / 下拉 |
| lg | `0 10px 30px rgba(28,25,23,.10)` | 弹窗 / 浮层 |

### 4.4 层级 z-index

`base 0 · dropdown 1000 · sticky 1020 · modal 1050 · popover 1060 · toast 1080`

---

## 五、组件规范

### 5.1 按钮 Button

| 变体 | 背景 | 文字 | 边框 | 场景 |
|---|---|---|---|---|
| primary | `--brand` | 白 | 无 | 页面唯一主操作 |
| secondary | `--bg-card` | 主文字 | `--border-strong` | 次操作 |
| danger | `#DC2626` | 白 | 无 | 删除 / 拒绝 |
| ghost | 透明 | 次文字 | 无 | 取消 / 低强调 |

- 高度：默认 36（`padding 8×16`）、大 40、小 30
- 圆角 8、字重 600、hover 用 `--brand-hover`、focus 有 2px ring（`rgba(5,150,105,.25)`）
- **一屏只允许一个 primary 按钮**

### 5.2 输入 Input / Select / DatePicker

高 36、圆角 8、边框 `--border-strong`、聚焦 2px 绿 ring + 边框透明化、占位 `--text-tertiary`。

### 5.3 卡片 Card / Panel

背景 `--bg-card`、圆角 12、边框 `--border`、阴影 sm、hover 抬升到 md。头部 `padding 12×16` + 底边框，体 `padding 16–20`。

### 5.4 徽章 / 状态标签 StatusTag

圆角 full 胶囊、`font-size 12 / weight 600`、`padding 2×9`、前置 5px 圆点。用"语义浅底 + 语义深文字"。映射见文档状态机（§5.8）。

### 5.5 表格 Table

- 表头：`--bg-subtle` 底、`--text-secondary`、13px、weight 600、`padding 10×12`
- 行：`padding 10×12`、底边框 `--border`、hover 行 `--bg-subtle`
- 数字列右对齐 + tabular-nums
- 操作列固定右侧，用 ghost 图标按钮

### 5.6 KPI 卡

图标方块（32×32，圆角 9，语义 soft 底 + 语义色图标）+ 大数字（3xl / 800）+ 标签（sm / 次文字）。可选迷你趋势 + 环比。

### 5.7 反馈态

- 空态：居中插画/图标 + 一句说明 + 一个引导按钮
- 加载：骨架屏（`--bg-subtle` + pulse），不用转圈遮罩
- 错误：`--danger-soft` 底卡片 + 重试按钮
- Toast：右上、圆角 8、阴影 lg、语义色左边条

### 5.8 文档状态机 → 状态标签色

| 状态 | 标签 | 色 |
|---|---|---|
| uploaded | 已上传 | info |
| extracting_text / analyzing | 抽取中 / AI分析中 | accent |
| analyzed | 分析完成 | accent |
| pending_review | 待审核 | warning |
| sensitive_review_required | 敏感审核 | danger |
| approved | 已通过 | success |
| rejected | 已拒绝 | danger |
| syncing / parsing | 同步中 / 解析中 | info |
| parsed（已同步） | 已同步 | success |
| failed | 失败 | danger |
| draft / disabled | 草稿 / 已禁用 | muted(中性) |

---

## 六、布局系统

### 6.1 应用外壳

```
┌────────┬──────────────────────────────┐
│ 侧边栏  │ 顶栏（56）                     │
│ (220)  ├──────────────────────────────┤
│        │ 内容区（padding 20–24）        │
└────────┴──────────────────────────────┘
```

- **侧边栏**：宽 220 / 收起 64；底 `--bg-card`；Logo（绿渐变方块）+ 分组菜单 + 底部服务状态；选中项 = `--brand-soft` 底 + 左 3px 绿条 + 绿字
- **顶栏**：高 56；`--bg-card` + 底边框；返回 + 页面标题 + 全局搜索 + 通知 + 用户
- **内容区**：`--bg-base` 底

### 6.2 栅格 & 响应式断点

12 列栅格，间距 16。断点：`sm 640 · md 768 · lg 1024 · xl 1280 · 2xl 1536`。≤1279 折叠侧边栏；≤1023 平板堆叠；≤640 单列。

### 6.3 页面模板（5 类）

1. **认证页**：居中单卡（max 440），底色与内页同系，Logo 渐变 + 光晕
2. **看板页**：KPI 行 + 图表/信息网格
3. **列表页**：页头 + 筛选工具栏 + 表格卡（+ 可选顶部统计条）
4. **详情页**：左主内容 + 右 sticky 侧栏
5. **表单/配置页**：分区卡片 + 两列表单网格

> 统一封装 `<PageContainer>`（面包屑 + 标题/描述/操作）与 `<CommandStrip>`（顶部统计条），替代现在各页各写的 5 种 strip。

---

## 七、图标 & 动效

- **图标**：线性风格，`stroke 1.5–2px`，尺寸 16/18/20；继承 `currentColor` 保证跟随语义色。建议统一用一套（如 lucide / Ant Design Icons 二选一）。
- **动效**：克制。仅保留 0.2–0.4s 的进入/过渡（fade / slide-up / scale-in）与 hover 抬升。**禁止无限循环装饰动画**（如旋转光环）。`prefers-reduced-motion` 下全部关闭。

---

## 八、落地映射

### 8.1 tokens.ts 结构（单一源）

```ts
export const colors = {
  brand: { 50:'#ECFDF5', 100:'#D1FAE5', 500:'#10B981', 600:'#059669', 700:'#047857', 800:'#065F46', 900:'#064E3B' },
  accent: '#0D9488',
  success:'#16A34A', warning:'#D97706', danger:'#DC2626', info:'#2563EB',
  stone: { 50:'#FAFAF9', 100:'#F5F5F4', 200:'#E7E5E4', 300:'#D6D3D1', 400:'#A8A29E', 500:'#78716C', 600:'#57534E', 700:'#44403C', 800:'#292524', 900:'#1C1917' },
} as const;

export const radius = { sm:6, md:8, lg:12, xl:16 } as const;
export const spacing = { /* 4px 基准阶 */ } as const;
export const shadow = { xs:'…', sm:'…', md:'…', lg:'…' } as const;
export const typography = { fontFamily:"'Inter','PingFang SC',…", fontMono:"'JetBrains Mono',…" } as const;
```

### 8.2 AntD 主题映射（ConfigProvider theme）

```ts
token: {
  colorPrimary: '#059669',
  colorSuccess: '#16A34A', colorWarning: '#D97706', colorError: '#DC2626', colorInfo: '#2563EB',
  colorBgLayout: '#FAFAF9', colorBgContainer: '#FFFFFF',
  colorText: '#1C1917', colorTextSecondary: '#57534E', colorBorder: '#E7E5E4',
  borderRadius: 8, borderRadiusLG: 12,
  fontFamily: "'Inter','PingFang SC','Microsoft YaHei',system-ui,sans-serif",
}
```

### 8.3 CSS 变量 & 深色

- 全部语义色以 `--ku-*` CSS 变量输出（§2.5 / 2.6）
- 深色模式：`darkMode: 'class'` 思路 —— 在 `<html>` 或根容器加 `.theme-dark`，覆盖同名 CSS 变量。第一阶段只实现浅色，变量结构预留深色（本规范已给全深色值）。

### 8.4 迁移策略（增量、低风险）

1. 先落 `tokens.ts` + AntD theme（改一处，全站换色）
2. 沉淀全局组件类（card / btn / badge / input），替代 2600 行一次性 CSS
3. 抽 `<CommandStrip>`、统一 `<KpiCard>`/`<StatusTag>`
4. 逐页迁移：认证页 → 员工 3 页 → 管理端
5. 第二阶段：接入深色模式

---

*本规范为 v1.0，随组件落地持续更新。设计 token 以 `frontend/src/theme/tokens.ts` 为唯一事实源。*
