<script setup lang="ts">
/**
 * 详情页目录：服务端分页 + 关键词筛选，大部头不再一次拉全量。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { fetchBookToc } from "@/api/admin";
import type { TocChapter } from "@/api/admin";
import BaseEmpty from "@/components/BaseEmpty.vue";
import type { BookDetail } from "@/api/types";

const props = defineProps<{
  bookId: number;
  book: BookDetail | null;
}>();

const router = useRouter();

type TocDisplayItem = TocChapter & { no: number };

const tocItems = ref<TocDisplayItem[]>([]);
/** 当前查询命中总数（筛选时为匹配数） */
const tocTotal = ref(0);
/** 全书有效章节总数（不受筛选影响） */
const tocBookTotal = ref(0);
const tocLoading = ref(false);

/** 每页 200 章；'all' 为一次性拉全集（仅未筛选时提供） */
const TOC_PAGE_SIZE = 200;
const tocPage = ref<number | "all">(1);
const tocFilter = ref("");

const filterActive = computed(() => tocFilter.value.trim().length > 0);

const tocPageRanges = computed(() => {
  const pages: { key: number; label: string }[] = [];
  if (tocTotal.value <= TOC_PAGE_SIZE) return pages;
  for (let start = 1; start <= tocTotal.value; start += TOC_PAGE_SIZE) {
    const end = Math.min(start + TOC_PAGE_SIZE - 1, tocTotal.value);
    pages.push({ key: Math.ceil(start / TOC_PAGE_SIZE), label: `${start}–${end}` });
  }
  return pages;
});

/** 页签条横向滚动（章节极多时避免铺满换行） */
const tocPagesTrack = ref<HTMLElement | null>(null);
const tocScrollLeft = ref(false);
const tocScrollRight = ref(false);

/** 页签较多时才显示左右按钮 */
const showTocNav = computed(() => tocPageRanges.value.length > 3);

function updateTocScrollState() {
  const el = tocPagesTrack.value;
  if (!el) {
    tocScrollLeft.value = false;
    tocScrollRight.value = false;
    return;
  }
  const max = el.scrollWidth - el.clientWidth;
  tocScrollLeft.value = el.scrollLeft > 1;
  tocScrollRight.value = el.scrollLeft < max - 1;
}

function scrollTocPages(dir: -1 | 1) {
  const el = tocPagesTrack.value;
  if (!el) return;
  el.scrollBy({ left: dir * Math.max(el.clientWidth * 0.6, 120), behavior: "smooth" });
}

function scrollTocIntoView() {
  const el = tocPagesTrack.value;
  if (!el) return;
  const active = el.querySelector<HTMLElement>(".page-chip.is-active");
  if (active) {
    const left = active.offsetLeft - el.clientWidth / 2 + active.offsetWidth / 2;
    el.scrollTo({ left: Math.max(0, left), behavior: "smooth" });
  }
  // 滚动动画结束后刷新按钮态
  window.setTimeout(updateTocScrollState, 280);
}

watch(
  tocPage,
  () => {
    nextTick(() => {
      scrollTocIntoView();
    });
  },
  { flush: "post" },
);

onMounted(() => {
  nextTick(() => {
    updateTocScrollState();
  });
  window.addEventListener("resize", updateTocScrollState);
  if (props.bookId) void loadToc(1);
});

onBeforeUnmount(() => {
  window.removeEventListener("resize", updateTocScrollState);
  clearTimeout(tocFilterTimer);
});

watch(
  () => props.bookId,
  (id) => {
    tocPage.value = 1;
    if (tocFilter.value) {
      suppressTocFilterWatch = true;
      tocFilter.value = "";
    }
    tocSeq += 1;
    if (id) void loadToc(1);
  },
);

let tocSeq = 0;
let tocFilterTimer: ReturnType<typeof setTimeout> | undefined;
/** 换书时程序化清空筛选，避免防抖定时器再触发一次加载 */
let suppressTocFilterWatch = false;

