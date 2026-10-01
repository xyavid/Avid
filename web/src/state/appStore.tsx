/**
 * 应用级状态：**导航与选择**（会话列表、当前会话、分支、检查器、左栏收起）。
 *
 * 为什么用 Context + useReducer 而不是引入状态库：跨组件共享的只有导航量这一小块。
 * 多一个依赖意味着多一套心智模型与打包体积，而它换来的能力（selector 细粒度订阅、
 * 中间件）在本规模下用不上。
 *
 * **拆分点很关键**：高频更新的流式视图（每帧都在变）**不放这里**，否则订阅了
 * 导航量的会话列表会跟着每帧重渲染。两者在 `App.tsx` 里各自挂载，互不牵连。
 *
 * 状态更新的三条纪律（前两条是本文件最容易写错的地方）：
 *   1. **不在渲染期改状态**。所有同步都走 `useEffect`，`StrictMode` 下双调用也安全。
 *   2. **不在渲染期做副作用**。本文件除 `useSessionData()` 外不发起任何请求。
 *   3. `activeSessionId` 的回落由 reducer 拥有，effect 只负责"告诉它列表变了"。
 */

import { createContext, useContext, useEffect, useMemo, useReducer, useState } from 'react'
import type { ReactNode } from 'react'

import type {
  Meta,
  PermissionMode,
  SandboxState,
  SessionSummary,
  WorkspaceSummary,
} from '../api/types'
import type { InspectorTab } from '../features/inspector'
import { describeError, useSessionData } from './useSessionData'
import { initialNavState, navReducer } from './navReducer'
import type { NavState } from './navReducer'

export interface AppActions {
  /** 刷新会话列表与工作区；返回的 Promise 让调用方可以显示等待态。 */
  refresh: () => Promise<void>
  /** 新建会话并选中它。失败时错误会挂到 `error` 上，同时**再次抛出**供调用点提示。 */
  createSession: () => Promise<void>
  selectSession: (id: string | null) => void
  renameSession: (id: string, name: string) => Promise<void>
  deleteSession: (id: string) => Promise<void>
  selectBranch: (branch: string) => void
  toggleNav: () => void
  openInspector: (toolCallId: string) => void
  closeInspector: () => void
  setInspectorTab: (tab: InspectorTab) => void
}

export interface AppContextValue {
  meta: Meta | null
  sessions: SessionSummary[]
  workspaces: WorkspaceSummary[]
  nav: NavState
  /** 当前会话的完整对象；列表里找不到（刚删掉）时为 null。 */
  activeSession: SessionSummary | null
  loading: boolean
  /** 读取会话列表这一层的错误；与某次运行的错误是两回事，不要混用一处 UI。 */
  error: string | null
  /** 可以建会话的工作区根路径；null = 不可建（界面应禁用新建）。 */
  defaultWorkspace: string | null
  /** 服务端实测的沙箱状态；null = 还没读到 `/api/meta`。 */
  sandbox: SandboxState | null
  /**
   * 权限模式的当前选择。缺省跟随工作区默认权限（`docs/guide/web-ui.md` §2 的回落顺序：
   * 工作区默认 → `manual`），但**用户显式选过之后不再被覆盖**。
   * 它表达的是"这次要按哪个档起运行"，不是上次运行的事实——后者在 SSE 的
   * `run_started` 事件里，由流式视图持有。
   */
  permission: PermissionMode
  setPermission: (mode: PermissionMode) => void
  /** 全部写动作。引用稳定：订阅它不会因为 `nav` 变了而重渲染。 */
  actions: AppActions
}

const AppContext = createContext<AppContextValue | null>(null)

/** 读状态与动作。 */
export function useApp(): AppContextValue {
  const value = useContext(AppContext)
  if (value === null) {
    // 静默返回 null 会让下游读到 undefined 崩在很远的地方；这里就地失败，报错点即现场。
    throw new Error('useApp 必须在 <AppProvider> 内使用')
  }
  return value
}

/**
 * 只取动作的便捷入口。
 *
 * 注意它**不能**独立于 `useApp` 提供更细的订阅粒度：两者都订阅同一个 context。
 * 之所以还留着这个入口，是因为调用点读 `const { refresh } = useAppActions()`
 * 比 `const { actions } = useApp()` 更能表达"我只用动作、不读状态"的意图。
 * 真需要避免重渲染时用 `useMemo` 包住消费组件，而不是指望这个入口。
 */
export function useAppActions(): AppActions {
  return useApp().actions
}

export interface AppProviderProps {
  children: ReactNode
  /**
   * 测试注入点：不传时走真实 `useSessionData()`。
   * 有了它，`App` 的整棵子树可以在 jsdom 里渲染而不发一个请求。
   */
  data?: ReturnType<typeof useSessionData>
}

