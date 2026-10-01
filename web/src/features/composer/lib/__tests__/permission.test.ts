/**
 * 权限预设的用例。
 *
 * 两条不变量：
 *  1. 三轴取值与 `docs/guide/web-ui.md` §2 的表**逐条**相符（表在用例里独立写一遍，
 *     不是引用实现，否则"实现改错"和"用例改错"会一起红或一起绿）。
 *  2. `full` 的 `full_access_ack` 只在显式确认后才出现——这条是安全关键：
 *     full 关掉沙箱与出网限制，必须是**一次有意识的动作**，不能靠选择器顺手带出来。
 */

import type { PermissionMode } from '../../../../api/types'
import { describe, expect, it } from 'vitest'

import { PERMISSION_PRESETS, buildStartRunInput, presetOf } from '../permission'

/** 文档 §2 的表，逐字抄写。 */
const DOC_TABLE: ReadonlyArray<{
  mode: PermissionMode
  approval: 'user' | 'classifier' | 'none'
  sandbox: 'workspace' | 'disabled'
  network: 'restricted' | 'open'
}> = [
  { mode: 'manual', approval: 'user', sandbox: 'workspace', network: 'restricted' },
  { mode: 'auto', approval: 'classifier', sandbox: 'workspace', network: 'restricted' },
  { mode: 'full', approval: 'none', sandbox: 'disabled', network: 'open' },
]

describe('PERMISSION_PRESETS 三轴', () => {
  it('档位与文档表同序同量', () => {
    expect(PERMISSION_PRESETS.map((preset) => preset.mode)).toEqual(
      DOC_TABLE.map((row) => row.mode),
    )
  })

  for (const row of DOC_TABLE) {
    it(`${row.mode} = approval:${row.approval} + sandbox:${row.sandbox} + network:${row.network}`, () => {
      const preset = presetOf(row.mode)

      expect(preset.approval).toBe(row.approval)
      expect(preset.sandbox).toBe(row.sandbox)
      expect(preset.network).toBe(row.network)
    })
  }

  it('只有 full 需要显式授权', () => {
    for (const preset of PERMISSION_PRESETS) {
      expect(preset.needsFullAck).toBe(preset.mode === 'full')
    }
  })

  it('每个档都有名字与一句代价说明', () => {
    for (const preset of PERMISSION_PRESETS) {
      expect(preset.label.length).toBeGreaterThan(0)
      expect(preset.caveat.length).toBeGreaterThan(0)
    }
  })
})

describe('buildStartRunInput', () => {
  it('透传 prompt 与 permission', () => {
    expect(buildStartRunInput({ prompt: '读 pyproject.toml', mode: 'auto' })).toEqual({
      prompt: '读 pyproject.toml',
      permission: 'auto',
    })
  })

  it('branch 与 autoApprove 给了才写', () => {
    const bare = buildStartRunInput({ prompt: 'x', mode: 'manual' })
    expect('branch' in bare).toBe(false)
    expect('auto_approve' in bare).toBe(false)

    const full = buildStartRunInput({
      prompt: 'x',
      mode: 'manual',
      branch: 'b2',
      autoApprove: true,
    })
    expect(full.branch).toBe('b2')
    expect(full.auto_approve).toBe(true)
  })

  it('full 不带 ack 时 full_access_ack 不是 true，且整个键不出现', () => {
    const input = buildStartRunInput({ prompt: 'rm -rf /', mode: 'full' })

    expect(input.full_access_ack).not.toBe(true)
    expect('full_access_ack' in input).toBe(false)
  })

  it('full 显式为 true 时才写 ack', () => {
    expect(
      buildStartRunInput({ prompt: 'rm -rf /', mode: 'full', fullAck: true }).full_access_ack,
    ).toBe(true)
  })

  it('full 显式为 false 时不写 ack', () => {
    const input = buildStartRunInput({ prompt: 'x', mode: 'full', fullAck: false })

    expect('full_access_ack' in input).toBe(false)
  })

  it('非 full 档即便传了 fullAck 也不写 ack', () => {
    // 免得某个调用点"统一传 fullAck: true"把 ack 泄露到 manual/auto 上。
    for (const mode of ['manual', 'auto'] as const) {
      const input = buildStartRunInput({ prompt: 'x', mode, fullAck: true })

      expect('full_access_ack' in input).toBe(false)
    }
  })
})
