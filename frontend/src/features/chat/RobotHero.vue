<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import homeImage from '../../assets/warm-home-hero.png'
import assistantImage from '../../assets/assistant-xiaozhi.png'
import assistantWaveImage from '../../assets/assistant-xiaozhi-wave.png'
import assistantPointImage from '../../assets/assistant-xiaozhi-point.png'
import { robotImages } from './robotImages'
import { robotModels } from './robotModels'
import Product3DViewer from './Product3DViewer.vue'

const props = defineProps<{
  authenticated: boolean
  requestedProductId?: string | null
}>()

const emit = defineEmits<{
  consult: []
  authRequired: [productId: string]
  usageEvent: [eventType: 'product_detail_viewed' | 'product_3d_viewed', productId: string]
}>()

type RobotProduct = {
  id: string
  image: string
  modelUrl: string
  name: string
  subtitle: string
  description: string
  stat: string
  accent: string
  price: number
  priceNote: string
  features: string[]
  scene: string
  floor: string
  care: string
  heroIndex: number
  featured?: boolean
}

const products: RobotProduct[] = [
  {
    id: 's8-luna', image: robotImages['s8-luna'].src, modelUrl: robotModels['s8-luna'], name: 'S8 皓月', subtitle: '静音深度清洁',
    description: '面向一居室、儿童房与夜间清扫；56 dB 静音运行，双线激光识别桌腿、电线和低矮障碍。', stat: '56 dB', accent: '#d9b07a', price: 3499, priceNote: '含基础上门安装与一年质保',
    features: ['双线激光避障', '零缠绕主刷', '静音夜航模式'], scene: '一居室、儿童房、夜间清扫', floor: '木地板 / 瓷砖 / 短毛地毯', care: '避障优先，降低夜间噪声', heroIndex: 0, featured: true,
  },
  {
    id: 'x9-obsidian', image: robotImages['x9-obsidian'].src, modelUrl: robotModels['x9-obsidian'], name: 'X9 曜石', subtitle: '全屋导航旗舰',
    description: '面向三居及复式大户型；12,000 Pa 吸力搭配多层地图，能按房间、楼层与清洁顺序自动重规划。', stat: '12,000 Pa', accent: '#7ec5d7', price: 4999, priceNote: '含自动集尘基站与两年质保',
    features: ['D 型边角贴合', '多层地图记忆', '12,000 Pa 强劲吸力'], scene: '三居、复式与开放式客餐厅', floor: '瓷砖 / 木地板 / 中短毛地毯', care: '大面积高频吸尘与分区管理', heroIndex: 1, featured: true,
  },
  {
    id: 'm6-terra', image: robotImages['m6-terra'].src, modelUrl: robotModels['m6-terra'], name: 'M6 霞陶', subtitle: '地面精细护理',
    description: '面向地毯与硬质地面混铺家庭；双旋拖布遇地毯自动抬升，180 分钟续航覆盖大面积拖洗。', stat: '180 min', accent: '#c9825d', price: 4599, priceNote: '含旋转拖布套件与两年质保',
    features: ['双旋拖布升降', '地毯自动增压', '180 分钟续航'], scene: '地毯混铺、餐桌区与高频拖洗', floor: '木地板 / 瓷砖 / 地毯混铺', care: '拖布抬升，避免湿拖地毯', heroIndex: 2, featured: true,
  },
  {
    id: 's8-air', image: robotImages['s8-air'].src, modelUrl: robotModels['s8-air'], name: 'S8 Air', subtitle: '小户型轻量方案',
    description: '面向租房、一居与书房；纤薄机身可进入沙发、床底，宠物毛发模式减少毛发缠绕和重复回扫。', stat: '0.4 L', accent: '#b9996d', price: 2799, priceNote: '含耗材礼包与一年质保',
    features: ['纤薄机身', '宠物毛发模式', '一键分区清洁'], scene: '租房、一居、书房与养宠家庭', floor: '木地板 / 瓷砖 / 低矮家具底部', care: '轻量日常维护与毛发收集', heroIndex: 0,
  },
  {
    id: 'x9-edge', image: robotImages['x9-edge'].src, modelUrl: robotModels['x9-edge'], name: 'X9 Edge', subtitle: '边角强化清洁',
    description: '面向桌椅多、墙根多的户型；D 型机身与伸缩边刷贴近踢脚线，重点补扫墙角和餐桌腿周边。', stat: '99.2%', accent: '#679fb1', price: 4299, priceNote: '含边角清洁套件与两年质保',
    features: ['毫米级贴边', '伸缩边刷', '家具识别建图'], scene: '餐桌区、桌椅密集与复杂墙角', floor: '瓷砖 / 木地板 / 踢脚线边缘', care: '贴边补扫，减少转角遗漏', heroIndex: 1,
  },
  {
    id: 'm6-mini', image: robotImages['m6-mini'].src, modelUrl: robotModels['m6-mini'], name: 'M6 Mini', subtitle: '木地板温柔护理',
    description: '面向原木地板、婴幼儿活动区；三档电子控水避免积水，低压拖洗适合日常浮灰与轻污渍。', stat: '3 档水量', accent: '#ad7057', price: 3299, priceNote: '含地板护理拖布与一年质保',
    features: ['电子水量控制', '可拆洗拖布盘', '低噪缓行模式'], scene: '原木地板、儿童活动区与轻污渍', floor: '原木 / 复合木地板 / 瓷砖', care: '控水湿拖，降低地板受潮风险', heroIndex: 2,
  },
]

