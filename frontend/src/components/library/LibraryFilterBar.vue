<script setup lang="ts">
/** 书库筛选条：书架来源 / 书源 / 分类 */
import { SOURCE_OPTS } from "@/stores/library";

defineProps<{
  origin: string;
  source: string;
  category: string;
  onlyUnscraped: boolean;
  categoryNames: string[];
  counts: Record<string, number> | null;
}>();

const emit = defineEmits<{
  origin: [value: string];
  source: [value: string];
  category: [value: string];
  "toggle-unscraped": [];
}>();

const ORIGIN_OPTS = [
  { value: "all", label: "全部书架" },
  { value: "both", label: "双端共有" },
  { value: "server", label: "仅服务端" },
  { value: "legado", label: "仅手机在线" },
];

const sourceLabels: Record<string, string> = {
  "": "全部",
  起点: "起点",
  番茄: "番茄",
  纵横: "纵横",
};
</script>

<template>
  <div class="filter-bar">
    <div class="chip-row" role="tablist" aria-label="书架来源筛选">
      <button
        v-for="opt in ORIGIN_OPTS"
        :key="opt.value"
        type="button"
        class="chip"
        :class="{ 'is-active': origin === opt.value }"
        @click="emit('origin', opt.value)"
      >
        {{ opt.label }}
      </button>
      <span v-if="counts" class="origin-counts muted-xs">
        共有 {{ counts.both }} · 仅服务端 {{ counts.server }} · 仅手机 {{ counts.legado }}
      </span>
    </div>

    <div class="chip-row" role="tablist" aria-label="书源筛选">
      <button
        v-for="src in SOURCE_OPTS"
        :key="src"
        type="button"
        class="chip"
        :class="{ 'is-active': source === src }"
        @click="emit('source', src)"
      >
        {{ sourceLabels[src] || src || "全部" }}
      </button>
      <span class="divider" />
      <button
        type="button"
        class="chip"
        :class="{ 'is-active': onlyUnscraped }"
        @click="emit('toggle-unscraped')"
      >
        仅未刮削
      </button>
    </div>

    <div class="chip-row" role="tablist" aria-label="分类筛选">
      <button type="button" class="chip" :class="{ 'is-active': !category }" @click="emit('category', '')">
        全部
      </button>
      <button
        v-for="cat in categoryNames"
        :key="cat"
        type="button"
        class="chip"
        :class="{ 'is-active': category === cat }"
        @click="emit('category', cat)"
      >
        {{ cat }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.origin-counts {
  margin-left: 8px;
  white-space: nowrap;
}

.filter-bar {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.divider {
  width: 1px;
  height: 18px;
  background: var(--color-border);
  margin: 0 4px;
}

@media (max-width: 576px) {
  /* 每行筛选各自横向滚动，避免两行被同一个容器绑在一起滚动 */
  .filter-bar {
    overflow: visible;
  }

  .chip-row {
    flex-wrap: nowrap;
    overflow-x: auto;
    padding-bottom: 4px;
  }
}
</style>
