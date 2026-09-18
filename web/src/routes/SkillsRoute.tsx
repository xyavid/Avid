import { SkillCatalog } from '../features/skills'

/** L4：技能目录（name + 一行描述，与 system prompt 同源）。 */
export function SkillsRoute() {
  return (
    <div className="scroll-area min-h-0 min-w-0 flex-1">
      <SkillCatalog />
    </div>
  )
}
