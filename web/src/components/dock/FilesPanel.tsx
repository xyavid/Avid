/**
 * Workspace files panel: lists a directory and opens a file for preview.
 * Read-only and confined to one workspace; the backend rejects escapes (`..`, symlink traversal),
 * credential files, oversize reads and binaries, and this panel relays its message verbatim.
 */

import { useCallback, useEffect, useState } from 'react'

import { ApiError, listFiles, readFile } from '../../api/client'
import type { FileContent, FileEntry, FileList } from '../../api/types'
import { CodeBlock } from '../../markdown'
import { langOf } from '../../markdown/langOf'
import { cx } from '../../ui/cx'
import { Icon } from '../../ui/Icon'

export type FilesPanelProps = {
  /** Owning workspace id; null = no session selected yet. */
  workspaceId: string | null
  /** Workspace root: the full path shown in the preview is built from it. */
  root?: string | null
}

function sizeText(size: number | null): string {
  if (size === null) return ''
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

/** Full path shown in the preview: root + relative path (relative alone when root is absent). */
export function fullPath(root: string | null, relative: string): string {
  if (root === null || root === '') return relative
  return `${root.replace(/\/+$/, '')}/${relative}`
}

/** Breadcrumbs: root + one per level; clicking a level navigates back to it. */
export function crumbs(path: string): { name: string; path: string }[] {
  const parts = path === '' ? [] : path.split('/')
  const trail = [{ name: '工作区', path: '' }]
  parts.forEach((part, index) => trail.push({ name: part, path: parts.slice(0, index + 1).join('/') }))
  return trail
}

export function FilesPanel({ workspaceId, root = null }: FilesPanelProps) {
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

  // Entering the panel or switching workspace goes back to the root.
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
            {file.binary ? (
              <p className="px-a4 py-a8 font-ui text-hint text-ink-muted">
                二进制文件，不预览内容（{sizeText(file.size)}）
              </p>
            ) : (
              // Same component as chat code blocks: path row + language tag + copy + line numbers.
              <CodeBlock
                lang={langOf(file.path)}
                text={file.text ?? ''}
                path={fullPath(root, file.path)}
                lineNumbers
              />
            )}
            <p className="px-a4 font-ui text-micro text-ink-muted">
              {sizeText(file.size)}
              {file.truncated && ' · 只显示前 256 KB'}
            </p>
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
