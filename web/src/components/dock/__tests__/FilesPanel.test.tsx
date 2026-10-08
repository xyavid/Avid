// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { FilesPanel, crumbs } from '../FilesPanel'

const listFiles = vi.fn()
const readFile = vi.fn()

// vi.mock 的工厂会被提升到文件顶部，类定义必须跟着一起提升（vi.hoisted）。
const { FakeApiError } = vi.hoisted(() => {
  class FakeApiError extends Error {
    readonly code = 'file_outside'
    readonly status = 403
    readonly detail = null
  }
  return { FakeApiError }
})

vi.mock('../../../api/client', () => ({
  ApiError: FakeApiError,
  listFiles: (...args: unknown[]) => listFiles(...args),
  readFile: (...args: unknown[]) => readFile(...args),
}))

afterEach(() => {
  cleanup()
  listFiles.mockReset()
  readFile.mockReset()
})

const root = {
  path: '',
  parent: null,
  truncated: false,
  entries: [
    { name: 'src', path: 'src', kind: 'dir' as const, size: null },
    { name: 'README.md', path: 'README.md', kind: 'file' as const, size: 2048 },
  ],
}

describe('工作区文件面板', () => {
  it('面包屑：根 + 每一级，都能点回去', () => {
    expect(crumbs('')).toEqual([{ name: '工作区', path: '' }])
    expect(crumbs('src/lib')).toEqual([
      { name: '工作区', path: '' },
      { name: 'src', path: 'src' },
      { name: 'lib', path: 'src/lib' },
    ])
  })

  it('进面板落在工作区根：目录在前、文件带大小', async () => {
    listFiles.mockResolvedValue(root)
    render(<FilesPanel workspaceId="w1" />)

    expect(await screen.findByText('src')).toBeTruthy()
    expect(screen.getByText('README.md')).toBeTruthy()
    expect(screen.getByText('2.0 KB')).toBeTruthy()
    expect(listFiles).toHaveBeenCalledWith('w1', '')
  })

  it('点目录进下一级，点文件读内容；面包屑跟着走', async () => {
    listFiles.mockResolvedValue(root)
    render(<FilesPanel workspaceId="w1" />)
    fireEvent.click(await screen.findByText('src'))
    expect(listFiles).toHaveBeenLastCalledWith('w1', 'src')

    listFiles.mockResolvedValue({
      path: 'src',
      parent: '',
      truncated: false,
      entries: [{ name: 'main.py', path: 'src/main.py', kind: 'file', size: 12 }],
    })
    fireEvent.click(screen.getByText('src'))
    expect(await screen.findByText('main.py')).toBeTruthy()

    readFile.mockResolvedValue({ path: 'src/main.py', size: 12, text: 'print(1)\n', binary: false, truncated: false })
    fireEvent.click(screen.getByText('main.py'))
    expect(await screen.findByText(/print\(1\)/)).toBeTruthy()
    expect(readFile).toHaveBeenCalledWith('w1', 'src/main.py')
  })

  it('后端的拒绝原样转述（不另编一套说法）', async () => {
    listFiles.mockRejectedValue(new FakeApiError('只能在当前工作区内浏览'))
    render(<FilesPanel workspaceId="w1" />)

    expect(await screen.findByText('只能在当前工作区内浏览')).toBeTruthy()
  })

  it('没有选中会话时给空态，不打请求', () => {
    render(<FilesPanel workspaceId={null} />)

    expect(screen.getByText(/选中一个会话后/)).toBeTruthy()
    expect(listFiles).not.toHaveBeenCalled()
  })

  it('二进制只报事实，不预览内容', async () => {
    listFiles.mockResolvedValue({
      path: '',
      parent: null,
      truncated: false,
      entries: [{ name: 'blob.bin', path: 'blob.bin', kind: 'file', size: 9 }],
    })
    readFile.mockResolvedValue({ path: 'blob.bin', size: 9, text: null, binary: true, truncated: false })
    render(<FilesPanel workspaceId="w1" />)

    fireEvent.click(await screen.findByText('blob.bin'))
    expect(await screen.findByText('二进制文件，不预览内容')).toBeTruthy()
  })
})
