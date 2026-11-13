<script setup lang="ts">
/**
 * 书库封面墙：书源/分类筛选、搜索、排序、视图切换、多选批量、分页。
 */
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { SOURCE_OPTS, libraryRouteQuery, useLibraryStore } from "@/stores/library";
import { batchDeleteBooks, fetchBooks, pullAllBookProgress } from "@/api/admin";
import type { PullAllProgressResult } from "@/api/admin";
import { startBatchScrape } from "@/api/progress";
import type { BookListItem } from "@/api/types";
import BookCard from "@/components/BookCard.vue";
import BaseEmpty from "@/components/BaseEmpty.vue";
import GroupTabs from "@/components/GroupTabs.vue";
import LibraryBatchEditDialog from "@/components/library/LibraryBatchEditDialog.vue";
import LibraryContinueRow from "@/components/library/LibraryContinueRow.vue";
import LibraryFilterBar from "@/components/library/LibraryFilterBar.vue";
import LibraryToolbar from "@/components/library/LibraryToolbar.vue";
import OnlineBookDialog from "@/components/library/OnlineBookDialog.vue";

const router = useRouter();
const route = useRoute();
const lib = useLibraryStore();

/** 书库分组：书架与在线搜索各占一个页面 */
const LIBRARY_TABS = [
  { name: "library", label: "书架" },
  { name: "online-search", label: "在线搜索" },
];

function onLibraryTab(name: string) {
  if (name === "library") return;
  // 带上当前筛选，从在线搜索返回时可直接还原
  void router.push({ name, query: desiredQuery() });
}

const searchInput = ref(lib.q);
/** 移动端默认更小封面，PC 保持约 150 */
const defaultCoverWidth = window.innerWidth < 768 ? 110 : 150;
const coverWidth = ref(Number(localStorage.getItem("novel_grid_size") || defaultCoverWidth));
const viewMode = ref<"grid" | "list">(
  (localStorage.getItem("novel_view_mode") as "grid" | "list") || "grid",
);
const selectedIds = ref<Set<number>>(new Set());
/** 批量选择模式：显式进入后卡片点击才改为勾选 */
const selectionMode = ref(false);
const showSkeleton = ref(true);
const batchBusy = ref(false);
const loadError = ref("");
let searchTimer: ReturnType<typeof setTimeout> | undefined;

/** 继续阅读：最近在读、且未读完的书（最多 8 本） */
const continueItems = ref<BookListItem[]>([]);
/** 有筛选/搜索时隐藏继续阅读，避免与筛选结果混淆 */
const showContinueRow = computed(
  () => lib.page === 1 && !lib.q && !lib.source && !lib.category && !lib.onlyUnscraped,
);

/** 批量编辑弹窗 */
const batchEditOpen = ref(false);
/** 勾选的书 id 列表（批量编辑弹窗使用） */
const selectedIdList = computed(() => [...selectedIds.value]);

const sortOptions = [
  { value: "updated", label: "导入/更新时间" },
  { value: "title", label: "书名" },
  { value: "author", label: "作者" },
  { value: "words", label: "字数" },
  { value: "chapters", label: "章节" },
];

/** 书架来源筛选：三部分拆开看 */
const ORIGIN_OPTS = [
  { value: "all", label: "全部书架" },
  { value: "both", label: "双端共有" },
  { value: "server", label: "仅服务端" },
  { value: "legado", label: "仅手机在线" },
];

/** 仅 Legado 的在线书：没有服务端正文，点开只展示信息 */
const onlineBook = ref<BookListItem | null>(null);

/** 三部分计数，随书库列表一起返回 */
const shelfCounts = computed(() => lib.originCounts);

function onOriginClick(value: string) {
  if (value === lib.origin) return;
  clearSelection();
  void lib.applyFilter({ origin: value });
}

const gridStyle = computed(() => {
  if (viewMode.value === "list") {
    return { gridTemplateColumns: "1fr" };
  }
  // 固定列宽 + 左对齐：卡片等宽，不被 1fr 拉变形
  return {
    gridTemplateColumns: `repeat(auto-fill, minmax(min(100%, ${coverWidth.value}px), ${coverWidth.value}px))`,
    justifyContent: "start",
  };
});

const selectedCount = computed(() => selectedIds.value.size);

