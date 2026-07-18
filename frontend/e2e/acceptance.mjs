#!/usr/bin/env node

import { mkdir, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173";
const acceptanceMode = process.env.E2E_ACCEPTANCE_MODE?.trim() || "smoke";
const artifactDir = process.env.E2E_ARTIFACT_DIR?.trim();
const protectedAcceptance = acceptanceMode === "protected";

if (!["smoke", "protected"].includes(acceptanceMode)) {
  throw new Error(`Unknown E2E_ACCEPTANCE_MODE: ${acceptanceMode}`);
}

if (protectedAcceptance) {
  if (!artifactDir) {
    throw new Error("Protected acceptance requires E2E_ARTIFACT_DIR");
  }
  if (!path.isAbsolute(artifactDir)) {
    throw new Error("E2E_ARTIFACT_DIR must be an absolute path");
  }
  const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
  const relativeArtifactPath = path.relative(repoRoot, artifactDir);
  const artifactInsideRepo =
    relativeArtifactPath === "" ||
    (!relativeArtifactPath.startsWith("..") && !path.isAbsolute(relativeArtifactPath));
  if (artifactInsideRepo) {
    throw new Error("Protected acceptance artifacts must be stored outside the repository");
  }
}

async function loadPlaywright() {
  try {
    return await import("@playwright/test");
  } catch {
    try {
      return await import("playwright");
    } catch {
      return null;
    }
  }
}

function jsonResponse(data) {
  return {
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ success: true, data, message: "ok" }),
  };
}

async function expectVisible(locator, label) {
  try {
    await locator.first().waitFor({ state: "visible", timeout: 5_000 });
  } catch (error) {
    const diagnostics = runtimeErrors.length > 0 ? `; ${runtimeErrors.join(" | ")}` : "";
    throw new Error(`Expected visible: ${label}${diagnostics}`, { cause: error });
  }
}

const playwright = await loadPlaywright();
if (!playwright?.chromium) {
  console.error(
    [
      "Missing Playwright runtime.",
      "Install it only when browser E2E is needed:",
      "  npm --prefix frontend install --save-dev @playwright/test",
      "  npx playwright install chromium",
    ].join("\n"),
  );
  process.exit(1);
}

const browser = await playwright.chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
const runtimeErrors = [];
const runtimeWarnings = [];
page.on("request", (request) => {
  const pathname = new URL(request.url()).pathname;
  const publicSystemEndpoint =
    pathname === "/api/system/health" || pathname === "/api/system/ready";
  if (
    pathname.startsWith("/api/") &&
    !publicSystemEndpoint &&
    request.headers().authorization !== "Bearer e2e-token"
  ) {
    runtimeErrors.push(`missing system-admin bearer: ${request.method()} ${pathname}`);
  }
});

page.on("pageerror", (error) => {
  runtimeErrors.push(`pageerror: ${error.message}`);
});
page.on("console", (message) => {
  if (message.type() === "error") {
    runtimeErrors.push(`console: ${message.text()}`);
  } else if (message.type() === "warning") {
    runtimeWarnings.push(`console: ${message.text()}`);
  }
});
page.on("response", (response) => {
  if (response.status() >= 400) {
    runtimeErrors.push(`http ${response.status()}: ${new URL(response.url()).pathname}`);
  }
});

await page.route("**/api/system/health", async (route) => {
  await route.fulfill(jsonResponse({ status: "ok" }));
});

await page.route("**/api/system/ready", async (route) => {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      status: "ok",
      dependencies: {
        database: { status: "ok" },
        redis: { status: "ok" },
        rabbitmq: { status: "ok" },
        minio: { status: "ok" },
      },
    }),
  });
});

await page.route("**/api/auth/me", async (route) => {
  await route.fulfill(
    jsonResponse({
      id: "admin-e2e",
      name: "E2E 管理员",
      email: "e2e@example.com",
      role: "system_admin",
      email_verified: true,
      department_assigned: true,
      department_id: "department-e2e",
      department_name: "知识库",
      department_code: "KB",
    }),
  );
});

await page.route("**/api/notifications**", async (route) => {
  await route.fulfill(jsonResponse({ items: [], total: 0, unread_count: 0 }));
});

await page.route("**/api/files/policy", async (route) => {
  await route.fulfill(
    jsonResponse({
      allowed_extensions: ["pdf", "docx", "xlsx", "txt"],
      allow_multi_file: true,
      upload_enabled: true,
      max_file_size_mb: 50,
      allow_user_delete: true,
    }),
  );
});

await page.route("**/api/datasets", async (route) => {
  await route.fulfill(jsonResponse({ items: [], total: 0 }));
});

await page.route("**/api/admin/configs**", async (route) => {
  await route.fulfill(
    jsonResponse({
      group: "upload",
      items: [
        {
          key: "upload.allowed_extensions",
          value: ["pdf", "docx", "xlsx", "txt"],
          value_type: "list",
          is_secret: false,
          masked_value: null,
          description: "允许的扩展名",
          updated_at: null,
        },
        {
          key: "upload.allow_multi_file",
          value: true,
          value_type: "bool",
          is_secret: false,
          masked_value: null,
          description: "允许批量上传",
          updated_at: null,
        },
        {
          key: "upload.enabled",
          value: true,
          value_type: "bool",
          is_secret: false,
          masked_value: null,
          description: "开放员工上传",
          updated_at: null,
        },
      ],
    }),
  );
});

await page.route("**/api/files/file-e2e", async (route) => {
  await route.fulfill(
    jsonResponse({
      id: "file-e2e",
      original_name: "浏览器验收手册.pdf",
      extension: "pdf",
      mime_type: "application/pdf",
      size: 4096,
      uploader_id: "user-e2e",
      owner_id: "user-e2e",
      owner_name: "E2E 管理员",
      department_id: "department-e2e",
      department_name: "知识库",
      department_code: "KB",
      department: "知识库",
      category_id: "cat-e2e",
      dataset_mapping_id: null,
      visibility: "company",
      description: "浏览器 E2E mock 文件",
      tags: ["验收", "R5"],
      status: "analyzed",
      review_status: "approved",
      ragflow_dataset_id: "dataset-e2e",
      ragflow_document_id: "doc-e2e",
      ragflow_parse_status: "parsed",
      ai_analysis_enabled_at_upload: true,
      series_id: "file-e2e",
      version_number: 1,
      replaces_file_id: null,
      replacement_remote_action: null,
      is_current_version: true,
      remote_visibility: "current",
      version_switch_status: "not_required",
      version_switch_error: null,
      version_switch_attempt_count: 0,
      predecessor_remote_deactivated_at: null,
      local_version_activated_at: "2026-06-15T08:10:00Z",
      remote_version_activated_at: "2026-06-15T08:10:00Z",
      uploaded_at: "2026-06-15T08:00:00Z",
      expires_at: "2026-06-20T00:00:00Z",
      expiry_status: "expiring",
      last_sync_at: "2026-06-15T08:10:00Z",
      created_at: "2026-06-15T08:00:00Z",
      updated_at: "2026-06-15T08:10:00Z",
      duplicate: false,
      duplicate_file_id: null,
      category_name: "制度文档",
      version_chain: [
        {
          id: "file-e2e",
          version_number: 1,
          replaces_file_id: null,
          replacement_remote_action: null,
          title: "浏览器验收手册.pdf",
          status: "analyzed",
          is_current_version: true,
          remote_visibility: "current",
          version_switch_status: "not_required",
          version_switch_error: null,
          created_at: "2026-06-15T08:00:00Z",
        },
      ],
      analysis: {
        status: "succeeded",
        summary: "浏览器验收使用的分析摘要。",
        sensitive_risk_level: "low",
        quality_score: 88,
        table_count: 1,
        tables_json: [
          {
            title: "费用明细",
            markdown: "| 项目 | 金额 |\n|---|---:|\n| 培训 | 1000 |",
          },
        ],
        similar_file_ids: ["similar-file-e2e"],
        extracted_text_preview: "浏览器验收提取文本预览。",
        error_message: null,
        finished_at: "2026-06-15T08:05:00Z",
      },
      sync_error: null,
    }),
  );
});

