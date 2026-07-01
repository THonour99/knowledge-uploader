# Login Page Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the information-dense login page with a warm, brand-focused auth experience — deep blue gradient with frosted glass orbs on the left, floating white card form on the right.

**Architecture:** Rewrite `AuthLayout.tsx` to strip all feature/status/preview markup and replace with brand-only left side + card wrapper right side. Rewrite all `.auth-*` CSS in `styles.css` with new gradient, orb, animation, and card styles. Minor text/prop changes in `Login/index.tsx`.

**Tech Stack:** React 18 + Ant Design 5 + CSS (no new dependencies)

## Global Constraints

- Do not modify theme tokens (`tokens.ts`, `antd-theme.ts`)
- Do not modify Register, ForgotPassword, or ResetPassword pages (they share AuthLayout and inherit changes automatically)
- Preserve all existing non-auth CSS in `styles.css` and its media queries
- All animations must respect `prefers-reduced-motion: reduce`
- Use existing CSS variables (`--ku-color-primary`, `--ku-radius-control`, etc.) where applicable
- Frontend lint: Prettier line width 100, ESLint strict TS

---

### Task 1: Rewrite AuthLayout component and update Login page

**Files:**
- Modify: `frontend/src/pages/AuthLayout.tsx` (full rewrite, lines 1-172)
- Modify: `frontend/src/pages/Login/index.tsx` (lines 1-99, minor changes)

**Interfaces:**
- Consumes: Ant Design `Typography`, `@ant-design/icons` `DatabaseOutlined`
- Produces: `AuthLayout` component with same props signature `{ title, description, children, footer }` — all consumers (Login, Register, ForgotPassword, ResetPassword) continue working unchanged

- [ ] **Step 1: Rewrite AuthLayout.tsx**

Replace the entire file content with:

```tsx
import type { ReactNode } from "react";
import { DatabaseOutlined } from "@ant-design/icons";
import { Typography } from "antd";

interface AuthLayoutProps {
  title: string;
  description: string;
  children: ReactNode;
  footer?: ReactNode;
}

export function AuthLayout({ title, description, children, footer }: AuthLayoutProps) {
  return (
    <main className="auth-page">
      <section className="auth-hero" aria-label="品牌展示">
        <div className="auth-orb auth-orb--1" />
        <div className="auth-orb auth-orb--2" />
        <div className="auth-orb auth-orb--3" />

        <div className="auth-brand">
          <span className="auth-brand__mark">
            <DatabaseOutlined />
          </span>
          <span className="auth-brand__name">知识库贡献平台</span>
          <span className="auth-brand__tagline">让企业知识持续沉淀与同步</span>
        </div>
      </section>

      <section className="auth-panel" aria-label={title}>
        <div className="auth-card">
          <div className="auth-card__icon">
            <DatabaseOutlined />
          </div>
          <Typography.Title level={3} className="auth-card__title">
            {title}
          </Typography.Title>
          <Typography.Paragraph className="auth-card__desc">{description}</Typography.Paragraph>
          {children}
          {footer ? <div className="auth-card__footer">{footer}</div> : null}
        </div>
      </section>
    </main>
  );
}
```

Key changes from the old version:
- Removed all imports: `BarChartOutlined`, `CheckCircleOutlined`, `CloudUploadOutlined`, `FileTextOutlined`, `RobotOutlined`, `SafetyCertificateOutlined`, `StatusTag`
- Removed `authFeatures` array (4 feature cards)
- Removed `authStatusItems` array (4 status items)
- Removed entire auth-status-strip section
- Removed entire auth-feature-list section
- Removed entire auth-preview mock dashboard section
- Removed hero copy section (duplicate title/subtitle)
- Added 3 `.auth-orb` divs for CSS animated background orbs
- Brand moved to bottom-left (`auth-brand`) with name + tagline
- Right panel now wraps children in `.auth-card` with icon + title + description inside the card
- Props interface unchanged — all consuming pages continue working

- [ ] **Step 2: Update Login/index.tsx**