/** 按封面宽度估算列数×可见行数，动态每页数量（保证正整数） */
function computePageSize(): number {
  const wrap = gridWrapRef.value;
  const gap = 14;
  const width = wrap?.clientWidth || 960;

  // 列表模式：按行高约 68px 估行数，避免套用封面高度导致每页过少
  if (viewMode.value === "list") {
    const rowH = 68;
    const viewH = window.innerHeight - 260;
    const rows = Math.max(6, Math.floor((viewH + gap) / (rowH + gap)));
    return Math.min(100, Math.max(rows * 2, 12));
  }

  const coverW = Math.floor(Number(coverWidth.value) || 150);
  const cols = Math.max(2, Math.floor((width + gap) / (coverW + gap)));
  const coverH = (coverW * 4) / 3 + 72;
  const viewH = window.innerHeight - 220;
  const rows = Math.max(1, Math.floor((viewH + gap) / (coverH + gap)));
  const n = cols * rows;
  if (!Number.isFinite(n)) return 24;
  return Math.min(200, Math.max(Math.floor(n), 8));
}

const gridWrapRef = ref<HTMLElement | null>(null);

function applyPageSize(load = true) {
  const next = computePageSize();
  if (next === lib.pageSize) return;
  const firstIndex = (lib.page - 1) * lib.pageSize;
  lib.pageSize = next;
  lib.page = Math.floor(firstIndex / next) + 1;
  if (load) void lib.loadBooks();
}

function onCoverWidthChange(value: number) {
  coverWidth.value = value;
  localStorage.setItem("novel_grid_size", String(coverWidth.value));
  applyPageSize();
}

function onViewModeChange(mode: "grid" | "list") {
  viewMode.value = mode;
  localStorage.setItem("novel_view_mode", mode);
  // 切换视图后按新模式重算每页数量
  applyPageSize();
}

function onSearchInput(value: string) {
  searchInput.value = value;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    void lib.applyFilter({ q: searchInput.value.trim() });
  }, 300);
}

function onSearchEnter(value: string) {
  searchInput.value = value;
  clearTimeout(searchTimer);
  void lib.applyFilter({ q: searchInput.value.trim() });
}

function onToggleSelection() {
  if (selectionMode.value) exitSelectionMode();
  else enterSelectionMode();
}

function onSourceClick(src: string) {
  clearSelection();
  void lib.applyFilter({ source: src });
}

function onCategoryClick(cat: string) {
  clearSelection();
  void lib.applyFilter({ category: cat });
}

function onSortChange(val: string) {
  clearSelection();
  void lib.applyFilter({ sort: val });
}

function onToggleUnscraped() {
  clearSelection();
  void lib.applyFilter({ onlyUnscraped: !lib.onlyUnscraped });
}

/** query 值可能是数组（重复参数），统一取第一个字符串 */
function queryStr(v: unknown): string {
  if (Array.isArray(v)) return typeof v[0] === "string" ? v[0] : "";
  return typeof v === "string" ? v : "";
}

/** 从 URL query 恢复筛选状态；返回是否发生变化 */
function readQueryToStore(): boolean {
  const q = route.query;
  const nextQ = queryStr(q.q);
  const rawSource = queryStr(q.source);
  const nextSource = (SOURCE_OPTS as readonly string[]).includes(rawSource) ? rawSource : "";
  const nextCategory = queryStr(q.category);
  const rawSort = queryStr(q.sort);
  const nextSort = sortOptions.some((o) => o.value === rawSort) ? rawSort : "updated";
  const nextUnscraped = queryStr(q.unscraped) === "1";
  const rawOrigin = queryStr(q.origin);
  const nextOrigin = ORIGIN_OPTS.some((o) => o.value === rawOrigin) ? rawOrigin : "all";
  const rawPage = Number.parseInt(queryStr(q.page), 10);
  const nextPage = Number.isInteger(rawPage) && rawPage > 0 ? rawPage : 1;

  const changed =
    lib.q !== nextQ ||
    lib.source !== nextSource ||
    lib.category !== nextCategory ||
    lib.sort !== nextSort ||
    lib.onlyUnscraped !== nextUnscraped ||
    lib.origin !== nextOrigin ||
    lib.page !== nextPage;
  if (!changed) return false;

  lib.q = nextQ;
  lib.source = nextSource;
  lib.category = nextCategory;
  lib.sort = nextSort;
  lib.onlyUnscraped = nextUnscraped;
  lib.origin = nextOrigin;
  lib.page = nextPage;
  searchInput.value = nextQ;
  localStorage.setItem("novel_sort", nextSort);
  return true;
}