await page.route("**/api/tasks**", async (route) => {
  await route.fulfill(jsonResponse({ items: [], total: 0 }));
});

await page.addInitScript(() => {
  localStorage.setItem(
    "knowledge-uploader-auth",
    JSON.stringify({
      state: {
        accessToken: "e2e-token",
        user: {
          id: "admin-e2e",
          name: "E2E 管理员",
          email: "e2e@example.com",
          role: "system_admin",
          email_verified: true,
          department_assigned: true,
          department_id: "department-e2e",
          department_name: "知识库",
        },
      },
      version: 0,
    }),
  );
});

const workbenchIds = {
  employee: "11111111-1111-4111-8111-111111111111",
  admin: "22222222-2222-4222-8222-222222222222",
  department: "33333333-3333-4333-8333-333333333333",
  draft: "44444444-4444-4444-8444-444444444444",
  rejected: "55555555-5555-4555-8555-555555555555",
  review: "66666666-6666-4666-8666-666666666666",
  notificationFile: "77777777-7777-4777-8777-777777777777",
  outOfScopeFile: "99999999-9999-4999-8999-999999999999",
  otherDepartment: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  otherReviewer: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
};

const requiredArtifactNames = [
  "employee-desktop-workbench",
  "employee-desktop-notification",
  "employee-mobile-360",
  "employee-mobile-390",
  "employee-mobile-768",
  "admin-desktop-claimed-sla",
  "admin-mobile-360",
  "admin-mobile-390",
  "admin-mobile-768",
];
const capturedArtifacts = new Map();

