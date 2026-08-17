<script setup lang="ts">
/* global ResizeObserver, document, Event, HTMLDivElement, HTMLElement */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'

const props = defineProps<{
  modelUrl: string
  productName: string
  accent: string
  fallbackImage: string
}>()

const canvasHost = ref<HTMLDivElement | null>(null)
const stage = ref<HTMLElement | null>(null)
const loading = ref(true)
const error = ref('')
const isFullscreen = ref(false)
const renderState = ref<'loading' | 'ready' | 'error'>('loading')

let renderer: THREE.WebGLRenderer | null = null
let scene: THREE.Scene | null = null
let camera: THREE.PerspectiveCamera | null = null
let controls: OrbitControls | null = null
let animationFrame = 0
let resizeObserver: ResizeObserver | null = null
let loadedRoot: THREE.Object3D | null = null
let defaultCameraPosition = new THREE.Vector3()
let defaultTarget = new THREE.Vector3()
let modelLoadTimer: ReturnType<typeof globalThis.setTimeout> | null = null

function setLoadingState() {
  loading.value = true
  error.value = ''
  renderState.value = 'loading'
  if (modelLoadTimer) globalThis.clearTimeout(modelLoadTimer)
}

function setError(message: string) {
  loading.value = false
  error.value = message
  renderState.value = 'error'
  if (modelLoadTimer) {
    globalThis.clearTimeout(modelLoadTimer)
    modelLoadTimer = null
  }
}

function disposeObject(object: THREE.Object3D) {
  object.traverse((child) => {
    const mesh = child as THREE.Mesh
    if (!mesh.isMesh) return
    mesh.geometry.dispose()
    const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material]
    materials.forEach((material) => {
      Object.values(material).forEach((value) => {
        if (value instanceof THREE.Texture) value.dispose()
      })
      material.dispose()
    })
  })
}

function clearLoadedModel() {
  if (!scene || !loadedRoot) return
  scene.remove(loadedRoot)
  disposeObject(loadedRoot)
  loadedRoot = null
}

function frameModel(model: THREE.Object3D) {
  if (!camera || !controls) return
  const bounds = new THREE.Box3().setFromObject(model)
  const size = bounds.getSize(new THREE.Vector3())
  const center = bounds.getCenter(new THREE.Vector3())
  const maxSize = Math.max(size.x, size.y, size.z, 1)

  model.position.x -= center.x
  model.position.y -= bounds.min.y
  model.position.z -= center.z

  const distance = maxSize * 2.25
  defaultTarget.set(0, Math.max(size.y * 0.35, maxSize * 0.18), 0)
  defaultCameraPosition.set(distance * 0.92, distance * 0.62, distance * 1.05)
  camera.near = Math.max(maxSize / 100, 0.01)
  camera.far = maxSize * 100
  camera.position.copy(defaultCameraPosition)
  camera.lookAt(defaultTarget)
  controls.target.copy(defaultTarget)
  controls.minDistance = maxSize * 0.75
  controls.maxDistance = maxSize * 5
  controls.update()
}

function resetView() {
  if (!camera || !controls) return
  camera.position.copy(defaultCameraPosition)
  controls.target.copy(defaultTarget)
  controls.update()
}

function loadModel() {
  if (!scene) return
  setLoadingState()
  clearLoadedModel()
  modelLoadTimer = globalThis.setTimeout(() => {
    setError('3D 模型加载超时，已保留产品图作为预览。')
  }, 20_000)
  const loader = new GLTFLoader()
  loader.load(
    props.modelUrl,
    (gltf) => {
      if (!scene) return
      loadedRoot = gltf.scene
      loadedRoot.traverse((child) => {
        const mesh = child as THREE.Mesh
        if (mesh.isMesh) {
          mesh.castShadow = true
          mesh.receiveShadow = true
        }
      })
      const bounds = new THREE.Box3().setFromObject(gltf.scene)
      const size = bounds.getSize(new THREE.Vector3())
      if (!Number.isFinite(size.x + size.y + size.z) || Math.max(size.x, size.y, size.z) < 0.001) {
        setError('3D 模型内容为空，已保留产品图作为预览。')
        return
      }
      scene.add(loadedRoot)
      frameModel(loadedRoot)
      loading.value = false
      renderState.value = 'ready'
      if (modelLoadTimer) {
        globalThis.clearTimeout(modelLoadTimer)
        modelLoadTimer = null
      }
    },
    undefined,
    () => setError('3D 模型暂时无法加载，已保留产品图作为预览。'),
  )
}

