/** 阅读进度：百分比、写回、WebDAV 拉取编排与离开页面时的 Beacon 推送 */
import { computed, ref } from "vue";
import type { ComputedRef, Ref } from "vue";
import { pullBookProgress, updateReadProgress } from "@/api/admin";
import type { PullProgressResult } from "@/api/admin";
import type { ReaderSyncState } from "@/views/reader/constants";
import type { BookDetail } from "@/api/types";

interface TocItem {
  id: number;
  index: number;
  title: string;
}

interface Options {
  bookId: Ref<number>;
  book: Ref<BookDetail | null>;
  activeChapterId: Ref<number | null>;
  chapterText: Ref<string>;
  contentEl: Ref<HTMLElement | null>;
  pagedMode: Ref<boolean>;
  pageIndex: Ref<number>;
  pageCount: Ref<number>;
  scrollFraction: Ref<number>;
  chapterNo: ComputedRef<number>;
  chapterTotal: ComputedRef<number>;
  chapters: ComputedRef<TocItem[]>;
  activeChapter: ComputedRef<TocItem | null>;
  loadChapterContent: (chapterId: number) => Promise<void>;
}

export function useReaderProgress({
  bookId,
  book,
  activeChapterId,
  chapterText,
  contentEl,
  pagedMode,
  pageIndex,
  pageCount,
  scrollFraction,
  chapterNo,
  chapterTotal,
  chapters,
  activeChapter,
  loadChapterContent,
}: Options) {
  /** WebDAV 同步状态：超时不再阻塞阅读，晚到的远端进度给出手动入口 */
  const syncState = ref<ReaderSyncState>("idle");
  const lateRemote = ref<PullProgressResult | null>(null);
  let syncStateTimer: ReturnType<typeof setTimeout> | undefined;

  /** 远端进度带来的章内字符位置（绑定目标章节，只消费一次） */
  const pendingRemotePos = ref<{ chapterId: number; pos: number } | null>(null);

  /** Beacon 推送节流：pagehide 与 visibilitychange 可能连续触发 */
  let lastBeaconAt = 0;

  function setSyncState(state: ReaderSyncState) {
    syncState.value = state;
    clearTimeout(syncStateTimer);
    if (state === "synced" || state === "failed") {
      syncStateTimer = setTimeout(() => {
        if (syncState.value === state) syncState.value = "idle";
      }, 4000);
    }
  }

  /** 按真实总章数换算阅读进度 0–100 */
  const readPercent = computed(() => {
    const total = chapterTotal.value;
    const n = chapterNo.value;
    if (!total || !n) return Number(book.value?.read_percent ?? 0) || 0;
    return Math.min(100, Math.round((n / total) * 100));
  });

  /** 展示用整体进度：章节序号 + 章内滚动/页码占比 */
  const overallPercent = computed(() => {
    const total = chapterTotal.value;
    const n = chapterNo.value;
    if (!total || !n) return Number(book.value?.read_percent ?? 0) || 0;
    const frac = pagedMode.value
      ? pageCount.value > 0
        ? (pageIndex.value + 1) / pageCount.value
        : 0
      : scrollFraction.value;
    return Math.min(100, Math.max(0, Math.round(((n - 1 + frac) / total) * 100)));
  });

  /** 顶栏进度文案：第 n / 总 · x%（翻页模式附带页码） */
  const progressText = computed(() => {
    const total = chapterTotal.value;
    const n = chapterNo.value;
    if (!total || !n) return "";
    const pageInfo =
      pagedMode.value && pageCount.value > 1 ? ` · ${pageIndex.value + 1}/${pageCount.value} 页` : "";
    return `第 ${n} / ${total} 章 · ${overallPercent.value}%${pageInfo}`;
  });

  /** 写回阅读进度（百分比 + 章节序号），失败静默不打断阅读 */
  async function persistProgress() {
    if (!bookId.value) return;
    try {
      await updateReadProgress(bookId.value, {
        percent: readPercent.value,
        chapter_index: activeChapter.value?.index ?? -1,
      });
      if (book.value) book.value.read_percent = readPercent.value;
    } catch {
      /* 阅读器不阻断 */
    }
  }

  /** 起拉 WebDAV 进度（与书籍/目录加载并行） */
  function startPull(loadBookId: number): Promise<PullProgressResult | null> {
    return pullBookProgress(loadBookId).then(
      (r) => (bookId.value === loadBookId ? r : null),
      () => {
        if (bookId.value === loadBookId) setSyncState("failed");
        return null;
      },
    );
  }

  /** 最多等 1.4 秒；超时后先按本地进度阅读，晚到结果转成手动入口 */
  async function awaitPull(
    loadBookId: number,
    pullTask: Promise<PullProgressResult | null>,
  ): Promise<PullProgressResult | null> {
    const timeout = new Promise<null>((resolve) => setTimeout(() => resolve(null), 1400));
    const pulled = await Promise.race([pullTask, timeout]);
    if (pulled) {
      setSyncState(pulled.enabled ? "synced" : "idle");
      return pulled;
    }
    if (syncState.value !== "failed") {
      setSyncState("syncing");
      void pullTask.then((late) => {
        if (bookId.value !== loadBookId) return;
        if (!late) {
          setSyncState("failed");
          return;
        }
        if (!late.enabled) {
          setSyncState("idle");
          return;
        }
        if (late.updated && late.chapter_id) {
          lateRemote.value = late;
          setSyncState("remote_newer");
        } else {
          setSyncState("synced");
        }
      });
    }
    return pulled;
  }

  /** 晚到的远端进度：用户点击后再切换，避免打断当前阅读 */
  async function applyLateRemote() {
    const remote = lateRemote.value;
    if (!remote?.chapter_id) return;
    const target = chapters.value.find((c) => c.id === remote.chapter_id);
    lateRemote.value = null;
    if (!target) {
      setSyncState("idle");
      return;
    }
    if (typeof remote.chapter_pos === "number" && remote.chapter_pos > 0) {
      pendingRemotePos.value = { chapterId: target.id, pos: remote.chapter_pos };
    }
    setSyncState("synced");
    if (target.id === activeChapterId.value) {
      await loadChapterContent(target.id);
    } else {
      activeChapterId.value = target.id;
    }
  }

  /** 估算章内字符位置（滚动/翻页进度换算，供 Legado 落点参考） */
  function estimateChapterPos(): number {
    const text = chapterText.value || "";
    if (!text) return 0;
    if (pagedMode.value && pageCount.value > 1) {
      return Math.round((pageIndex.value / (pageCount.value - 1)) * text.length);
    }
    const el = contentEl.value;
    if (!el) return 0;
    const max = Math.max(1, el.scrollHeight - el.clientHeight);
    return Math.max(0, Math.min(text.length, Math.round((el.scrollTop / max) * text.length)));
  }

  /** 关闭/隐藏页面时把进度推送到 WebDAV（Beacon 不阻塞页面） */
  function pushProgressBeacon() {
    const bid = bookId.value;
    const chid = activeChapterId.value;
    if (!bid || !chid) return;
    const now = Date.now();
    if (now - lastBeaconAt < 4000) return;
    lastBeaconAt = now;
    const body = JSON.stringify({
      chapter_id: chid,
      chapter_pos: estimateChapterPos(),
      client_time: now,
    });
    const url = `/api/admin/books/${bid}/sync/push`;
    // 优先 fetch+keepalive（保留 JSON Content-Type），失败再退回 Beacon
    try {
      void fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
        credentials: "include",
        keepalive: true,
      }).catch(() => {
        if (navigator.sendBeacon) {
          navigator.sendBeacon(url, new Blob([body], { type: "application/json" }));
        }
      });
    } catch {
      if (navigator.sendBeacon) {
        navigator.sendBeacon(url, new Blob([body], { type: "application/json" }));
      }
    }
  }

  function onVisibilityChange() {
    if (document.visibilityState === "hidden") pushProgressBeacon();
  }

  /** 切书时重置同步状态与晚到进度 */
  function resetSync() {
    lateRemote.value = null;
    pendingRemotePos.value = null;
    clearTimeout(syncStateTimer);
    syncState.value = "idle";
  }

  function disposeSync() {
    clearTimeout(syncStateTimer);
  }

  return {
    syncState,
    lateRemote,
    pendingRemotePos,
    setSyncState,
    readPercent,
    overallPercent,
    progressText,
    persistProgress,
    startPull,
    awaitPull,
    applyLateRemote,
    estimateChapterPos,
    pushProgressBeacon,
    onVisibilityChange,
    resetSync,
    disposeSync,
  };
}
