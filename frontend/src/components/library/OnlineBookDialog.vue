<script setup lang="ts">
/** 手机书架在线书信息：服务端没有正文，只展示详情 */
import { ref, watch } from "vue";
import { fetchShelfBook } from "@/api/shelf";
import type { BookListItem } from "@/api/types";

const props = defineProps<{ book: BookListItem | null }>();

const emit = defineEmits<{ close: [] }>();

const detail = ref<BookListItem | null>(null);
const busy = ref(false);

watch(
  () => props.book,
  async (book) => {
    detail.value = book;
    if (!book?.shelf_id) return;
    busy.value = true;
    try {
      const data = await fetchShelfBook(book.shelf_id);
      detail.value = { ...book, ...(data as unknown as BookListItem) };
    } catch {
      // 列表里已有基本信息，详情失败不阻断展示
    } finally {
      busy.value = false;
    }
  },
  { immediate: true },
);
</script>

<template>
  <el-dialog
    :model-value="!!book"
    title="手机书架 · 在线书"
    width="480px"
    append-to-body
    @update:model-value="emit('close')"
  >
    <div v-if="detail" v-loading="busy" class="online-detail">
      <div class="online-head">
        <img
          v-if="detail.cover_url || detail.cover_thumb_url"
          class="online-cover"
          :src="detail.cover_url || detail.cover_thumb_url || ''"
          :alt="detail.name"
        />
        <div class="online-meta">
          <h3>{{ detail.name }}</h3>
          <p>{{ detail.author || "佚名" }}</p>
          <p v-if="detail.source_name">书源：{{ detail.source_name }}</p>
          <p v-if="detail.latest_chapter">最新：{{ detail.latest_chapter }}</p>
          <p v-if="detail.dur_chapter_title">读到：{{ detail.dur_chapter_title }}</p>
          <p v-if="detail.read_at">时间：{{ detail.read_at }}</p>
        </div>
      </div>
      <p class="online-intro">{{ detail.intro || "暂无简介" }}</p>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="正文保存在手机 Legado"
        description="这本书只在手机书架里，服务端没有正文文件，请在手机端继续阅读。要在本端阅读，可把书导入服务端书库。"
      />
      <div v-if="detail.book_url" class="online-link">
        <a :href="detail.book_url" target="_blank" rel="noopener noreferrer">打开书籍源站 ↗</a>
      </div>
    </div>
    <template #footer>
      <el-button type="primary" @click="emit('close')">知道了</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.online-detail {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.online-head {
  display: flex;
  gap: 14px;
  align-items: flex-start;
}

.online-cover {
  width: 84px;
  height: 112px;
  object-fit: cover;
  border-radius: var(--radius-sm);
  background: var(--color-surface-2);
  flex-shrink: 0;
}

.online-meta {
  min-width: 0;
}

.online-meta h3 {
  margin: 0 0 6px;
  font-size: var(--text-md);
  color: var(--color-text);
}

.online-meta p {
  margin: 3px 0;
  font-size: var(--text-xs);
  color: var(--color-text-2);
  word-break: break-all;
}

.online-intro {
  margin: 0;
  font-size: var(--text-sm);
  line-height: 1.6;
  color: var(--color-text-2);
  max-height: 120px;
  overflow-y: auto;
}

.online-link {
  font-size: var(--text-xs);
}
</style>
