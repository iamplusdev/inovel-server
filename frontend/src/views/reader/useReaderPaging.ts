/** 左右翻页模式：页宽/页数测量、横向平移样式、翻页与跨章落页 */
import { computed, nextTick, ref, watch, type Ref } from "vue";
import { FONT_STACKS, type ReaderFont } from "@/views/reader/constants";

/** 页与页之间的横向间距（px），测量与平移共用 */
export const PAGE_GAP = 48;

interface PagingChapter {
  id: number;
  index: number;
  title: string;
}

interface PagingOptions {
  pagedMode: Ref<boolean>;
  chapterText: Ref<string>;
  activeChapterId: Ref<number | null>;
  fontSize: Ref<number>;
  lineHeight: Ref<number>;
  contentWidth: Ref<number>;
  readerFont: Ref<ReaderFont>;
  tocOpen: Ref<boolean>;
  settingsOpen: Ref<boolean>;
  trackEl: Ref<HTMLElement | null>;
  articleEl: Ref<HTMLElement | null>;
  chapters: () => PagingChapter[];
  chapterNo: () => number;
  hasNext: () => boolean;
  rememberPage: (chapterId: number, page: number) => void;
  goChapter: (id: number) => void;
  goNextChapter: () => void;
  scheduleTopbarHide: () => void;
}

export function useReaderPaging({
  pagedMode,
  chapterText,
  activeChapterId,
  fontSize,
  lineHeight,
  contentWidth,
  readerFont,
  tocOpen,
  settingsOpen,
  trackEl,
  articleEl,
  chapters,
  chapterNo,
  hasNext,
  rememberPage,
  goChapter,
  goNextChapter,
  scheduleTopbarHide,
}: PagingOptions) {
  const pageWidth = ref(720);
  const pageCount = ref(1);
  const pageIndex = ref(0);
  /** 向上跨章时指定要落到最后一页的章节 id（避免标记串到其它章节） */
  const pendingLastPageFor = ref<number | null>(null);

  /** 首次进入翻页模式的交互提示（只提示一次） */
  const pagedHint = ref(false);
  let pagedHintTimer: ReturnType<typeof setTimeout> | undefined;

  function maybeShowPagedHint() {
    if (localStorage.getItem("novel_read_paged_hint") === "1") return;
    localStorage.setItem("novel_read_paged_hint", "1");
    pagedHint.value = true;
    clearTimeout(pagedHintTimer);
    pagedHintTimer = setTimeout(() => {
      pagedHint.value = false;
    }, 6000);
  }

  function dismissPagedHint() {
    pagedHint.value = false;
  }

  /** 测量翻页模式的总页数（横向多栏宽度） */
  function measurePaged() {
    const art = articleEl.value;
    if (!pagedMode.value || !art) return;
    const width = pageWidth.value;
    if (width <= 0) return;
    const total = Math.max(1, Math.ceil((art.scrollWidth + PAGE_GAP) / (width + PAGE_GAP)));
    pageCount.value = total;
    if (pageIndex.value > total - 1) pageIndex.value = Math.max(0, total - 1);
  }

  /** 重新计算页宽与页数（切模式/改字号/改宽度/窗口变化时调用） */
  async function relayoutPaged() {
    if (!pagedMode.value || !chapterText.value) return;
    await nextTick();
    const w = trackEl.value?.clientWidth || 0;
    if (w > 0 && w !== pageWidth.value) pageWidth.value = w;
    await nextTick();
    await new Promise<void>((resolve) => {
      requestAnimationFrame(() => resolve());
    });
    measurePaged();
  }

  /** 翻页模式：正文按栏排布，整体横向平移展示当前页 */
  const pagedArticleStyle = computed<Record<string, string>>(() => ({
    fontFamily: FONT_STACKS[readerFont.value] || FONT_STACKS.serif,
    columnWidth: `${pageWidth.value}px`,
    columnGap: `${PAGE_GAP}px`,
    columnFill: "auto",
    transform: `translateX(-${pageIndex.value * (pageWidth.value + PAGE_GAP)}px)`,
  }));

  /** 翻页模式下的内容宽度变量（供 track 的 max-width 使用） */
  const pagedVars = computed<Record<string, string>>(() => {
    const vars: Record<string, string> = {};
    if (pagedMode.value) vars["--paged-width"] = `${contentWidth.value}px`;
    return vars;
  });

  /** 翻页模式：下一页 / 下一章 */
  function nextPage() {
    if (!pagedMode.value) return;
    if (pageIndex.value + 1 < pageCount.value) {
      pageIndex.value += 1;
      const id = activeChapterId.value;
      if (id) rememberPage(id, pageIndex.value);
      scheduleTopbarHide();
      return;
    }
    if (hasNext()) goNextChapter();
  }

  /** 翻页模式：上一页 / 上一章（跨章落到上一章最后一页） */
  function prevPage() {
    if (!pagedMode.value) return;
    if (pageIndex.value > 0) {
      pageIndex.value -= 1;
      const id = activeChapterId.value;
      if (id) rememberPage(id, pageIndex.value);
      scheduleTopbarHide();
      return;
    }
    const list = chapters();
    const n = chapterNo();
    const target = n > 1 ? list[n - 2] : undefined;
    if (target) {
      pendingLastPageFor.value = target.id;
      goChapter(target.id);
    }
  }

  // 字号/行距/宽度/字体变化后重新分页（滑杆拖动时防抖）
  let pagedLayoutTimer: ReturnType<typeof setTimeout> | undefined;
  watch([fontSize, lineHeight, contentWidth, readerFont], () => {
    if (!pagedMode.value) return;
    clearTimeout(pagedLayoutTimer);
    pagedLayoutTimer = setTimeout(() => void relayoutPaged(), 120);
  });

  // 目录/排版面板开合会改变桌面端正文宽度，需要重新分页
  watch([tocOpen, settingsOpen], async () => {
    if (!pagedMode.value) return;
    await nextTick();
    await relayoutPaged();
  });

  let pagedResizeTimer: ReturnType<typeof setTimeout> | undefined;

  /** 窗口尺寸变化后延迟重排（拖拽窗口时防抖） */
  function scheduleResizeRelayout() {
    clearTimeout(pagedResizeTimer);
    pagedResizeTimer = setTimeout(() => void relayoutPaged(), 150);
  }

  /** 切章/切书时重置页码状态 */
  function resetPaging() {
    pendingLastPageFor.value = null;
    pageIndex.value = 0;
    pageCount.value = 1;
  }

  function disposePaging() {
    clearTimeout(pagedHintTimer);
    clearTimeout(pagedLayoutTimer);
    clearTimeout(pagedResizeTimer);
  }

  return {
    pageWidth,
    pageCount,
    pageIndex,
    pendingLastPageFor,
    pagedHint,
    pagedArticleStyle,
    pagedVars,
    maybeShowPagedHint,
    dismissPagedHint,
    measurePaged,
    relayoutPaged,
    nextPage,
    prevPage,
    scheduleResizeRelayout,
    resetPaging,
    disposePaging,
  };
}
