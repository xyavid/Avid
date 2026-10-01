/**
 * 行级 diff（自实现 LCS，不引依赖）。
 *
 * 为什么要自己写：检查器的「改动」页签只需要**行级、只读、可复制**的差异，
 * 引一个 diff 库要多背一份体积与一套 API，而上游产物（工具参数里的 before/after 文本）
 * 形状很简单。代价是这里只有最朴素的 LCS：它给的是"最短编辑脚本"的一种，不做
 * hunk 归并、不做 move 检测、不看字符级差异——够用，且行为完全可预测、可单测。
 *
 * 复杂度护栏：LCS 是 O(n·m)，两侧各 1500 行就到了 200 万格。超过阈值时退回
 * "全删 + 全加"，输出仍然正确（只是不好看）——**宁可难读，不能卡住界面**。
 */

export type DiffLine = {
  kind: 'add' | 'del' | 'context'
  text: string
  oldNo: number | null
  newNo: number | null
}

/** 超过这个行数就不做 LCS，直接整段替换。 */
const MAX_LCS_LINES = 1500

/**
 * 按行切分。
 *
 * 末尾的单个换行**不算作一行**：文件内容 "a\n" 是 1 行而不是 2 行，
 * 否则每次编辑都会多出一条"空行差异"，噪声大过信息。
 */
function splitLines(text: string): string[] {
  if (text === '') return []
  const body = text.endsWith('\n') ? text.slice(0, -1) : text
  return body.split('\n')
}

function replaceAll(before: string[], after: string[]): DiffLine[] {
  const lines: DiffLine[] = []
  before.forEach((text, index) => {
    lines.push({ kind: 'del', text, oldNo: index + 1, newNo: null })
  })
  after.forEach((text, index) => {
    lines.push({ kind: 'add', text, oldNo: null, newNo: index + 1 })
  })
  return lines
}

export function diffLines(before: string, after: string): DiffLine[] {
  const oldLines = splitLines(before)
  const newLines = splitLines(after)
  if (oldLines.length > MAX_LCS_LINES || newLines.length > MAX_LCS_LINES) {
    return replaceAll(oldLines, newLines)
  }

  const rows = oldLines.length
  const cols = newLines.length
  const width = cols + 1
  // dp[i][j] = 后缀 oldLines[i..] 与 newLines[j..] 的最长公共子序列长度。
  // 从后往前填，回溯时就能从 (0,0) 正向走，输出天然是阅读顺序。
  const dp = new Int32Array((rows + 1) * width)
  const at = (row: number, col: number): number => dp[row * width + col] ?? 0

  for (let i = rows - 1; i >= 0; i -= 1) {
    for (let j = cols - 1; j >= 0; j -= 1) {
      dp[i * width + j] =
        oldLines[i] === newLines[j]
          ? at(i + 1, j + 1) + 1
          : Math.max(at(i + 1, j), at(i, j + 1))
    }
  }

  const lines: DiffLine[] = []
  let i = 0
  let j = 0
  let oldNo = 1
  let newNo = 1

  // 相等一定走 context；不等时先出 del 再出 add（替换读起来是"旧行没了、新行来了"）。
  while (i < rows && j < cols) {
    if (oldLines[i] === newLines[j]) {
      // i < rows 与 j < cols 已保证下标命中，这里的断言只是绕开 noUncheckedIndexedAccess。
      lines.push({ kind: 'context', text: oldLines[i]!, oldNo, newNo })
      i += 1
      j += 1
      oldNo += 1
      newNo += 1
    } else if (at(i + 1, j) >= at(i, j + 1)) {
      lines.push({ kind: 'del', text: oldLines[i]!, oldNo, newNo: null })
      i += 1
      oldNo += 1
    } else {
      lines.push({ kind: 'add', text: newLines[j]!, oldNo: null, newNo })
      j += 1
      newNo += 1
    }
  }
  while (i < rows) {
    lines.push({ kind: 'del', text: oldLines[i]!, oldNo, newNo: null })
    i += 1
    oldNo += 1
  }
  while (j < cols) {
    lines.push({ kind: 'add', text: newLines[j]!, oldNo: null, newNo })
    j += 1
    newNo += 1
  }

  return lines
}

/** 统一格式输出（`+`/`-`/` ` 前缀），便于复制。 */
export function toUnified(before: string, after: string): string {
  return diffLines(before, after)
    .map((line) => {
      if (line.kind === 'add') return `+${line.text}`
      if (line.kind === 'del') return `-${line.text}`
      return ` ${line.text}`
    })
    .join('\n')
}
