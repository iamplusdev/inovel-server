<script setup lang="ts">
/** 书库工具条：搜索、排序、视图/封面尺寸、批量模式切换 */
import AppIcon from "@/components/AppIcon.vue";

defineProps<{
  search: string;
  sort: string;
  sortOptions: Array<{ value: string; label: string }>;
  viewMode: "grid" | "list";
  coverWidth: number;
  selectionMode: boolean;
}>();

const emit = defineEmits<{
  search: [value: string];
  "search-enter": [value: string];
  sort: [value: string];
  "view-mode": [mode: "grid" | "list"];
  "cover-width": [value: number];
  "toggle-selection": [];
}>();

function inputValue(e: Event): string {
  return (e.target as HTMLInputElement).value;
}
</script>

<template>
  <div class="page-actions">
    <div class="search-box">
      <AppIcon name="search" :size="16" />
      <input
        :value="search"
        type="search"
        class="search-input"
        placeholder="搜索书名 / 作者 / 标签…"
        @input="emit('search', inputValue($event))"
        @keyup.enter="emit('search-enter', inputValue($event))"
      />
    </div>

    <select
      class="sort-select"
      :value="sort"
      aria-label="排序"
      @change="emit('sort', inputValue($event))"
    >
      <option v-for="opt in sortOptions" :key="opt.value" :value="opt.value">
        {{ opt.label }}
      </option>
    </select>

    <div class="view-toggle" role="group" aria-label="视图">
      <button
        type="button"
        class="toggle-btn"
        :class="{ 'is-active': viewMode === 'grid' }"
        title="网格"
        @click="emit('view-mode', 'grid')"
      >
        <AppIcon name="grid" :size="16" />
      </button>
      <button
        type="button"
        class="toggle-btn"
        :class="{ 'is-active': viewMode === 'list' }"
        title="列表"
        @click="emit('view-mode', 'list')"
      >
        <AppIcon name="list" :size="16" />
      </button>
    </div>

    <div v-if="viewMode === 'grid'" class="size-ctrl">
      <span class="muted-xs">大小</span>
      <input
        :value="coverWidth"
        type="range"
        min="120"
        max="220"
        step="10"
        class="size-slider"
        aria-label="封面大小"
        @change="emit('cover-width', Number(inputValue($event)))"
      />
    </div>

    <button
      type="button"
      class="ghost-btn"
      :class="{ 'is-active': selectionMode }"
      @click="emit('toggle-selection')"
    >
      {{ selectionMode ? "退出批量" : "批量管理" }}
    </button>
  </div>
</template>

<style scoped>
.search-box {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 36px;
  padding: 0 12px;
  min-width: 220px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  color: var(--color-text-3);
}

.search-box:focus-within {
  border-color: var(--color-accent);
  box-shadow: var(--shadow-focus);
  color: var(--color-accent);
}

.search-input {
  flex: 1;
  border: none;
  outline: none;
  background: transparent;
  color: var(--color-text);
  font-size: var(--text-sm);
  font-family: inherit;
  min-width: 0;
}

.search-input::placeholder {
  color: var(--color-text-3);
}

.sort-select {
  height: 36px;
  padding: 0 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  color: var(--color-text);
  font-size: var(--text-sm);
  font-family: inherit;
  cursor: pointer;
}

.view-toggle {
  display: inline-flex;
  padding: 3px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-2);
  gap: 2px;
}

.toggle-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 28px;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text-3);
  cursor: pointer;
}

.toggle-btn.is-active {
  background: var(--color-surface);
  color: var(--color-accent);
  box-shadow: var(--shadow-sm);
}

.size-ctrl {
  display: flex;
  align-items: center;
  gap: 8px;
}

.size-slider {
  width: 96px;
  accent-color: var(--color-accent);
}

/* 父视图原来的 scoped 样式不会作用到组件内部元素，这里补上工具条所需部分 */
.ghost-btn {
  height: 32px;
  padding: 0 12px;
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
}

.ghost-btn.is-active {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
  color: var(--color-accent);
}

@media (max-width: 576px) {
  .search-box {
    min-width: 0;
    flex: 1;
  }

  .size-ctrl {
    display: none;
  }
}
</style>