/** 当前筛选对应的 URL query */
function desiredQuery(): Record<string, string> {
  return libraryRouteQuery(lib);
}

/** 把筛选写回 URL；query 未变化时不做无谓导航 */
function syncQuery() {
  const next = desiredQuery();
  const cur: Record<string, string> = {};
  for (const [k, v] of Object.entries(route.query)) {
    const s = queryStr(v);
    if (s) cur[k] = s;
  }
  for (const k of new Set([...Object.keys(next), ...Object.keys(cur)])) {
    if ((next[k] || "") !== (cur[k] || "")) {
      void router.replace({ query: next });
      return;
    }
  }
}

// 筛选变化 → 写回 URL（replace，不污染返回历史）
watch(
  () => [lib.q, lib.source, lib.category, lib.sort, lib.onlyUnscraped, lib.origin, lib.page] as const,
  syncQuery,
);
// URL 变化（刷新/浏览器返回/前进）→ 恢复筛选并重新拉取
watch(
  () => route.query,
  () => {
    if (readQueryToStore()) void lib.loadBooks();
  },
);

function openBook(id: number) {
  const book = lib.items.find((b) => b.id === id);
  // 负数 id = 仅 Legado 书架里的书，服务端没有来源信息，展示信息而不是进编辑页；
  // 在线搜索登记的书（online=true）现在由服务端按需抓目录/正文，正常进详情页
  if (book && book.readable === false) {
    onlineBook.value = book;
    return;
  }
  router.push({ name: "book-detail", params: { id: String(id) } });
}

/** 继续阅读直接进阅读器（阅读器会按 read_chapter_index 续读） */
function openReader(id: number) {
  void router.push({ name: "book-read", params: { id: String(id) } });
}

async function loadContinue() {
  try {
    const data = await fetchBooks({ sort: "read", read: true, page: 1, page_size: 12 });
    continueItems.value = (data.items || [])
      .filter((b) => {
        // 已读完的不再提示续读；刚开读（0%）仍保留
        const p = Number(b.read_percent ?? 0);
        return Number.isFinite(p) && p < 100;
      })
      .slice(0, 8);
  } catch {
    // 继续阅读只是增强区块，失败不影响书库主列表
    continueItems.value = [];
  }
}

// —— 书库打开时从 WebDAV 批量同步进度（与 Windows 端行为一致） ——
// 只同步书库中已存在的书籍：手机 Legado 书架通常比服务端书库大，书库外的
// 进度文件由服务端直接忽略，因此「继续阅读」永远只展示书架里存在的书。
const PROGRESS_SYNC_THROTTLE = 15_000;
let lastProgressSyncAt = 0;
let syncingProgress = false;

type SyncedProgressBook = PullAllProgressResult["books"][number];

/** 把同步到的进度写回卡片；返回 false 表示必须重新拉取列表才能保证正确 */
function applySyncedProgress(updates: SyncedProgressBook[]): boolean {
  const byId = new Map<number, SyncedProgressBook>();
  for (const u of updates) byId.set(u.book_id, u);

  const merge = (list: BookListItem[]): void => {
    for (const b of list) {
      const u = byId.get(b.id);
      if (!u) continue;
      if (typeof u.read_percent === "number") b.read_percent = u.read_percent;
      if (typeof u.read_at === "string" && u.read_at) b.read_at = u.read_at;
    }
  };

  const continueIds = new Set(continueItems.value.map((b) => b.id));
  // 更新到的书不在「继续阅读」里 ⇒ 它可能刚进入前 8 名；或有书读完了需要移出
  // 并补位。这两种情况重新拉一次继续阅读列表，网格卡片仍可原地更新。
  const covered =
    updates.every((u) => continueIds.has(u.book_id)) &&
    !updates.some((u) => continueIds.has(u.book_id) && Number(u.read_percent ?? 0) >= 100);
  merge(continueItems.value);
  merge(lib.items);
  if (covered) {
    continueItems.value = continueItems.value
      .filter((b) => Number(b.read_percent ?? 0) < 100)
      .sort((a, b) => (Date.parse(b.read_at ?? "") || 0) - (Date.parse(a.read_at ?? "") || 0));
  }
  return covered;
}