Remove `LockOutlined` and `MailOutlined` imports. Remove `prefix` props from both inputs. Update title and description text:

```tsx
import { App as AntdApp, Button, Checkbox, Form, Input, Typography } from "antd";
import type { CheckboxChangeEvent } from "antd/es/checkbox";
import { useMutation } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";

import { login } from "../../api/client";
import { defaultRouteForRole, useAuthStore } from "../../store/auth.store";
import { AuthLayout } from "../AuthLayout";

interface LoginFormValues {
  email: string;
  password: string;
  remember?: boolean;
}

export default function LoginPage() {
  const navigate = useNavigate();
  const { message } = AntdApp.useApp();
  const setSession = useAuthStore((state) => state.setSession);
  const [form] = Form.useForm<LoginFormValues>();

  const mutation = useMutation({
    mutationFn: (values: LoginFormValues) =>
      login({
        email: values.email,
        password: values.password,
        remember_me: Boolean(values.remember),
      }),
    onSuccess: (session) => {
      setSession(session.access_token, session.user);
      navigate(defaultRouteForRole[session.user.role], { replace: true });
    },
    onError: (error) => {
      message.error(error.message);
    },
  });

  const handleRememberChange = (event: CheckboxChangeEvent) => {
    form.setFieldValue("remember", event.target.checked);
  };

  return (
    <AuthLayout
      title="欢迎回来"
      description="使用公司邮箱登录知识库工作台"
      footer={
        <Typography.Text type="secondary">
          还没有账号？ <Link to="/register">立即注册</Link>
        </Typography.Text>
      }
    >
      <Form<LoginFormValues>
        form={form}
        className="auth-form"
        layout="vertical"
        initialValues={{ remember: true }}
        onFinish={(values) => mutation.mutate(values)}
        requiredMark={false}
      >
        <Form.Item
          label="公司邮箱"
          name="email"
          rules={[
            { required: true, message: "请输入公司邮箱" },
            { type: "email", message: "请输入有效邮箱" },
          ]}
        >
          <Input placeholder="name@company.com" autoComplete="email" size="large" />
        </Form.Item>

        <Form.Item label="密码" name="password" rules={[{ required: true, message: "请输入密码" }]}>
          <Input.Password placeholder="请输入密码" autoComplete="current-password" size="large" />
        </Form.Item>

        <div className="auth-form-row">
          <Form.Item name="remember" valuePropName="checked" noStyle>
            <Checkbox onChange={handleRememberChange}>记住我</Checkbox>
          </Form.Item>
          <Link to="/forgot-password">忘记密码</Link>
        </div>

        <Button type="primary" htmlType="submit" size="large" block loading={mutation.isPending}>
          登录
        </Button>
      </Form>
    </AuthLayout>
  );
}
```

Changes from the old version:
- Removed `LockOutlined`, `MailOutlined` imports (line 1)
- Removed `prefix={<MailOutlined />}` from email Input (line 74)
- Removed `prefix={<LockOutlined />}` from password Input (line 82)
- Title: `"欢迎登录"` → `"欢迎回来"` (line 45)
- Description: `"使用公司邮箱登录，进入知识库贡献与审核工作台"` → `"使用公司邮箱登录知识库工作台"` (line 46)

- [ ] **Step 3: Verify TypeScript compiles**