export function AppProvider({ children, data }: AppProviderProps) {
  /*
   * `enabled: data === undefined` —— 传了注入数据就**完全不起**真实数据层。
   *
   * 这不是"省一个请求"的优化，是正确性。`useSessionData` 与注入数据会各自维护一份
   * 「会话列表」，而 React 只渲染后者算出的树；若真实那份也跑起来，同一棵子树里就
   * 存在**两份互相竞争的状态源**——在 jsdom 里真实那份必然失败并写进 `error`，
   * 表现是页面莫名回落成空态，且只在特定渲染次序下复现（这个 bug 真出现过）。
   *
   * 注入点的语义是"**替换**"，不是"叠加"。`enabled` 把这条语义写进了代码。
   * 注意 Hooks 规则：调用仍然无条件，只是钩子内部据此选择"真跑或完全不跑"。
   */
  const fetched = useSessionData(data === undefined)
  const source = data ?? fetched
  const [nav, dispatch] = useReducer(navReducer, initialNavState)

  const sessions = source.sessions

  /*
   * 把列表同步进 reducer。依赖用 id 拼出的**串**而不是数组引用：
   * 数据层每次轮询都会给出新数组，用引用会让这段 effect 每 5 秒跑一次，
   * 而"列表内容没变"时它确实什么都不用做。
   */
  const sessionsKey = sessions.map((item) => item.id).join('|')
  useEffect(() => {
    dispatch({ type: 'sessions/loaded', sessions })
    // sessions 与 sessionsKey 是同一份数据，用 sessionsKey 作为唯一依赖（见上）。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionsKey])

  /*
   * 选中项的回落：`activeSessionId` 在 reducer 里、`sessions` 在数据层里，
   * 两边会各自变化（另一标签页删了当前会话 → 列表少了它）。这里只在
   * "当前选中已不在列表里"时补一次 null，由 reducer 决定回落到哪一项。
   */
  const activeMissing =
    nav.activeSessionId !== null && !sessions.some((item) => item.id === nav.activeSessionId)
  useEffect(() => {
    if (activeMissing) dispatch({ type: 'session/selected', id: null })
  }, [activeMissing])

  /*
   * 权限缺省值。三个状态，不能简化成两个：
   *   · `permission`：界面当前显示与将随请求发出的档位；
   *   · `pinned`：用户是否已经自己选过。没有它，每次列表轮询回来都会把用户的选择冲掉；
   *   · `appliedDefault`：工作区默认权限是否已经补过一次。没有它，用户在缺省工作区里
   *     把档位改回 `manual` 后会被再次补成默认值——看起来像"改不回去"。
   */
  const [pinned, setPinned] = useState(false)
  const [appliedDefault, setAppliedDefault] = useState(false)
  const [permission, setPermissionState] = useState<PermissionMode>('manual')

  const workspaceDefault = source.workspaces.find((item) => item.default_permission !== null)
  const workspaceDefaultMode: PermissionMode | null =
    workspaceDefault?.default_permission ?? null

  useEffect(() => {
    if (pinned || appliedDefault || workspaceDefaultMode === null) return
    setAppliedDefault(true)
    setPermissionState(workspaceDefaultMode)
  }, [pinned, appliedDefault, workspaceDefaultMode])

  const setPermission = useMemo(
    () => (mode: PermissionMode) => {
      // 用户显式选择后置位，缺省值从此不再参与。
      setPinned(true)
      setPermissionState(mode)
    },
    [],
  )

  const activeSession = useMemo(
    () => sessions.find((item) => item.id === nav.activeSessionId) ?? null,
    [sessions, nav.activeSessionId],
  )

  const actions = useMemo<AppActions>(
    () => ({
      refresh: source.refresh,

      async createSession(): Promise<void> {
        /*
         * 建完**必须选中**：只把新会话插进列表而让主区停在旧会话上，
         * 用户会以为"新建没生效"（新会话是空的，看不出来）。
         * 选中动作交给 reducer，随后 `sessions/loaded` 的回落逻辑也会认同它。
         */
        const created = await source.create()
        dispatch({ type: 'session/selected', id: created.id })
        await source.refresh()
      },

      selectSession(id: string | null): void {
        dispatch({ type: 'session/selected', id })
      },

      async renameSession(id: string, name: string): Promise<void> {
        /*
         * 空名字不是"改成无名"，是无效输入：服务端会把它当 null 存下去，
         * 界面上变成"未命名会话"，用户会以为改名失败了。静默忽略更好——
         * 弹一条"名字不能为空"对一个双击即编辑的输入框来说太重。
         */
        const trimmed = name.trim()
        if (trimmed === '') return
        await source.rename(id, trimmed)
      },

      async deleteSession(id: string): Promise<void> {
        await source.remove(id)
        /*
         * 本地先摘掉再回落：删除不可逆，界面必须立刻反映结果。
         * 等一次往返会让用户以为没删掉而再点一次。
         */
        dispatch({ type: 'session/removed', id })
      },

      selectBranch(branch: string): void {
        dispatch({ type: 'branch/selected', branch })
      },

      toggleNav(): void {
        dispatch({ type: 'nav/toggled' })
      },

      openInspector(toolCallId: string): void {
        dispatch({ type: 'inspector/opened', toolCallId })
      },

      closeInspector(): void {
        dispatch({ type: 'inspector/closed' })
      },

      setInspectorTab(tab: InspectorTab): void {
        dispatch({ type: 'inspector/tab', tab })
      },
    }),
    [source, dispatch],
  )

  const value = useMemo<AppContextValue>(
    () => ({
      meta: source.meta,
      sessions,
      workspaces: source.workspaces,
      nav,
      activeSession,
      loading: source.loading,
      error: source.error,
      defaultWorkspace: source.defaultWorkspace,
      sandbox: source.meta?.capabilities.sandbox ?? null,
      permission,
      setPermission,
      actions,
    }),
    [
      source.meta,
      source.workspaces,
      source.loading,
      source.error,
      source.defaultWorkspace,
      sessions,
      nav,
      activeSession,
      permission,
      setPermission,
      actions,
    ],
  )

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>
}

/** 把未知异常转成可显示文案（错误横幅与 toast 共用一套口径）。 */
export const formatError = describeError
