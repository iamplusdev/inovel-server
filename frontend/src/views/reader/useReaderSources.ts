/** 在线书换源面板：候选书源搜索、切换与重载目录 */
import { ref } from "vue";
import type { ComputedRef, Ref } from "vue";
import { listOnlineBookSources, switchOnlineBookSource } from "@/api/online";
import type { OnlineSourceCandidate } from "@/api/online";
import type { BookDetail } from "@/api/types";

interface Options {
  bookId: Ref<number>;
  book: Ref<BookDetail | null>;
  isOnlineBook: ComputedRef<boolean>;
  chapterText: Ref<string>;
  chapterError: Ref<string>;
  activeChapterId: Ref<number | null>;
  persistProgress: () => Promise<void>;
  /** 切源后重新拉书籍/目录并定位章节 */
  reload: () => Promise<void>;
  revealTopbar: () => void;
  /** 打开换源面板前收起其它面板 */
  closeOtherPanels: () => void;
}

export function useReaderSources({
  bookId,
  book,
  isOnlineBook,
  chapterText,
  chapterError,
  activeChapterId,
  persistProgress,
  reload,
  revealTopbar,
  closeOtherPanels,
}: Options) {
  const sourceOpen = ref(false);
  const sourceLoading = ref(false);
  const sourceError = ref("");
  const sourceHint = ref("");
  const sourceItems = ref<OnlineSourceCandidate[]>([]);
  const sourceCurrentName = ref("");
  /** 正在切换到的书源 id（0=空闲） */
  const switchingSource = ref(0);
  let sourceSeq = 0;

  function toggleSources() {
    if (!isOnlineBook.value) return;
    if (sourceOpen.value) sourceOpen.value = false;
    else openSources();
  }

  function openSources() {
    sourceOpen.value = true;
    closeOtherPanels();
    revealTopbar();
    void loadSources();
  }

  async function loadSources() {
    if (!bookId.value) return;
    const seq = ++sourceSeq;
    sourceLoading.value = true;
    sourceError.value = "";
    try {
      const data = await listOnlineBookSources(bookId.value);
      if (seq !== sourceSeq) return;
      sourceItems.value = data.items || [];
      sourceCurrentName.value = data.current?.source_name || book.value?.source || "";
      sourceHint.value = data.hint || "";
    } catch (e) {
      if (seq !== sourceSeq) return;
      sourceItems.value = [];
      sourceHint.value = "";
      sourceError.value = e instanceof Error ? e.message : "搜索书源失败";
    } finally {
      if (seq === sourceSeq) sourceLoading.value = false;
    }
  }

  /** 切换书源：先落进度，再重绑并重建目录，最后按章节序号恢复阅读 */
  async function applySource(item: OnlineSourceCandidate) {
    if (item.current || switchingSource.value || !bookId.value) return;
    switchingSource.value = item.source_id;
    sourceError.value = "";
    try {
      await persistProgress();
      await switchOnlineBookSource(bookId.value, {
        source_id: item.source_id,
        book_url: item.book_url,
        toc_url: item.toc_url,
        word_count: item.word_count,
      });
      chapterText.value = "";
      chapterError.value = "";
      // 章节 id 可能被原地复用，先清空以便重新定位并加载新书源正文
      activeChapterId.value = null;
      await reload();
      sourceOpen.value = false;
    } catch (e) {
      sourceError.value = e instanceof Error ? e.message : "切换书源失败";
    } finally {
      switchingSource.value = 0;
    }
  }

  function resetSources() {
    sourceOpen.value = false;
    sourceItems.value = [];
    sourceError.value = "";
    sourceHint.value = "";
    sourceCurrentName.value = "";
    switchingSource.value = 0;
    sourceSeq++;
  }

  return {
    sourceOpen,
    sourceLoading,
    sourceError,
    sourceHint,
    sourceItems,
    sourceCurrentName,
    switchingSource,
    toggleSources,
    openSources,
    loadSources,
    applySource,
    resetSources,
  };
}
