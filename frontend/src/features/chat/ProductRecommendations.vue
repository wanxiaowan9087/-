<script setup lang="ts">
import { computed } from 'vue'
import type { ProductRecommendationView } from '../../stores/chat'
import ivoryRobot from '../../assets/robot-ivory.png'
import graphiteRobot from '../../assets/robot-graphite.png'
import terracottaRobot from '../../assets/robot-terracotta.png'

const props = defineProps<{ recommendations: ProductRecommendationView[] }>()
const emit = defineEmits<{ select: [productId: string] }>()

const productIndex: Record<string, { name: string; subtitle: string; image: string }> = {
  's8-luna': { name: 'S8 皓月', subtitle: '静音深度清洁', image: ivoryRobot },
  's8-air': { name: 'S8 Air', subtitle: '小户型轻量方案', image: ivoryRobot },
  'x9-obsidian': { name: 'X9 曜石', subtitle: '全屋导航旗舰', image: graphiteRobot },
  'x9-edge': { name: 'X9 Edge', subtitle: '边角强化清洁', image: graphiteRobot },
  'm6-terra': { name: 'M6 霁陶', subtitle: '地面精细护理', image: terracottaRobot },
  'm6-mini': { name: 'M6 Mini', subtitle: '木地板温柔护理', image: terracottaRobot },
}

const products = computed(() => props.recommendations.flatMap(recommendation => {
  const product = productIndex[recommendation.productId]
  return product ? [{ ...product, ...recommendation, name: recommendation.name || product.name }] : []
}))
</script>

<template>
  <section v-if="products.length" class="recommendations" aria-label="推荐产品">
    <p>匹配方案</p>
    <div class="recommendations__grid">
      <button v-for="product in products" :key="product.productId" type="button" class="recommendation-card" @click="emit('select', product.productId)">
        <img :src="product.image" :alt="product.name" />
        <span><b>{{ product.name }}</b><small>{{ product.price ? `¥${product.price.toLocaleString()}` : product.subtitle }}</small><em>{{ product.highlights?.slice(0, 2).join(' · ') || product.subtitle }}</em><em v-if="product.reason">{{ product.reason }}</em></span>
        <i>查看</i>
      </button>
    </div>
  </section>
</template>

<style scoped>
.recommendations { margin-top: 16px; }
.recommendations > p { margin: 0 0 8px; color: #687785; font: 10px "Cascadia Mono", monospace; letter-spacing: .1em; text-transform: uppercase; }
.recommendations__grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 8px; }
.recommendation-card { min-height: 92px; padding: 10px; display: grid; grid-template-columns: 66px minmax(0, 1fr) auto; align-items: center; gap: 10px; border: 1px solid rgba(78, 104, 120, .28); border-radius: 2px; background: rgba(255, 255, 255, .72); color: #173043; text-align: left; transition: border-color .18s ease, transform .18s ease, box-shadow .18s ease; }
.recommendation-card:hover { border-color: #4f89aa; box-shadow: 0 8px 18px rgba(31, 74, 98, .12); transform: translateY(-1px); }
.recommendation-card img { width: 62px; height: 62px; object-fit: contain; filter: drop-shadow(0 8px 8px rgba(22, 47, 62, .16)); }
.recommendation-card span { min-width: 0; display: grid; gap: 3px; }
.recommendation-card b { font-size: 12px; }.recommendation-card small, .recommendation-card em { color: #627483; font-size: 10px; font-style: normal; line-height: 1.35; }.recommendation-card i { color: #2c6d91; font-size: 10px; font-style: normal; white-space: nowrap; }
</style>
