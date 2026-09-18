#!/usr/bin/env node
/**
 * §4.3 的产物搬运（`pnpm copy:dist`）：`web/dist/**` → `src/avid/web/static/`。
 *
 * 先清空目标目录但保留目录本身（后端 `app.py` 挂载的是这个路径，目录不存在会起不来）。
 * 复制完写 `.build.json` 构建戳：git sha、构建时间、文件数、来源。git 不可用时不报错，
 * `git_sha` 写 null——CI 的浅克隆里 `rev-parse` 可能失败，不该因此挡住部署。
 */

import { cpSync, existsSync, mkdirSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const WEB_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const DIST = join(WEB_ROOT, 'dist')
const DEST = resolve(WEB_ROOT, '../src/avid/web/static')

function countFiles(dir) {
  let count = 0
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name)
    count += entry.isDirectory() ? countFiles(full) : statSync(full).isFile() ? 1 : 0
  }
  return count
}

function gitSha() {
  try {
    const sha = execFileSync('git', ['rev-parse', '--short', 'HEAD'], {
      cwd: WEB_ROOT,
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim()
    return sha.length > 0 ? sha : null
  } catch {
    return null // 没有 git（或浅克隆）不是错误：构建戳照写，sha 为 null。
  }
}

if (!existsSync(DIST) || countFiles(DIST) === 0) {
  console.error('先 pnpm build')
  process.exit(1)
}

// 清空目标目录，但保留 .gitkeep：它是空目录能进版本库的唯一理由。
rmSync(DEST, { recursive: true, force: true })
mkdirSync(DEST, { recursive: true })
writeFileSync(join(DEST, '.gitkeep'), '')

for (const entry of readdirSync(DIST)) {
  cpSync(join(DIST, entry), join(DEST, entry), { recursive: true })
}

const files = countFiles(DEST)
const stamp = {
  git_sha: gitSha(),
  built_at: new Date().toISOString(),
  files,
  source: 'copy-dist',
}
const stampPath = join(DEST, '.build.json')
writeFileSync(stampPath, `${JSON.stringify(stamp)}\n`, 'utf8')

console.log(`copy:dist 复制 ${files} 个文件：${relative(WEB_ROOT, DIST)} → ${DEST}`)
console.log(`构建戳：${stampPath}（git_sha=${stamp.git_sha ?? 'null'}）`)
