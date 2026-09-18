/**
 * 错误码 → 文案：服务端的 code 是稳定字符串，前端按它取词条；缺词条时回落
 * 「未知错误：{code}」而不是显示一个裸 code。
 */

import { useTranslation } from '../lib/i18n'

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
])

export function useErrorText(): (code: string | undefined, fallback?: string) => string {
  const { t } = useTranslation()
  return (code, fallback) => {
    if (fallback) return fallback
    if (!code) return t('errors.unknown', { code: 'unknown' })
    if (KNOWN.has(code)) return t(`errors.${code}`)
    return t('errors.unknown', { code })
  }
}
