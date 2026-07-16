import { describe, expect, it } from "vitest";

import {
  ApiError,
  DEPARTMENT_ASSIGNMENT_REQUIRED_MESSAGE,
  getUserFacingErrorMessage,
} from "./client";

describe("getUserFacingErrorMessage", () => {
  it("maps missing department to an actionable message", () => {
    const error = new ApiError("forbidden", {
      status: 403,
      code: "DEPARTMENT_ASSIGNMENT_REQUIRED",
    });

    expect(getUserFacingErrorMessage(error, "提交失败")).toBe(
      DEPARTMENT_ASSIGNMENT_REQUIRED_MESSAGE,
    );
  });

  it("keeps email verification separate from department assignment", () => {
    const error = new ApiError("请先验证邮箱", {
      status: 403,
      code: "EMAIL_NOT_VERIFIED",
    });

    expect(getUserFacingErrorMessage(error, "登录失败")).toBe("请先验证邮箱");
  });
});
