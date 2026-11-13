/** 书内搜索：请求、命中列表与同章跳转高亮 */
import { nextTick, ref } from "vue";
import type { Ref } from "vue";
import { searchBookContent } from "@/api/admin";
import type { BookSearchHit } from "@/api/admin";

export interface SearchJump {
  chapterId: number;
  offset: number;
  query: string;
}

interface Options {
  bookId: Ref<number>;
  activeChapterId: Ref<number | null>;
  chapterText: Ref<string>;
  contentEl: Ref<HTMLElement | null>;
  pagedMode: Ref<boolean>;
  pageCount: Ref<number>;
  pageIndex: Ref<number>;
  relayoutPaged: () => Promise<void>;
  scrollTopForChar: (charPos: number) => number;
  /** 程序化滚动（不参与顶栏显示/隐藏判断） */
  scrollToTop: (top: number) => void;
  goChapter: (id: number) => void;
}

export function useReaderSearch({
  bookId,
  activeChapterId,
  chapterText,
  contentEl,
  pagedMode,
  pageCount,
  pageIndex,
  relayoutPaged,
  scrollTopForChar,
  scrollToTop,
  goChapter,
}: Options) {
  const searchOpen = ref(false);
  const searchQuery = ref("");
  const searchLoading = ref(false);
  const searchError = ref("");
  const searchHits = ref<BookSearchHit[]>([]);
  const searchTotal = ref(0);
  /** 搜索请求序号：快速连续搜索时丢弃过期响应 */
  let searchSeq = 0;

  /** 搜索命中跳转：目标章节 + 字内偏移 + 关键词（用于高亮） */
  const pendingSearchJump = ref<SearchJump | null>(null);
  const activeHighlightQuery = ref("");

  async function runSearch() {
    const q = searchQuery.value.trim();
    if (!q || !bookId.value) return;
    const seq = ++searchSeq;
    searchLoading.value = true;
    searchError.value = "";
    try {
      const data = await searchBookContent(bookId.value, q);
      if (seq !== searchSeq) return;
      searchHits.value = data.items || [];
      searchTotal.value = Number(data.total) || 0;
      if (!searchHits.value.length) searchError.value = "没有找到相关内容";
    } catch (e) {
      if (seq !== searchSeq) return;
      searchHits.value = [];
      searchTotal.value = 0;
      searchError.value = e instanceof Error ? e.message : "搜索失败";
    } finally {
      if (seq === searchSeq) searchLoading.value = false;
    }
  }

  /** 对当前已加载章节执行搜索跳转（同章命中时） */
  async function applySearchJump() {
    const jump = pendingSearchJump.value;
    if (!jump || jump.chapterId !== activeChapterId.value || !chapterText.value) return;
    pendingSearchJump.value = null;
    activeHighlightQuery.value = jump.query;
    await nextTick();
    if (!contentEl.value) return;
    if (pagedMode.value) {
      await relayoutPaged();
      const last = Math.max(0, pageCount.value - 1);
      pageIndex.value = Math.min(
        last,
        Math.max(0, Math.round((jump.offset / Math.max(1, chapterText.value.length)) * last)),
      );
      return;
    }
    scrollToTop(scrollTopForChar(jump.offset));
  }

  function openSearchResult(hit: BookSearchHit) {
    pendingSearchJump.value = {
      chapterId: hit.chapter_id,
      offset: hit.offset,
      query: searchQuery.value.trim(),
    };
    searchOpen.value = false;
    if (hit.chapter_id === activeChapterId.value) {
      void applySearchJump();
    } else {
      goChapter(hit.chapter_id);
    }
  }

  function resetSearch() {
    searchOpen.value = false;
    searchQuery.value = "";
    searchHits.value = [];
    searchTotal.value = 0;
    searchError.value = "";
  }

  return {
    searchOpen,
    searchQuery,
    searchLoading,
    searchError,
    searchHits,
    searchTotal,
    pendingSearchJump,
    activeHighlightQuery,
    runSearch,
    applySearchJump,
    openSearchResult,
    resetSearch,
  };
}
