/**
 * 会话导航态的纯 reducer。
 *
 * 为什么把它从 Provider 里拆出来单独成文件：这块逻辑有 6 个动作分支与两处
 * "跨域联动"（删会话要连带清掉它的视图与草稿、选中项失效要回落），是界面最容易
 * 长歪的地方。做成纯函数就能在默认 node 环境下逐条钉住，不必起 jsdom。
 *
 * 它**只管导航与选择**：一次运行的流式视图（`RunView`）另由 `useRunStream` 持有，
 * 因为它的更新频率是每帧级、而选中项是每次点击一次——两者放同一个 state 会让
 * 高频更新把整棵会话列表带着重渲染。
 */

import type { SessionSummary } from '../api/types'
import type { InspectorTab } from '../features/inspector'

export interface NavState {
  /** 全部会话（未过滤）。搜索词由列表组件本地持有：它不影响别的组件。 */
  sessions: SessionSummary[]
  /** 当前选中的会话 id；null = 还没有选中（首屏空态）。 */
  activeSessionId: string | null
  /** 当前会话的分支名，缺省 main（服务端默认值）。 */
  branch: string
  /** 左栏是否收起。 */
  navCollapsed: boolean
  /** 检查器选中的工具调用 id；null = 未选中。 */
  inspectorToolCallId: string | null
  /** 检查器页签。 */
  inspectorTab: InspectorTab
}

export const initialNavState: NavState = {
  sessions: [],
  activeSessionId: null,
  branch: 'main',
  navCollapsed: false,
  inspectorToolCallId: null,
  inspectorTab: 'content',
}

export type NavAction =
  | { type: 'sessions/loaded'; sessions: SessionSummary[] }
  | { type: 'session/selected'; id: string | null }
  | { type: 'session/updated'; session: SessionSummary }
  | { type: 'session/removed'; id: string }
  | { type: 'branch/selected'; branch: string }
  | { type: 'nav/toggled' }
  | { type: 'inspector/opened'; toolCallId: string }
  | { type: 'inspector/closed' }
  | { type: 'inspector/tab'; tab: InspectorTab }

export function navReducer(state: NavState, action: NavAction): NavState {
  switch (action.type) {
    case 'sessions/loaded': {
      /*
       * 选中项失效时**必须回落**：`sessions/loaded` 是刷新与 reconcile 的入口，
       * 服务端返回的列表里可能已经没有当前会话（另一个标签页删掉了它）。
       * 保持一个已不存在的 id 会让主区一直请求一个 404，界面看起来像卡死。
       * 刷新是幂等的，所以这里顺带按 created_at 降序定序——服务端不保证顺序。
       */
      const sessions = [...action.sessions].sort((a, b) => b.created_at - a.created_at)
      const stillThere = sessions.some((item) => item.id === state.activeSessionId)
      return {
        ...state,
        sessions,
        activeSessionId: stillThere ? state.activeSessionId : (sessions[0]?.id ?? null),
      }
    }

    case 'session/selected':
      /*
       * `id === null` 是**回落**语义而不是"什么都不选"：它有两个调用点
       * （首屏列表到达、当前会话被别的标签页删掉），两处都期望"落到最近的会话"。
       * 列表为空时才真的是空态。
       *
       * 换会话时把检查器一起关掉：检查器里显示的是**上一个会话**某次工具调用的结果，
       * 留着它等于把两个会话的事实并排放在一个屏幕上——比空着更容易误读。
       */
      return {
        ...state,
        activeSessionId: action.id ?? state.sessions[0]?.id ?? null,
        inspectorToolCallId: null,
      }

    case 'session/updated': {
      const index = state.sessions.findIndex((item) => item.id === action.session.id)
      if (index === -1) return state
      const sessions = [...state.sessions]
      sessions[index] = action.session
      return { ...state, sessions }
    }

    case 'session/removed': {
      /*
       * 删除要连带清理三处联动，少一处就会留下悬空引用：
       *   · sessions 里的那一项；
       *   · activeSessionId（若删的正是当前会话，回落到新列表的第一项）；
       *   · 检查器选中（它指向被删会话里的一个工具调用）。
       * 注意这里**不**回落到"上一项"而是"第一项"：会话列表按时间降序，
       * 删掉当前的之后，最近的会话就是第一项，这与用户预期一致。
       */
      const sessions = state.sessions.filter((item) => item.id !== action.id)
      if (sessions.length === state.sessions.length) return state
      const active = state.activeSessionId === action.id ? (sessions[0]?.id ?? null) : state.activeSessionId
      return {
        ...state,
        sessions,
        activeSessionId: active,
        inspectorToolCallId: state.activeSessionId === action.id ? null : state.inspectorToolCallId,
      }
    }

    case 'branch/selected':
      // 换分支等价于换一条链尾，检查器里的旧结果不再属于当前视图。
      return { ...state, branch: action.branch, inspectorToolCallId: null }

    case 'nav/toggled':
      return { ...state, navCollapsed: !state.navCollapsed }

    case 'inspector/opened':
      /*
       * 打开检查器时页签**回到 content**：上次看的可能是 diff，但新选中的工具
       * 未必有 diff（页签会被禁用），停在 disabled 的页签上看起来像坏了。
       */
      return { ...state, inspectorToolCallId: action.toolCallId, inspectorTab: 'content' }

    case 'inspector/closed':
      return { ...state, inspectorToolCallId: null }

    case 'inspector/tab':
      return { ...state, inspectorTab: action.tab }
  }
}
