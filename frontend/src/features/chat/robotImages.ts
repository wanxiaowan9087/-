import s8LunaRobot from '../../assets/robot-s8-luna.png'
import s8AirRobot from '../../assets/robot-s8-air.png'
import x9ObsidianRobot from '../../assets/robot-x9-obsidian.png'
import x9EdgeRobot from '../../assets/robot-x9-edge.png'
import m6TerraRobot from '../../assets/robot-m6-terra.png'
import m6MiniRobot from '../../assets/robot-m6-mini.png'

export type RobotImageAsset = {
  src: string
}

export const robotImages: Record<string, RobotImageAsset> = {
  's8-luna': { src: s8LunaRobot },
  's8-air': { src: s8AirRobot },
  'x9-obsidian': { src: x9ObsidianRobot },
  'x9-edge': { src: x9EdgeRobot },
  'm6-terra': { src: m6TerraRobot },
  'm6-mini': { src: m6MiniRobot },
}