Run: `npx --prefix frontend tsc --noEmit`
Expected: No errors. The AuthLayout export signature is unchanged, so Register/ForgotPassword/ResetPassword pages continue working.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/AuthLayout.tsx frontend/src/pages/Login/index.tsx
git commit -m "refactor(auth): simplify AuthLayout and update Login page text"
```

---

### Task 2: Replace auth CSS with new design

**Files:**
- Modify: `frontend/src/styles.css` (replace lines 1900-2295 auth styles + update auth-related rules in media queries at lines 2313-2354 and 2518-2536)

**Interfaces:**
- Consumes: CSS class names from Task 1's new AuthLayout markup: `.auth-page`, `.auth-hero`, `.auth-orb`, `.auth-orb--1/2/3`, `.auth-brand`, `.auth-brand__mark`, `.auth-brand__name`, `.auth-brand__tagline`, `.auth-panel`, `.auth-card`, `.auth-card__icon`, `.auth-card__title`, `.auth-card__desc`, `.auth-card__footer`, `.auth-form`, `.auth-form-grid`, `.auth-form-row`
- Produces: Complete visual implementation of the login page redesign spec

- [ ] **Step 1: Replace main auth styles (lines 1900-2295)**

Delete everything from `.auth-page {` (line 1900) through `.auth-panel__footer { ... }` closing brace (line 2294). Replace with the new auth CSS block below. The line just before (1899) should be a blank line after the previous rule; the line just after the old block (2295) is a blank line before the `@media (max-width: 1439px)` query — preserve that boundary.

New CSS to insert at line 1900:

```css
/* ── Auth page ── */

@keyframes auth-float-1 {
  0%, 100% { transform: translate(0, 0); }
  50% { transform: translate(30px, -20px); }
}

@keyframes auth-float-2 {
  0%, 100% { transform: translate(0, 0); }
  50% { transform: translate(-20px, 30px); }
}

@keyframes auth-float-3 {
  0%, 100% { transform: translate(0, 0); }
  50% { transform: translate(20px, 25px); }
}

@keyframes auth-card-enter {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.auth-page {
  display: grid;
  min-height: 100vh;
  grid-template-columns: minmax(400px, 1.2fr) minmax(380px, 0.8fr);
}

.auth-hero {
  position: relative;
  display: flex;
  min-height: 100vh;
  align-items: flex-end;
  overflow: hidden;
  background: linear-gradient(135deg, #1a3a6e 0%, #2563eb 50%, #7c93e0 100%);
}

.auth-orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(70px);
  animation-timing-function: ease-in-out;
  animation-iteration-count: infinite;
}

.auth-orb--1 {
  top: -80px;
  right: -60px;
  width: 350px;
  height: 350px;
  background: rgba(99, 102, 241, 0.3);
  animation: auth-float-1 15s infinite;
}

.auth-orb--2 {
  top: 35%;
  right: 10%;
  width: 280px;
  height: 280px;
  background: rgba(56, 189, 248, 0.25);
  animation: auth-float-2 18s infinite;
}

.auth-orb--3 {
  bottom: -40px;
  left: -60px;
  width: 320px;
  height: 320px;
  background: rgba(59, 130, 246, 0.35);
  animation: auth-float-3 20s infinite;
}

.auth-brand {
  position: absolute;
  bottom: 48px;
  left: 48px;
  z-index: 1;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.auth-brand__mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  margin-bottom: 6px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.15);
  color: #fff;
  font-size: 22px;
}

.auth-brand__name {
  color: #fff;
  font-size: 16px;
  font-weight: 700;
}

.auth-brand__tagline {
  color: rgba(255, 255, 255, 0.65);
  font-size: 14px;
}

.auth-panel {
  display: flex;
  min-height: 100vh;
  align-items: center;
  justify-content: center;
  padding: 40px;
  background: #f8fafc;
}

.auth-card {
  width: 100%;
  max-width: 440px;
  padding: 40px;
  border-radius: 20px;
  background: #fff;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.12);
  text-align: center;
  animation: auth-card-enter 0.3s ease-out both;
}

.auth-card__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  margin: 0 auto 16px;
  border-radius: 12px;
  background: var(--ku-color-primary);
  color: #fff;
  font-size: 22px;
}

.auth-card__title.ant-typography {
  margin: 0 0 6px;
  color: #0f172a;
  font-size: 24px;
  font-weight: 700;
}

.auth-card__desc.ant-typography {
  margin: 0 0 32px;
  color: var(--ku-text-secondary);
  font-size: 14px;
}

.auth-form {
  width: 100%;
  text-align: left;
}

.auth-form .ant-input-affix-wrapper,
.auth-form .ant-input,
.auth-form .ant-input-password {
  height: 48px;
  border-radius: 10px;
}

