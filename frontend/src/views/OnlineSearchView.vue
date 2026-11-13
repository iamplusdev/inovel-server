<script setup lang="ts">
/**
 * 在线搜索：用已导入的 Legado 书源跨站检索。
 * 与书架分成两个页面，避免搜索状态与本地筛选互相干扰。
 */
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { libraryRouteQuery, useLibraryStore } from "@/stores/library";
import { addOnlineBook, fetchOnlineSources, searchOnline, syncOnlineToc } from "@/api/online";
import type { OnlineItem, OnlineSourceOption, OnlineSourceRef, OnlineSourceStatus } from "@/api/online";
import AppIcon from "@/components/AppIcon.vue";
import GroupTabs from "@/components/GroupTabs.vue";

const router = useRouter();
const lib = useLibraryStore();

const LIBRARY_TABS = [
  { name: "library", label: "书架" },
  { name: "online-search", label: "在线搜索" },
];

const searchInput = ref("");
const onlineLoading = ref(false);
const onlineError = ref("");
const onlineItems = ref<OnlineItem[]>([]);
const onlineStatus = ref<OnlineSourceStatus[]>([]);
const onlineSources = ref<OnlineSourceOption[]>([]);
/** 限定搜索范围；空数组=全部可用书源 */
const onlinePicked = ref<number[]>([]);
/** 正在加入书库的条目键："书名|源ID"，用于按钮 loading */
const addingKey = ref("");

const onlineSourceNames = computed(() => onlineSources.value.map((s) => s.name).join("、"));

function onLibraryTab(name: string) {
  // 返回书架时带上离开前的筛选，避免切一次页就丢失
  if (name === "online-search") return;
  void router.push({ name, query: libraryRouteQuery(lib) });
}

async function loadOnlineSources() {
  try {
    const data = await fetchOnlineSources();
    onlineSources.value = data.items || [];
    if (!onlineSources.value.length) {
      onlineError.value = "还没有可用于搜索的书源，请先到「接入 → 书源管理」导入 Legado 书源。";
    }
  } catch {
    onlineError.value = "书源列表加载失败";
  }
}

async function runOnlineSearch() {
  const q = searchInput.value.trim();
  if (!q) {
    ElMessage.warning("请输入要搜索的书名或作者");
    return;
  }
  onlineLoading.value = true;
  onlineError.value = "";
  try {
    const data = await searchOnline({
      q,
      source_ids: onlinePicked.value.length ? onlinePicked.value : undefined,
    });
    onlineItems.value = data.items || [];
    onlineStatus.value = data.sources || [];
    if (!data.items.length) {
      onlineError.value = data.hint || "没有搜到结果，换个关键词或检查书源是否可用";
    }
  } catch (err) {
    onlineItems.value = [];
    onlineStatus.value = [];
    onlineError.value = err instanceof Error ? err.message : "在线搜索失败";
  } finally {
    onlineLoading.value = false;
  }
}

function toggleOnlinePicked(id: number) {
  const idx = onlinePicked.value.indexOf(id);
  if (idx >= 0) onlinePicked.value.splice(idx, 1);
  else onlinePicked.value.push(id);
}

/** 登记到书库（幂等），返回书库内 id */
async function registerOnline(item: OnlineItem, src: OnlineSourceRef): Promise<number> {
  const res = await addOnlineBook({
    source_id: src.source_id,
    name: item.name,
    author: item.author,
    book_url: src.book_url,
    toc_url: src.toc_url,
    cover_url: src.cover_url || item.cover_url,
    intro: src.intro || item.intro,
    kind: src.kind || item.kind,
    last_chapter: src.last_chapter || item.last_chapter,
    word_count: item.word_count,
  });
  return res.book_id;
}

async function onAddOnline(item: OnlineItem, src: OnlineSourceRef) {
  const key = `${item.name}|${src.source_id}`;
  if (addingKey.value) return;
  addingKey.value = key;
  try {
    const id = await registerOnline(item, src);
    ElMessage.success(`已加入书库：${item.name}（#${id}）`);
    void lib.loadBooks();
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : "加入书库失败");
  } finally {
    addingKey.value = "";
  }
}

/** 直接阅读：先登记，再同步目录；目录拿到后进入阅读器 */
async function onReadOnline(item: OnlineItem, src: OnlineSourceRef) {
  const key = `${item.name}|${src.source_id}`;
  if (addingKey.value) return;
  addingKey.value = key;
  try {
    const id = await registerOnline(item, src);
    await syncOnlineToc(id);
    void lib.loadBooks();
    router.push({ name: "book-read", params: { id: String(id) } });
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : "打开失败，请换一个书源试试");
  } finally {
    addingKey.value = "";
  }
}

onMounted(() => {
  void loadOnlineSources();
});
</script>

