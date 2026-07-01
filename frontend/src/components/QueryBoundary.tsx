import type { ReactNode } from "react";
import { ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Empty, Skeleton } from "antd";

export interface QueryBoundaryProps {
  /** 是否处于加载中（通常来自 useQuery 的 isLoading） */
  isLoading: boolean;
  /** 是否请求失败（通常来自 useQuery 的 isError） */
  isError: boolean;
  /** 数据是否为空，由调用方按业务判断（如 list.length === 0） */
  isEmpty?: boolean;
  /** 失败时的错误对象，用于提取错误信息 */
  error?: unknown;
  /** 重试回调，传入后错误态会渲染“重试”按钮（通常是 refetch） */
  onRetry?: () => void;
  /** 自定义加载骨架；不传则用默认的 Skeleton */
  skeleton?: ReactNode;
  /** 默认骨架的段落行数 */
  skeletonRows?: number;
  /** 空态描述文案 */
  emptyDescription?: ReactNode;
  /** 错误态标题 */
  errorTitle?: ReactNode;
  /** 数据就绪时渲染的内容 */
  children: ReactNode;
}

function resolveErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return "数据加载失败，请稍后重试";
}

/**
 * 统一封装异步数据的四种状态：加载 / 错误 / 空 / 成功。
 *
 * 解决全站“接口失败静默退化为空数据”的问题：错误态显式渲染 Alert + 重试，
 * 与“真的没有数据”的空态区分开。各页区块直接包裹即可，无需重复手写三段式判断。
 */
export function QueryBoundary(props: QueryBoundaryProps) {
  const {
    isLoading,
    isError,
    isEmpty = false,
    error,
    onRetry,
    skeleton,
    skeletonRows = 4,
    emptyDescription = "暂无数据",
    errorTitle = "加载失败",
    children,
  } = props;

  if (isLoading) {
    return (
      <>
        {skeleton ?? (
          <div
            aria-label="正在加载数据"
            aria-live="polite"
            className="query-boundary-state query-boundary-state--loading"
            role="status"
          >
            <Skeleton active paragraph={{ rows: skeletonRows }} />
          </div>
        )}
      </>
    );
  }

  if (isError) {
    return (
      <div className="query-boundary-state query-boundary-state--error">
        <Alert
          className="query-boundary-alert"
          type="error"
          showIcon
          message={errorTitle}
          description={resolveErrorMessage(error)}
          action={
            onRetry ? (
              <Button size="small" icon={<ReloadOutlined />} onClick={onRetry}>
                重试
              </Button>
            ) : undefined
          }
        />
      </div>
    );
  }

  if (isEmpty) {
    return (
      <div
        aria-label="暂无数据"
        aria-live="polite"
        className="query-boundary-state query-boundary-state--empty"
        role="status"
      >
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={emptyDescription} />
      </div>
    );
  }

  return <>{children}</>;
}
