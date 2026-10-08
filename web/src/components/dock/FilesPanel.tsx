/**
 * 工作区文件（右列的默认面板）：列目录、点开看文件。
 *
 * 只读、只在一个工作区里——越界（`..` 与 symlink 穿透）、凭据类、超大与二进制都由
 * 后端拦下并给出说法，这里只做导航与呈现，错误原样转述后端的话（前端不另编一套）。
 * 进面板落在工作区根；换会话（工作区变了）也回到根。
 */

import { useCallback, useEffect, useState } from 'react'

import { ApiError, listFiles, readFile } from '../../api/client'
import type { FileContent, FileEntry, FileList } from '../../api/types'
import { cx } from '../../ui/cx'
import { Icon } from '../../ui/Icon'

export type FilesPanelProps = {
  /** 归属工作区 id；null = 还没有选中会话。 */
  workspaceId: string | null
}

function sizeText(size: number | null): string {
  if (size === null) return ''
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

/** 面包屑：根 + 每一级；点哪一级就回哪一级。 */
export function crumbs(path: string): { name: string; path: string }[] {
  const parts = path === '' ? [] : path.split('/')
  const trail = [{ name: '工作区', path: '' }]
  parts.forEach((part, index) => trail.push({ name: part, path: parts.slice(0, index + 1).join('/') }))
  return trail
}

export function FilesPanel({ workspaceId }: FilesPanelProps) {
  const [path, setPath] = useState('')
  const [list, setList] = useState<FileList | null>(null)
  const [file, setFile] = useState<FileContent | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const openDir = useCallback(
    async (next: string) => {
      if (!workspaceId) return
      setBusy(true)
      setError(null)
      setFile(null)
      try {
        const loaded = await listFiles(workspaceId, next)
        setList(loaded)
        setPath(loaded.path)
      } catch (failure) {
        setError(failure instanceof ApiError ? failure.message : String(failure))
      } finally {
        setBusy(false)
      }
    },
    [workspaceId],
  )

  const openFile = useCallback(
    async (entry: FileEntry) => {
      if (!workspaceId) return
      setBusy(true)
      setError(null)
      try {
        setFile(await readFile(workspaceId, entry.path))
      } catch (failure) {
        setError(failure instanceof ApiError ? failure.message : String(failure))
      } finally {
        setBusy(false)
      }
    },
    [workspaceId],
  )

  // 进面板 / 换工作区：回到根。
  useEffect(() => {
    setPath('')
    setList(null)
    setFile(null)
    setError(null)
    if (workspaceId) void openDir('')
  }, [workspaceId, openDir])

  if (!workspaceId) {
    return <p className="p-a12 font-ui text-hint text-ink-muted">选中一个会话后可以浏览它的工作区文件</p>
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-a4 border-b border-hair px-a8 py-a6">
        <span className="flex min-w-0 flex-1 items-center gap-a4 font-ui text-hint text-ink-muted">
          {crumbs(path).map((crumb, index) => (
            <span key={crumb.path} className="flex min-w-0 items-center gap-a4">
              {index > 0 && <span aria-hidden>/</span>}
              <button
                type="button"
                onClick={() => void openDir(crumb.path)}
                className="min-w-0 truncate rounded-xs px-a4 py-[1px] transition-colors duration-fast ease-out hover:bg-overlay-light hover:text-ink"
              >
                {crumb.name}
              </button>
            </span>
          ))}
        </span>
        <button
          type="button"
          onClick={() => void (file === null ? openDir(path) : openFile({ path: file.path } as FileEntry))}
          className="shrink-0 rounded-xs px-a6 py-[1px] font-ui text-micro text-ink-muted transition-colors duration-fast ease-out hover:bg-overlay-light hover:text-ink"
        >
          刷新
        </button>
      </div>

      <div className="scroll-auto min-h-0 flex-1 overflow-y-auto">
        {error !== null && <p className="p-a12 font-ui text-hint text-danger">{error}</p>}

        {file !== null && (
          <div className="p-a8">
            <p className="flex items-baseline gap-a8 px-a4 font-ui text-hint text-ink-muted">
              <span className="min-w-0 truncate">{file.path}</span>
              <span className="ml-auto shrink-0">{sizeText(file.size)}</span>
            </p>
            {file.binary ? (
              <p className="px-a4 py-a8 font-ui text-hint text-ink-muted">二进制文件，不预览内容</p>
            ) : (
              <pre className="mt-a4 px-a4 font-mono text-micro leading-[1.6] text-ink">
                <code>{file.text}</code>
              </pre>
            )}
            {file.truncated && (
              <p className="px-a4 py-a8 font-ui text-micro text-ink-muted">只显示前 256 KB</p>
            )}
          </div>
        )}

        {file === null && list !== null && (
          <div className="flex flex-col py-a4">
            {list.path !== '' && (
              <button
                type="button"
                onClick={() => void openDir(list.parent ?? '')}
                className="flex items-center gap-a8 rounded-sm px-a8 py-a6 text-left font-ui text-hint text-ink-muted transition-colors duration-fast ease-out hover:bg-overlay-light"
              >
                <Icon name="folder" size={13} />
                ..
              </button>
            )}
            {list.entries.map((entry) => (
              <button
                key={entry.path}
                type="button"
                onClick={() => void (entry.kind === 'dir' ? openDir(entry.path) : openFile(entry))}
                className={cx(
                  'flex items-center gap-a8 rounded-sm px-a8 py-a6 text-left transition-colors duration-fast ease-out hover:bg-overlay-light',
                  entry.kind === 'dir' ? 'text-ink' : 'text-ink-light',
                )}
              >
                <span className={cx('shrink-0', entry.kind === 'dir' ? 'text-accent' : 'text-ink-muted')}>
                  <Icon name={entry.kind === 'dir' ? 'folder' : 'file-frame'} size={13} />
                </span>
                <span className="min-w-0 flex-1 truncate font-ui text-hint">{entry.name}</span>
                <span className="shrink-0 font-ui text-micro text-ink-muted">{sizeText(entry.size)}</span>
              </button>
            ))}
            {list.entries.length === 0 && (
              <p className="px-a12 py-a8 font-ui text-hint text-ink-muted">这个目录是空的</p>
            )}
            {list.truncated && (
              <p className="px-a12 py-a8 font-ui text-micro text-ink-muted">条目过多，只列前 400 条</p>
            )}
          </div>
        )}

        {file === null && list === null && busy && (
          <p className="p-a12 font-ui text-hint text-ink-muted">加载中…</p>
        )}
      </div>
    </div>
  )
}
