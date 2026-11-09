<script setup lang="ts">
/**
 * 书库封面墙：书源/分类筛选、搜索、排序、视图切换、多选批量、分页。
 */
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { SOURCE_OPTS, useLibraryStore } from "@/stores/library";
import { batchDeleteBooks, batchUpdateBooks, fetchBooks, pullAllBookProgress } from "@/api/admin";
import { fetchShelfBook } from "@/api/shelf";
import { addOnlineBook, fetchOnlineSources, searchOnline } from "@/api/online";
import type { OnlineItem, OnlineSourceOption, OnlineSourceRef, OnlineSourceStatus } from "@/api/online";
import type { BatchUpdateBody } from "@/api/admin";
import { startBatchScrape } from "@/api/progress";
import type { BookListItem } from "@/api/types";
import BookCard from "@/components/BookCard.vue";
import AppIcon from "@/components/AppIcon.vue";
import BaseEmpty from "@/components/BaseEmpty.vue";

const router = useRouter();
const route = useRoute();
const lib = useLibraryStore();

const searchInput = ref(lib.q);
/** 移动端默认更小封面，PC 保持约 150 */
const defaultCoverWidth = window.innerWidth < 768 ? 110 : 150;
const coverWidth = ref(Number(localStorage.getItem("novel_grid_size") || defaultCoverWidth));
const viewMode = ref<"grid" | "list">(
  (localStorage.getItem("novel_view_mode") as "grid" | "list") || "grid",
);
const selectedIds = ref<Set<number>>(new Set());
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
const editCategory = ref(false);
const editSource = ref("");
const editCategoryName = ref("");
const editTagsMode = ref<"append" | "remove" | "replace">("append");
const editTags = ref("");
const editStatus = ref("");

/** 当前所选书源下的分类选项（与编辑页共用统计里的分类树） */
const editCategoryOptions = computed(() => {
  const tree = lib.stats?.category_tree || [];
  const node = tree.find((n) => n.key === editSource.value);
  return node?.categories || [];
});

// 切换书源后，分类不在新列表中则清空
watch(editSource, () => {
  if (!editCategoryOptions.value.includes(editCategoryName.value)) {
    editCategoryName.value = "";
  }
});

const sourceLabels: Record<string, string> = {
  "": "全部",
  起点: "起点",
  番茄: "番茄",
  纵横: "纵横",
};

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
const onlineDetail = ref<BookListItem | null>(null);
const onlineBusy = ref(false);
/** 三部分计数，随书库列表一起返回 */
const shelfCounts = computed(() => lib.originCounts);

async function showOnlineBook(book: BookListItem) {
  onlineDetail.value = book;
  if (!book.shelf_id) return;
  onlineBusy.value = true;
  try {
    const detail = await fetchShelfBook(book.shelf_id);
    onlineDetail.value = { ...book, ...(detail as unknown as BookListItem) };
  } catch {
    // 列表里已有基本信息，详情失败不阻断展示
  } finally {
    onlineBusy.value = false;
  }
}

function onOriginClick(value: string) {
  if (value === lib.origin) return;
  clearSelection();
  void lib.applyFilter({ origin: value });
}

// ------------------------------ 在线搜索 ------------------------------
/** local=搜书库内书籍；online=用已导入的 Legado 书源在服务端检索 */
const searchMode = ref<"local" | "online">("local");
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

function switchMode(mode: "local" | "online") {
  if (searchMode.value === mode) return;
  searchMode.value = mode;
  if (mode === "online") {
    if (!onlineSources.value.length) void loadOnlineSources();
    if (searchInput.value.trim() && !onlineItems.value.length) void runOnlineSearch();
  }
}