.auth-form .ant-input-affix-wrapper:focus,
.auth-form .ant-input-affix-wrapper-focused,
.auth-form .ant-input:focus {
  box-shadow: 0 0 0 3px rgba(22, 119, 255, 0.1);
}

.auth-form .ant-btn-primary {
  height: 48px;
  border-radius: 10px;
  font-weight: 600;
}

.auth-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.auth-form-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 20px;
}

.auth-card__footer {
  margin-top: 24px;
  padding-top: 20px;
  border-top: 1px solid var(--ku-border);
  text-align: center;
}

@media (prefers-reduced-motion: reduce) {
  .auth-orb {
    animation: none;
  }

  .auth-card {
    animation: none;
  }
}
```

- [ ] **Step 2: Replace auth rules in the 1023px media query (lines 2313-2354)**

Within the `@media (max-width: 1023px)` block, find and replace only the auth-related rules. The old rules are:

```css
  .auth-page {
    grid-template-columns: minmax(0, 1fr);
  }

  .auth-hero {
    min-height: auto;
    padding: 32px 24px;
  }

  .auth-brand {
    position: static;
  }

  .auth-hero__copy {
    margin-top: 32px;
  }

  .auth-hero__title.ant-typography {
    font-size: 36px;
  }

  .auth-preview {
    display: none;
  }

  .auth-status-strip {
    max-width: none;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .auth-panel {
    min-height: auto;
    padding: 32px 24px 48px;
    border-left: 0;
  }

  .auth-form,
  .auth-panel__heading,
  .auth-panel__footer {
    max-width: none;
  }
```

Replace with:

```css
  .auth-page {
    grid-template-columns: minmax(0, 1fr);
  }

  .auth-hero {
    display: none;
  }

  .auth-panel {
    min-height: 100vh;
    background: linear-gradient(135deg, #eef5ff, #f8fafc);
  }

  .auth-panel::before {
    content: "";
    position: absolute;
    top: 24px;
    left: 24px;
    display: flex;
    align-items: center;
    gap: 10px;
  }
```

- [ ] **Step 3: Replace auth rules in the 640px media query (lines 2518-2536)**

Within the `@media (max-width: 640px)` block, find and replace only the auth-related rules. The old rules are:

```css
  .auth-feature-list,
  .auth-status-strip,
  .auth-form-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .auth-hero__title.ant-typography {
    font-size: 30px;
  }

  .auth-hero__subtitle.ant-typography {
    font-size: 17px;
  }

  .auth-form-row {
    align-items: flex-start;
    flex-direction: column;
  }
```

Replace with:

```css
  .auth-card {
    padding: 24px;
    border-radius: 16px;
  }

  .auth-form-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .auth-form-row {
    align-items: flex-start;
    flex-direction: column;
  }

  .auth-panel {
    padding: 24px;
  }
```

- [ ] **Step 4: Verify build passes**

Run: `npx --prefix frontend tsc --noEmit`
Expected: No errors.

- [ ] **Step 5: Start dev server and visually verify**

Run: `npm --prefix frontend run dev`

Open `http://localhost:5173/login` and verify:
1. Left side: deep blue gradient with 3 floating orbs animating slowly, brand logo + text at bottom-left
2. Right side: light gray background with centered white card (rounded corners, shadow)
3. Card contains: blue brand icon → "欢迎回来" title → subtitle → email input (no icon prefix) → password input (no icon prefix) → remember/forgot row → blue login button → register link
4. Resize to < 1024px: left side disappears, card centered on light gradient background
5. Resize to < 640px: card padding shrinks, register form grid stacks to single column
6. Open `/register`: same layout, form works correctly with 2-column grid for name/department
7. Open `/forgot-password`: same layout, single email field
8. Check `prefers-reduced-motion`: orbs and card entrance animation should be disabled

- [ ] **Step 6: Commit**

```bash
git add frontend/src/styles.css
git commit -m "style(auth): redesign login page with gradient background and floating card"
```
