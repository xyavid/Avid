import { describe, expect, it, vi } from 'vitest'

import { createSseParser } from '../events'

describe('SSE 解析（avid web 的帧格式）', () => {
  it('解析 event/data/id 三行帧，业务字段在 data 里（与 event_payload 同形）', () => {
    const events = vi.fn()
    const parser = createSseParser({ onEvent: events })

    parser.feed('id: 7\nevent: user_message\ndata: {"type":"user_message","seq":7,"data":{"entry_id":"e1","message":{"role":"user","content":"跑一下"}}}\n\n')

    expect(events).toHaveBeenCalledOnce()
    const e = events.mock.calls[0]?.[0]
    expect(e.type).toBe('user_message')
    expect(e.seq).toBe(7)
    expect(e.data.message.content).toBe('跑一下')
  })

  it('心跳注释帧（: ping）被跳过，不产生事件', () => {
    const events = vi.fn()
    const parser = createSseParser({ onEvent: events })

    parser.feed(': ping\n\nid: 8\nevent: run_finished\ndata: {"type":"run_finished","seq":8,"data":{}}\n\n')

    expect(events).toHaveBeenCalledOnce()
    expect(events.mock.calls[0]?.[0].type).toBe('run_finished')
  })

  it('跨 chunk 的帧能拼接；delta 无 id 行 → seq 归一为 null', () => {
    const events = vi.fn()
    const parser = createSseParser({ onEvent: events })

    parser.feed('event: assistant_delta\ndata: {"type":"assistant_d')
    parser.feed('elta","data":{"text":"好"}}\n\n')

    const e = events.mock.calls[0]?.[0]
    expect(e.type).toBe('assistant_delta')
    expect(e.seq).toBeNull()
    expect(e.data.text).toBe('好')
  })

  it('data 多行拼接（SSE 规范）：多行 data 以换行合并后解析', () => {
    const events = vi.fn()
    const parser = createSseParser({ onEvent: events })

    parser.feed('event: x\ndata: {"a":\ndata: 1}\n\n')
    expect(events.mock.calls[0]?.[0]).toEqual({ a: 1, seq: null })
  })

  it('非法 JSON 的 data 块被丢弃，不中断后续帧', () => {
    const events = vi.fn()
    const parser = createSseParser({ onEvent: events })

    parser.feed('event: bad\ndata: {oops\n\nevent: good\ndata: {"type":"run_status","data":{}}\n\n')
    expect(events).toHaveBeenCalledOnce()
    expect(events.mock.calls[0]?.[0].type).toBe('run_status')
  })
})