const robots = products.filter((product) => product.featured)
const activeIndex = ref(1)
const isAnimating = ref(false)
const selectedProduct = ref<RobotProduct | null>(null)
const detailMode = ref<'image' | '3d'>('image')
const assistantActing = ref(false)
const activeRobot = computed(() => robots[activeIndex.value])

function navigate(direction: 'next' | 'previous') {
  if (isAnimating.value) return
  isAnimating.value = true
  activeIndex.value = direction === 'next'
    ? (activeIndex.value + 1) % robots.length
    : (activeIndex.value + robots.length - 1) % robots.length
  globalThis.setTimeout(() => { isAnimating.value = false }, 620)
}

function selectRobot(index: number) {
  if (index === activeIndex.value || isAnimating.value) return
  isAnimating.value = true
  activeIndex.value = index
  globalThis.setTimeout(() => { isAnimating.value = false }, 620)
}

function roleFor(index: number) {
  if (index === activeIndex.value) return 'center'
  return index === (activeIndex.value + robots.length - 1) % robots.length ? 'left' : 'right'
}

function openProduct(product: RobotProduct) {
  if (!props.authenticated) {
    emit('authRequired', product.id)
    return
  }
  selectedProduct.value = product
  detailMode.value = 'image'
  emit('usageEvent', 'product_detail_viewed', product.id)
}

watch(
  () => props.requestedProductId,
  (productId) => {
    if (!props.authenticated || !productId) return
    selectedProduct.value = products.find(product => product.id === productId) ?? null
    detailMode.value = 'image'
    if (selectedProduct.value) emit('usageEvent', 'product_detail_viewed', selectedProduct.value.id)
  },
)

function startConsultation() {
  selectedProduct.value = null
  emit('consult')
}

function activateAssistant() {
  if (assistantActing.value) return
  assistantActing.value = true
  globalThis.setTimeout(() => emit('consult'), 460)
}

</script>