<template>
  <div class="page online-page">
    <header class="page-header">
      <div>
        <h1 class="page-title">在线搜索</h1>
        <p class="page-subtitle">用已导入的 Legado 书源检索，可直接阅读或加入书库</p>
      </div>
      <div class="search-box">
        <AppIcon name="search" :size="16" />
        <input
          v-model="searchInput"
          type="search"
          class="search-input"
          placeholder="搜索书名 / 作者，回车开始"
          @keyup.enter="runOnlineSearch"
        />
        <button type="button" class="search-go" :disabled="onlineLoading" @click="runOnlineSearch">
          {{ onlineLoading ? "搜索中" : "搜索" }}
        </button>
      </div>
    </header>

    <GroupTabs :items="LIBRARY_TABS" active="online-search" @select="onLibraryTab" />

    <div class="online-toolbar">
      <div class="online-scope">
        <span class="muted-xs">搜索范围</span>
        <button
          type="button"
          class="chip"
          :class="{ 'is-active': !onlinePicked.length }"
          @click="onlinePicked = []"
        >
          全部书源（{{ onlineSources.length }}）
        </button>
        <button
          v-for="src in onlineSources"
          :key="src.id"
          type="button"
          class="chip"
          :class="{ 'is-active': onlinePicked.includes(src.id) }"
          :title="src.source_url"
          @click="toggleOnlinePicked(src.id)"
        >
          {{ src.name }}
        </button>
      </div>
      <span v-if="onlineSourceNames" class="muted-xs online-scope-hint">
        {{ onlineSourceNames }}
      </span>
    </div>

    <el-alert
      v-if="onlineError"
      type="info"
      :closable="false"
      show-icon
      :title="onlineError"
      class="online-alert"
    />

    <div v-if="onlineStatus.length" class="online-status">
      <span v-for="s in onlineStatus" :key="s.id" class="status-chip" :class="{ 'is-bad': !!s.error }">
        {{ s.name }}<template v-if="s.error"> · 失败</template><template v-else> · {{ s.count }}</template>
      </span>
    </div>

    <div v-loading="onlineLoading" class="online-results">
      <p v-if="!onlineLoading && !onlineItems.length && !onlineError" class="muted online-empty">
        输入关键词后回车，或用右侧「搜索」按钮。搜到后可直接「阅读」，也可以先「加入书库」统一管理。
      </p>

      <article v-for="item in onlineItems" :key="item.key" class="online-card">
        <div class="oc-cover">
          <img v-if="item.cover_url" :src="item.cover_url" :alt="item.name" loading="lazy" />
          <span v-else class="oc-cover-ph">{{ item.name.slice(0, 1) }}</span>
        </div>
        <div class="oc-body">
          <h3 class="oc-title">{{ item.name }}</h3>
          <p class="oc-meta">
            {{ item.author || "佚名" }}
            <span v-if="item.word_count"> · {{ (item.word_count / 10000).toFixed(1) }} 万字</span>
          </p>
          <p v-if="item.last_chapter" class="oc-last">最新：{{ item.last_chapter }}</p>
          <p v-if="item.intro" class="oc-intro">{{ item.intro }}</p>
          <div class="oc-sources">
            <div v-for="src in item.sources" :key="src.source_id" class="oc-source-row">
              <button
                type="button"
                class="oc-add oc-read"
                :disabled="!!addingKey"
                @click="onReadOnline(item, src)"
              >
                {{ addingKey === `${item.name}|${src.source_id}` ? "加载中…" : `阅读 · ${src.source_name}` }}
              </button>
              <button
                type="button"
                class="oc-add"
                :disabled="!!addingKey"
                @click="onAddOnline(item, src)"
              >
                加入书库
              </button>
            </div>
          </div>
        </div>
      </article>
    </div>
  </div>
</template>

<style scoped>
.online-page {
  max-width: none;
  width: 100%;
}

.search-box {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 36px;
  padding: 0 12px;
  min-width: 280px;
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

.search-go {
  flex-shrink: 0;
  border: none;
  background: var(--color-accent);
  color: #fff;
  height: 26px;
  padding: 0 10px;
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
  font-family: inherit;
  cursor: pointer;
}

.search-go:disabled {
  opacity: 0.6;
  cursor: default;
}

.online-toolbar {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.online-scope {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.online-scope-hint {
  opacity: 0.75;
  word-break: break-all;
}

.online-alert {
  margin: 0;
}

.online-status {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.status-chip {
  padding: 2px 9px;
  border-radius: var(--radius-full);
  border: 1px solid var(--color-border);
  background: var(--color-surface-2);
  color: var(--color-text-3);
  font-size: var(--text-xs);
}

.status-chip.is-bad {
  border-color: var(--color-danger, #f56c6c);
  color: var(--color-danger, #f56c6c);
}

.online-results {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 120px;
}

.online-empty {
  padding: var(--space-5) 0;
  text-align: center;
}

.online-card {
  display: flex;
  gap: 12px;
  padding: 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.oc-cover {
  flex-shrink: 0;
  width: 64px;
  height: 88px;
  border-radius: var(--radius-sm);
  overflow: hidden;
  background: var(--color-surface-2);
  display: flex;
  align-items: center;
  justify-content: center;
}

.oc-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.oc-cover-ph {
  font-size: 24px;
  color: var(--color-text-3);
}

.oc-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.oc-title {
  margin: 0;
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--color-text);
}

.oc-meta,
.oc-last {
  margin: 0;
  font-size: var(--text-xs);
  color: var(--color-text-3);
}

.oc-intro {
  margin: 2px 0 0;
  font-size: var(--text-xs);
  color: var(--color-text-2);
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.oc-sources {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 6px;
}

.oc-source-row {
  display: flex;
  gap: 4px;
  align-items: center;
}

.oc-read {
  border-color: var(--color-accent);
  background: var(--color-accent);
  color: #fff;
}

.oc-read:hover:not(:disabled) {
  border-color: var(--color-accent);
  color: #fff;
  filter: brightness(1.08);
}

.oc-add {
  border: 1px solid var(--color-border);
  background: var(--color-surface-2);
  color: var(--color-text-2);
  height: 28px;
  padding: 0 10px;
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
  font-family: inherit;
  cursor: pointer;
}

.oc-add:hover:not(:disabled) {
  border-color: var(--color-accent);
  color: var(--color-accent);
}

.oc-add:disabled {
  opacity: 0.55;
  cursor: default;
}

@media (max-width: 720px) {
  .search-box {
    min-width: 0;
    width: 100%;
  }

  .online-card {
    padding: 10px;
  }

  .oc-cover {
    width: 54px;
    height: 74px;
  }
}
</style>
