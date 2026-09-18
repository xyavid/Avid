import { SettingsPanel } from '../features/settings'

/** L4：设置（读 `/api/meta`；改配置仍走 CLI 与环境变量）。 */
export function SettingsRoute() {
  return (
    <div className="min-h-0 min-w-0 flex-1">
      <SettingsPanel />
    </div>
  )
}
