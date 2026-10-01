/**
 * 添加工作区：两条路都要有。
 *
 * 一、「选择文件夹…」——走 `POST /api/workspaces/pick`，由**本机后端**弹系统对话框。
 *     浏览器拿不到目录绝对路径，所以这是唯一能"点着选"的路。
 * 二、手动填绝对路径——无选择器的机器（远端 / 无 GUI）与脚本化场景的唯一路，
 *     也是排查时的路。两条路并存不是冗余：把选择器做成唯一入口，等于把
 *     "这台机器没装 tkinter"变成一个死胡同。
 *
 * 三个容易写错的地方，都在代码里显式处理：
 *   · `path: null` 是**用户取消**，不是错误（服务端 `PickFolderOut` 注释写死了）——
 *     这里静默保持原状，绝不出红字；
 *   · 错误按 `code` 给不同文案（`lib/workspaceError.ts` 单点），并显示在弹窗内
 *     `role="alert"` 上，不是 console；
 *   · 提交期间整块禁用：同一路径点两次会拿到 409，但那是一条本可以不出现的错误。
 *
 * 为什么没有 `full` 档：工作区的**默认权限**不接受 `full`（服务端
 * `Literal["manual", "auto"]`，`schemas.py:115`）。`full` 只能是对某一次运行的
 * 显式授权，不能变成一个目录的长期默认值。
 */

import { useEffect, useId, useState } from 'react'
import type { ReactElement } from 'react'

import type { WorkspaceSummary } from '../../../api/types'
import { createWorkspace, fetchPickFolder } from '../../../api/workspaces'
import { Button, Dialog } from '../../../ui/primitives'
import { describeWorkspaceError } from '../lib/workspaceError'

export interface AddWorkspaceDialogProps {
  open: boolean
  /** 这台机器上会用到哪个文件夹选择器（`meta.capabilities.workspace_picker`）；null = 没有可用的。 */
  picker: string | null
  onClose: () => void
  /** 成功后由上层刷新工作区列表并（可选）选中它。 */
  onCreated: (workspace: WorkspaceSummary) => void
}

/** 两档默认权限的说明。文案说"批准发生在哪一步"，而不是重复档位名。 */
const PERMISSION_OPTIONS: ReadonlyArray<{
  value: 'manual' | 'auto'
  label: string
  hint: string
}> = [
  { value: 'manual', label: '手动', hint: '每次工具调用都要人工批准' },
  { value: 'auto', label: '自动', hint: '由分类器自动批准低风险调用' },
]