async function loadToc(page: number | "all" = tocPage.value) {
  if (!props.bookId) return;
  clearTimeout(tocFilterTimer);
  const seq = ++tocSeq;
  const kw = tocFilter.value.trim();
  tocLoading.value = true;
  try {
    if (page === "all") {
      const all: TocDisplayItem[] = [];
      let p = 1;
      let matched = 0;
      for (;;) {
        const res = await fetchBookToc(props.bookId, { page: p, page_size: 500, q: kw });
        if (seq !== tocSeq) return;
        all.push(...res.items);
        matched = res.total;
        tocBookTotal.value = res.book_total || matched;
        if (all.length >= matched || !res.items.length) break;
        p += 1;
      }
      tocItems.value = all;
      tocTotal.value = matched;
    } else {
      const res = await fetchBookToc(props.bookId, {
        page,
        page_size: TOC_PAGE_SIZE,
        q: kw,
      });
      if (seq !== tocSeq) return;
      tocItems.value = res.items;
      tocTotal.value = res.total;
      tocBookTotal.value = res.book_total || res.total;
    }
  } catch (e) {
    if (seq !== tocSeq) return;
    // 只有首页失败才回退到详情里的章节预览，翻页失败保留当前列表
    if (page !== "all" && page > 1) {
      ElMessage.error(e instanceof Error ? e.message : "目录加载失败");
      return;
    }
    tocItems.value = (props.book?.chapters_preview || []).map((c) => ({
      id: c.id,
      index: c.index,
      title: c.title,
      no: c.index + 1,
    }));
    tocTotal.value = tocBookTotal.value =
      props.book?.chapter_count || tocItems.value.length;
    if (props.book?.online) {
      ElMessage.error(e instanceof Error ? e.message : "在线目录加载失败，请检查书源");
    }
  } finally {
    if (seq === tocSeq) {
      tocLoading.value = false;
      // 页签条出现后刷新左右按钮可用态
      nextTick(() => {
        updateTocScrollState();
        scrollTocIntoView();
      });
    }
  }
}

/** 切页：页码从 1 开始；'all' 仅在未筛选时可选 */
function onTocPage(page: number | "all") {
  tocPage.value = page;
  void loadToc(page);
}

watch(tocFilter, () => {
  if (suppressTocFilterWatch) {
    suppressTocFilterWatch = false;
    return;
  }
  clearTimeout(tocFilterTimer);
  tocFilterTimer = setTimeout(() => {
    tocPage.value = 1;
    void loadToc(1);
  }, 300);
});

/** 从目录进入阅读器并定位到该章 */
function openReaderAt(ch: TocChapter) {
  if (!props.bookId) return;
  router.push({
    name: "book-read",
    params: { id: String(props.bookId) },
    query: { chapter: String(ch.id) },
  });
}
</script>

<template>
  <aside class="toc-side page-card">
    <div class="toc-side-head">
      <h2 class="section-title">目录</h2>
      <div v-if="!filterActive || tocPageRanges.length" class="toc-pages">
        <button
          v-if="!filterActive"
          type="button"
          class="page-chip"
          :class="{ 'is-active': tocPage === 'all' }"
          @click="onTocPage('all')"
        >
          全部
        </button>
        <button
          v-if="showTocNav"
          type="button"
          class="page-nav-btn"
          :disabled="!tocScrollLeft"
          title="向前翻页"
          aria-label="向前翻页"
          @click="scrollTocPages(-1)"
        >
          ‹
        </button>
        <div ref="tocPagesTrack" class="toc-pages-track" @scroll.passive="updateTocScrollState">
          <button
            v-for="p in tocPageRanges"
            :key="p.key"
            type="button"
            class="page-chip"
            :class="{ 'is-active': tocPage === p.key }"
            @click="onTocPage(p.key)"
          >
            {{ p.label }}
          </button>
        </div>
        <button
          v-if="showTocNav"
          type="button"
          class="page-nav-btn"
          :disabled="!tocScrollRight"
          title="向后翻页"
          aria-label="向后翻页"
          @click="scrollTocPages(1)"
        >
          ›
        </button>
      </div>
      <span class="muted-xs toc-count">共 {{ tocBookTotal || book?.chapter_count || 0 }} 章</span>
    </div>
    <div class="toc-filter">
      <input v-model="tocFilter" class="toc-filter-input" placeholder="筛选章节…" />
      <button
        v-if="tocFilter"
        type="button"
        class="toc-clear"
        title="清除筛选"
        @click="tocFilter = ''"
      >
        ×
      </button>
      <span v-if="filterActive" class="muted-xs">匹配 {{ tocTotal }} 章</span>
    </div>
    <div v-loading="tocLoading" class="toc-side-list">
      <button
        v-for="ch in tocItems"
        :key="ch.id"
        type="button"
        class="toc-side-item"
        @click="openReaderAt(ch)"
      >
        <span class="idx muted-xs">{{ ch.no }}</span>
        <span class="ch-title">{{ ch.title }}</span>
      </button>
      <BaseEmpty
        v-if="!tocItems.length && !tocLoading"
        compact
        icon="list"
        :title="filterActive ? '没有匹配章节' : '暂无章节'"
      />
    </div>
  </aside>
