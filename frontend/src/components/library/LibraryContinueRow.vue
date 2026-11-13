<script setup lang="ts">
/** 继续阅读横向卡片区 */
import type { BookListItem } from "@/api/types";
import BookCard from "@/components/BookCard.vue";

defineProps<{ items: BookListItem[] }>();

const emit = defineEmits<{ open: [id: number] }>();
</script>

<template>
  <section class="continue-row" aria-label="继续阅读">
    <div class="continue-head">
      <h2 class="continue-title">继续阅读</h2>
      <span class="muted-xs">最近在读</span>
    </div>
    <div class="continue-scroll">
      <div v-for="b in items" :key="b.id" class="continue-item">
        <BookCard :book="b" :cover-width="140" @open="emit('open', $event)" />
      </div>
    </div>
  </section>
</template>

<style scoped>
.continue-row {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.continue-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.continue-title {
  margin: 0;
  font-size: var(--text-sm);
  font-weight: 600;
}

/* 横向滚动，避免继续阅读占用过多纵向空间 */
.continue-scroll {
  display: flex;
  gap: 12px;
  overflow-x: auto;
  padding-bottom: 4px;
}

.continue-item {
  width: 140px;
  flex: 0 0 140px;
}
</style>