function assertCondition(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

async function waitForCondition(predicate, message, timeoutMs = 5_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await predicate()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error(message);
}

function fixtureId(namespace, index) {
  const tail = index.toString(16).padStart(12, "0");
  return `${namespace}-0000-4000-8000-${tail}`;
}

function workbenchUser(role) {
  const employee = role === "employee";
  return {
    id: employee ? workbenchIds.employee : workbenchIds.admin,
    name: employee ? "E2E 员工" : "E2E 部门管理员",
    email: employee ? "employee-e2e@example.com" : "dept-admin-e2e@example.com",
    role,
    email_verified: true,
    department_assigned: true,
    department_id: workbenchIds.department,
    department_name: "知识管理部",
    department_code: "KB",
  };
}

function workbenchFile(overrides = {}) {
  const now = Date.now();
  return {
    id: workbenchIds.draft,
    original_name: "知识库使用手册.pdf",
    title: "知识库使用手册",
    extension: "pdf",
    mime_type: "application/pdf",
    size: 204_800,
    uploader_id: workbenchIds.employee,
    uploader_name: "E2E 员工",
    owner_id: workbenchIds.employee,
    owner_name: "E2E 员工",
    department_id: workbenchIds.department,
    department_name: "知识管理部",
    department_code: "KB",
    department: "知识管理部",
    category_id: null,
    dataset_mapping_id: null,
    visibility: "department",
    description: "用于工作台浏览器验收",
    tags: ["验收"],
    status: "uploaded",
    review_status: "pending",
    ragflow_dataset_id: null,
    ragflow_document_id: null,
    ragflow_parse_status: null,
    ai_analysis_enabled_at_upload: false,
    series_id: workbenchIds.draft,
    version_number: 1,
    replaces_file_id: null,
    replacement_remote_action: null,
    is_current_version: true,
    remote_visibility: "candidate",
    version_switch_status: "not_required",
    version_switch_error: null,
    version_switch_attempt_count: 0,
    predecessor_remote_deactivated_at: null,
    local_version_activated_at: null,
    remote_version_activated_at: null,
    uploaded_at: new Date(now - 86_400_000).toISOString(),
    expires_at: null,
    expiry_status: "never",
    last_sync_at: null,
    created_at: new Date(now - 86_400_000).toISOString(),
    updated_at: new Date(now - 3_600_000).toISOString(),
    duplicate: false,
    duplicate_file_id: null,
    sensitive_risk_level: "none",
    claimed_by: null,
    claimed_by_name: null,
    claimed_at: null,
    claim_expires_at: null,
    review_due_at: null,
    review_version: 1,
    ...overrides,
  };
}

const employeeStatusFixtures = [
  { status: "uploaded", label: "草稿", count: 31, nextAction: "submit_review" },
  { status: "rejected", label: "驳回", count: 31, nextAction: "revise_rejected" },
  { status: "pending_review", label: "待审核", count: 31, nextAction: "view_progress" },
  { status: "parsed", label: "已入库", count: 31, nextAction: "view_detail" },
];

const employeeFiles = employeeStatusFixtures.flatMap((definition, groupIndex) =>
  Array.from({ length: definition.count }, (_, index) => {
    const ordinal = index + 1;
    const id =
      definition.status === "uploaded" && index === 0
        ? workbenchIds.draft
        : definition.status === "rejected" && index === 0
          ? workbenchIds.rejected
          : definition.status === "pending_review" && index === 0
            ? workbenchIds.notificationFile
            : fixtureId(`e${groupIndex + 1}000000`, ordinal);
    const rejectedDocx = definition.status === "rejected" && index === 0;
    const reviewStatus =
      definition.status === "rejected"
        ? "rejected"
        : definition.status === "parsed"
          ? "approved"
          : "pending";
    return workbenchFile({
      id,
      series_id: id,
      original_name: `${definition.label}-${String(ordinal).padStart(3, "0")}-知识制度.${
        rejectedDocx ? "docx" : "pdf"
      }`,
      title: `${definition.label}-${String(ordinal).padStart(3, "0")}-知识制度`,
      extension: rejectedDocx ? "docx" : "pdf",
      mime_type: rejectedDocx
        ? "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        : "application/pdf",
      status: definition.status,
      review_status: reviewStatus,
      ragflow_parse_status: definition.status === "parsed" ? "done" : null,
      ragflow_dataset_id: definition.status === "parsed" ? "dataset-e2e" : null,
      ragflow_document_id: definition.status === "parsed" ? `ragflow-${ordinal}` : null,
      sensitive_risk_level: definition.status === "rejected" ? "low" : "none",
      submitted_at:
        definition.status === "pending_review"
          ? new Date(Date.now() - ordinal * 60_000).toISOString()
          : null,
      next_action: definition.nextAction,
      updated_at: new Date(Date.now() - (groupIndex * 40 + ordinal) * 60_000).toISOString(),
    });
  }),
);

const outOfScopeEmployeeFile = workbenchFile({
  id: workbenchIds.outOfScopeFile,
  series_id: workbenchIds.outOfScopeFile,
  original_name: "其他部门机密制度.pdf",
  title: "其他部门机密制度",
  uploader_id: workbenchIds.otherReviewer,
  uploader_name: "其他部门员工",
  owner_id: workbenchIds.otherReviewer,
  owner_name: "其他部门员工",
  department_id: workbenchIds.otherDepartment,
  department_name: "其他部门",
  department_code: "OTHER",
  department: "其他部门",
  status: "parsed",
  review_status: "approved",
});

function employeeDashboardPayload(files, notifications) {
  const statusCount = (status) => files.filter((file) => file.status === status).length;
  const draftCount = statusCount("uploaded");
  const rejectedCount = statusCount("rejected");
  return {
    role: "employee",
    generated_at: new Date().toISOString(),
    access: { scope: "self", ready: true, department_ids: [workbenchIds.department] },
    employee: {
      status_counts: {
        total: files.length,
        draft: draftCount,
        ai_processing: 0,
        analysis_failed: 0,
        sensitive_review: 0,
        pending_review: statusCount("pending_review"),
        approved: 0,
        rejected: rejectedCount,
        sync_processing: 0,
        parsed: statusCount("parsed"),
        sync_failed: 0,
        archived: 0,
      },
      action_counts: {
        total: draftCount + rejectedCount,
        submit_draft: draftCount,
        revise_rejected: rejectedCount,
        confirm_sensitive: 0,
        analysis_failed: 0,
      },
      recent_documents: ["uploaded", "rejected", "pending_review", "parsed"].map((status) => {
        const file = files.find((candidate) => candidate.status === status);
        return {
          id: file.id,
          original_name: file.original_name,
          title: file.title,
          extension: file.extension,
          status: file.status,
          review_status: file.review_status,
          updated_at: file.updated_at,
          next_action: file.next_action,
        };
      }),
      recent_notifications: [],
      unread_notification_count: notifications.filter((item) => !item.read_at).length,
    },
    admin: null,
    system: null,
  };
}

function workbenchNotifications() {
  const now = new Date().toISOString();
  return Array.from({ length: 12 }, (_, index) => {
    const ordinal = index + 1;
    const isRead = ![1, 2, 3, 11].includes(ordinal);
    const common = {
      id: `notification-${ordinal}`,
      type: "file.updated",
      title: `知识动态 ${ordinal}`,
      body: "文档状态已更新。",
      metadata: {
        resource_type: "file",
        resource_id: workbenchIds.notificationFile,
        file_id: workbenchIds.notificationFile,
      },
      read_at: isRead ? now : null,
      created_at: new Date(Date.now() - index * 60_000).toISOString(),
    };
    if (ordinal === 1) {
      return {
        ...common,
        title: "仅状态通知",
        body: "这条通知没有详情链接，用于验证单条已读。",
        metadata: {},
      };
    }
    if (ordinal === 2) {
      return {
        ...common,
        title: "无权文档更新",
        body: "关联文档不属于当前员工，服务端应统一返回 404。",
        metadata: {
          resource_type: "file",
          resource_id: workbenchIds.outOfScopeFile,
          file_id: workbenchIds.outOfScopeFile,
        },
      };
    }
    if (ordinal === 11) {
      return {
        ...common,
        type: "file.rejected",
        title: "审核结果待处理",
        body: "制度草案已驳回，请修改后重新提交。",
      };
    }
    return common;
  });
}

function reviewFileFixtures() {
  const now = Date.now();
  const scoped = Array.from({ length: 128 }, (_, index) => {
    const ordinal = index + 1;
    const id = index === 0 ? workbenchIds.review : fixtureId("f1000000", ordinal);
    const expiredClaim = index === 1;
    const reassignedClaim = index === 2;
    const overdue = index === 1 || index % 11 === 0;
    const dueSoon = !overdue && (index === 0 || index % 7 === 0);
    return workbenchFile({
      id,
      series_id: id,
      original_name: `待审制度-${String(ordinal).padStart(3, "0")}.pdf`,
      title: `待审制度-${String(ordinal).padStart(3, "0")}`,
      status: "pending_review",
      review_status: "pending",
      sensitive_risk_level: index === 0 ? "high" : "none",
      submitted_at: new Date(now - (ordinal + 3) * 60_000).toISOString(),
      review_due_at: new Date(
        overdue ? now - 60 * 60_000 : dueSoon ? now + 45 * 60_000 : now + 8 * 60 * 60_000,
      ).toISOString(),
      claimed_by: expiredClaim || reassignedClaim ? workbenchIds.otherReviewer : null,
      claimed_by_name: expiredClaim || reassignedClaim ? "王审核员" : null,
      claimed_at:
        expiredClaim || reassignedClaim ? new Date(now - 60 * 60_000).toISOString() : null,
      claim_expires_at: expiredClaim
        ? new Date(now - 5 * 60_000).toISOString()
        : reassignedClaim
          ? new Date(now + 15 * 60_000).toISOString()
          : null,
    });
  });
  const otherDepartment = Array.from({ length: 17 }, (_, index) =>
    workbenchFile({
      id: fixtureId("f2000000", index + 1),
      series_id: fixtureId("f2000000", index + 1),
      original_name: `跨部门待审-${String(index + 1).padStart(3, "0")}.pdf`,
      title: `跨部门待审-${String(index + 1).padStart(3, "0")}`,
      uploader_id: workbenchIds.otherReviewer,
      uploader_name: "其他部门员工",
      owner_id: workbenchIds.otherReviewer,
      owner_name: "其他部门员工",
      department_id: workbenchIds.otherDepartment,
      department_name: "其他部门",
      department_code: "OTHER",
      department: "其他部门",
      status: "pending_review",
      review_status: "pending",
      review_due_at: new Date(now + 60 * 60_000).toISOString(),
    }),
  );
  return { scoped, otherDepartment };
}
function expectedFailureKey(method, pathname, status) {
  return `${method.toUpperCase()} ${pathname} ${status}`;
}

function expectHttpFailure(state, method, pathname, status) {
  state.expectedHttpFailures.add(expectedFailureKey(method, pathname, status));
}

function attachRuntimeMonitoring(targetPage, label, state) {
  targetPage.on("pageerror", (error) => runtimeErrors.push(`${label} pageerror: ${error.message}`));
  targetPage.on("console", (message) => {
    const text = message.text();
    if (message.type() === "error") {
      if (text.startsWith("Failed to load resource:")) {
        // HTTP response monitoring below is authoritative and understands expected 404/409 cases.
        return;
      }
      runtimeErrors.push(`${label} console: ${text}`);
    } else if (message.type() === "warning") {
      runtimeWarnings.push(`${label} console: ${text}`);
    }
  });
  targetPage.on("response", (response) => {
    if (response.status() < 400) {
      return;
    }
    const pathname = new URL(response.url()).pathname;
    const key = expectedFailureKey(response.request().method(), pathname, response.status());
    if (state.expectedHttpFailures.has(key)) {
      state.observedHttpFailures.add(key);
      return;
    }
    runtimeErrors.push(`${label} http ${response.status()}: ${pathname}`);
  });
}

async function captureScreenshot(targetPage, name) {
  if (!artifactDir) {
    assertCondition(!protectedAcceptance, `Protected screenshot missing directory: ${name}`);
    return;
  }
  await mkdir(artifactDir, { recursive: true });
  const screenshotPath = path.join(artifactDir, `${name}.png`);
  await targetPage.screenshot({ path: screenshotPath, fullPage: true });
  const screenshotStat = await stat(screenshotPath);
  assertCondition(
    screenshotStat.isFile() && screenshotStat.size >= 1_024,
    `Invalid screenshot: ${name}`,
  );
  capturedArtifacts.set(name, { path: screenshotPath, size: screenshotStat.size });
}

function validateProtectedArtifacts() {
  if (!protectedAcceptance) {
    return;
  }
  for (const name of requiredArtifactNames) {
    assertCondition(capturedArtifacts.has(name), `Required screenshot was not captured: ${name}`);
  }
  assertCondition(
    capturedArtifacts.size === requiredArtifactNames.length,
    `Unexpected protected artifact set: ${[...capturedArtifacts.keys()].join(", ")}`,
  );
}

async function focusAndActivate(targetPage, locator, label) {
  await expectVisible(locator, label);
  await locator.focus();
  assertCondition(
    await locator.evaluate((element) => element === document.activeElement),
    `${label}: keyboard focus was not retained`,
  );
  await targetPage.keyboard.press("Enter");
}

function filterEmployeeFiles(files, url) {
  const q = (url.searchParams.get("q") ?? "").trim().toLocaleLowerCase("zh-CN");
  const status = url.searchParams.get("status");
  const extension = url.searchParams.get("extension");
  const filtered = files.filter((file) => {
    const searchMatch =
      !q ||
      [file.original_name, file.title, file.description].some((value) =>
        String(value ?? "")
          .toLocaleLowerCase("zh-CN")
          .includes(q),
      );
    return (
      searchMatch &&
      (!status || status === "all" || file.status === status) &&
      (!extension || extension === "all" || file.extension === extension)
    );
  });
  return paginate(filtered, url);
}

function claimIsActive(file, now = Date.now()) {
  return Boolean(
    file.claimed_by &&
    file.claim_expires_at &&
    Number.isFinite(Date.parse(file.claim_expires_at)) &&
    Date.parse(file.claim_expires_at) > now,
  );
}

function filterReviewFiles(files, url, user) {
  const now = Date.now();
  const q = (url.searchParams.get("q") ?? "").trim().toLocaleLowerCase("zh-CN");
  const queue = url.searchParams.get("queue") ?? "all";
  const filtered = files.filter((file) => {
    const searchMatch =
      !q ||
      [file.original_name, file.title, file.description].some((value) =>
        String(value ?? "")
          .toLocaleLowerCase("zh-CN")
          .includes(q),
      );
    const dueAt = file.review_due_at ? Date.parse(file.review_due_at) : Number.NaN;
    const queueMatch =
      queue === "all" ||
      (queue === "unclaimed" && !claimIsActive(file, now)) ||
      (queue === "my_claims" && claimIsActive(file, now) && file.claimed_by === user.id) ||
      (queue === "claimed" && claimIsActive(file, now)) ||
      (queue === "due_soon" &&
        Number.isFinite(dueAt) &&
        dueAt >= now &&
        dueAt <= now + 4 * 60 * 60_000) ||
      (queue === "overdue" && Number.isFinite(dueAt) && dueAt < now);
    return searchMatch && queueMatch;
  });
  return paginate(filtered, url);
}

function paginate(items, url) {
  const page = Math.max(1, Number(url.searchParams.get("page") ?? "1"));
  const pageSize = Math.max(1, Number(url.searchParams.get("page_size") ?? "20"));
  const start = (page - 1) * pageSize;
  return {
    items: items.slice(start, start + pageSize),
    total: items.length,
    page,
    page_size: pageSize,
  };
}

function apiFailure(status, message, errorCode) {
  return {
    status,
    contentType: "application/json",
    body: JSON.stringify({ success: false, data: null, message, error_code: errorCode }),
  };
}

function isEmployeeForbiddenApi(pathname) {
  return (
    pathname.startsWith("/api/review/") ||
    pathname.startsWith("/api/admin/") ||
    pathname.startsWith("/api/audit/")
  );
}
async function assertHealthyPage(targetPage, label) {
  assertCondition((await targetPage.title()) === "Knowledge Uploader", `${label}: wrong title`);
  assertCondition(
    (await targetPage.locator("body").innerText()).trim().length > 40,
    `${label}: blank`,
  );
  assertCondition(
    (await targetPage.locator("vite-error-overlay, .vite-error-overlay").count()) === 0,
    `${label}: Vite overlay`,
  );
  const dimensions = await targetPage.evaluate(() => ({
    viewport: window.innerWidth,
    html: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
  }));
  assertCondition(
    dimensions.html <= dimensions.viewport && dimensions.body <= dimensions.viewport,
    `${label}: horizontal overflow ${JSON.stringify(dimensions)}`,
  );
}

async function createWorkbenchPage(role) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const targetPage = await context.newPage();
  const user = workbenchUser(role);
  const reviewFixtures = reviewFileFixtures();
  const state = {
    employeeFiles: [...employeeFiles],
    reviewFiles: reviewFixtures.scoped,
    otherReviewFiles: reviewFixtures.otherDepartment,
    notifications: workbenchNotifications(),
    fileQueries: [],
    reviewQueries: [],
    notificationQueries: [],
    notificationReads: [],
    notificationReadAll: [],
    claimRequests: [],
    releaseRequests: [],
    forbiddenRequests: [],
    expectedHttpFailures: new Set(),
    observedHttpFailures: new Set(),
    claimConflictNext: false,
  };

  attachRuntimeMonitoring(targetPage, role, state);
  await targetPage.addInitScript((sessionUser) => {
    localStorage.setItem(
      "knowledge-uploader-auth",
      JSON.stringify({
        state: { accessToken: `${sessionUser.role}-e2e-token`, user: sessionUser },
        version: 0,
      }),
    );
  }, user);

  await targetPage.route(
    /\/api\/(?:system|auth|notifications|dashboard|files|review|admin|audit|tags|categories|datasets|saved-views)(?:[/?]|$)/,
    async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const pathname = url.pathname;
      const method = request.method();
      const fulfill = (data) => route.fulfill(jsonResponse(data));
      const publicSystemEndpoint =
        pathname.endsWith("/system/health") || pathname.endsWith("/system/ready");
      if (!publicSystemEndpoint) {
        const expectedAuthorization = `Bearer ${role}-e2e-token`;
        assertCondition(
          request.headers().authorization === expectedAuthorization,
          `${role} missing bearer authorization: ${method} ${pathname}`,
        );
      }

      if (role === "employee" && isEmployeeForbiddenApi(pathname)) {
        const violation = `${method} ${pathname}`;
        state.forbiddenRequests.push(violation);
        await route.fulfill(apiFailure(403, "员工无权调用管理员接口", "ROLE_FORBIDDEN"));
        throw new Error(`Employee called forbidden admin API: ${violation}`);
      }

      if (pathname.endsWith("/system/health")) {
        await fulfill({ status: "ok" });
        return;
      }
      if (pathname.endsWith("/system/ready")) {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            status: "ok",
            dependencies: {
              database: { status: "ok" },
              redis: { status: "ok" },
              rabbitmq: { status: "ok" },
              minio: { status: "ok" },
            },
          }),
        });
        return;
      }
      if (pathname.endsWith("/auth/me")) {
        await fulfill(user);
        return;
      }
      if (pathname.includes("/notifications")) {
        if (method === "POST") {
          if (pathname.endsWith("/read-all")) {
            const unread = state.notifications.filter((notification) => !notification.read_at);
            const readAt = new Date().toISOString();
            unread.forEach((notification) => {
              notification.read_at = readAt;
            });
            state.notificationReadAll.push({ method, pathname, updatedCount: unread.length });
            await fulfill({ updated_count: unread.length });
            return;
          }
          const notificationId = pathname.split("/").at(-2);
          const item = state.notifications.find(
            (notification) => notification.id === notificationId,
          );
          if (!item) {
            await route.fulfill(apiFailure(404, "通知不存在", "NOTIFICATION_NOT_FOUND"));
            return;
          }
          item.read_at = item.read_at ?? new Date().toISOString();
          state.notificationReads.push(notificationId);
          await fulfill(item);
          return;
        }
        const unreadOnly = url.searchParams.get("unread_only") === "true";
        const visibleNotifications = unreadOnly
          ? state.notifications.filter((notification) => !notification.read_at)
          : state.notifications;
        const result = paginate(visibleNotifications, url);
        state.notificationQueries.push(url.search);
        await fulfill({
          ...result,
          unread_count: state.notifications.filter((notification) => !notification.read_at).length,
        });
        return;
      }
      if (pathname.endsWith("/dashboard")) {
        await fulfill(employeeDashboardPayload(state.employeeFiles, state.notifications));
        return;
      }
      if (pathname.endsWith("/files/policy")) {
        await fulfill({
          allowed_extensions: ["pdf", "docx", "xlsx", "txt"],
          allow_multi_file: true,
          upload_enabled: true,
          max_file_size_mb: 50,
          allow_user_delete: true,
        });
        return;
      }
      if (pathname === "/api/files" && method === "GET") {
        state.fileQueries.push(url.search);
        await fulfill(filterEmployeeFiles(state.employeeFiles, url));
        return;
      }
      if (pathname === "/api/review/files" && method === "GET") {
        state.reviewQueries.push(url.search);
        await fulfill(filterReviewFiles(state.reviewFiles, url, user));
        return;
      }
      if (pathname.startsWith("/api/review/files/") && pathname.endsWith("/claim")) {
        const fileId = pathname.split("/").at(-2);
        const file = state.reviewFiles.find((candidate) => candidate.id === fileId);
        if (!file) {
          await route.fulfill(apiFailure(404, "审核任务不存在", "REVIEW_NOT_FOUND"));
          return;
        }
        if (method === "POST") {
          state.claimRequests.push({ method, pathname, fileId });
          if (state.claimConflictNext) {
            state.claimConflictNext = false;
            file.claimed_by = workbenchIds.otherReviewer;
            file.claimed_by_name = "王审核员";
            file.claimed_at = new Date().toISOString();
            file.claim_expires_at = new Date(Date.now() + 15 * 60_000).toISOString();
            await route.fulfill(
              apiFailure(409, "该审核任务刚刚被其他审核员领取", "REVIEW_ALREADY_CLAIMED"),
            );
            return;
          }
          file.claimed_by = user.id;
          file.claimed_by_name = user.name;
          file.claimed_at = new Date().toISOString();
          file.claim_expires_at = new Date(Date.now() + 15 * 60_000).toISOString();
          await fulfill(file);
          return;
        }
        if (method === "DELETE") {
          let reason = null;
          try {
            reason = request.postDataJSON()?.reason ?? null;
          } catch {
            reason = null;
          }
          state.releaseRequests.push({ method, pathname, fileId, reason });
          file.claimed_by = null;
          file.claimed_by_name = null;
          file.claimed_at = null;
          file.claim_expires_at = null;
          await fulfill(file);
          return;
        }
      }
      if (pathname.endsWith("/tags")) {
        await fulfill({
          items: [
            {
              id: "88888888-8888-4888-8888-888888888888",
              name: "验收",
              description: null,
              usage_count: 124,
              is_system_generated: false,
              enabled: true,
              created_at: new Date().toISOString(),
              updated_at: new Date().toISOString(),
            },
          ],
          total: 1,
          page: 1,
          page_size: 200,
        });
        return;
      }
      if (pathname.endsWith("/categories") || pathname.endsWith("/datasets")) {
        await fulfill({ items: [], total: 0 });
        return;
      }
      if (pathname === "/api/saved-views" && method === "GET") {
        await fulfill({ items: [], total: 0, page: 1, page_size: 100 });
        return;
      }
      if (pathname.startsWith("/api/files/")) {
        const segments = pathname.split("/");
        const fileId = segments[3];
        if (pathname.endsWith("/tasks")) {
          await fulfill({ items: [], total: 0 });
          return;
        }
        const visibleFiles = role === "employee" ? state.employeeFiles : state.reviewFiles;
        const file = visibleFiles.find((candidate) => candidate.id === fileId);
        if (!file) {
          await route.fulfill(
            apiFailure(404, "文件不存在或不在当前角色的数据范围内", "FILE_NOT_FOUND"),
          );
          return;
        }
        await fulfill(file);
        return;
      }
      await route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ detail: `Unhandled E2E API: ${pathname}` }),
      });
    },
  );

  return { context, page: targetPage, state, user };
}
async function validateEmployeeWorkbench() {
  const session = await createWorkbenchPage("employee");
  const targetPage = session.page;
  const expectedStatusCount = 31;

  await targetPage.goto(`${baseUrl}/dashboard`, { waitUntil: "domcontentloaded" });
  await expectVisible(
    targetPage.getByRole("heading", { name: "E2E 员工的知识工作台" }),
    "employee workbench title",
  );
  const statusRail = targetPage.getByRole("region", { name: "文档状态轨道" });
  await expectVisible(statusRail, "status rail");
  await expectVisible(targetPage.getByText("继续处理", { exact: true }), "continue processing");
  await expectVisible(targetPage.getByText("最近文档", { exact: true }), "recent documents");
  await expectVisible(targetPage.getByText("服务端共 124 条", { exact: false }), "employee total");
  for (const label of ["草稿", "待审核", "已入库", "已驳回"]) {
    const item =
      label === "草稿"
        ? statusRail.locator("article").filter({ hasText: label })
        : statusRail.getByRole("button", { name: new RegExp(label) });
    await expectVisible(item, `employee KPI ${label}`);
    assertCondition(
      (await item.locator(".status-rail__count").innerText()).trim() ===
        String(expectedStatusCount),
      `employee KPI count mismatch: ${label}`,
    );
  }
  assertCondition(
    (await targetPage.getByText("其他部门机密制度", { exact: true }).count()) === 0,
    "employee list leaked an out-of-scope document",
  );
  await assertHealthyPage(targetPage, "employee desktop");
  await captureScreenshot(targetPage, "employee-desktop-workbench");

  const statusSelect = targetPage.locator(".workbench-filter-toolbar .ant-select").first();
  const statusSelectInput = statusSelect.locator("input");
  await statusSelectInput.focus();
  assertCondition(
    await statusSelectInput.evaluate((element) => element === document.activeElement),
    "draft status selector did not retain keyboard focus",
  );
  await targetPage.keyboard.press("ArrowDown");
  await targetPage.keyboard.press("ArrowDown");
  await targetPage.keyboard.press("Enter");
  await targetPage.waitForURL((url) => url.searchParams.get("status") === "uploaded");
  await waitForCondition(
    () =>
      session.state.fileQueries.some(
        (query) => new URLSearchParams(query).get("status") === "uploaded",
      ),
    "draft server query evidence missing",
  );
  await expectVisible(
    targetPage.getByText(`服务端共 ${expectedStatusCount} 条`, { exact: false }),
    "draft drill-down total",
  );
  await expectVisible(targetPage.getByText("草稿-001-知识制度", { exact: true }), "draft result");

  for (const [label, status, expectedTitle] of [
    ["待审核", "pending_review", "待审核-001-知识制度"],
    ["已入库", "parsed", "已入库-001-知识制度"],
    ["已驳回", "rejected", "驳回-001-知识制度"],
  ]) {
    const responsePromise = targetPage.waitForResponse((response) => {
      const url = new URL(response.url());
      return (
        url.pathname === "/api/files" &&
        url.searchParams.get("status") === status &&
        response.status() === 200
      );
    });
    const button = statusRail.getByRole("button", { name: new RegExp(label) });
    await focusAndActivate(targetPage, button, `employee drill-down ${label}`);
    await responsePromise;
    assertCondition(
      new URL(targetPage.url()).searchParams.get("status") === status,
      `employee drill-down URL mismatch: ${label}`,
    );
    await expectVisible(
      targetPage.getByText(`服务端共 ${expectedStatusCount} 条`, { exact: false }),
      `${label} drill-down total`,
    );
    await expectVisible(targetPage.getByText(expectedTitle, { exact: true }), `${label} result`);
  }

  const rejectedPageResponse = targetPage.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname === "/api/files" &&
      url.searchParams.get("status") === "rejected" &&
      url.searchParams.get("page") === "2"
    );
  });
  await targetPage.locator(".ant-pagination-item-2").click();
  await rejectedPageResponse;
  await expectVisible(
    targetPage.getByText("驳回-021-知识制度", { exact: true }),
    "employee filtered server page two",
  );

  await targetPage.goto(`${baseUrl}/dashboard`, { waitUntil: "domcontentloaded" });
  const searchResponse = targetPage.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === "/api/files" && url.searchParams.get("q") === "草稿-011";
  });
  const search = targetPage.getByPlaceholder("搜索文件名或说明");
  await search.fill("草稿-011");
  await search.press("Enter");
  await searchResponse;
  await expectVisible(
    targetPage.getByText("服务端共 1 条", { exact: false }),
    "employee search total",
  );
  await expectVisible(
    targetPage.getByText("草稿-011-知识制度", { exact: true }),
    "employee search row",
  );

  await targetPage.goto(`${baseUrl}/dashboard`, { waitUntil: "domcontentloaded" });
  const pageResponse = targetPage.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === "/api/files" && url.searchParams.get("page") === "2";
  });
  await targetPage.locator(".ant-pagination-item-2").click();
  await pageResponse;
  assertCondition(new URL(targetPage.url()).searchParams.get("page") === "2", "employee page URL");
  await expectVisible(
    targetPage.getByText("草稿-021-知识制度", { exact: true }),
    "employee server page two",
  );

  const notificationButton = targetPage.getByRole("button", { name: "通知中心" });
  const badge = notificationButton.locator("xpath=..").locator("sup");
  await expectVisible(badge, "employee unread badge");
  assertCondition((await badge.innerText()).trim() === "4", "initial unread badge must be 4");
  await notificationButton.click();
  await expectVisible(targetPage.getByText("通知中心", { exact: true }), "notification drawer");
  const notificationPageResponse = targetPage.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === "/api/notifications" && url.searchParams.get("page") === "2";
  });
  await targetPage.locator(".notification-center__pagination .ant-pagination-item-2").click();
  await notificationPageResponse;
  await expectVisible(
    targetPage.getByRole("button", { name: "打开通知：审核结果待处理" }),
    "notification server page two",
  );
  await captureScreenshot(targetPage, "employee-desktop-notification");
  await targetPage.locator(".notification-center__pagination .ant-pagination-item-1").click();
  await expectVisible(
    targetPage.getByRole("button", { name: "打开通知：仅状态通知" }),
    "notification cached first page",
  );

  const singleReadResponse = targetPage.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/notifications/notification-1/read" &&
      response.request().method() === "POST",
  );
  await targetPage.getByRole("button", { name: "打开通知：仅状态通知" }).click();
  await singleReadResponse;
  await expectVisible(targetPage.getByText("3 条未读", { exact: true }), "single read count");
  assertCondition(
    session.state.notificationReads.includes("notification-1"),
    "single notification read mutation missing",
  );
  await waitForCondition(
    async () => (await badge.innerText()).trim() === "3",
    "single read badge must decrement",
  );

  const unreadOnlyResponse = targetPage.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname === "/api/notifications" &&
      url.searchParams.get("unread_only") === "true" &&
      response.status() === 200
    );
  });
  await targetPage
    .locator(".notification-center__toolbar .ant-segmented-item")
    .filter({ hasText: "未读" })
    .click();
  await unreadOnlyResponse;
  assertCondition(
    session.state.notificationQueries.some(
      (query) => new URLSearchParams(query).get("unread_only") === "true",
    ),
    "notification unread-only server query missing",
  );
  await expectVisible(
    targetPage.getByRole("button", { name: "打开通知：无权文档更新" }),
    "unread-only notification result",
  );
  const deniedPath = `/api/files/${workbenchIds.outOfScopeFile}`;
  expectHttpFailure(session.state, "GET", deniedPath, 404);
  const deniedResponse = targetPage.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === deniedPath &&
      response.request().method() === "GET" &&
      response.status() === 404,
  );
  await targetPage.getByRole("button", { name: "打开通知：无权文档更新" }).click();
  await deniedResponse;
  await targetPage.waitForURL(new RegExp(`/files/${workbenchIds.outOfScopeFile}$`));
  await expectVisible(
    targetPage.getByText("文件不存在", { exact: true }),
    "role-scoped deep link uses uniform 404",
  );
  assertCondition(
    session.state.observedHttpFailures.has(expectedFailureKey("GET", deniedPath, 404)),
    "role-scoped deep-link 404 was not observed",
  );

  await targetPage.goto(`${baseUrl}/dashboard`, { waitUntil: "domcontentloaded" });
  await targetPage.getByRole("button", { name: "通知中心" }).click();
  await expectVisible(targetPage.getByText("2 条未读", { exact: true }), "remaining unread count");
  const readAllResponse = targetPage.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/notifications/read-all" &&
      response.request().method() === "POST",
  );
  const readAllButton = targetPage.getByRole("button", { name: "全部标为已读" });
  await focusAndActivate(targetPage, readAllButton, "notification mark all read");
  await readAllResponse;
  await expectVisible(targetPage.getByText("0 条未读", { exact: true }), "all read count");
  assertCondition(
    session.state.notificationReadAll.at(-1)?.updatedCount === 2,
    "mark-all-read mutation count mismatch",
  );
  await targetPage.waitForFunction(() => !document.querySelector(".top-header__actions sup"));
  assertCondition(await readAllButton.isDisabled(), "mark-all-read button must disable at zero");

  for (const width of [360, 390, 768]) {
    await targetPage.setViewportSize({ width, height: 844 });
    await targetPage.goto(`${baseUrl}/dashboard`, { waitUntil: "domcontentloaded" });
    await expectVisible(
      targetPage.getByRole("heading", { name: "E2E 员工的知识工作台" }),
      `employee ${width} title`,
    );
    const menu = targetPage.getByRole("button", { name: "打开导航菜单" });
    await focusAndActivate(targetPage, menu, `employee ${width} keyboard menu`);
    await expectVisible(targetPage.getByLabel("移动导航"), `employee ${width} drawer`);
    await targetPage.keyboard.press("Escape");
    await targetPage.getByLabel("移动导航").waitFor({ state: "hidden" });

    const uploadButton = targetPage.locator(".page-actions button", { hasText: "上传文档" });
    await focusAndActivate(targetPage, uploadButton, `employee ${width} primary upload action`);
    await targetPage.waitForURL(/\/upload$/);
    await targetPage.goto(`${baseUrl}/dashboard`, { waitUntil: "domcontentloaded" });

    const rejectedResponse = targetPage.waitForResponse((response) => {
      const url = new URL(response.url());
      return url.pathname === "/api/files" && url.searchParams.get("status") === "rejected";
    });
    await focusAndActivate(
      targetPage,
      targetPage.getByRole("button", { name: /已驳回/ }),
      `employee ${width} keyboard status action`,
    );
    await rejectedResponse;
    await expectVisible(targetPage.getByLabel("移动端文档列表"), `employee ${width} cards`);
    await assertHealthyPage(targetPage, `employee ${width}`);
    await captureScreenshot(targetPage, `employee-mobile-${width}`);
  }

  assertCondition(
    session.state.employeeFiles.length > 100 &&
      session.state.fileQueries.some((query) => new URLSearchParams(query).get("page") === "2"),
    "employee >100 server pagination evidence missing",
  );
  assertCondition(
    session.state.forbiddenRequests.length === 0,
    `employee called forbidden admin APIs: ${session.state.forbiddenRequests.join(", ")}`,
  );
  await session.context.close();
}
async function validateAdminWorkbench() {
  const session = await createWorkbenchPage("dept_admin");
  const targetPage = session.page;

  await targetPage.goto(`${baseUrl}/dashboard`, { waitUntil: "domcontentloaded" });
  await expectVisible(
    targetPage.getByRole("heading", { name: "部门审核工作台" }),
    "admin workbench title",
  );
  const allQueueTab = targetPage.getByRole("tab", { name: /全部/ });
  await expectVisible(allQueueTab, "admin all queue");
  assertCondition(
    (await allQueueTab.innerText()).includes("128"),
    "admin scoped total must be 128",
  );
  assertCondition(
    (await targetPage.getByText(/跨部门待审-/).count()) === 0,
    "department admin list leaked another department",
  );
  await expectVisible(targetPage.getByText(/剩余 \d+ 分钟|已超时/).first(), "review SLA");
  await expectVisible(targetPage.getByText("领取已失效", { exact: true }), "expired claim state");
  await expectVisible(targetPage.getByText("王审核员", { exact: true }), "reassigned reviewer");
  await assertHealthyPage(targetPage, "admin desktop");

  const reviewRow = () => targetPage.locator("tr").filter({ hasText: "待审制度-001" }).first();
  const claimRequestCount = session.state.claimRequests.length;
  await reviewRow().locator("button", { hasText: "领取" }).click();
  await waitForCondition(
    () => session.state.claimRequests.length === claimRequestCount + 1,
    `admin claim request missing; observed=${JSON.stringify(session.state.claimRequests)}`,
  );
  await expectVisible(
    reviewRow().getByRole("button", { name: "审核", exact: true }),
    "claimed action",
  );
  assertCondition(
    session.state.claimRequests.at(-1)?.fileId === workbenchIds.review,
    "admin claim success request missing",
  );

  const releaseResponse = targetPage.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === `/api/review/files/${workbenchIds.review}/claim` &&
      response.request().method() === "DELETE" &&
      response.status() === 200,
  );
  await reviewRow().locator("button", { hasText: "释放" }).click();
  await releaseResponse;
  await expectVisible(reviewRow().locator("button", { hasText: "领取" }), "released row");
  assertCondition(
    session.state.releaseRequests.at(-1)?.fileId === workbenchIds.review,
    "admin release request evidence missing",
  );

  const conflictPath = `/api/review/files/${workbenchIds.review}/claim`;
  session.state.claimConflictNext = true;
  expectHttpFailure(session.state, "POST", conflictPath, 409);
  const conflictResponse = targetPage.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === conflictPath &&
      response.request().method() === "POST" &&
      response.status() === 409,
  );
  await reviewRow().locator("button", { hasText: "领取" }).click();
  await conflictResponse;
  await expectVisible(
    reviewRow().getByText("该任务刚刚被他人领取，队列已刷新", { exact: true }),
    "claim conflict row feedback",
  );
  await expectVisible(reviewRow().getByText("王审核员", { exact: true }), "conflict reassignment");
  assertCondition(
    session.state.observedHttpFailures.has(expectedFailureKey("POST", conflictPath, 409)),
    "claim conflict response was not observed",
  );

  const expiredRow = targetPage.locator("tr").filter({ hasText: "待审制度-002" }).first();
  await expectVisible(expiredRow.getByText("领取已失效", { exact: true }), "expired claim row");
  await expectVisible(
    expiredRow.locator("button", { hasText: "重新领取" }),
    "expired reclaim action",
  );
  const reassignedRow = targetPage.locator("tr").filter({ hasText: "待审制度-003" }).first();
  await expectVisible(
    reassignedRow.getByText("王审核员", { exact: true }),
    "active reassignment row",
  );
  assertCondition(
    (await reassignedRow.getByRole("button", { name: "审核", exact: true }).count()) === 0,
    "department admin must not decide another reviewer's active claim",
  );

  const searchResponse = targetPage.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === "/api/review/files" && url.searchParams.get("q") === "待审制度-127";
  });
  const search = targetPage.getByPlaceholder("搜索文件名称、关键词");
  await search.fill("待审制度-127");
  await search.press("Enter");
  await searchResponse;
  await expectVisible(targetPage.getByText("待审制度-127", { exact: true }), "admin server search");
  assertCondition(
    (await targetPage.getByRole("tab", { name: /全部/ }).innerText()).includes("1"),
    "admin filtered total must be 1",
  );

  await targetPage.goto(`${baseUrl}/dashboard`, { waitUntil: "domcontentloaded" });
  const pageResponse = targetPage.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === "/api/review/files" && url.searchParams.get("page") === "2";
  });
  await targetPage.locator(".ant-pagination-item-2").first().click();
  await pageResponse;
  assertCondition(new URL(targetPage.url()).searchParams.get("page") === "2", "admin page URL");
  await expectVisible(
    targetPage.getByText("待审制度-021", { exact: true }),
    "admin server page two",
  );

  const expectedOverdueTotal = filterReviewFiles(
    session.state.reviewFiles,
    new URL(`${baseUrl}/api/review/files?queue=overdue`),
    session.user,
  ).total;
  const queueResponse = targetPage.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === "/api/review/files" && url.searchParams.get("queue") === "overdue";
  });
  await focusAndActivate(
    targetPage,
    targetPage.getByRole("tab", { name: /已超时/ }),
    "admin overdue queue keyboard action",
  );
  await queueResponse;
  const overdueTab = targetPage.getByRole("tab", { name: /已超时/ });
  assertCondition(
    (await overdueTab.innerText()).includes(String(expectedOverdueTotal)),
    "admin overdue server total mismatch",
  );
  await expectVisible(targetPage.getByText("领取已失效", { exact: true }), "overdue expired claim");
  await captureScreenshot(targetPage, "admin-desktop-claimed-sla");

  const otherDepartmentFile = session.state.otherReviewFiles[0];
  const deniedPath = `/api/files/${otherDepartmentFile.id}`;
  expectHttpFailure(session.state, "GET", deniedPath, 404);
  const deniedResponse = targetPage.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === deniedPath &&
      response.request().method() === "GET" &&
      response.status() === 404,
  );
  await targetPage.goto(`${baseUrl}/files/${otherDepartmentFile.id}`, {
    waitUntil: "domcontentloaded",
  });
  await deniedResponse;
  await expectVisible(
    targetPage.getByText("文件不存在", { exact: true }),
    "department-scoped detail uses uniform 404",
  );

  for (const width of [360, 390, 768]) {
    await targetPage.setViewportSize({ width, height: 844 });
    await targetPage.goto(`${baseUrl}/dashboard`, { waitUntil: "domcontentloaded" });
    await expectVisible(
      targetPage.getByRole("heading", { name: "部门审核工作台" }),
      `admin ${width} title`,
    );
    const menu = targetPage.getByRole("button", { name: "打开导航菜单" });
    await focusAndActivate(targetPage, menu, `admin ${width} keyboard menu`);
    await expectVisible(targetPage.getByLabel("移动导航"), `admin ${width} drawer`);
    await targetPage.keyboard.press("Escape");
    await targetPage.getByLabel("移动导航").waitFor({ state: "hidden" });

    const claimCount = session.state.claimRequests.length;
    const reviewQueryCount = session.state.reviewQueries.length;
    await focusAndActivate(
      targetPage,
      targetPage.getByRole("button", { name: /领取下一份/ }),
      `admin ${width} primary claim action`,
    );
    await waitForCondition(
      () => session.state.claimRequests.length === claimCount + 1,
      `admin ${width} keyboard claim request missing`,
    );
    await waitForCondition(
      () => session.state.reviewQueries.length > reviewQueryCount,
      `admin ${width} claim did not refresh the review queue`,
    );
    await expectVisible(
      targetPage.getByRole("list", { name: "移动端审核队列" }),
      `admin ${width} cards`,
    );
    const claimedFileId = session.state.claimRequests.at(-1)?.fileId;
    const claimedFile = session.state.reviewFiles.find((file) => file.id === claimedFileId);
    assertCondition(Boolean(claimedFile), `admin ${width} claimed file missing from scoped state`);
    const claimedCard = targetPage
      .locator(".review-mobile-card")
      .filter({ hasText: claimedFile.title });
    await expectVisible(claimedCard, `admin ${width} claimed card`);
    await expectVisible(
      claimedCard.getByText("由我领取", { exact: true }),
      `admin ${width} current claimant`,
    );
    const claimedButtonLabels = (await claimedCard.locator("button").allInnerTexts()).map((label) =>
      label.replace(/\s+/gu, ""),
    );
    assertCondition(
      claimedButtonLabels.includes("批准"),
      `admin ${width} claimed card lacks decision action: ${JSON.stringify({
        fileId: claimedFile.id,
        status: claimedFile.status,
        claimedBy: claimedFile.claimed_by,
        currentUser: session.user.id,
        buttons: claimedButtonLabels,
      })}`,
    );

    const dueSoonResponse = targetPage.waitForResponse((response) => {
      const url = new URL(response.url());
      return (
        url.pathname === "/api/review/files" &&
        url.searchParams.get("queue") === "due_soon" &&
        response.status() === 200
      );
    });
    await focusAndActivate(
      targetPage,
      targetPage.getByRole("tab", { name: /临近 SLA/ }),
      `admin ${width} keyboard SLA queue`,
    );
    await dueSoonResponse;
    await targetPage.waitForURL((url) => url.searchParams.get("queue") === "due_soon");
    await assertHealthyPage(targetPage, `admin ${width}`);
    await captureScreenshot(targetPage, `admin-mobile-${width}`);
  }

  assertCondition(
    session.state.reviewFiles.length > 100 &&
      session.state.reviewQueries.some((query) => new URLSearchParams(query).get("page") === "2"),
    "admin >100 server pagination evidence missing",
  );
  assertCondition(
    session.state.claimRequests.some(
      (entry) => entry.method === "POST" && entry.pathname === conflictPath,
    ) &&
      session.state.releaseRequests.some(
        (entry) => entry.method === "DELETE" && entry.pathname === conflictPath,
      ),
    "claim/release request evidence missing; UI cannot attest audit_logs persistence",
  );
  assertCondition(
    session.state.observedHttpFailures.has(expectedFailureKey("GET", deniedPath, 404)),
    "department-scoped detail 404 was not observed",
  );
  await session.context.close();
}
try {
  await page.goto(`${baseUrl}/upload`, { waitUntil: "domcontentloaded" });
  await expectVisible(page.getByRole("heading", { name: "上传知识文件" }), "upload page title");
  await expectVisible(page.getByText("上传后提交审核"), "submit-after-upload switch");
  await expectVisible(page.getByText("启用 AI 分析"), "AI analysis switch");

  await page.goto(`${baseUrl}/files/file-e2e`, { waitUntil: "domcontentloaded" });
  await expectVisible(
    page.getByRole("heading", { name: "浏览器验收手册.pdf" }),
    "file detail title",
  );
  await expectVisible(page.getByText("88%"), "quality score");
  await expectVisible(page.getByText("检测到 1 个相似文档"), "similar file alert");
  await expectVisible(page.getByText("即将过期"), "expiry indicator");

  await page.getByText("费用明细").click();
  await expectVisible(page.getByText(/培训/), "table extraction preview");

  await validateEmployeeWorkbench();
  await validateAdminWorkbench();
  validateProtectedArtifacts();

  if (runtimeWarnings.length > 0) {
    throw new Error(`Browser runtime warnings: ${runtimeWarnings.join(" | ")}`);
  }
  if (runtimeErrors.length > 0) {
    throw new Error(`Browser runtime errors: ${runtimeErrors.join(" | ")}`);
  }

  if (protectedAcceptance) {
    console.log(
      "Protected UI acceptance passed: status drill-down, claim/release/conflict/expiry/SLA, notifications, " +
        "role-scoped pagination, responsive keyboard actions and screenshots",
    );
  } else {
    console.log(
      "Browser smoke passed; protected UI acceptance was not attested " +
        "(set E2E_ACCEPTANCE_MODE=protected and E2E_ARTIFACT_DIR)",
    );
  }
} finally {
  await browser.close();
}
