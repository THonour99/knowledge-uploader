# 04. 前端开发规范

## 1. 前端技术栈

```text
React
TypeScript
Ant Design
React Router
Axios
Zustand 或 Redux Toolkit
ECharts
```

---

## 2. 页面结构

```text
src/
  pages/
    Login/
    Register/
    ForgotPassword/
    ResetPassword/
    Dashboard/
    Upload/
    MyFiles/
    FileManagement/
    FileDetail/
    DatasetConfig/
    AiConfig/
    AiProviders/
    PromptTemplates/
    SensitiveRules/
    Statistics/
    Users/
    SystemConfig/
  components/
  api/
  store/
  router/
  types/
  utils/
```

---

## 3. 页面清单

### 3.1 登录页

字段：

- 邮箱
- 密码

入口：

- 注册
- 忘记密码

### 3.2 注册页

字段：

- 姓名
- 公司邮箱
- 密码
- 确认密码
- 部门，可选
- 手机号，可选

提示：

```text
仅支持公司邮箱注册。
```

### 3.3 忘记密码页

流程：

```text
输入邮箱 → 邮件已发送提示 → 重置密码 → 重置成功
```

提示必须避免泄露邮箱是否存在：

```text
如果该邮箱已注册，我们会发送一封密码重置邮件。
```

### 3.4 首页 / 仪表盘

显示：

- 总上传文件数
- 已同步文件数
- 解析成功数量
- 解析失败数量
- 待审核数量
- 今日上传
- 本周上传
- 本月上传
- 同步成功率
- 最近上传记录
- 最近失败任务

### 3.5 文件上传页

功能：

- 拖拽上传
- 多文件上传
- 上传进度
- 分类选择
- 标签输入
- 可见范围选择
- 目标 Dataset 选择
- 是否立即同步

### 3.6 我的文件页

字段：

- 文件名
- 分类
- 上传时间
- 审核状态
- 同步状态
- RAGFlow 解析状态
- 操作按钮

### 3.7 管理员文件管理页

功能：

- 查看全部文件
- 多条件筛选
- 审核
- 修改分类
- 修改 Dataset
- 手动同步
- 失败重试
- 禁用文件
- 删除文件

### 3.8 文件详情页

展示：

- 文件基本信息
- 上传人
- hash
- 文件大小
- MinIO object key
- 分类
- 标签
- RAGFlow dataset_id
- RAGFlow document_id
- AI 分析是否执行
- AI 摘要
- AI 推荐分类
- AI 推荐标签
- 敏感风险
- 同步日志
- 审计日志

### 3.9 统计分析页

图表：

- 用户上传排行
- 部门上传分布
- 分类占比
- 上传趋势
- 同步成功率
- 失败任务统计

支持导出 CSV / Excel。

### 3.10 AI 配置页

配置：

- AI 总开关
- 摘要开关
- 自动分类开关
- 标签开关
- 敏感检测开关
- 外部模型开关
- AI 失败是否允许继续审核

### 3.11 模型供应商页

功能：

- 新增供应商
- 编辑供应商
- 禁用供应商
- 测试连接
- API Key 脱敏显示

### 3.12 Prompt 模板页

功能：

- 查看默认模板
- 编辑模板
- 测试 Prompt
- 恢复默认模板

---

## 4. 前端权限控制

前端根据角色控制菜单显示：

| 角色 | 菜单 |
|---|---|
| employee | 上传、我的文件、个人统计 |
| knowledge_admin | 文件管理、审核、统计、失败任务 |
| system_admin | 全部菜单 |

注意：前端隐藏不等于权限控制，后端必须再次校验。

---

## 5. 状态显示规范

| 状态 | 中文 | 建议颜色 |
|---|---|---|
| uploaded | 已上传 | 蓝色 |
| pending_review | 待审核 | 橙色 |
| analyzing | AI 分析中 | 紫色 |
| sensitive_review_required | 敏感审核 | 红色 |
| approved | 已审核 | 绿色 |
| syncing | 同步中 | 青色 |
| parsing | 解析中 | 青色 |
| parsed | 解析完成 | 绿色 |
| failed | 失败 | 红色 |
| disabled | 已禁用 | 灰色 |

---

## 6. 交互要求

- 上传页面必须显示每个文件进度。
- 长任务不阻塞页面。
- 文件状态通过轮询或刷新接口更新。
- 失败信息要可读，但不能暴露密钥或服务器敏感路径。
- 管理员高风险操作需要二次确认。
- 导出统计需要记录审计日志。