async function syncContinueFromWebDAV(): Promise<void> {
  if (syncingProgress || Date.now() - lastProgressSyncAt < PROGRESS_SYNC_THROTTLE) return;
  syncingProgress = true;
  lastProgressSyncAt = Date.now();
  try {
    const res = await pullAllBookProgress();
    console.debug(
      `[sync] WebDAV 批量进度：远端 ${res.remote_files} 个文件 / 命中书库 ${res.matched} 本 / 忽略书库外 ${res.ignored} 个 / 跳过未变 ${res.skipped ?? 0} 个 / 更新 ${res.updated} 本`,
    );
    if (res.updated > 0) {
      const books = Array.isArray(res.books) ? res.books : null;
      const applied = books?.length ? applySyncedProgress(books) : false;
      if (!applied) {
        // 老服务端没有逐本明细，或继续阅读需要补位：整页重拉一次
        await Promise.all([loadContinue(), lib.loadBooks()]);
      }
    }
  } catch {
    // WebDAV 未配置 / 网络异常 / 接口 404（旧服务端）：静默降级，
    // 继续阅读仍展示本地 DB 里的进度
  } finally {
    syncingProgress = false;
  }
}

function toggleSelect(id: number) {
  if (id < 0) return; // 在线书不在本地库，不能批量编辑/删除
  const next = new Set(selectedIds.value);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  selectedIds.value = next;
}

function enterSelectionMode() {
  selectionMode.value = true;
}

/** 退出批量时一并清空勾选，避免下次进入残留 */
function exitSelectionMode() {
  selectionMode.value = false;
  clearSelection();
}

function onCardOpen(id: number) {
  if (selectionMode.value) {
    toggleSelect(id);
    return;
  }
  openBook(id);
}

function selectAllOnPage() {
  const next = new Set(selectedIds.value);
  for (const b of lib.items) if (b.id > 0) next.add(b.id);
  selectedIds.value = next;
}

function clearSelection() {
  selectedIds.value = new Set();
}