function resize() {
  if (!canvasHost.value || !renderer || !camera) return
  const { clientWidth, clientHeight } = canvasHost.value
  if (!clientWidth || !clientHeight) return
  renderer.setPixelRatio(Math.min(globalThis.devicePixelRatio || 1, 2))
  renderer.setSize(clientWidth, clientHeight, false)
  camera.aspect = clientWidth / clientHeight
  camera.updateProjectionMatrix()
}

function animate() {
  if (!renderer || !scene || !camera) return
  animationFrame = globalThis.requestAnimationFrame(animate)
  controls?.update()
  renderer.render(scene, camera)
}

function toggleFullscreen() {
  if (!stage.value) return
  if (document.fullscreenElement) {
    void document.exitFullscreen()
  } else {
    void stage.value.requestFullscreen()
  }
}

function syncFullscreen() {
  isFullscreen.value = document.fullscreenElement === stage.value
  globalThis.setTimeout(resize, 40)
}

function handleContextLost(event: Event) {
  event.preventDefault()
  setError('当前浏览器暂时无法维持 3D 图形上下文，请刷新后重试。')
}

function initScene() {
  if (!canvasHost.value) return
  try {
    scene = new THREE.Scene()
    scene.background = new THREE.Color('#e8e0d5')
    camera = new THREE.PerspectiveCamera(33, 1, 0.1, 1000)
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true, powerPreference: 'high-performance' })
    renderer.outputColorSpace = THREE.SRGBColorSpace
    renderer.toneMapping = THREE.ACESFilmicToneMapping
    renderer.toneMappingExposure = 1.12
    renderer.shadowMap.enabled = true
    renderer.shadowMap.type = THREE.PCFShadowMap
    renderer.domElement.style.width = '100%'
    renderer.domElement.style.height = '100%'
    renderer.domElement.style.display = 'block'
    renderer.domElement.addEventListener('webglcontextlost', handleContextLost, false)
    canvasHost.value.appendChild(renderer.domElement)

    const ambient = new THREE.HemisphereLight('#fffaf2', '#86796e', 2.2)
    scene.add(ambient)
    const key = new THREE.DirectionalLight('#fff7e7', 3.2)
    key.position.set(4, 7, 5)
    key.castShadow = true
    scene.add(key)
    const fill = new THREE.DirectionalLight(new THREE.Color(props.accent), 1.4)
    fill.position.set(-5, 3, -4)
    scene.add(fill)

    const floor = new THREE.Mesh(
      new THREE.CircleGeometry(6, 64),
      new THREE.ShadowMaterial({ color: '#46392e', opacity: 0.18 }),
    )
    floor.rotation.x = -Math.PI / 2
    floor.position.y = -0.02
    floor.receiveShadow = true
    scene.add(floor)

    controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.075
    controls.enableRotate = true
    controls.enablePan = false
    // Keep a small pole offset so users can inspect the model from every side
    // without OrbitControls becoming unstable when the camera is exactly vertical.
    controls.minPolarAngle = 0.01
    controls.maxPolarAngle = Math.PI - 0.01
    controls.minAzimuthAngle = -Infinity
    controls.maxAzimuthAngle = Infinity
    controls.rotateSpeed = 0.72
    resizeObserver = new ResizeObserver(resize)
    resizeObserver.observe(canvasHost.value)
    document.addEventListener('fullscreenchange', syncFullscreen)
    resize()
    loadModel()
    animate()
  } catch {
    setError('当前浏览器不支持 3D 预览，已保留产品图作为预览。')
  }
}

onMounted(initScene)
watch(() => props.modelUrl, loadModel)
onBeforeUnmount(() => {
  globalThis.cancelAnimationFrame(animationFrame)
  resizeObserver?.disconnect()
  document.removeEventListener('fullscreenchange', syncFullscreen)
  if (modelLoadTimer) globalThis.clearTimeout(modelLoadTimer)
  clearLoadedModel()
  controls?.dispose()
  renderer?.domElement.removeEventListener('webglcontextlost', handleContextLost)
  renderer?.dispose()
  renderer?.domElement.remove()
  controls = null
  renderer = null
  camera = null
  scene = null
})
</script>

