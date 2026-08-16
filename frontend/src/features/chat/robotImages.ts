import ivoryRobot from '../../assets/robot-ivory.png'
import graphiteRobot from '../../assets/robot-graphite.png'
import terracottaRobot from '../../assets/robot-terracotta.png'

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
  's8-luna': { src: ivoryRobot, variant: 'ivory-luna' },
  's8-air': { src: ivoryRobot, variant: 'ivory-air' },
  'x9-obsidian': { src: graphiteRobot, variant: 'graphite-obsidian' },
  'x9-edge': { src: graphiteRobot, variant: 'graphite-edge' },
  'm6-terra': { src: terracottaRobot, variant: 'terracotta-terra' },
  'm6-mini': { src: terracottaRobot, variant: 'terracotta-mini' },
}
