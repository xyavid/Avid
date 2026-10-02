/**
 * 剪贴板写入（阶段 33 · 阶段 9）。
 *
 * 为什么单独一个文件：`navigator.clipboard` 只在安全上下文（https / localhost）可用，
 * 而且用户可能拒绝权限、或在非浏览器环境里根本没有它。代码块与 svg 预览都要复制，
 * 与其两处各写一遍 try/catch，不如在这里收一次，并且**失败不抛**——
 * 复制失败不该把整条消息的渲染带崩。
 */

export async function copyText(text: string): Promise<boolean> {
  try {
    if (typeof navigator === 'undefined' || navigator.clipboard === undefined) return false
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}