<template>
  <section ref="stage" class="product-3d" :data-render-state="renderState" :aria-label="`${productName} 3D 效果`" :aria-busy="loading">
    <div ref="canvasHost" class="product-3d__canvas"></div>
    <div class="product-3d__wash" aria-hidden="true"></div>
    <div v-if="loading" class="product-3d__state"><span class="product-3d__spinner"></span><b>正在加载 3D 模型</b><small>首次打开需要一点时间</small></div>
    <div v-else-if="error" class="product-3d__state product-3d__state--error"><img class="product-3d__fallback" :src="fallbackImage" :alt="`${productName} 产品图`" /><b>{{ error }}</b><button type="button" @click="loadModel">重新加载</button></div>
    <div class="product-3d__toolbar" aria-label="3D 查看操作">
      <button type="button" aria-label="重置 3D 视角" title="重置视角" @click="resetView">↺</button>
      <button type="button" :aria-label="isFullscreen ? '退出全屏' : '全屏查看'" :title="isFullscreen ? '退出全屏' : '全屏查看'" @click="toggleFullscreen">{{ isFullscreen ? '×' : '⛶' }}</button>
    </div>
    <p class="product-3d__hint">拖拽 360°旋转 · 滚轮缩放</p>
  </section>
</template>

<style scoped>
.product-3d { min-height: 438px; position: relative; align-self: stretch; overflow: hidden; isolation: isolate; background: radial-gradient(circle at 50% 26%, #fffaf0 0, #e9decf 62%, #c6b4a2 100%); }
.product-3d:fullscreen { width: 100vw; height: 100vh; min-height: 100vh; }
.product-3d__canvas { position: absolute; z-index: 1; inset: 0; min-height: 0; }
.product-3d__canvas canvas { width: 100%; height: 100%; display: block; }
.product-3d__wash { position: absolute; z-index: 0; inset: auto 7% 3%; height: 38%; border-radius: 50%; background: color-mix(in srgb, v-bind(accent) 22%, transparent); filter: blur(32px); opacity: .55; pointer-events: none; }
.product-3d__state { position: absolute; z-index: 3; inset: 0; display: grid; place-content: center; justify-items: center; gap: 8px; background: rgba(246, 239, 229, .74); color: #3d3026; text-align: center; }
.product-3d__fallback { width: min(54%, 280px); max-height: 220px; object-fit: contain; filter: drop-shadow(0 18px 14px rgba(61, 38, 20, .22)); }
.product-3d__state b { font: 600 15px var(--font-ui); }.product-3d__state small { color: #806b5b; font-size: 11px; }.product-3d__state--error button { margin-top: 7px; padding: 9px 14px; border: 1px solid rgba(53, 78, 79, .24); background: #234d50; color: #fffaf1; cursor: pointer; }
.product-3d__spinner { width: 24px; height: 24px; border: 2px solid rgba(35, 77, 80, .18); border-top-color: #234d50; border-radius: 50%; animation: product-3d-spin .8s linear infinite; }
.product-3d__toolbar { position: absolute; z-index: 4; right: 18px; bottom: 18px; display: flex; gap: 6px; }.product-3d__toolbar button { width: 34px; height: 34px; border: 1px solid rgba(54, 43, 34, .2); background: rgba(255, 251, 243, .78); color: #3a3028; font-size: 18px; cursor: pointer; backdrop-filter: blur(8px); }.product-3d__toolbar button:hover, .product-3d__toolbar button:focus-visible { border-color: #234d50; background: #fffaf1; }
.product-3d__hint { position: absolute; z-index: 4; left: 18px; bottom: 20px; margin: 0; color: #725e4f; font: 10px var(--font-mono); letter-spacing: .04em; pointer-events: none; }
@keyframes product-3d-spin { to { transform: rotate(360deg); } }
@media (max-width: 640px) { .product-3d { min-height: 340px; }.product-3d__hint { bottom: 16px; left: 14px; }.product-3d__toolbar { right: 14px; bottom: 14px; } }
</style>
