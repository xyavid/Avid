export interface JsonViewProps {
  value: unknown
}

/** 原始 JSON 视图：把内核发来的东西原样给出来（排查协议问题时的第一手材料）。 */
export function JsonView({ value }: JsonViewProps) {
  let text: string
  try {
    text = JSON.stringify(value, null, 2)
  } catch (error) {
    text = String((error as Error)?.message ?? value)
  }
  return (
    <pre className="term overflow-x-auto whitespace-pre-wrap break-anywhere p-3 text-[11px]">
      {text}
    </pre>
  )
}