<template>
  <section class="robot-hero" :style="{ '--home-image': `url(${homeImage})`, '--accent': activeRobot.accent }" aria-label="智扫通扫地机器人系列">
    <div class="robot-hero__grain" aria-hidden="true"></div>
    <div class="robot-hero__wash" aria-hidden="true"></div>
    <p class="robot-hero__ghost" aria-hidden="true">CLEAN / HOME</p>
    <header class="robot-hero__header"><span class="robot-hero__brand">智扫通 · ZENMOP</span><span>HOME INTELLIGENCE / 2026</span></header>

    <div class="robot-hero__carousel" aria-label="机器人型号轮播">
      <button v-for="(robot, index) in robots" :key="robot.id" class="robot-hero__robot" :class="[`is-${roleFor(index)}`, { 'is-active': index === activeIndex }]" type="button" :aria-label="`查看 ${robot.name}`" @mouseenter="selectRobot(index)" @focus="selectRobot(index)" @click="index === activeIndex ? openProduct(robot) : selectRobot(index)">
        <img :src="robot.image" :alt="`${robot.name} 扫地机器人`" draggable="false" />
        <span class="robot-hero__model">{{ robot.name }} · 查看价格</span>
      </button>
    </div>

    <div class="robot-hero__copy" aria-live="polite">
      <p class="robot-hero__eyebrow">{{ activeRobot.subtitle }}</p>
      <h1 :key="activeRobot.name">让家的每一处<br />都有被照顾的秩序。</h1>
      <p>{{ activeRobot.description }}</p>
      <div class="robot-hero__meter"><span>核心能力</span><i></i><b>{{ activeRobot.stat }}</b></div>
      <button class="robot-hero__consult" type="button" @click="openProduct(activeRobot)">查看 {{ activeRobot.name }} 价格 <span>→</span></button>
    </div>

    <div class="robot-hero__navigation">
      <button type="button" aria-label="上一款机器人" @click="navigate('previous')">←</button><button type="button" aria-label="下一款机器人" @click="navigate('next')">→</button>
    </div>
    <button class="robot-hero__assistant" :class="{ 'is-activating': assistantActing }" type="button" aria-label="向小智发起智能诊断" @click="activateAssistant">
      <span class="robot-hero__assistant-character" aria-hidden="true">
        <img class="assistant-frame assistant-frame--idle" :src="assistantImage" alt="" />
        <img class="assistant-frame assistant-frame--wave" :src="assistantWaveImage" alt="" />
        <img class="assistant-frame assistant-frame--point" :src="assistantPointImage" alt="" />
      </span>
      <span class="robot-hero__assistant-copy"><b>小智</b><small>智能诊断</small></span>
      <i aria-hidden="true">✦</i>
    </button>
    <a class="robot-hero__discover" href="#robot-lineup">探索全系 <span>↓</span></a>
  </section>

  <section id="robot-lineup" class="robot-lineup" aria-labelledby="robot-lineup-title">
    <div class="robot-lineup__intro"><p class="eyebrow">SIX WAYS TO CARE</p><h2 id="robot-lineup-title">每一种居住方式，<br />都有一台恰好的机器人。</h2><p>六款模拟产品覆盖静音日常、边角强化、地板护理等不同场景。悬停查看动态反馈，点击任意产品卡即可查看价格与配置。</p></div>
    <div class="robot-lineup__catalog">
      <button v-for="(robot, index) in products" :key="robot.id" class="robot-lineup__card" :style="{ '--card-accent': robot.accent, '--card-order': index }" type="button" @mouseenter="selectRobot(robot.heroIndex)" @focus="selectRobot(robot.heroIndex)" @click="openProduct(robot)">
        <div class="robot-lineup__head"><p>0{{ index + 1 }} / SERIES</p><h3>{{ robot.name }}</h3><b>{{ robot.subtitle }}</b></div>
         <div class="robot-lineup__image" :class="`robot-lineup__image--shape-${robot.heroIndex}`"><img :src="robot.image" :alt="`${robot.name} 机身细节`" /><span class="robot-lineup__focus-frame" aria-hidden="true"><b>{{ robot.name }}</b></span></div>
        <div class="robot-lineup__foot"><small>适用：{{ robot.scene }}</small><span>{{ robot.description }}</span><i>查看配置与价格 <strong>→</strong></i></div>
      </button>
    </div>
  </section>

  <Teleport to="body">
    <div v-if="selectedProduct" class="robot-dialog" role="presentation" @click.self="selectedProduct = null">
      <section class="robot-dialog__panel" :class="{ 'is-3d': detailMode === '3d' }" role="dialog" aria-modal="true" :aria-labelledby="`product-${selectedProduct.id}`">
        <button class="robot-dialog__close" type="button" aria-label="关闭产品详情" @click="selectedProduct = null">×</button>
        <div v-if="detailMode === 'image'" class="robot-dialog__visual" :style="{ '--dialog-accent': selectedProduct.accent }"><img :src="selectedProduct.image" :alt="selectedProduct.name" /></div>
        <Product3DViewer v-else :model-url="selectedProduct.modelUrl" :product-name="selectedProduct.name" :accent="selectedProduct.accent" :fallback-image="selectedProduct.image" />
        <div class="robot-dialog__content">
          <p>ZENMOP / SMART HOME</p><h2 :id="`product-${selectedProduct.id}`">{{ selectedProduct.name }}</h2><b>{{ selectedProduct.subtitle }}</b>
          <div class="robot-dialog__modes" role="tablist" aria-label="产品展示方式">
            <button type="button" role="tab" :aria-selected="detailMode === 'image'" :class="{ active: detailMode === 'image' }" @click="detailMode = 'image'">产品图</button>
            <button type="button" role="tab" :aria-selected="detailMode === '3d'" :class="{ active: detailMode === '3d' }" @click="detailMode = '3d'; emit('usageEvent', 'product_3d_viewed', selectedProduct.id)">3D 效果</button>
          </div>
          <span class="robot-dialog__price">¥ {{ selectedProduct.price.toLocaleString('zh-CN') }}</span><small>模拟起售价 · {{ selectedProduct.priceNote }}</small>
          <p class="robot-dialog__description">{{ selectedProduct.description }}</p>
          <dl class="robot-dialog__facts"><div><dt>适用场景</dt><dd>{{ selectedProduct.scene }}</dd></div><div><dt>推荐地面</dt><dd>{{ selectedProduct.floor }}</dd></div><div><dt>清洁重点</dt><dd>{{ selectedProduct.care }}</dd></div></dl>
          <ul><li v-for="feature in selectedProduct.features" :key="feature">{{ feature }}</li></ul>
          <button class="robot-dialog__consult" type="button" @click="startConsultation">让小智帮我选 →</button>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.robot-hero { min-height: calc(100vh - 64px); position: relative; isolation: isolate; overflow: hidden; color: #fff8ec; background: #3b2d23 var(--home-image) center/cover no-repeat; }
.robot-hero::after { content: ''; position: absolute; inset: 0; z-index: -1; background: linear-gradient(90deg, rgba(37, 27, 20, .74), rgba(54, 40, 29, .16) 51%, rgba(38, 28, 20, .25)), linear-gradient(0deg, rgba(33, 25, 18, .5), transparent 38%); }
.robot-hero__wash { position: absolute; z-index: -1; inset: 0; background: radial-gradient(circle at 53% 46%, rgba(255, 239, 198, .24), transparent 25%), radial-gradient(circle at 73% 55%, rgba(120, 203, 219, .13), transparent 23%); pointer-events: none; }
.robot-hero__grain { position: absolute; inset: 0; z-index: 5; pointer-events: none; opacity: .14; background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 180 180' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.88' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='.35'/%3E%3C/svg%3E"); }
.robot-hero__ghost { margin: 0; position: absolute; z-index: 0; left: 49%; top: 13%; transform: translateX(-50%); color: rgba(255, 247, 229, .42); font: 700 clamp(65px, 14vw, 240px)/.86 Impact, "Arial Narrow", sans-serif; letter-spacing: -.055em; white-space: nowrap; user-select: none; }
.robot-hero__header { padding: 25px clamp(22px, 4vw, 68px); position: relative; z-index: 7; display: flex; justify-content: space-between; color: rgba(255, 248, 235, .82); font: 10px "Cascadia Mono", monospace; letter-spacing: .12em; }
.robot-hero__brand { color: #fff9ef; font: 700 13px "STSong", "Noto Serif CJK SC", serif; letter-spacing: .16em; }
.robot-hero__carousel { position: absolute; z-index: 2; inset: 0; }
.robot-hero__robot { width: clamp(210px, 34vw, 500px); height: clamp(210px, 34vw, 500px); padding: 0; position: absolute; bottom: clamp(7%, 7vw, 14%); border: 0; background: transparent; cursor: pointer; transition: left .62s cubic-bezier(.4,0,.2,1), transform .62s cubic-bezier(.4,0,.2,1), filter .62s ease, opacity .62s ease; }
.robot-hero__robot img { width: 100%; height: 100%; display: block; object-fit: contain; filter: drop-shadow(0 32px 24px rgba(43, 25, 11, .34)); transition: transform .4s ease, filter .4s ease; }
.robot-hero__robot.is-center { left: 50%; z-index: 3; opacity: 1; transform: translateX(-50%) scale(1.16); }.robot-hero__robot.is-left { left: 30%; z-index: 2; opacity: .68; filter: blur(1.6px); transform: translateX(-50%) scale(.64); }.robot-hero__robot.is-right { left: 73%; z-index: 1; opacity: .62; filter: blur(2.2px); transform: translateX(-50%) scale(.58); }
.robot-hero__robot.is-center:hover img { transform: translateY(-10px) rotate(-1deg); filter: drop-shadow(0 42px 28px rgba(37, 22, 10, .44)); }.robot-hero__model { position: absolute; right: 7%; bottom: -7%; color: rgba(255, 248, 235, .8); font: 10px "Cascadia Mono", monospace; letter-spacing: .1em; opacity: 0; transition: opacity .25s ease; }.robot-hero__robot.is-center .robot-hero__model { opacity: 1; }
.robot-hero__copy { width: min(364px, 34vw); position: absolute; z-index: 6; left: clamp(22px, 8vw, 132px); bottom: clamp(48px, 10vh, 110px); }.robot-hero__eyebrow { margin: 0 0 13px; color: var(--accent); font: 10px "Cascadia Mono", monospace; letter-spacing: .14em; text-transform: uppercase; }.robot-hero__copy h1 { margin: 0; color: #fff9ef; font: 600 clamp(34px, 3.4vw, 57px)/1.13 "STSong", "Noto Serif CJK SC", serif; letter-spacing: .025em; animation: reveal-title .52s ease both; }.robot-hero__copy > p:not(.robot-hero__eyebrow) { margin: 16px 0 0; color: rgba(255, 244, 226, .8); font-size: 13px; line-height: 1.8; }.robot-hero__meter { margin-top: 23px; display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 10px; color: #efdcc6; font: 10px "Cascadia Mono", monospace; }.robot-hero__meter i { height: 1px; background: linear-gradient(90deg, var(--accent), rgba(255,255,255,.15)); }.robot-hero__meter b { color: #fff9ed; font-weight: 500; }.robot-hero__consult { margin-top: 25px; padding: 0 0 7px; border: 0; border-bottom: 1px solid rgba(255, 247, 232, .55); background: none; color: #fff9ed; font: 600 12px "Microsoft YaHei UI", sans-serif; letter-spacing: .04em; }.robot-hero__consult span { margin-left: 10px; color: var(--accent); font-size: 18px; }.robot-hero__consult:hover { border-color: var(--accent); }
.robot-hero__navigation { position: absolute; z-index: 6; left: clamp(22px, 8vw, 132px); bottom: 28px; display: flex; gap: 7px; }.robot-hero__navigation button { width: 43px; height: 43px; border: 1px solid rgba(255, 247, 232, .64); border-radius: 50%; background: rgba(42, 30, 20, .1); color: #fff8ed; font-size: 20px; transition: transform .18s ease, background .18s ease; }.robot-hero__navigation button:hover { transform: scale(1.08); background: rgba(255, 247, 232, .17); }.robot-hero__discover { position: absolute; z-index: 6; right: clamp(22px, 4vw, 68px); bottom: 31px; color: #fff8ed; font: 600 clamp(14px, 1.8vw, 22px) "STSong", "Noto Serif CJK SC", serif; letter-spacing: .08em; text-decoration: none; }.robot-hero__discover span { margin-left: 9px; color: var(--accent); }
.robot-hero__assistant { width: 142px; padding: 5px 13px 5px 5px; position: absolute; z-index: 7; right: clamp(22px, 4vw, 68px); top: 72px; display: flex; align-items: center; gap: 8px; border: 1px solid rgba(255, 248, 233, .58); border-radius: 99px; background: rgba(54, 35, 23, .43); color: #fff9ed; text-align: left; box-shadow: 0 11px 22px rgba(29, 16, 9, .16); backdrop-filter: blur(10px); transition: transform .2s ease, background .2s ease; }.robot-hero__assistant:hover { transform: translateY(-3px); background: rgba(61, 41, 27, .7); }.robot-hero__assistant img { width: 39px; height: 39px; border: 1px solid rgba(255, 243, 223, .7); border-radius: 50%; object-fit: cover; object-position: center 25%; }.robot-hero__assistant span { display: grid; gap: 2px; color: #ecd9c2; font-size: 9px; }.robot-hero__assistant b { color: #fffaf1; font: 600 12px "STSong", "Noto Serif CJK SC", serif; letter-spacing: .08em; }
.robot-lineup { padding: clamp(66px, 10vw, 146px) clamp(22px, 6vw, 100px); position: relative; display: grid; grid-template-columns: minmax(250px, .62fr) minmax(0, 1.38fr); align-items: start; gap: clamp(28px, 5vw, 78px); background: #f2e9da; color: #2b241d; }.robot-lineup__intro { padding: 12px 0 18px; }.robot-lineup__intro .eyebrow { color: #8b6548; }.robot-lineup__intro h2 { margin: 12px 0 18px; font: 600 clamp(27px, 2.6vw, 42px)/1.25 "STSong", "Noto Serif CJK SC", serif; letter-spacing: .035em; }.robot-lineup__intro > p:not(.eyebrow) { color: #755f51; font-size: 13px; line-height: 1.85; }.robot-lineup__catalog { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }
.robot-lineup__card { min-height: 386px; padding: 17px; position: relative; overflow: hidden; display: flex; flex-direction: column; border: 1px solid rgba(99, 72, 50, .15); background: rgba(255, 250, 241, .72); color: inherit; text-align: left; cursor: pointer; outline: none; transition: transform .35s cubic-bezier(.22,.8,.3,1), box-shadow .35s ease, background .35s ease; }.robot-lineup__card:hover, .robot-lineup__card:focus-visible { transform: translateY(-11px); background: #fffaf1; box-shadow: 0 21px 38px rgba(91, 57, 28, .17); }.robot-lineup__image { height: 192px; margin: -17px -17px 17px; overflow: hidden; background: radial-gradient(circle at 50% 44%, #fffaf0, #e8d6ba 72%); }.robot-lineup__image img { width: 100%; height: 100%; display: block; object-fit: contain; transition: transform .48s cubic-bezier(.22,.8,.3,1); }.robot-lineup__card:hover img, .robot-lineup__card:focus-visible img { transform: scale(1.1) rotate(-1deg); }.robot-lineup__card > p { margin: 0; color: #9e7c60; font: 9px "Cascadia Mono", monospace; letter-spacing: .1em; }.robot-lineup__card h3 { margin: 8px 0 3px; font: 600 25px "STSong", "Noto Serif CJK SC", serif; }.robot-lineup__card > b { color: var(--card-accent); font-size: 11px; }.robot-lineup__card > span { margin-top: 12px; color: #765f50; font-size: 12px; line-height: 1.65; }.robot-lineup__card > i { margin-top: auto; padding-top: 16px; color: #47392e; font: 11px "Cascadia Mono", monospace; font-style: normal; }.robot-lineup__target { width: 46px; height: 46px; position: absolute; z-index: 8; margin: -23px 0 0 -23px; pointer-events: none; opacity: 0; transition: opacity .15s ease; }.robot-lineup__target.visible { opacity: 1; }.robot-lineup__target::before, .robot-lineup__target::after, .robot-lineup__target i, .robot-lineup__target span { content: ''; position: absolute; width: 11px; height: 11px; border-color: #2c6c7d; border-style: solid; }.robot-lineup__target::before { left: 0; top: 0; border-width: 1px 0 0 1px; }.robot-lineup__target::after { right: 0; top: 0; border-width: 1px 1px 0 0; }.robot-lineup__target i { left: 0; bottom: 0; border-width: 0 0 1px 1px; }.robot-lineup__target span { right: 0; bottom: 0; border-width: 0 1px 1px 0; }.robot-lineup__target { border: 1px solid rgba(44, 108, 125, .5); border-radius: 50%; animation: target-spin 2.8s linear infinite; }
.robot-hero__header, .robot-hero__eyebrow, .robot-hero__meter, .robot-hero__model { font-family: var(--font-mono); }
.robot-hero__brand, .robot-hero__copy h1, .robot-hero__discover { font-family: var(--font-display); }
.robot-hero__copy > p:not(.robot-hero__eyebrow) { font-family: var(--font-ui); font-size: 15px; }
.robot-hero__consult, .robot-hero__assistant, .robot-hero__assistant b { font-family: var(--font-ui); }

.robot-lineup__catalog { gap: 18px; }
.robot-lineup__card { min-height: 480px; padding: 0; border-color: rgba(99, 72, 50, .18); }
.robot-lineup__card.is-targeted { border-color: var(--card-accent); box-shadow: 0 18px 42px color-mix(in srgb, var(--card-accent) 24%, transparent); }
.robot-lineup__head { min-height: 115px; padding: 21px 20px 15px; position: relative; z-index: 1; background: #fffaf1; }
.robot-lineup__head > p { margin: 0; color: #99775a; font: 11px var(--font-mono); letter-spacing: .11em; }
.robot-lineup__head h3 { margin: 7px 0 4px; color: #31251d; font: 600 27px/1 var(--font-display); }
.robot-lineup__head b { color: var(--card-accent); font: 600 13px var(--font-ui); }
.robot-lineup__image { height: 194px; margin: 0; background: radial-gradient(circle at 50% 47%, #fffdf7 0, #ead8bc 74%); }
.robot-lineup__image img { padding: 7px 14px; }
.robot-lineup__foot { min-height: 171px; padding: 16px 20px 17px; display: flex; flex-direction: column; background: #fffaf1; }
.robot-lineup__foot small { margin-bottom: 8px; color: #936b4f; font: 10px var(--font-mono); letter-spacing: .035em; }
.robot-lineup__foot > span { color: #6f5949; font: 14px/1.7 var(--font-ui); }
.robot-lineup__foot i { margin-top: auto; color: #294f50; font: 12px var(--font-ui); font-style: normal; }
.robot-lineup__foot i strong { margin-left: 7px; color: var(--card-accent); font-size: 16px; }
.robot-lineup__target { width: 88px; height: 88px; margin: -44px 0 0 -44px; display: grid; place-items: center; border: 1px solid rgba(20, 99, 107, .75); border-radius: 50%; background: rgba(245, 255, 253, .12); box-shadow: 0 0 0 7px rgba(60, 167, 170, .09), 0 12px 26px rgba(13, 63, 66, .18); mix-blend-mode: multiply; animation: target-breathe 1.35s ease-in-out infinite; }
.robot-lineup__target::before, .robot-lineup__target::after, .robot-lineup__target i, .robot-lineup__target b, .robot-lineup__target em { width: 16px; height: 16px; position: absolute; border-color: #17666d; border-style: solid; content: ''; }
.robot-lineup__target::before { left: -3px; top: -3px; border-width: 2px 0 0 2px; }.robot-lineup__target::after { right: -3px; top: -3px; border-width: 2px 2px 0 0; }
.robot-lineup__target b { left: -3px; bottom: -3px; border-width: 0 0 2px 2px; }.robot-lineup__target em { right: -3px; bottom: -3px; border-width: 0 2px 2px 0; }
.robot-lineup__target i { width: 8px; height: 8px; border: 0; border-radius: 50%; background: #d99b60; box-shadow: 0 0 0 5px rgba(217, 155, 96, .2); }
.robot-lineup__target span { width: auto; height: auto; position: absolute; top: calc(100% + 10px); right: auto; bottom: auto; left: 50%; transform: translateX(-50%); border: 0; color: #17484d; font: 700 10px var(--font-mono); letter-spacing: .06em; white-space: nowrap; }

.robot-lineup { display: block; }.robot-lineup__intro { max-width: 720px; padding: 0; }.robot-lineup__intro h2 { max-width: 650px; }.robot-lineup__catalog { margin-top: 42px; grid-template-columns: repeat(3, minmax(0, 1fr)); }
.robot-lineup__target { top: 0; left: 0; margin: 0; border-color: rgba(29, 94, 93, .75); border-radius: 0; background: rgba(255, 249, 238, .08); box-shadow: 0 0 0 1px rgba(233, 189, 123, .65), 0 10px 30px rgba(65, 44, 27, .12); animation: none; transition: width .2s cubic-bezier(.2,.8,.2,1), height .2s cubic-bezier(.2,.8,.2,1), border-radius .2s ease, opacity .15s ease; }.robot-lineup__target:not(.is-locked) { border-radius: 2px; }.robot-lineup__target.is-locked { border-radius: 0; background: rgba(245, 255, 252, .04); box-shadow: 0 0 0 2px rgba(58, 132, 129, .2), 0 12px 28px rgba(26, 78, 75, .18); }.robot-lineup__target::before, .robot-lineup__target::after, .robot-lineup__target i, .robot-lineup__target b, .robot-lineup__target em { border-color: #2a7876; }.robot-lineup__target i { width: 6px; height: 6px; background: #d59a60; box-shadow: 0 0 0 4px rgba(213, 154, 96, .16); }.robot-lineup__target.is-locked i { opacity: 0; }.robot-lineup__target span { top: -25px; padding: 4px 7px; background: #234f4e; color: #fff7e9; font-size: 10px; opacity: 0; transition: opacity .15s ease; }.robot-lineup__target.is-locked span { opacity: 1; }

.robot-dialog { position: fixed; z-index: 1000; inset: 0; display: grid; place-items: center; padding: 24px; background: rgba(38, 27, 18, .58); backdrop-filter: blur(12px); }.robot-dialog__panel { width: min(770px, 100%); min-height: 438px; position: relative; overflow: hidden; display: grid; grid-template-columns: minmax(245px, .92fr) 1.08fr; border: 1px solid rgba(255, 248, 236, .55); background: #fffaf1; box-shadow: 0 38px 96px rgba(30, 18, 10, .38); animation: dialog-in .24s ease both; }.robot-dialog__close { width: 37px; height: 37px; position: absolute; z-index: 2; top: 14px; right: 14px; border: 1px solid rgba(57, 40, 26, .2); border-radius: 50%; background: rgba(255, 250, 241, .76); color: #37291f; font-size: 24px; line-height: 1; }.robot-dialog__visual { min-height: 100%; display: grid; place-items: center; overflow: hidden; background: radial-gradient(circle at 50% 44%, #fffaf0 0, color-mix(in srgb, var(--dialog-accent) 30%, #d5c1a3) 70%, #c8ad8d 100%); }.robot-dialog__visual img { width: 93%; max-height: 410px; object-fit: contain; filter: drop-shadow(0 30px 20px rgba(61, 38, 20, .3)); animation: product-float 3.8s ease-in-out infinite; }.robot-dialog__content { padding: clamp(34px, 6vw, 64px) clamp(28px, 5vw, 52px) 36px; color: #30241b; }.robot-dialog__content > p:first-child { margin: 0; color: #977555; font: 11px var(--font-mono); letter-spacing: .12em; }.robot-dialog__content h2 { margin: 10px 0 4px; font: 600 clamp(37px, 5vw, 56px)/1 var(--font-display); }.robot-dialog__content > b { color: #8a614b; font-size: 14px; }.robot-dialog__price { display: block; margin-top: 29px; color: #1e514f; font: 600 clamp(30px, 4vw, 42px)/1 var(--font-mono); letter-spacing: -.06em; }.robot-dialog__content small { display: block; margin-top: 8px; color: #846d5b; font-size: 12px; }.robot-dialog__description { margin: 21px 0 0 !important; color: #705947; font-size: 14px; line-height: 1.8; }.robot-dialog__content ul { margin: 18px 0 0; padding: 0; display: flex; flex-wrap: wrap; gap: 7px; list-style: none; }.robot-dialog__content li { padding: 7px 9px; border: 1px solid rgba(124, 93, 68, .17); border-radius: 999px; color: #594737; font-size: 12px; }.robot-dialog__consult { margin-top: 27px; padding: 12px 17px; border: 0; background: #234d50; color: #fff9ee; font: 600 13px var(--font-ui); letter-spacing: .04em; transition: transform .18s ease, background .18s ease; }.robot-dialog__consult:hover { transform: translateY(-2px); background: #173a3d; }
.robot-hero__assistant { overflow: visible; }.robot-hero__assistant img { transform-origin: 50% 82%; animation: assistant-idle 4.8s cubic-bezier(.45,0,.25,1) infinite; transition: transform .18s ease, filter .18s ease; }.robot-hero__assistant:hover img { animation-play-state: paused; transform: translateY(-2px) rotate(-4deg) scale(1.08); filter: saturate(1.08) brightness(1.05); }.robot-hero__assistant > i { width: 17px; height: 17px; position: absolute; top: -6px; left: 31px; display: grid; place-items: center; border-radius: 50%; background: #e5b46e; color: #2e241b; font-size: 10px; font-style: normal; box-shadow: 0 0 0 4px rgba(229,180,110,.18); animation: assistant-spark 3.6s ease-in-out infinite; }.robot-hero__assistant:active { transform: translateY(-1px) scale(.98); }
.robot-lineup__image { position: relative; }.robot-lineup__focus-zone { position: absolute; z-index: 2; left: 50%; top: 52%; transform: translate(-50%, -50%); pointer-events: none; }.robot-lineup__image--shape-0 .robot-lineup__focus-zone { width: 58%; height: 48%; }.robot-lineup__image--shape-1 .robot-lineup__focus-zone { width: 53%; height: 62%; top: 54%; }.robot-lineup__image--shape-2 .robot-lineup__focus-zone { width: 56%; height: 68%; top: 54%; }
.robot-lineup__focus-frame { position: absolute; z-index: 2; inset: 10px 12px; display: block; border: 1px solid color-mix(in srgb, var(--card-accent) 72%, #245c65); box-shadow: inset 0 0 0 1px rgba(255, 250, 238, .46), 0 8px 20px rgba(39, 62, 58, .08); opacity: 0; pointer-events: none; transition: opacity .2s ease, inset .2s ease; }
.robot-lineup__focus-frame::before, .robot-lineup__focus-frame::after { content: ''; position: absolute; width: 16px; height: 16px; border-color: #245c65; border-style: solid; }
.robot-lineup__focus-frame::before { left: -2px; top: -2px; border-width: 2px 0 0 2px; }.robot-lineup__focus-frame::after { right: -2px; bottom: -2px; border-width: 0 2px 2px 0; }
.robot-lineup__focus-frame b { position: absolute; left: 11px; top: 0; padding: 6px 9px 5px; background: #234f4e; color: #fff7e9; font: 700 10px var(--font-mono); letter-spacing: .06em; white-space: nowrap; }
.robot-lineup__card:hover .robot-lineup__focus-frame, .robot-lineup__card:focus-visible .robot-lineup__focus-frame { opacity: 1; inset: 7px 9px; }
.robot-lineup__card { border: 0; box-shadow: 0 0 0 1px rgba(105,76,48,.1), 0 8px 24px rgba(90,60,32,.055); }.robot-lineup__card.is-targeted { border-color: transparent; box-shadow: 0 13px 35px color-mix(in srgb, var(--card-accent) 17%, transparent); }.robot-lineup__card:hover, .robot-lineup__card:focus-visible { transform: translateY(-6px); }.robot-lineup__card:active { transform: translateY(-3px) scale(.985); transition-duration: 120ms; }.robot-lineup__foot i strong { display: inline-block; transition: transform 180ms ease; }.robot-lineup__card:hover .robot-lineup__foot i strong { transform: translateX(6px); }
.robot-hero__consult:active, .robot-dialog__consult:active, .topbar-agent-entry:active { transform: scale(.98); transition-duration: 120ms; }
.robot-dialog__facts { margin: 18px 0 0; display: grid; gap: 8px; }.robot-dialog__facts div { padding: 9px 0; display: grid; grid-template-columns: 72px 1fr; gap: 10px; border-top: 1px solid rgba(124, 93, 68, .14); }.robot-dialog__facts dt { color: #96765b; font: 10px var(--font-mono); letter-spacing: .06em; }.robot-dialog__facts dd { margin: 0; color: #4d3c2f; font: 12px/1.5 var(--font-ui); }
.robot-dialog__panel.is-3d { width: min(1120px, 100%); grid-template-columns: minmax(0, 1.28fr) minmax(300px, .72fr); }
.robot-dialog__panel.is-3d .robot-dialog__content { padding-top: clamp(34px, 5vw, 52px); }
.robot-dialog__modes { margin-top: 24px; display: inline-flex; gap: 3px; padding: 3px; border: 1px solid rgba(124, 93, 68, .18); background: rgba(244, 234, 220, .72); }
.robot-dialog__modes button { min-width: 76px; padding: 8px 11px; border: 0; background: transparent; color: #876e5c; font: 11px var(--font-ui); cursor: pointer; }
.robot-dialog__modes button.active { background: #234d50; color: #fff9ee; }
.robot-hero__assistant { width: 190px; min-height: 72px; padding: 6px 15px 6px 6px; gap: 10px; border-radius: 20px; }
.robot-hero__assistant .robot-hero__assistant-character { width: 60px; height: 60px; position: relative; flex: 0 0 60px; display: block; overflow: hidden; border: 1px solid rgba(255, 243, 223, .72); border-radius: 17px; background: #eee3d0; box-shadow: inset 0 0 0 1px rgba(255,255,255,.24); }
.robot-hero__assistant .assistant-frame { width: 100%; height: 100%; position: absolute; inset: 0; border: 0; border-radius: 0; object-fit: cover; object-position: center; opacity: 0; transform: none; filter: none; transition: none; }
.robot-hero__assistant .assistant-frame--idle { opacity: 1; animation: assistant-frame-idle 9s linear infinite; }
.robot-hero__assistant .assistant-frame--wave { animation: assistant-frame-wave 9s ease-in-out infinite; }
.robot-hero__assistant .assistant-frame--point { animation: assistant-frame-point 9s ease-in-out infinite; }
.robot-hero__assistant .robot-hero__assistant-copy { display: grid; gap: 3px; color: #ecd9c2; font-size: 10px; }
.robot-hero__assistant .robot-hero__assistant-copy b { font-size: 13px; }
.robot-hero__assistant .robot-hero__assistant-copy small { color: #ecd9c2; font: 10px var(--font-ui); letter-spacing: .04em; white-space: nowrap; }
.robot-hero__assistant:hover .assistant-frame--idle { animation: none; opacity: 0; }
.robot-hero__assistant:hover .assistant-frame--wave { opacity: 1; animation: assistant-wave-greeting .7s cubic-bezier(.2,.7,.2,1) both; }
.robot-hero__assistant:hover .assistant-frame--point { animation: none; opacity: 0; }
.robot-hero__assistant.is-activating { pointer-events: none; background: rgba(61, 41, 27, .82); }
.robot-hero__assistant.is-activating .assistant-frame--idle,
.robot-hero__assistant.is-activating .assistant-frame--wave { animation: none; opacity: 0; }
.robot-hero__assistant.is-activating .assistant-frame--point { opacity: 1; animation: assistant-point-confirm .44s cubic-bezier(.18,.8,.25,1) both; }
.robot-hero__assistant.is-activating .robot-hero__assistant-copy small { color: #fff4df; }
.robot-hero__assistant > i { left: 51px; }
@keyframes assistant-frame-idle { 0%, 48%, 64%, 76%, 91%, 100% { opacity: 1; } 51%, 61%, 79%, 88% { opacity: 0; } }
@keyframes assistant-frame-wave { 0%, 49%, 63%, 100% { opacity: 0; transform: scale(.985) rotate(0); } 51%, 60% { opacity: 1; } 54% { transform: scale(1.025) rotate(-1.5deg); } 58% { transform: scale(1.01) rotate(1deg); } }
@keyframes assistant-frame-point { 0%, 76%, 91%, 100% { opacity: 0; transform: translateX(-2px) scale(.98); } 79%, 88% { opacity: 1; transform: translateX(0) scale(1.02); } }
@keyframes assistant-wave-greeting { 0% { opacity: 0; transform: scale(.96) rotate(0); } 18% { opacity: 1; transform: scale(1.04) rotate(-2deg); } 46% { transform: scale(1.015) rotate(1.5deg); } 72%, 100% { opacity: 1; transform: scale(1.03) rotate(0); } }
@keyframes assistant-point-confirm { 0% { opacity: 0; transform: translateX(-5px) scale(.94); } 34% { opacity: 1; transform: translateX(1px) scale(1.04); } 72%, 100% { opacity: 1; transform: translateX(0) scale(1); } }
@keyframes assistant-idle { 0%, 72%, 100% { transform: translateY(0) rotate(0); } 78% { transform: translateY(-3px) rotate(-3deg); } 84% { transform: translateY(-1px) rotate(3deg); } 90% { transform: translateY(-2px) rotate(-1deg); } } @keyframes assistant-spark { 0%, 68%, 100% { opacity: .45; transform: scale(.78) rotate(0); } 76% { opacity: 1; transform: scale(1.14) rotate(18deg); } 86% { opacity: .7; transform: scale(.94) rotate(-8deg); } } @keyframes reveal-title { from { opacity: .18; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } } @keyframes target-spin { to { rotate: 360deg; } } @keyframes target-breathe { 50% { opacity: .72; box-shadow: 0 0 0 11px rgba(60, 167, 170, .05), 0 15px 32px rgba(13, 63, 66, .24); } } @keyframes product-float { 50% { transform: translateY(-9px) rotate(-1deg); } } @keyframes dialog-in { from { opacity: 0; transform: translateY(12px) scale(.985); } to { opacity: 1; transform: translateY(0) scale(1); } }
@media (max-width: 1080px) { .robot-lineup { grid-template-columns: 1fr; }.robot-lineup__intro { max-width: 570px; }.robot-lineup__catalog { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (max-width: 900px) { .robot-hero__copy { width: min(315px, 44vw); }.robot-hero__robot.is-left { left: 23%; }.robot-hero__robot.is-right { left: 77%; }.robot-lineup__catalog { grid-template-columns: repeat(2, minmax(0, 1fr)); }.robot-dialog__panel { grid-template-columns: .85fr 1.15fr; } }
@media (max-width: 640px) { .robot-hero { min-height: 700px; }.robot-hero__header { padding: 20px; }.robot-hero__header > span:last-child { display: none; }.robot-hero__ghost { top: 22%; font-size: 24vw; }.robot-hero__robot { width: 260px; height: 260px; bottom: 30%; }.robot-hero__robot.is-center { transform: translateX(-50%) scale(1.22); }.robot-hero__robot.is-left { left: 18%; transform: translateX(-50%) scale(.46); }.robot-hero__robot.is-right { left: 82%; transform: translateX(-50%) scale(.46); }.robot-hero__copy { width: auto; right: 24px; left: 24px; bottom: 93px; }.robot-hero__copy h1 { font-size: 35px; }.robot-hero__copy > p:not(.robot-hero__eyebrow) { max-width: 330px; }.robot-hero__navigation { left: 24px; bottom: 24px; }.robot-hero__assistant { top: 64px; right: 18px; transform: scale(.88); transform-origin: top right; }.robot-hero__discover { right: 24px; bottom: 35px; }.robot-lineup { padding: 64px 20px; }.robot-lineup__catalog { grid-template-columns: 1fr; }.robot-lineup__card { min-height: 366px; }.robot-lineup__target { display: none; }.robot-dialog { padding: 14px; align-items: end; }.robot-dialog__panel, .robot-dialog__panel.is-3d { max-height: calc(100vh - 28px); overflow: auto; grid-template-columns: 1fr; }.robot-dialog__visual { min-height: 245px; }.robot-dialog__visual img { max-height: 240px; }.robot-dialog__content { padding: 29px 25px 31px; } }
/* Keep each catalog variant visible in every presentation surface. */
.robot-hero__robot img,
.robot-lineup__image img,
.robot-dialog__visual img { filter: drop-shadow(0 24px 18px rgba(43, 25, 11, .24)); }
</style>
