<script setup lang="ts">
/** 分组页内的页签：由父级路由壳控制激活项，点击切换命名路由 */
interface TabItem {
  name: string;
  label: string;
}

defineProps<{ items: TabItem[]; active: string }>();
const emit = defineEmits<{ (e: "select", name: string): void }>();
</script>

<template>
  <nav class="group-tabs" role="tablist">
    <button
      v-for="item in items"
      :key="item.name"
      type="button"
      role="tab"
      class="group-tab"
      :class="{ 'is-active': active === item.name }"
      :aria-selected="active === item.name"
      @click="emit('select', item.name)"
    >
      {{ item.label }}
    </button>
  </nav>
</template>

<style scoped>
.group-tabs {
  display: flex;
  gap: 4px;
  align-self: flex-start;
  padding: 4px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.group-tab {
  padding: 6px 14px;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text-2);
  font-family: inherit;
  font-size: var(--text-sm);
  cursor: pointer;
  transition:
    background var(--duration) ease,
    color var(--duration) ease;
}

.group-tab:hover {
  background: var(--color-surface-2);
  color: var(--color-text);
}

.group-tab.is-active {
  background: var(--color-accent-soft);
  color: var(--color-accent);
  font-weight: 600;
}
</style>
