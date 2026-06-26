import { useEffect, useRef, useState } from "react";

function prefersReducedMotion(): boolean {
  return (
    import.meta.env.MODE === "test" ||
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/**
 * 把数字从当前值平滑滚动到目标值的动画 hook(easeOutCubic)。
 * 首次渲染直接显示真实目标值，后续目标变化再动画，避免数据面板短暂显示 0。
 * 尊重无障碍:用户开启「减少动态效果」时直接返回目标值,不做动画。
 */
export function useCountUp(target: number, duration = 800): number {
  const [value, setValue] = useState<number>(() => target);
  const frameRef = useRef<number | undefined>(undefined);
  const fromRef = useRef<number>(target);

  useEffect(() => {
    if (prefersReducedMotion() || duration <= 0) {
      setValue(target);
      fromRef.current = target;
      return;
    }

    const from = fromRef.current;
    let start: number | undefined;

    const tick = (now: number) => {
      start ??= now;
      const elapsed = Math.max(0, now - start);
      const progress = Math.min(Math.max(elapsed / duration, 0), 1);
      const eased = 1 - (1 - progress) ** 3;
      setValue(from + (target - from) * eased);
      if (progress < 1) {
        frameRef.current = requestAnimationFrame(tick);
      } else {
        fromRef.current = target;
      }
    };

    frameRef.current = requestAnimationFrame(tick);

    return () => {
      if (frameRef.current !== undefined) {
        cancelAnimationFrame(frameRef.current);
      }
    };
  }, [target, duration]);

  return value;
}
