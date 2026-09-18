/**
 * 错误码 → 文案：服务端的 code 是稳定字符串，前端按它取词条；缺词条时回落
 * 「未知错误：{code}」而不是显示一个裸 code。
 *
 * 两条纪律：
 * * **已知 code 一律走词条**，不回显服务端给的 message。`internal` 的 message 曾经
 *   是 `f"{type(exc).__name__}: {exc}"`（可能带绝对路径），直接渲染等于把内核内部
 *   细节贴到界面上；现在后端只回一个 error_id，细节在服务端日志里。
 * * 只有**没有 code**（网络层失败之类）时才用 fallback 的原始文本。
 */

import { useTranslation } from '../lib/i18n'
import type { TranslateVars } from '../lib/i18n'

const KNOWN = new Set([
  'run_busy',
  'run_not_found',
  'run_already_finished',
  'session_not_found',
  'session_exists',
  'session_busy',
  'session_error',
  'workspace_required',
  'workspace_not_found',
  'workspace_invalid',
  'workspace_exists',
  'picker_unavailable',
  'picker_busy',
  'picker_failed',
  'approval_not_found',
  'approval_expired',
  'approval_resolved',
  'task_not_found',
  'task_corrupt',
  'invalid_schema',
  'invalid_request',
  'static_missing',
  'not_found',
  'network_error',
  'timeout',
  'aborted',
  'bad_json',
  'unexpected_response',
  'config_error',
  'llm_error',
  'round_limit',
  'internal',
  'too_many_streams',
])

type Translate = (key: string, params?: TranslateVars) => string

function codeText(t: Translate, code: string): string {
  return KNOWN.has(code) ? t(`errors.${code}`) : t('errors.unknown', { code })
}

/** 统一的错误文案（查询失败等"只有 error 对象"的场景，三处 feature 共用一份）。 */
export function errorMessage(t: Translate, error: unknown): string {
  if (error instanceof Error && 'code' in error && typeof error.code === 'string') {
    return codeText(t, error.code)
  }
  return t('common.networkError')
}

/**
 * 运行错误条用：事件流/注册表给的 `{code, message}`。
 *
 * 已知 code 只出词条（见文件头）；未知 code 出「未知错误：{code}」；没有 code 时
 * 才显示 fallback（那是网络层的原始信息，不含服务端内部细节）。
 */
export function useErrorText(): (code: string | undefined, fallback?: string) => string {
  const { t } = useTranslation()
  return (code, fallback) => {
    if (code) return codeText(t, code)
    return fallback ?? t('errors.unknown', { code: 'unknown' })
  }
}