export function AddWorkspaceDialog({
  open,
  picker,
  onClose,
  onCreated,
}: AddWorkspaceDialogProps): ReactElement {
  const [path, setPath] = useState('')
  const [name, setName] = useState('')
  const [permission, setPermission] = useState<'manual' | 'auto'>('manual')
  const [picking, setPicking] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const pathId = useId()
  const nameId = useId()
  const busy = picking || saving

  /*
   * 每次打开都回到干净状态。
   *
   * 不复位的话，"加完一个再点一次添加"会把上次的路径留在输入框里，
   * 一不小心就是一次没打算的重复登记（服务端会挡，但那是一条可以避免的 409）。
   */
  useEffect(() => {
    if (!open) return
    setPath('')
    setName('')
    setPermission('manual')
    setError(null)
    setPicking(false)
    setSaving(false)
  }, [open])

  /*
   * busy 期间不响应关闭：Esc 与点遮罩都会走到这里，而请求已经在飞。
   * 允许关掉的话，界面消失了、工作区却真的登记了——用户会以为它没生效。
   */
  const handleClose = () => {
    if (!busy) onClose()
  }

  const handlePick = async () => {
    if (picker === null || busy) return
    setPicking(true)
    setError(null)
    try {
      const result = await fetchPickFolder()
      // null = 用户取消：什么都不做，包括不报错、不清空已填的路径。
      if (result.path !== null) setPath(result.path)
    } catch (cause) {
      setError(describeWorkspaceError(cause))
    } finally {
      setPicking(false)
    }
  }

  const handleSubmit = async () => {
    const trimmedPath = path.trim()
    if (trimmedPath === '') {
      setError('请先选择或填写一个目录的绝对路径。')
      return
    }
    setSaving(true)
    setError(null)
    try {
      const trimmedName = name.trim()
      const created = await createWorkspace({
        path: trimmedPath,
        // 空名字宁可不发：发出去会被当成"把名字设成空"，而语义是"没给名字"。
        name: trimmedName === '' ? undefined : trimmedName,
        permission,
      })
      onCreated(created)
      onClose()
    } catch (cause) {
      setError(describeWorkspaceError(cause))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog
      open={open}
      width="md"
      title="添加工作区"
      description="登记一个目录，之后新建会话时就能把它选为工作目录。"
      onClose={handleClose}
      footer={
        <>
          <Button variant="secondary" type="button" disabled={busy} onClick={handleClose}>
            取消
          </Button>
          <Button
            variant="primary"
            type="button"
            loading={saving}
            /* picking 期间也禁用：选择器还没返回时提交会用上一次的路径，是最难查的一类错 */
            disabled={picking}
            onClick={handleSubmit}
          >
            添加
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-a12">
        {/* ---- 一：系统文件夹选择器 ---- */}
        <div>
          <Button
            variant="secondary"
            size="sm"
            loading={picking}
            disabled={picker === null || saving}
            onClick={handlePick}
          >
            选择文件夹…
          </Button>
          {picker === null ? (
            <p className="mt-a6 text-hint text-ink-muted">
              这台机器没有可用的文件夹选择器（后端未探测到），请手动填写绝对路径。
            </p>
          ) : (
            <p className="mt-a6 text-hint text-ink-faint">使用系统选择器：{picker}</p>
          )}
        </div>

        {/* ---- 二：手动填写（回落路径，也是无选择器机器的唯一路） ---- */}
        <div className="flex flex-col gap-a4">
          <label htmlFor={pathId} className="text-hint text-ink-muted">
            工作区路径
          </label>
          <input
            id={pathId}
            aria-label="工作区路径"
            type="text"
            value={path}
            disabled={busy}
            spellCheck={false}
            autoComplete="off"
            placeholder="/home/you/project"
            onChange={(event) => setPath(event.target.value)}
            /* 边框：`border-hair` 一次就是"0.5px 线宽 + 发丝线色"（同名 token 分属两个轴）。
               不要在它前后再叠方向变体或彩色类：方向变体不会带线色，彩色类会被它覆盖。 */
            className="min-h-[--avid-control-h] w-full rounded-sm border-hair bg-inset px-a10 text-ui text-ink placeholder:text-ink-faint disabled:opacity-45"
          />
        </div>

        <div className="flex flex-col gap-a4">
          <label htmlFor={nameId} className="text-hint text-ink-muted">
            显示名（可选）
          </label>
          <input
            id={nameId}
            type="text"
            value={name}
            disabled={busy}
            spellCheck={false}
            autoComplete="off"
            placeholder="缺省用目录末段"
            onChange={(event) => setName(event.target.value)}
            className="min-h-[--avid-control-h] w-full rounded-sm border-hair bg-inset px-a10 text-ui text-ink placeholder:text-ink-faint disabled:opacity-45"
          />
        </div>

        <fieldset disabled={busy} className="flex flex-col gap-a6">
          <legend className="text-hint text-ink-muted">默认权限</legend>
          {PERMISSION_OPTIONS.map((option) => (
            <label
              key={option.value}
              className="flex items-start gap-a6 text-caption text-ink-light"
            >
              <input
                type="radio"
                name="workspace-permission"
                value={option.value}
                checked={permission === option.value}
                disabled={busy}
                onChange={() => setPermission(option.value)}
                className="mt-a2"
              />
              <span>
                <span className="text-ink">{option.label}</span>
                <span className="ml-a4 text-hint text-ink-muted">{option.hint}</span>
              </span>
            </label>
          ))}
        </fieldset>

        {/* 错误必须在弹窗里：这是用户唯一在看的地方，console 等于没报。 */}
        {error === null ? null : (
          <p role="alert" className="rounded-sm bg-danger-bg px-a8 py-a6 text-hint text-danger">
            {error}
          </p>
        )}
      </div>
    </Dialog>
  )
}
