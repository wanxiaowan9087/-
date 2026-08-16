import s8LunaRobot from '../../assets/robot-s8-luna.png'
import s8AirRobot from '../../assets/robot-s8-air.png'
import x9ObsidianRobot from '../../assets/robot-x9-obsidian.png'
import x9EdgeRobot from '../../assets/robot-x9-edge.png'
import m6TerraRobot from '../../assets/robot-m6-terra.png'
import m6MiniRobot from '../../assets/robot-m6-mini.png'

export type RobotImageVariant =
  | 'ivory-luna'
  | 'ivory-air'
  | 'graphite-obsidian'
  | 'graphite-edge'
  | 'terracotta-terra'
  | 'terracotta-mini'

export type RobotImageAsset = {
  src: string
  variant: RobotImageVariant
}

export const robotImages: Record<string, RobotImageAsset> = {
  's8-luna': { src: s8LunaRobot, variant: 'ivory-luna' },
  's8-air': { src: s8AirRobot, variant: 'ivory-air' },
  'x9-obsidian': { src: x9ObsidianRobot, variant: 'graphite-obsidian' },
  'x9-edge': { src: x9EdgeRobot, variant: 'graphite-edge' },
  'm6-terra': { src: m6TerraRobot, variant: 'terracotta-terra' },
  'm6-mini': { src: m6MiniRobot, variant: 'terracotta-mini' },
}
