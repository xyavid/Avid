// @vitest-environment jsdom
/**
 * 骨架的第一条用例，也是唯一一条。
 *
 * 它守的不是"页面长什么样"（那还没有设计），而是**链路还在**：入口能挂载、JSX 能编译、
 * DOM 能渲染。没有一个会失败的用例时，`pnpm test` 会以 0 个用例退出 0，门禁就此空转。
 *
 * `afterEach(cleanup)` 不是为了好看：vitest 没有开 `globals` 时 Testing Library 的
 * 自动清理不会注册，两次 render 的 DOM 会叠在一起，按文本查询就会命中多个节点——
 * 实测过，第二个用例就是这么红的。
 */

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { App } from './App'

afterEach(cleanup)

describe('App 占位壳', () => {
  it('渲染出挂载点内容', () => {
    render(<App />)

    expect(screen.getByRole('heading').textContent).toBe('Avid')
  })

  it('显式声明设计未定，而不是留一块空白', () => {
    render(<App />)

    expect(screen.getByText(/待定/)).toBeTruthy()
  })
})