/** 批量删除：危险操作二次确认 */
async function onBatchDelete() {
  const ids = [...selectedIds.value];
  if (!ids.length || batchBusy.value) return;
  try {
    await ElMessageBox.confirm(
      `将删除已选 ${ids.length} 本书（含封面与本地库记录，不删源 TXT），确认？`,
      "批量删除",
      { type: "warning", confirmButtonText: "删除", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  batchBusy.value = true;
  try {
    const res = await batchDeleteBooks(ids);
    ElMessage.success(`已删除 ${res.deleted_count} 本`);
    clearSelection();
    await Promise.all([lib.loadBooks(), lib.loadStats(), loadContinue()]);
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "批量删除失败");
  } finally {
    batchBusy.value = false;
  }
}

/** 对已选书发起批量刮削（跳到体检页可看进度） */
async function onBatchScrape() {
  const ids = [...selectedIds.value];
  if (!ids.length || batchBusy.value) return;
  try {
    await ElMessageBox.confirm(
      `对已选 ${ids.length} 本执行刮削？可在「体检 → 刮削」查看进度。`,
      "批量刮削",
      { type: "info", confirmButtonText: "开始刮削", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  batchBusy.value = true;
  try {
    const res = await startBatchScrape({
      source: "all",
      only_missing: false,
      min_score: 0.7,
      book_ids: ids,
    });
    if (res.started) {
      ElMessage.success("刮削任务已启动");
      clearSelection();
      router.push({ name: "check", query: { tab: "scrape" } });
    } else {
      ElMessage.warning("已有刮削任务在运行，请稍后再试");
    }
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "启动刮削失败");
  } finally {
    batchBusy.value = false;
  }
}

/** 打开批量编辑 */
function openBatchEdit() {
  if (!selectedCount.value) return;
  batchEditOpen.value = true;
}

/** 批量编辑保存成功：清空勾选并刷新列表 */
async function onBatchSaved() {
  clearSelection();
  await Promise.all([lib.loadBooks(), lib.loadStats(), loadContinue()]);
}

async function loadAll() {
  showSkeleton.value = true;
  loadError.value = "";
  try {
    await Promise.all([lib.loadStats(), lib.loadBooks(), loadContinue()]);
    // 后台同步，不阻塞书库首屏；有更新时同步函数内部会刷新卡片与书库列表
    void syncContinueFromWebDAV();
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : "加载失败，请稍后重试";
  } finally {
    showSkeleton.value = false;
  }
}

onMounted(() => {
  // 先按视口确定每页数量，再从 URL 恢复页码/筛选，最后统一拉取一次
  applyPageSize(false);
  readQueryToStore();
  void loadAll();
  window.addEventListener("resize", onResize);
});

onUnmounted(() => {
  window.removeEventListener("resize", onResize);
});

let resizeTimer: ReturnType<typeof setTimeout> | undefined;
function onResize() {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(applyPageSize, 200);
}
</script>

<template>
  <div class="page library-page">
    <header class="page-header">
      <div class="title-row">
        <h1 class="page-title">书库</h1>
        <span class="count-pill">{{ lib.total }} 本</span>
      </div>
      <LibraryToolbar
        :search="searchInput"
        :sort="lib.sort"
        :sort-options="sortOptions"
        :view-mode="viewMode"
        :cover-width="coverWidth"
        :selection-mode="selectionMode"
        @search="onSearchInput"
        @search-enter="onSearchEnter"
        @sort="onSortChange"
        @view-mode="onViewModeChange"
        @cover-width="onCoverWidthChange"
        @toggle-selection="onToggleSelection"
      />
    </header>

    <GroupTabs :items="LIBRARY_TABS" active="library" @select="onLibraryTab" />

    <div v-if="(loadError || lib.error)" class="load-error" role="alert">
      <span>{{ loadError || lib.error }}</span>
      <button type="button" class="ghost-btn" @click="loadAll">重试</button>
    </div>

    <!-- 继续阅读：无筛选时展示最近在读的书，点击直接续读 -->
    <LibraryContinueRow
      v-if="showContinueRow && continueItems.length"
      :items="continueItems"
      @open="openReader"
    />

    <LibraryFilterBar
      :origin="lib.origin"
      :source="lib.source"
      :category="lib.category"
      :only-unscraped="lib.onlyUnscraped"
      :category-names="lib.categoryNames"
      :counts="shelfCounts"
      @origin="onOriginClick"
      @source="onSourceClick"
      @category="onCategoryClick"
      @toggle-unscraped="onToggleUnscraped"
    />

    <!-- 批量操作条：仅在批量模式下常驻 -->
    <div v-if="selectionMode" class="batch-bar">
      <span>已选 <strong>{{ selectedCount }}</strong> 本</span>
      <div class="batch-actions">
        <button type="button" class="ghost-btn" @click="selectAllOnPage">全选本页</button>
        <button type="button" class="ghost-btn" @click="clearSelection">取消选择</button>
        <button
          type="button"
          class="primary-btn"
          :disabled="batchBusy || !selectedCount"
          @click="openBatchEdit"
        >
          批量编辑
        </button>
        <button
          type="button"
          class="primary-btn"
          :disabled="batchBusy || !selectedCount"
          @click="onBatchScrape"
        >
          批量刮削
        </button>
        <button
          type="button"
          class="ghost-btn danger"
          :disabled="batchBusy || !selectedCount"
          @click="onBatchDelete"
        >
          批量删除
        </button>
        <button type="button" class="ghost-btn" @click="exitSelectionMode">退出批量</button>
      </div>
    </div>

    <!-- 骨架屏 -->
    <div
      v-if="showSkeleton && lib.loading"
      class="grid-wrap"
      :style="gridStyle"
      aria-hidden="true"
    >
      <div v-for="i in Math.min(Math.max(lib.pageSize, 8), 24)" :key="i" class="skel-card">
        <div class="skeleton skel-cover" />
        <div class="skeleton skel-line" />
        <div class="skeleton skel-line short" />
      </div>
    </div>

    <div
      v-else
      v-loading="lib.loading"
      ref="gridWrapRef"
      class="grid-wrap"
      :style="gridStyle"
    >
      <BaseEmpty
        v-if="!lib.loading && !lib.items.length"
        title="书库还是空的"
        description="到「导入」页扫描本地 TXT，即可开始管理你的小说。"
        action-text="去导入"
        @action="router.push({ name: 'import' })"
      />

      <BookCard
        v-for="b in lib.items"
        :key="b.id"
        :book="b"
        :view="viewMode"
        :cover-width="coverWidth"
        :highlight="lib.q"
        :selectable="selectionMode && b.id > 0"
        :selected="selectedIds.has(b.id)"
        :select-mode="selectionMode"
        @open="onCardOpen"
        @toggle-select="toggleSelect"
      />
    </div>

    <div class="pager">
      <button
        type="button"
        class="ghost-btn"
        :disabled="lib.page <= 1"
        @click="void lib.gotoPage(lib.page - 1)"
      >
        上一页
      </button>
      <span class="page-indicator muted">
        {{ lib.page }} / {{ Math.max(lib.totalPages, 1) }}
      </span>
      <button
        type="button"
        class="ghost-btn"
        :disabled="lib.page >= lib.totalPages"
        @click="void lib.gotoPage(lib.page + 1)"
      >
        下一页
      </button>
    </div>

    <!-- 批量编辑弹窗：只提交勾选项 -->
    <LibraryBatchEditDialog
      v-model:visible="batchEditOpen"
      :ids="selectedIdList"
      :category-tree="lib.stats?.category_tree || []"
      @saved="onBatchSaved"
    />

    <!-- 在线书信息：仅 Legado 书架有，服务端没有正文 -->
    <OnlineBookDialog :book="onlineBook" @close="onlineBook = null" />
  </div>
</template>

<style scoped>
.title-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.count-pill {
  padding: 2px 10px;
  border-radius: var(--radius-full);
  background: var(--color-surface-2);
  color: var(--color-text-2);
  font-size: var(--text-xs);
  border: 1px solid var(--color-border);
}

.batch-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: 10px 14px;
  border-radius: var(--radius-md);
  background: var(--color-accent-soft);
  border: 1px solid var(--color-accent);
  color: var(--color-accent);
  font-size: var(--text-sm);
  /* 滚动到书库下方勾选后，操作条仍然可见 */
  position: sticky;
  top: 0;
  z-index: 25;
  backdrop-filter: blur(8px);
  box-shadow: var(--shadow-md);
}

.load-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: 10px 14px;
  border: 1px solid var(--color-danger);
  border-radius: var(--radius-md);
  background: var(--color-danger-soft);
  color: var(--color-danger);
  font-size: var(--text-sm);
}

.load-error .ghost-btn {
  flex-shrink: 0;
}

.batch-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

/* 批量条内按钮统一高度，保证四按钮同行对齐 */
.batch-actions .ghost-btn,
.batch-actions .primary-btn {
  height: 32px;
  margin-top: 0;
  padding: 0 12px;
}

/* PC 书库铺满主区，不限制 1280 内容宽 */
.library-page {
  max-width: none;
  width: 100%;
}

.grid-wrap {
  display: grid;
  gap: 14px;
  min-height: 200px;
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
  overflow-x: hidden;
  align-content: start;
}

.skel-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.skel-cover {
  aspect-ratio: 3 / 4;
  border-radius: var(--radius-md);
}

.skel-line {
  height: 12px;
  border-radius: 4px;
}

.skel-line.short {
  width: 60%;
}

.empty-state {
  grid-column: 1 / -1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: var(--space-7) var(--space-4);
  text-align: center;
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.empty-icon {
  width: 56px;
  height: 56px;
  border-radius: var(--radius-md);
  background: var(--color-surface-2);
  color: var(--color-text-3);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 4px;
}

.empty-state h3 {
  margin: 0;
  font-size: var(--text-lg);
}

.primary-btn {
  height: 32px;
  padding: 0 16px;
  border: none;
  border-radius: var(--radius-sm);
  background: var(--color-accent);
  color: #fff;
  font-size: var(--text-sm);
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
}

.primary-btn:hover {
  background: var(--color-accent-hover);
}

.ghost-btn {
  height: 32px;
  padding: 0 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-text-2);
  font-size: var(--text-sm);
  font-family: inherit;
  cursor: pointer;
}

.ghost-btn:hover:not(:disabled) {
  border-color: var(--color-border-strong);
  color: var(--color-text);
}

.ghost-btn.is-active {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
  color: var(--color-accent);
}

.ghost-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 8px 0 16px;
}

.page-indicator {
  min-width: 64px;
  text-align: center;
  font-variant-numeric: tabular-nums;
}

@media (max-width: 576px) {
  /* 移动端走尺寸档位（120px 起），不强制过小列宽 */
  .grid-wrap {
    gap: 10px;
  }

  .batch-bar {
    flex-direction: column;
    align-items: stretch;
  }

  .batch-actions {
    width: 100%;
  }

  .batch-actions .ghost-btn,
  .batch-actions .primary-btn {
    flex: 1;
  }
}
</style>
