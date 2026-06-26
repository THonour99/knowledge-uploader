#!/usr/bin/env node

const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173";

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
    throw new Error(`Expected visible: ${label}`, { cause: error });
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

await page.route("**/api/notifications**", async (route) => {
  await route.fulfill(jsonResponse({ items: [], total: 0, unread_count: 0 }));
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
      uploaded_at: "2026-06-15T08:00:00Z",
      expires_at: "2026-06-20T00:00:00Z",
      expiry_status: "expiring",
      last_sync_at: "2026-06-15T08:10:00Z",
      created_at: "2026-06-15T08:00:00Z",
      updated_at: "2026-06-15T08:10:00Z",
      duplicate: false,
      duplicate_file_id: null,
      category_name: "制度文档",
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
        },
      },
      version: 0,
    }),
  );
});

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
  await expectVisible(page.getByText("88 分"), "quality score");
  await expectVisible(page.getByText("检测到 1 个相似文档"), "similar file alert");
  await expectVisible(page.getByText("即将过期"), "expiry indicator");

  await page.getByText("费用明细").click();
  await expectVisible(page.getByText(/培训/), "table extraction preview");

  console.log("Browser acceptance passed");
} finally {
  await browser.close();
}