</template>

<style scoped>
.toc-side {
  display: flex;
  flex-direction: column;
  min-height: 0;
  /* 目录吃满剩余高度，仅列表内滚动（页面无滚动条） */
  flex: 1;
  overflow: hidden;
}

.toc-side-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 12px;
  margin-bottom: 8px;
}

.toc-side-head .section-title {
  margin: 0;
}

.section-title {
  margin: 10px 0 6px;
  font-size: var(--text-sm);
  font-weight: 600;
  flex-shrink: 0;
}

.toc-filter {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 8px;
}

.toc-filter-input {
  flex: 1;
  min-width: 0;
  height: 30px;
  padding: 0 8px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface-2);
  color: var(--color-text);
  font-size: var(--text-xs);
  font-family: inherit;
  outline: none;
}

.toc-filter-input:focus {
  border-color: var(--color-accent);
  box-shadow: var(--shadow-focus);
  background: var(--color-surface);
}

.toc-clear {
  width: 22px;
  height: 22px;
  flex-shrink: 0;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--color-text-3);
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
}

.toc-clear:hover {
  background: var(--color-surface-2);
  color: var(--color-text);
}

.toc-pages {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
  min-width: 0;
}

.toc-pages-track {
  display: flex;
  flex-wrap: nowrap;
  gap: 6px;
  overflow-x: auto;
  overflow-y: hidden;
  min-width: 0;
  flex: 1;
  padding: 2px 0;
  scrollbar-width: none;
  -ms-overflow-style: none;
  scroll-behavior: smooth;
}

.toc-pages-track::-webkit-scrollbar {
  display: none;
}

.page-chip {
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  color: var(--color-text-2);
  font-size: var(--text-xs);
  padding: 3px 10px;
  border-radius: var(--radius-full);
  cursor: pointer;
  font-family: inherit;
  flex-shrink: 0;
  white-space: nowrap;
}

.page-nav-btn {
  flex-shrink: 0;
  width: 24px;
  height: 24px;
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  color: var(--color-text-2);
  border-radius: var(--radius-full);
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-family: inherit;
  padding: 0;
}

.page-nav-btn:hover:not(:disabled) {
  border-color: var(--color-border-strong);
  color: var(--color-text);
}

.page-nav-btn:disabled {
  opacity: 0.35;
  cursor: default;
}

.page-chip:hover {
  border-color: var(--color-border-strong);
  color: var(--color-text);
}

.page-chip.is-active {
  background: var(--color-accent-soft);
  border-color: var(--color-accent);
  color: var(--color-accent);
}

.toc-count {
  flex-shrink: 0;
}

.toc-side-list {
  overflow-y: auto;
  overflow-x: hidden;
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-height: 0;
  flex: 1;
  /* 细滚动条，长目录可滚 */
  scrollbar-width: thin;
  scrollbar-color: var(--color-border-strong) transparent;
  padding-right: 4px;
}

.toc-side-list::-webkit-scrollbar {
  width: 6px;
}

.toc-side-list::-webkit-scrollbar-thumb {
  background: var(--color-border-strong);
  border-radius: 3px;
}

.toc-side-list::-webkit-scrollbar-track {
  background: transparent;
}

.toc-side-item {
  display: flex;
  align-items: baseline;
  gap: 8px;
  width: 100%;
  text-align: left;
  border: none;
  background: transparent;
  padding: 6px 8px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font: inherit;
  color: var(--color-text);
}

.toc-side-item:hover {
  background: var(--color-surface-2);
  color: var(--color-accent);
}

.toc-side-item .idx {
  flex-shrink: 0;
  min-width: 2.2em;
  text-align: right;
}

.toc-side-item .ch-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-sm);
}

@media (max-width: 900px) {
  .toc-side {
    min-height: 240px;
    overflow: hidden;
  }
}
</style>
