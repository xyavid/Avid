/*
 * 线性图标集：**唯一的图形语言出口**。
 *
 * 为什么自绘而不用图标库：这套视觉的全部图形纪律只有四行——
 * `fill="none" stroke="currentColor" stroke-width=1.5` + 24 网格 + round 端点。
 * 一个 40k 的图标包会带进实心图标、emoji 兜底、自己的颜色 API 和尺寸体系，
 * 而这里一个都不许用（报告 §9：无 emoji、不用实心 fill）。自绘 29 个图标 ≈ 4k，
 * 且**每个图标的形状被钉死在同一份源码里**，不会因为升级依赖而悄悄变粗变圆。
 *
 * 尺寸只开放 14 / 16 / 18 / 20 四档（IconProps.size 的字面量联合）：
 * 图标尺寸一旦变成任意像素，"同类元素同尺寸"这条就守不住了；
 * 锁成联合类型后，想加一档必须改这个文件的契约，改动会被 review 看见。
 *
 * 颜色一律 `currentColor`：图标不持有颜色决策，只继承所在文本的墨色。
 * 这保证「图标与它标注的文字恒同色」，也让 hover 变色只需文字类名一处。
 */

import type { FC, ReactNode } from 'react'
import { cx } from '../cx'

export interface IconProps {
  /** 像素尺寸；默认 16。只允许 14/16/18/20 —— 同类元素必须同尺寸。 */
  size?: 14 | 16 | 18 | 20
  className?: string
  strokeWidth?: number
  'aria-hidden'?: boolean
  'aria-label'?: string
}

interface IconBaseProps extends IconProps {
  children: ReactNode
}

/*
 * 底座：所有图标共用同一组 svg 属性，避免 29 处各写一遍——
 * 少写一遍就少一次"某个图标 stroke-width 忘了改"的漂移。
 *
 * 可访问性策略：图标默认是**装饰性的**（`aria-hidden`），因为绝大多数场景下
 * 图标旁边的文字已经说清了语义，再给图标一个名字只会让读屏念两遍。
 * 只有调用方显式传 `aria-label` 时才升级为语义节点（role="img"），
 * 此时不再加 aria-hidden —— 否则那个 label 等于写在隐身元素上。
 */
function IconBase({
  size = 16,
  className,
  strokeWidth = 1.5,
  'aria-hidden': ariaHidden,
  'aria-label': ariaLabel,
  children,
}: IconBaseProps): JSX.Element {
  const decorative = ariaHidden ?? ariaLabel === undefined
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      // shrink-0：图标永远不参与 flex 收缩。被压扁的图标比溢出更难看，也更难查。
      className={cx('shrink-0', className)}
      {...(ariaLabel === undefined ? {} : { role: 'img', 'aria-label': ariaLabel })}
      aria-hidden={decorative ? true : undefined}
    >
      {children}
    </svg>
  )
}

/*
 * 具名组件由工厂产出：29 个图标只有"路径数据"是不同的，
 * 逐个手写会复制 29 份 svg 属性，改一处属性要改 29 处。
 * displayName 显式设置，让 React DevTools 与测试报错里出现的是 `PlusIcon` 而不是 `Icon`。
 */
function createIcon(name: string, glyph: ReactNode): FC<IconProps> {
  const Icon: FC<IconProps> = (props) => <IconBase {...props}>{glyph}</IconBase>
  Icon.displayName = name
  return Icon
}

/* ---- 输入区 ---- */

export const PaperclipIcon = createIcon(
  'PaperclipIcon',
  <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />,
)

export const SendIcon = createIcon(
  'SendIcon',
  <>
    <path d="M22 2 11 13" />
    <path d="M22 2l-7 20-4-9-9-4 20-7z" />
  </>,
)

/** 停止用空心方框而非实心：全程 `fill="none"`，实心块会破坏线性语言的统一。 */
export const StopIcon = createIcon('StopIcon', <rect x="6" y="6" width="12" height="12" rx="1" />)

/* ---- 基础操作 ---- */

export const PlusIcon = createIcon(
  'PlusIcon',
  <>
    <path d="M12 5v14" />
    <path d="M5 12h14" />
  </>,
)

export const CloseIcon = createIcon(
  'CloseIcon',
  <>
    <path d="M18 6 6 18" />
    <path d="m6 6 12 12" />
  </>,
)

export const SearchIcon = createIcon(
  'SearchIcon',
  <>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.8-3.8" />
  </>,
)

/* ---- 布局 ---- */

export const PanelLeftIcon = createIcon(
  'PanelLeftIcon',
  <>
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <path d="M9 4v16" />
  </>,
)

export const PanelRightIcon = createIcon(
  'PanelRightIcon',
  <>
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <path d="M15 4v16" />
  </>,
)

/* ---- 方向 ---- */

export const ChevronDownIcon = createIcon('ChevronDownIcon', <path d="m6 9 6 6 6-6" />)