async function loadOnlineSources() {
  try {
    const data = await fetchOnlineSources();
    onlineSources.value = data.items || [];
    if (!onlineSources.value.length) {
      onlineError.value = "还没有可用于搜索的书源，请先到「书源管理」导入 Legado 书源。";
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

async function onAddOnline(item: OnlineItem, src: OnlineSourceRef) {
  const key = `${item.name}|${src.source_id}`;
  if (addingKey.value) return;
  addingKey.value = key;
  try {
    const res = await addOnlineBook({
      source_id: src.source_id,
      name: item.name,
      author: item.author,
      book_url: src.book_url,
      cover_url: src.cover_url || item.cover_url,
      intro: src.intro || item.intro,
      kind: src.kind || item.kind,
      last_chapter: src.last_chapter || item.last_chapter,
      word_count: item.word_count,
    });
    ElMessage.success(res.created ? `已加入书库：${res.title}` : `已在书库中：${res.title}`);
    void lib.loadBooks();
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : "加入书库失败");
  } finally {
    addingKey.value = "";
  }
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

function onGridSize() {
  localStorage.setItem("novel_grid_size", String(coverWidth.value));
  applyPageSize();
}

function onViewModeChange(mode: "grid" | "list") {
  viewMode.value = mode;
  localStorage.setItem("novel_view_mode", mode);
  // 切换视图后按新模式重算每页数量
  applyPageSize();
}

function onSearchInput() {
  // 在线模式不自动搜（每次搜索要打多个外部站点），回车或点按钮才触发
  if (searchMode.value !== "local") return;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    void lib.applyFilter({ q: searchInput.value.trim() });
  }, 300);
}

function onSearchEnter() {
  if (searchMode.value === "online") {
    void runOnlineSearch();
    return;
  }
  clearTimeout(searchTimer);
  void lib.applyFilter({ q: searchInput.value.trim() });
}

function onSourceClick(src: string) {
  void lib.applyFilter({ source: src });
}

function onCategoryClick(cat: string) {
  void lib.applyFilter({ category: cat });
}

function onSortChange(val: string) {
  void lib.applyFilter({ sort: val });
}

function onToggleUnscraped() {
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
  const next: Record<string, string> = {};
  if (lib.q) next.q = lib.q;
  if (lib.source) next.source = lib.source;
  if (lib.category) next.category = lib.category;
  if (lib.sort && lib.sort !== "updated") next.sort = lib.sort;
  if (lib.onlyUnscraped) next.unscraped = "1";
  if (lib.origin && lib.origin !== "all") next.origin = lib.origin;
  if (lib.page > 1) next.page = String(lib.page);
  return next;
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
  // 在线搜索登记进来的书：书库有记录但没有正文，进详情页只会看到空目录
  if (book?.online) {
    const link = book.book_url ? `\n原书地址：${book.book_url}` : "";
    void ElMessageBox.alert(
      `《${book.name}》是在线搜索登记的书，服务端还没有正文文件。\n可在原书源（${
        book.source || "未知书源"
      }）继续阅读。${link}`,
      "在线书",
      { confirmButtonText: "知道了", type: "info" },
    );
    return;
  }
  // 负数 id = 仅 Legado 的在线书，服务端没有正文，展示信息而不是进编辑页
  if (book && book.readable === false) {
    void showOnlineBook(book);
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

async function syncContinueFromWebDAV(): Promise<void> {
  if (syncingProgress || Date.now() - lastProgressSyncAt < PROGRESS_SYNC_THROTTLE) return;
  syncingProgress = true;
  lastProgressSyncAt = Date.now();
  try {
    const res = await pullAllBookProgress();
    console.debug(
      `[sync] WebDAV 批量进度：远端 ${res.remote_files} 个文件 / 命中书库 ${res.matched} 本 / 忽略书库外 ${res.ignored} 个 / 更新 ${res.updated} 本`,
    );
    if (res.updated > 0) {
      // DB 进度已变：刷新继续阅读卡片与封面进度条（不阻塞首屏）
      await Promise.all([loadContinue(), lib.loadBooks()]);
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

/** 打开批量编辑：每次重置表单 */
function openBatchEdit() {
  if (!selectedCount.value) return;
  editCategory.value = false;
  editSource.value = "";
  editCategoryName.value = "";
  editTagsMode.value = "append";
  editTags.value = "";
  editStatus.value = "";
  batchEditOpen.value = true;
}

async function submitBatchEdit() {
  const ids = [...selectedIds.value];
  if (!ids.length || batchBusy.value) return;
  if (editCategory.value && !editCategoryName.value) {
    ElMessage.warning("请选择分类");
    return;
  }
  const tags = editTags.value.trim();
  // 替换模式允许空串（清空标签）；追加/移除空串视为不修改
  const withTags = tags.length > 0 || editTagsMode.value === "replace";
  if (!editCategory.value && !withTags && !editStatus.value) {
    ElMessage.warning("没有需要修改的内容");
    return;
  }
  const body: BatchUpdateBody = { ids };
  if (editCategory.value) {
    body.category_source = editSource.value;
    body.category_name = editCategoryName.value;
  }
  if (withTags) {
    body.tags = tags;
    body.tags_mode = editTagsMode.value;
  }
  if (editStatus.value) body.status = editStatus.value;

  batchBusy.value = true;
  try {
    const res = await batchUpdateBooks(body);
    ElMessage.success(`已更新 ${res.updated_count} 本`);
    batchEditOpen.value = false;
    clearSelection();
    await Promise.all([lib.loadBooks(), lib.loadStats(), loadContinue()]);
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "批量更新失败");
  } finally {
    batchBusy.value = false;
  }
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
      <div class="page-actions">
        <div class="mode-toggle" role="group" aria-label="搜索范围">
          <button
            type="button"
            class="mode-btn"
            :class="{ 'is-active': searchMode === 'local' }"
            @click="switchMode('local')"
          >
            本地
          </button>
          <button
            type="button"
            class="mode-btn"
            :class="{ 'is-active': searchMode === 'online' }"
            @click="switchMode('online')"
          >
            在线
          </button>
        </div>

        <div class="search-box">
          <AppIcon name="search" :size="16" />
          <input
            v-model="searchInput"
            type="search"
            class="search-input"
            :placeholder="
              searchMode === 'online'
                ? '用书源搜索书名 / 作者，回车开始'
                : '搜索书名 / 作者 / 标签…'
            "
            @input="onSearchInput"
            @keyup.enter="onSearchEnter"
          />
          <button
            v-if="searchMode === 'online'"
            type="button"
            class="search-go"
            :disabled="onlineLoading"
            @click="runOnlineSearch"
          >
            {{ onlineLoading ? "搜索中" : "搜索" }}
          </button>
        </div>

        <select
          class="sort-select"
          :value="lib.sort"
          aria-label="排序"
          @change="onSortChange(($event.target as HTMLSelectElement).value)"
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
            @click="onViewModeChange('grid')"
          >
            <AppIcon name="grid" :size="16" />
          </button>
          <button
            type="button"
            class="toggle-btn"
            :class="{ 'is-active': viewMode === 'list' }"
            title="列表"
            @click="onViewModeChange('list')"
          >
            <AppIcon name="list" :size="16" />
          </button>
        </div>

        <div v-if="viewMode === 'grid'" class="size-ctrl">
          <span class="muted-xs">大小</span>
          <input
            v-model.number="coverWidth"
            type="range"
            min="120"
            max="220"
            step="10"
            class="size-slider"
            aria-label="封面大小"
            @change="onGridSize"
          />
        </div>
      </div>
    </header>

    <div v-if="(loadError || lib.error) && searchMode === 'local'" class="load-error" role="alert">
      <span>{{ loadError || lib.error }}</span>
      <button type="button" class="ghost-btn" @click="loadAll">重试</button>
    </div>

    <template v-if="searchMode === 'local'">
    <!-- 继续阅读：无筛选时展示最近在读的书，点击直接续读 -->
    <section
      v-if="showContinueRow && continueItems.length"
      class="continue-row"
      aria-label="继续阅读"
    >
      <div class="continue-head">
        <h2 class="continue-title">继续阅读</h2>
        <span class="muted-xs">最近在读</span>
      </div>
      <div class="continue-scroll">
        <div v-for="b in continueItems" :key="b.id" class="continue-item">
          <BookCard :book="b" :cover-width="140" @open="openReader" />
        </div>
      </div>
    </section>

    <div class="filter-bar">
      <div class="chip-row" role="tablist" aria-label="书架来源筛选">
        <button
          v-for="opt in ORIGIN_OPTS"
          :key="opt.value"
          type="button"
          class="chip"
          :class="{ 'is-active': lib.origin === opt.value }"
          @click="onOriginClick(opt.value)"
        >
          {{ opt.label }}
        </button>
        <span v-if="shelfCounts" class="origin-counts muted-xs">
          共有 {{ shelfCounts.both }} · 仅服务端 {{ shelfCounts.server }} · 仅手机
          {{ shelfCounts.legado }}
        </span>
      </div>

      <div class="chip-row" role="tablist" aria-label="书源筛选">
        <button
          v-for="src in SOURCE_OPTS"
          :key="src"
          type="button"
          class="chip"
          :class="{ 'is-active': lib.source === src }"
          @click="onSourceClick(src)"
        >
          {{ sourceLabels[src] || src || "全部" }}
        </button>
        <span class="divider" />
        <button
          type="button"
          class="chip"
          :class="{ 'is-active': lib.onlyUnscraped }"
          @click="onToggleUnscraped"
        >
          仅未刮削
        </button>
      </div>

      <div class="chip-row" role="tablist" aria-label="分类筛选">
        <button
          type="button"
          class="chip"
          :class="{ 'is-active': !lib.category }"
          @click="onCategoryClick('')"
        >
          全部
        </button>
        <button
          v-for="cat in lib.categoryNames"
          :key="cat"
          type="button"
          class="chip"
          :class="{ 'is-active': lib.category === cat }"
          @click="onCategoryClick(cat)"
        >
          {{ cat }}
        </button>
      </div>
    </div>

    <!-- 批量操作条 -->
    <div v-if="selectedCount" class="batch-bar">
      <span>已选 <strong>{{ selectedCount }}</strong> 本</span>
      <div class="batch-actions">
        <button type="button" class="ghost-btn" @click="selectAllOnPage">全选本页</button>
        <button type="button" class="ghost-btn" @click="clearSelection">取消选择</button>
        <button
          type="button"
          class="primary-btn"
          :disabled="batchBusy"
          @click="openBatchEdit"
        >
          批量编辑
        </button>
        <button
          type="button"
          class="primary-btn"
          :disabled="batchBusy"
          @click="onBatchScrape"
        >
          批量刮削
        </button>
        <button
          type="button"
          class="ghost-btn danger"
          :disabled="batchBusy"
          @click="onBatchDelete"
        >
          批量删除
        </button>
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
        :selectable="b.id > 0"
        :selected="selectedIds.has(b.id)"
        @open="openBook"
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
    </template>

    <!-- 在线搜索：用已导入的 Legado 书源检索，可登记进书库 -->
    <section v-else class="online-panel">
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
          输入关键词后回车，或用右侧「搜索」按钮。搜到喜欢的书可以「加入书库」，方便统一管理。
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
              <button
                v-for="src in item.sources"
                :key="src.source_id"
                type="button"
                class="oc-add"
                :disabled="!!addingKey"
                @click="onAddOnline(item, src)"
              >
                {{ addingKey === `${item.name}|${src.source_id}` ? "加入中…" : `加入书库 · ${src.source_name}` }}
              </button>
            </div>
          </div>
        </article>
      </div>
    </section>

    <!-- 批量编辑弹窗：只提交勾选项 -->
    <el-dialog v-model="batchEditOpen" title="批量编辑" width="460px" append-to-body>
      <div class="batch-form">
        <div class="batch-form-row">
          <el-checkbox v-model="editCategory">修改分类</el-checkbox>
        </div>
        <div v-if="editCategory" class="batch-form-row">
          <span class="batch-form-label">书源</span>
          <el-select v-model="editSource" size="small" style="width: 110px">
            <el-option label="本地" value="" />
            <el-option
              v-for="src in ['起点', '番茄', '纵横']"
              :key="src"
              :label="src"
              :value="src"
            />
          </el-select>
          <span class="batch-form-label">分类</span>
          <el-select
            v-model="editCategoryName"
            size="small"
            placeholder="选择分类"
            style="width: 150px"
          >
            <el-option v-for="cat in editCategoryOptions" :key="cat" :label="cat" :value="cat" />
          </el-select>
        </div>
        <div class="batch-form-row">
          <span class="batch-form-label">标签</span>
          <el-select v-model="editTagsMode" size="small" style="width: 92px">
            <el-option label="追加" value="append" />
            <el-option label="移除" value="remove" />
            <el-option label="替换" value="replace" />
          </el-select>
          <el-input
            v-model="editTags"
            size="small"
            placeholder="逗号分隔，留空不修改"
            style="flex: 1; min-width: 140px"
          />
        </div>
        <div class="batch-form-row">
          <span class="batch-form-label">状态</span>
          <el-select v-model="editStatus" size="small" style="width: 110px">
            <el-option label="不修改" value="" />
            <el-option label="连载" value="连载" />
            <el-option label="完结" value="完结" />
            <el-option label="未知" value="未知" />
          </el-select>
        </div>
        <p class="muted-xs">已选 {{ selectedCount }} 本，只修改勾选的项。</p>
      </div>
      <template #footer>
        <el-button @click="batchEditOpen = false">取消</el-button>
        <el-button type="primary" :loading="batchBusy" @click="submitBatchEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 在线书信息：仅 Legado 书架有，服务端没有正文 -->
    <el-dialog
      :model-value="!!onlineDetail"
      title="手机书架 · 在线书"
      width="480px"
      append-to-body
      @update:model-value="onlineDetail = null"
    >
      <div v-if="onlineDetail" v-loading="onlineBusy" class="online-detail">
        <div class="online-head">
          <img
            v-if="onlineDetail.cover_url || onlineDetail.cover_thumb_url"
            class="online-cover"
            :src="onlineDetail.cover_url || onlineDetail.cover_thumb_url || ''"
            :alt="onlineDetail.name"
          />
          <div class="online-meta">
            <h3>{{ onlineDetail.name }}</h3>
            <p>{{ onlineDetail.author || "佚名" }}</p>
            <p v-if="onlineDetail.source_name">书源：{{ onlineDetail.source_name }}</p>
            <p v-if="onlineDetail.latest_chapter">最新：{{ onlineDetail.latest_chapter }}</p>
            <p v-if="onlineDetail.dur_chapter_title">
              读到：{{ onlineDetail.dur_chapter_title }}
            </p>
            <p v-if="onlineDetail.read_at">时间：{{ onlineDetail.read_at }}</p>
          </div>
        </div>
        <p class="online-intro">{{ onlineDetail.intro || "暂无简介" }}</p>
        <el-alert
          type="info"
          :closable="false"
          show-icon
          title="正文保存在手机 Legado"
          description="这本书只在手机书架里，服务端没有正文文件，请在手机端继续阅读。要在本端阅读，可把书导入服务端书库。"
        />
        <div v-if="onlineDetail.book_url" class="online-link">
          <a :href="onlineDetail.book_url" target="_blank" rel="noopener noreferrer">
            打开书籍源站 ↗
          </a>
        </div>
      </div>
      <template #footer>
        <el-button type="primary" @click="onlineDetail = null">知道了</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.origin-counts {
  margin-left: 8px;
  white-space: nowrap;
}

/* 在线书信息弹窗 */
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

.mode-toggle {
  display: inline-flex;
  padding: 2px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-2);
}

.mode-btn {
  border: none;
  background: none;
  padding: 5px 12px;
  border-radius: calc(var(--radius-md) - 2px);
  color: var(--color-text-2);
  font-size: var(--text-xs);
  font-family: inherit;
  cursor: pointer;
}

.mode-btn.is-active {
  background: var(--color-surface);
  color: var(--color-accent);
  font-weight: 600;
  box-shadow: var(--shadow-sm);
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

.batch-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.batch-form-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.batch-form-label {
  min-width: 32px;
  font-size: var(--text-xs);
  color: var(--color-text-2);
}

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
  .search-box {
    min-width: 0;
    flex: 1;
  }

  .size-ctrl {
    display: none;
  }

  /* 移动端走尺寸档位（120px 起），不强制过小列宽 */
  .grid-wrap {
    gap: 10px;
  }

  /* 工具条纵向堆叠 */
  .page-actions {
    width: 100%;
  }

  /* 每行筛选各自横向滚动，避免两行被同一个容器绑在一起滚动 */
  .filter-bar {
    overflow: visible;
  }

  .chip-row {
    flex-wrap: nowrap;
    overflow-x: auto;
    padding-bottom: 4px;
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

/* ---------------------------- 在线搜索结果 ---------------------------- */
.online-panel {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
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
  .mode-toggle {
    order: -1;
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
