/** 极简类名拼接：真值才收进来。组件层不引第三方依赖。 */
export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ')
}