export const ChevronRightIcon = createIcon('ChevronRightIcon', <path d="m9 18 6-6-6-6" />)

export const ChevronLeftIcon = createIcon('ChevronLeftIcon', <path d="m15 18-6-6 6-6" />)

/* ---- 工具与运行 ---- */

export const WrenchIcon = createIcon(
  'WrenchIcon',
  <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />,
)

export const TerminalIcon = createIcon(
  'TerminalIcon',
  <>
    <path d="m4 17 6-6-6-6" />
    <path d="M12 19h8" />
  </>,
)

/* ---- 状态 ---- */

export const CheckIcon = createIcon('CheckIcon', <path d="M20 6 9 17l-5-5" />)

export const CheckCircleIcon = createIcon(
  'CheckCircleIcon',
  <>
    <circle cx="12" cy="12" r="9" />
    <path d="m8.5 12.5 2.5 2.5 4.5-5" />
  </>,
)

export const XCircleIcon = createIcon(
  'XCircleIcon',
  <>
    <circle cx="12" cy="12" r="9" />
    <path d="m9 9 6 6" />
    <path d="m15 9-6 6" />
  </>,
)

export const AlertTriangleIcon = createIcon(
  'AlertTriangleIcon',
  <>
    <path d="M10.29 3.86 2.09 18.06A2 2 0 0 0 3.82 21h16.36a2 2 0 0 0 1.73-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
    <path d="M12 9v4" />
    <path d="M12 17h.01" />
  </>,
)

export const InfoIcon = createIcon(
  'InfoIcon',
  <>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 16v-5" />
    <path d="M12 8h.01" />
  </>,
)

/*
 * 旋转由调用方给 `animate-spin`：图标自己不知道"要不要转"——
 * 同一个图形在"正在加载"处转、在静态说明处不转，动效决策属于调用现场。
 */
export const LoaderIcon = createIcon(
  'LoaderIcon',
  <>
    <path d="M12 2v4" />
    <path d="m16.24 7.76 2.83-2.83" />
    <path d="M18 12h4" />
    <path d="m16.24 16.24 2.83 2.83" />
    <path d="M12 18v4" />
    <path d="m4.93 19.07 2.83-2.83" />
    <path d="M2 12h4" />
    <path d="m4.93 4.93 2.83 2.83" />
  </>,
)

/* ---- 内容类型 ---- */

export const FileIcon = createIcon(
  'FileIcon',
  <>
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
    <path d="M14 2v6h6" />
  </>,
)

export const GitBranchIcon = createIcon(
  'GitBranchIcon',
  <>
    <path d="M6 3v12" />
    <circle cx="18" cy="6" r="3" />
    <circle cx="6" cy="18" r="3" />
    <path d="M18 9a9 9 0 0 1-9 9" />
  </>,
)

/** diff 用「+ 在上、− 在下」两个减号式子表示：不画文件框，避免和 FileIcon 混淆。 */
export const DiffIcon = createIcon(
  'DiffIcon',
  <>
    <path d="M9 7h6" />
    <path d="M12 4v6" />
    <path d="M9 17h6" />
  </>,
)

/** 花括号按 24 网格的对称轴画：左右各一条，中枢对齐 y=12，缩到 14px 也不糊。 */
export const BracesIcon = createIcon(
  'BracesIcon',
  <>
    <path d="M9 3H8a2 2 0 0 0-2 2v4a2 2 0 0 1-2 2 2 2 0 0 1 2 2v4a2 2 0 0 0 2 2h1" />
    <path d="M15 3h1a2 2 0 0 1 2 2v4a2 2 0 0 1 2 2 2 2 0 0 1-2 2v4a2 2 0 0 1-2 2h-1" />
  </>,
)

export const MessageIcon = createIcon(
  'MessageIcon',
  <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />,
)

export const UserIcon = createIcon(
  'UserIcon',
  <>
    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
    <circle cx="12" cy="7" r="4" />
  </>,
)

/** 主星 + 一颗小星：单星会被读成「星标收藏」，双星才读得出「新的想法 / 提示」。 */
export const SparkIcon = createIcon(
  'SparkIcon',
  <>
    <path d="M12 3.5l1.7 4.8 4.8 1.7-4.8 1.7L12 16.5l-1.7-4.8L5.5 10l4.8-1.7z" />
    <path d="M18.5 15.5l.6 1.7 1.7.6-1.7.6-.6 1.7-.6-1.7-1.7-.6 1.7-.6z" />
  </>,
)

export const ClockIcon = createIcon(
  'ClockIcon',
  <>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7v5l3 2" />
  </>,
)

export const ShieldIcon = createIcon(
  'ShieldIcon',
  <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />,
)

export const TrashIcon = createIcon(
  'TrashIcon',
  <>
    <path d="M3 6h18" />
    <path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2" />
    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
    <path d="M10 11v6" />
    <path d="M14 11v6" />
  </>,
)
