// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { SessionsDir } from '../../../api/types'
import { SessionsSection } from '../SessionsSection'

const getSessionsDir = vi.fn()
const setSessionsDir = vi.fn()
const pickFolder = vi.fn()

vi.mock('../../../api/client', () => ({
  ApiError: class ApiError extends Error {},
  getSessionsDir: (...a: unknown[]) => getSessionsDir(...a),
  setSessionsDir: (...a: unknown[]) => setSessionsDir(...a),
  pickFolder: (...a: unknown[]) => pickFolder(...a),
}))

const DEFAULT: SessionsDir = {
  dir: '/home/u/.avid/sessions',
  default_dir: '/home/u/.avid/sessions',
  source: 'default',
  editable: true,
}

beforeEach(() => {
  getSessionsDir.mockReset().mockResolvedValue(DEFAULT)
  setSessionsDir.mockReset()
  pickFolder.mockReset().mockResolvedValue({ path: null })
})
afterEach(cleanup)

describe('SessionsSection（设置 · 会话存储）', () => {
  it('显示当前目录与来源，未改动时保存钮不可按', async () => {
    render(<SessionsSection />)

    await waitFor(() => expect(screen.getByText(DEFAULT.dir)).toBeTruthy())
    expect(screen.getByText(/默认位置/)).toBeTruthy()
    expect((screen.getByRole('button', { name: '保存位置' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('输入新路径后保存：把新目录写进服务端，并说明旧会话留在原处', async () => {
    setSessionsDir.mockResolvedValue({
      dir: '/data/avid-sessions',
      default_dir: DEFAULT.default_dir,
      source: 'settings',
      editable: true,
    })
    render(<SessionsSection />)
    await waitFor(() => expect(screen.getByLabelText('会话目录')).toBeTruthy())

    fireEvent.change(screen.getByLabelText('会话目录'), { target: { value: '/data/avid-sessions' } })
    fireEvent.click(screen.getByRole('button', { name: '保存位置' }))

    await waitFor(() => expect(setSessionsDir).toHaveBeenCalledWith('/data/avid-sessions'))
    await waitFor(() => expect(screen.getByText(/已改到新目录/)).toBeTruthy())
  })

  it('选择文件夹只填路径，不保存', async () => {
    pickFolder.mockResolvedValue({ path: '/mnt/disk/sessions' })
    render(<SessionsSection />)
    await waitFor(() => expect(screen.getByRole('button', { name: '选择文件夹' })).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: '选择文件夹' }))

    await waitFor(() =>
      expect((screen.getByLabelText('会话目录') as HTMLInputElement).value).toBe('/mnt/disk/sessions'),
    )
    expect(setSessionsDir).not.toHaveBeenCalled()
  })

  it('恢复默认只在来源是设置文件时可用，点了就写空串', async () => {
    getSessionsDir.mockResolvedValue({
      dir: '/data/avid-sessions',
      default_dir: DEFAULT.default_dir,
      source: 'settings',
      editable: true,
    })
    setSessionsDir.mockResolvedValue(DEFAULT)
    render(<SessionsSection />)
    await waitFor(() =>
      expect((screen.getByRole('button', { name: '恢复默认' }) as HTMLButtonElement).disabled).toBe(false),
    )

    fireEvent.click(screen.getByRole('button', { name: '恢复默认' }))

    await waitFor(() => expect(setSessionsDir).toHaveBeenCalledWith(''))
    await waitFor(() => expect(screen.getByText(/已恢复默认位置/)).toBeTruthy())
  })

  it('环境变量赢时整段只读，并说明为什么改不动', async () => {
    getSessionsDir.mockResolvedValue({
      dir: '/from/env',
      default_dir: DEFAULT.default_dir,
      source: 'env',
      editable: false,
    })
    render(<SessionsSection />)

    await waitFor(() => expect(screen.getByText('/from/env')).toBeTruthy())
    expect((screen.getByLabelText('会话目录') as HTMLInputElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: '保存位置' }) as HTMLButtonElement).disabled).toBe(true)
    expect(
      (screen.getByRole('button', { name: '选择文件夹' }) as HTMLButtonElement).disabled,
    ).toBe(true)
    expect(screen.getByText(/改这里不会生效/)).toBeTruthy()
  })

  it('保存失败（比如相对路径被拒）把服务端的原话显示出来', async () => {
    setSessionsDir.mockRejectedValue(new Error('要一个绝对路径：relative/dir'))
    render(<SessionsSection />)
    await waitFor(() => expect(screen.getByLabelText('会话目录')).toBeTruthy())

    fireEvent.change(screen.getByLabelText('会话目录'), { target: { value: 'relative/dir' } })
    fireEvent.click(screen.getByRole('button', { name: '保存位置' }))

    await waitFor(() => expect(screen.getByText('要一个绝对路径：relative/dir')).toBeTruthy())
  })
})
