/** 阅读器偏好：字号 / 行距 / 主题 / 字体 / 宽度，统一读写 localStorage */
import { computed, ref } from "vue";
import type { Ref } from "vue";
import { FONT_STACKS } from "@/views/reader/constants";
import type { ReaderFont, ReaderTheme } from "@/views/reader/constants";

interface Options {
  pagedMode: Ref<boolean>;
  tocOpen: Ref<boolean>;
  autoHideTopbar: Ref<boolean>;
}

export function useReaderPreferences({ pagedMode, tocOpen, autoHideTopbar }: Options) {
  const fontSize = ref(Number(localStorage.getItem("novel_read_font") || 18));
  const lineHeight = ref(Number(localStorage.getItem("novel_read_leading") || 1.8));
  const readerTheme = ref<ReaderTheme>(
    (localStorage.getItem("novel_read_theme") as ReaderTheme) || "paper",
  );
  /** 正文字体：宋体 / 黑体 / 系统 */
  const readerFont = ref<ReaderFont>((() => {
    const saved = localStorage.getItem("novel_read_font_family") as ReaderFont | null;
    return saved && saved in FONT_STACKS ? saved : "serif";
  })());
  /** 正文最大宽度（px），窄屏由容器自动收缩 */
  const contentWidth = ref((() => {
    const n = Number(localStorage.getItem("novel_read_width") || 720);
    return Number.isFinite(n) ? Math.min(1000, Math.max(560, n)) : 720;
  })());

  const readerStyle = computed(() => ({
    fontSize: `${fontSize.value}px`,
    lineHeight: String(lineHeight.value),
  }));

  /** 正文排版：字体族与内容宽度 */
  const articleStyle = computed(() => ({
    fontFamily: FONT_STACKS[readerFont.value] || FONT_STACKS.serif,
    maxWidth: `${contentWidth.value}px`,
  }));

  const themeClass = computed(() => `theme-${readerTheme.value}`);

  function persistPrefs() {
    localStorage.setItem("novel_read_font", String(fontSize.value));
    localStorage.setItem("novel_read_leading", String(lineHeight.value));
    localStorage.setItem("novel_read_theme", readerTheme.value);
    localStorage.setItem("novel_read_font_family", readerFont.value);
    localStorage.setItem("novel_read_width", String(contentWidth.value));
    localStorage.setItem("novel_read_autohide", autoHideTopbar.value ? "1" : "0");
    localStorage.setItem("novel_read_mode", pagedMode.value ? "paged" : "scroll");
    localStorage.setItem("novel_read_toc", tocOpen.value ? "1" : "0");
  }

  /** 快捷键调整字号（14–28） */
  function changeFont(delta: number) {
    fontSize.value = Math.min(28, Math.max(14, fontSize.value + delta));
  }

  return {
    fontSize,
    lineHeight,
    readerTheme,
    readerFont,
    contentWidth,
    readerStyle,
    articleStyle,
    themeClass,
    persistPrefs,
    changeFont,
  };
}
